#!/usr/bin/env python3
"""
ABS Spine Server — Custom book spine images for AudiobookShelf

A zero-dependency HTTP server with a built-in web UI.
Connects to your ABS instance, auto-matches spine images to books by title,
and serves them to the mobile app.

Usage:
  python3 spine_server.py                # Start server (opens setup in browser)
  python3 spine_server.py --list-books   # Show all books with IDs
  python3 spine_server.py --scan-library # Find spine.png files in your ABS library
  python3 spine_server.py --no-open      # Start without opening browser
"""

import os
import re
import sys
import json
import time
import socket
import shutil
import platform
import subprocess
import threading
import webbrowser
import unicodedata
import argparse
import mimetypes
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

try:
    from http.server import ThreadingHTTPServer
except ImportError:
    import socketserver
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True


# =============================================================================
# CONFIGURATION
# =============================================================================

_script_dir = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_script_dir, "config.json")
DEFAULT_SPINES_DIR = os.path.join(_script_dir, "spines")
DEFAULT_PORT = 8786


def load_config():
    """Load config from config.json, with env var overrides."""
    config = {
        "abs_url": "",
        "abs_api_key": "",
        "port": DEFAULT_PORT,
        "spines_dir": DEFAULT_SPINES_DIR,
        "linked_folders": [],
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                config.update(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass

    if os.environ.get("ABS_URL"):
        config["abs_url"] = os.environ["ABS_URL"]
    if os.environ.get("ABS_API_KEY"):
        config["abs_api_key"] = os.environ["ABS_API_KEY"]
    if os.environ.get("PORT"):
        config["port"] = int(os.environ["PORT"])
    if os.environ.get("SPINES_DIR"):
        config["spines_dir"] = os.environ["SPINES_DIR"]
    if os.environ.get("LIBRARY_PATH"):
        config["library_path"] = os.environ["LIBRARY_PATH"]

    return config


def save_config(config):
    """Save config to config.json."""
    saveable = {
        "abs_url": config.get("abs_url", ""),
        "abs_api_key": config.get("abs_api_key", ""),
        "port": config.get("port", DEFAULT_PORT),
        "linked_folders": config.get("linked_folders", []),
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(saveable, f, indent=2)


def get_local_ip():
    """Detect this machine's local network IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def pick_folder():
    """Open a native OS folder picker dialog. Returns path string or None."""
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["osascript", "-e",
                 'POSIX path of (choose folder with prompt "Select a folder containing spine images")'],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return result.stdout.strip().rstrip("/")

        elif system == "Windows":
            ps = (
                'Add-Type -AssemblyName System.Windows.Forms;'
                '$d=New-Object System.Windows.Forms.FolderBrowserDialog;'
                '$d.Description="Select a folder containing spine images";'
                'if($d.ShowDialog() -eq "OK"){$d.SelectedPath}'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

        else:  # Linux
            for cmd in [
                ["zenity", "--file-selection", "--directory",
                 "--title=Select spine images folder"],
                ["kdialog", "--getexistingdirectory", ".",
                 "--title", "Select spine images folder"],
            ]:
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout.strip()
                except FileNotFoundError:
                    continue
    except Exception:
        pass
    return None


# Global config (loaded at startup)
_config = {}


# =============================================================================
# ABS API HELPERS
# =============================================================================

def abs_api_get(endpoint, abs_url=None, api_key=None):
    """Make a GET request to the ABS API. Returns parsed JSON or None."""
    url = abs_url or _config.get("abs_url", "")
    key = api_key or _config.get("abs_api_key", "")

    if not key or not url:
        return None

    full_url = f"{url.rstrip('/')}{endpoint}"
    req = urllib.request.Request(full_url)
    req.add_header("Authorization", f"Bearer {key}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, Exception):
        return None


def get_all_books(abs_url=None, api_key=None):
    """Get ALL books from ALL libraries."""
    books = []
    data = abs_api_get("/api/libraries", abs_url, api_key)
    if not data:
        return books

    for lib in data.get("libraries", []):
        lib_id = lib["id"]
        lib_name = lib.get("name", lib_id)
        items_data = abs_api_get(
            f"/api/libraries/{lib_id}/items?limit=100000", abs_url, api_key
        )
        if not items_data:
            continue

        for item in items_data.get("results", []):
            meta = item.get("media", {}).get("metadata", {})
            books.append({
                "id": item["id"],
                "title": meta.get("title", "Unknown"),
                "author": meta.get("authorName", "Unknown"),
                "path": item.get("path", ""),
                "libraryId": lib_id,
                "libraryName": lib_name,
            })

    return books


def test_connection(abs_url, api_key):
    """Test ABS connection. Returns {ok, message, books_count}."""
    if not abs_url:
        return {"ok": False, "message": "No URL provided."}
    if not api_key:
        return {"ok": False, "message": "No API key provided."}

    try:
        data = abs_api_get("/api/libraries", abs_url, api_key)
        if data is None:
            return {"ok": False, "message": "Could not connect. Check the URL and API key."}

        total = 0
        for lib in data.get("libraries", []):
            items = abs_api_get(
                f"/api/libraries/{lib['id']}/items?limit=100000", abs_url, api_key
            )
            if items:
                total += len(items.get("results", []))

        return {"ok": True, "message": f"Connected! Found {total} books.", "books_count": total}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# =============================================================================
# TITLE MATCHING
# =============================================================================

def normalize(text):
    """
    Normalize a string for fuzzy comparison.
    "The Hitchhiker's Guide" -> "hitchhikers guide"
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    text = re.sub(r"[_.\-]+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_title_index(books):
    """
    Build lookup tables: normalized_string -> book_id.
    Multiple keys per book for flexible matching.
    """
    index = {}
    collisions = {}

    def add(key, book_id, description):
        if not key:
            return
        if key in index and index[key] != book_id:
            collisions[key] = collisions.get(key, [index[key]])
            collisions[key].append(book_id)
            return
        index[key] = book_id

    for book in books:
        bid = book["id"]
        title = book["title"]
        author = book["author"]
        nt = normalize(title)
        na = normalize(author)

        add(nt, bid, "title")

        if na and na != "unknown":
            add(f"{na} {nt}", bid, "author title")
            add(f"{nt} {na}", bid, "title author")

        for article in ("the ", "a ", "an "):
            if nt.startswith(article):
                add(nt[len(article):], bid, "title without article")
                break

        for sep in [":", " - "]:
            if sep in title:
                base = normalize(title.split(sep)[0])
                add(base, bid, "title without subtitle")

    for key in collisions:
        if key in index:
            del index[key]

    return index, collisions


def match_filename_to_book(filename, title_index):
    """
    Match a filename (without extension) to a book ID.
    Returns (book_id, match_type) or (None, None).
    """
    if filename.startswith("li_"):
        return filename, "id"

    nf = normalize(filename)
    if not nf:
        return None, None

    if nf in title_index:
        return title_index[nf], "exact"

    for sep in [" - ", " — ", " – "]:
        if sep in filename:
            parts = filename.split(sep, 1)
            for combo in [parts, list(reversed(parts))]:
                joined = normalize(" ".join(combo))
                if joined in title_index:
                    return title_index[joined], "author-title split"
                for part in combo:
                    np = normalize(part)
                    if np in title_index:
                        return title_index[np], "part match"

    best_match = None
    best_len = 0
    for key, book_id in title_index.items():
        if len(key) < 5 or len(nf) < 5:
            continue
        if nf in key or key in nf:
            overlap = min(len(nf), len(key))
            if overlap > best_len:
                best_len = overlap
                best_match = book_id

    if best_match:
        return best_match, "fuzzy"

    return None, None


# =============================================================================
# SPINE IMAGE MANAGEMENT
# =============================================================================

_title_index = {}
_books_by_id = {}
_books_list = []
_index_loaded = False
_collisions = {}
_lock = threading.Lock()


def load_book_index():
    """Fetch books from ABS and build the matching index."""
    global _title_index, _books_by_id, _books_list, _index_loaded, _collisions

    api_key = _config.get("abs_api_key", "")

    if not api_key:
        _index_loaded = True
        return 0

    print("Connecting to ABS to build book index...")
    books = get_all_books()

    with _lock:
        if books:
            _title_index, _collisions = build_title_index(books)
            _books_by_id = {b["id"]: b for b in books}
            _books_list = books
        _index_loaded = True

    count = len(books)
    if count:
        print(f"Indexed {count} books ({len(_title_index)} matchable keys)")
    else:
        print("WARNING: Could not load books from ABS.")

    if _collisions:
        print(f"  {len(_collisions)} ambiguous titles (use book IDs for these)")

    return count


def reload_index():
    """Re-index books and rescan spines. Called after config changes."""
    load_book_index()
    spines, unmatched = find_spine_files()
    with _lock:
        SpineHandler._spine_files = spines
        SpineHandler._manifest = build_manifest(spines)
        SpineHandler._last_scan = time.time()


def ensure_spines_dir():
    """Create the default spines folder if it doesn't exist."""
    spines_dir = _config.get("spines_dir", DEFAULT_SPINES_DIR)
    os.makedirs(spines_dir, exist_ok=True)


def get_all_spine_dirs():
    """Get list of all directories to scan for spine images."""
    dirs = [_config.get("spines_dir", DEFAULT_SPINES_DIR)]
    for folder in _config.get("linked_folders", []):
        if folder not in dirs:
            dirs.append(folder)
    return dirs


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def find_spine_files():
    """Scan all spine directories for image files. Returns (spines, unmatched)."""
    spines = {}
    unmatched = []

    for spine_dir in get_all_spine_dirs():
        if not os.path.isdir(spine_dir):
            continue

        for filename in os.listdir(spine_dir):
            filepath = os.path.join(spine_dir, filename)
            if not os.path.isfile(filepath):
                continue

            name, ext = os.path.splitext(filename)
            if ext.lower() not in IMAGE_EXTS:
                continue

            book_id, match_type = match_filename_to_book(name, _title_index)

            if book_id:
                if book_id not in spines or match_type == "id":
                    spines[book_id] = filepath
            else:
                unmatched.append(filename)

    return spines, unmatched


def scan_library_for_spines(books):
    """Walk ABS library folders looking for spine.png/jpg files. Copies to spines/."""
    library_path = _config.get("library_path", "")
    if not library_path:
        print("ERROR: LIBRARY_PATH not set.")
        return 0
    if not os.path.isdir(library_path):
        print(f"ERROR: LIBRARY_PATH '{library_path}' not found.")
        return 0

    ensure_spines_dir()
    spines_dir = _config.get("spines_dir", DEFAULT_SPINES_DIR)
    found = 0

    for book in books:
        abs_path = book["path"]
        candidates = [abs_path]
        parts = Path(abs_path).parts
        if len(parts) >= 2:
            candidates.append(os.path.join(library_path, *parts[-2:]))
        if len(parts) >= 3:
            candidates.append(os.path.join(library_path, *parts[-3:]))
        candidates.append(os.path.join(library_path, abs_path.lstrip("/")))

        for folder in candidates:
            if not os.path.isdir(folder):
                continue
            for spine_name in ["spine.png", "spine.jpg", "spine.jpeg", "spine.webp"]:
                spine_path = os.path.join(folder, spine_name)
                if os.path.isfile(spine_path):
                    ext = os.path.splitext(spine_name)[1]
                    dest = os.path.join(spines_dir, f"{book['id']}{ext}")
                    if not os.path.exists(dest):
                        shutil.copy2(spine_path, dest)
                        print(f"  Found: {book['title']}")
                    found += 1
                    break
            else:
                continue
            break

    return found


def build_manifest(spine_files):
    """Build the manifest JSON."""
    return {
        "items": sorted(spine_files.keys()),
        "version": 1,
        "count": len(spine_files),
        "generated": datetime.now().isoformat(),
    }


# =============================================================================
# WEB UI
# =============================================================================

def get_ui_html():
    """Return the complete HTML for the web UI."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ABS Spine Server</title>
<style>
:root {
  --bg:#09090b; --surface:#18181b; --border:#27272a;
  --text:#fafafa; --text2:#a1a1aa;
  --accent:#F3B60C; --accent-dim:rgba(243,182,12,0.12);
  --success:#22c55e; --success-dim:rgba(34,197,94,0.12);
  --error:#ef4444; --error-dim:rgba(239,68,68,0.12);
  --warning:#f59e0b; --warning-dim:rgba(245,158,11,0.12);
  --radius:12px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.5;min-height:100vh}
.wrap{max-width:720px;margin:0 auto;padding:32px 16px 64px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.header h1{font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px}
.header h1 span{font-size:22px}
.badge{padding:3px 10px;border-radius:100px;font-size:11px;font-weight:600;letter-spacing:0.02em}
.badge-ok{background:var(--success-dim);color:var(--success)}
.badge-warn{background:var(--warning-dim);color:var(--warning)}
.badge-err{background:var(--error-dim);color:var(--error)}
.badge-dim{background:var(--border);color:var(--text2);font-size:10px;padding:2px 8px;border-radius:4px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px}
.card-hl{border-color:rgba(243,182,12,0.25);background:linear-gradient(135deg,rgba(243,182,12,0.04),transparent)}
.card h2{font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:12px}
.card-hdr{display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none}
.card-hdr h2{margin-bottom:0}
.chev{color:var(--text2);font-size:12px;transition:transform 0.2s}
.chev.open{transform:rotate(180deg)}
.hide{display:none}
.sub{color:var(--text2);margin-bottom:20px;font-size:14px}
.field{margin-bottom:16px}
.field label{display:block;font-size:13px;font-weight:500;margin-bottom:6px}
.field input,.field-row input{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;font-family:inherit;outline:none;transition:border-color 0.2s}
.field input:focus,.field-row input:focus{border-color:var(--accent)}
.help{display:block;font-size:12px;color:var(--text2);margin-top:6px}
.field-row{display:flex;gap:8px;margin-top:12px}
.field-row input{flex:1}
.btn-row{display:flex;gap:8px;margin-top:8px}
.btn{padding:10px 20px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.15s;font-family:inherit}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.btn-pri{background:var(--accent);color:#000}
.btn-pri:hover:not(:disabled){filter:brightness(1.1)}
.btn-sec{background:var(--border);color:var(--text)}
.btn-sec:hover:not(:disabled){background:#333}
.btn-sm{padding:6px 12px;font-size:12px;background:var(--border);color:var(--text);border:none;border-radius:6px;cursor:pointer;font-family:inherit}
.btn-sm:hover{background:#333}
.btn-xs{padding:3px 8px;font-size:11px;background:transparent;color:var(--error);border:1px solid rgba(239,68,68,0.3);border-radius:4px;cursor:pointer}
.btn-xs:hover{background:var(--error-dim)}
.result{padding:12px;border-radius:8px;margin-top:12px;font-size:13px}
.result.load{background:var(--accent-dim);color:var(--accent)}
.result.ok{background:var(--success-dim);color:var(--success)}
.result.err{background:var(--error-dim);color:var(--error)}
.url-box{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.url-box code{flex:1;padding:12px 16px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-family:'SF Mono',Menlo,'Cascadia Code',monospace;font-size:15px;color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.stat{text-align:center;padding:20px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.stat-val{display:block;font-size:36px;font-weight:700;color:var(--accent);line-height:1}
.stat-lbl{display:block;font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.05em;margin-top:8px}
.folder{display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--border)}
.folder:last-child{border-bottom:none}
.folder-path{flex:1;font-size:13px;font-family:'SF Mono',Menlo,monospace;word-break:break-all}
.folder-info{font-size:12px;color:var(--text2);white-space:nowrap}
.list{max-height:400px;overflow-y:auto}
.item{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px}
.item:last-child{border-bottom:none}
.item .f{font-family:'SF Mono',Menlo,monospace;color:var(--text2);min-width:180px;word-break:break-all}
.item .a{color:var(--accent);flex-shrink:0}
.item .b{flex:1}
.item.un .f{color:var(--error)}
.empty{padding:24px;text-align:center;color:var(--text2);font-size:13px}
.empty b{color:var(--text);display:block;margin-bottom:4px}
.setup-center{max-width:480px;margin:60px auto 0}
@media(max-width:600px){
  .stats{grid-template-columns:1fr}
  .item{flex-direction:column;align-items:flex-start}
  .item .f{min-width:0}
  .field-row{flex-direction:column}
  .btn-row{flex-direction:column}
  .url-box{flex-direction:column}
  .url-box code{width:100%}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1><span>&#128218;</span> ABS Spine Server</h1>
    <span id="badge" class="badge badge-warn">Loading</span>
  </div>

  <!-- SETUP VIEW -->
  <div id="V-setup" class="setup-center hide">
    <div class="card">
      <h2>Welcome</h2>
      <p class="sub">Connect to your AudiobookShelf server to get started.</p>
      <div class="field">
        <label for="s-url">AudiobookShelf URL</label>
        <input id="s-url" type="text" placeholder="http://192.168.1.50:13378" spellcheck="false">
        <span class="help">The address where you access ABS in your browser</span>
      </div>
      <div class="field">
        <label for="s-key">API Key</label>
        <input id="s-key" type="password" placeholder="Paste your API key here" spellcheck="false">
        <span class="help">ABS &rarr; Settings &rarr; Users &rarr; your name &rarr; API Tokens &rarr; Create</span>
      </div>
      <div class="btn-row">
        <button class="btn btn-sec" onclick="doTest()">Test Connection</button>
        <button class="btn btn-pri" onclick="doSave()">Save &amp; Start</button>
      </div>
      <div id="s-result" class="result hide"></div>
    </div>
  </div>

  <!-- DASHBOARD VIEW -->
  <div id="V-dash" class="hide">
    <!-- App URL -->
    <div class="card card-hl">
      <h2>Connect Your App</h2>
      <div class="url-box">
        <code id="app-url">-</code>
        <button class="btn-sm" onclick="copyUrl()">Copy</button>
      </div>
      <span class="help">Settings &rarr; Display &rarr; Server Spines &rarr; ON &rarr; paste this URL</span>
    </div>

    <!-- Stats -->
    <div class="stats">
      <div class="stat"><span class="stat-val" id="n-books">-</span><span class="stat-lbl">Books</span></div>
      <div class="stat"><span class="stat-val" id="n-match">-</span><span class="stat-lbl">Matched</span></div>
      <div class="stat"><span class="stat-val" id="n-unm">-</span><span class="stat-lbl">Unmatched</span></div>
    </div>

    <!-- Folders -->
    <div class="card">
      <h2>Spine Folders</h2>
      <div id="folders"></div>
      <div style="margin-top:12px">
        <button class="btn btn-pri" onclick="browseFolder()" style="width:100%">Browse for Folder&hellip;</button>
      </div>
      <div id="folder-msg" class="result hide" style="margin-top:8px"></div>
      <details style="margin-top:12px">
        <summary style="font-size:12px;color:var(--text2);cursor:pointer">Or enter a path manually</summary>
        <div class="field-row" style="margin-top:8px">
          <input id="new-folder" type="text" placeholder="/path/to/your/spine/images" spellcheck="false">
          <button class="btn btn-sec" onclick="addFolder()" style="white-space:nowrap">Link</button>
        </div>
      </details>
    </div>

    <!-- Matched -->
    <div class="card">
      <div class="card-hdr" onclick="tog('matched')">
        <h2>Matched Spines <span id="mc"></span></h2>
        <span class="chev open" id="chev-matched">&#9662;</span>
      </div>
      <div id="matched" class="list"></div>
    </div>

    <!-- Unmatched -->
    <div class="card" id="unm-card" style="display:none">
      <div class="card-hdr" onclick="tog('unmatched')">
        <h2>Unmatched Files <span id="uc"></span></h2>
        <span class="chev open" id="chev-unmatched">&#9662;</span>
      </div>
      <div id="unmatched" class="list"></div>
    </div>

    <!-- Settings -->
    <div class="card">
      <div class="card-hdr" onclick="tog('settings')">
        <h2>Settings</h2>
        <span class="chev" id="chev-settings">&#9662;</span>
      </div>
      <div id="settings" class="hide" style="padding-top:16px">
        <div class="field">
          <label for="d-url">AudiobookShelf URL</label>
          <input id="d-url" type="text" spellcheck="false">
        </div>
        <div class="field">
          <label for="d-key">API Key</label>
          <input id="d-key" type="password" spellcheck="false">
        </div>
        <div class="btn-row">
          <button class="btn btn-sec" onclick="doTestDash()">Test</button>
          <button class="btn btn-pri" onclick="doSaveDash()">Save</button>
        </div>
        <div id="d-result" class="result hide"></div>
      </div>
    </div>
  </div>
</div>

<script>
let S={};

async function api(path,body){
  const opts=body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
  const r=await fetch(path,opts);
  return r.json();
}

async function load(){
  try{S=await api('/api/ui/status');render()}
  catch(e){console.error(e)}
}

function render(){
  const badge=document.getElementById('badge');
  const setup=document.getElementById('V-setup');
  const dash=document.getElementById('V-dash');

  if(!S.configured){
    setup.classList.remove('hide');dash.classList.add('hide');
    badge.textContent='Not configured';badge.className='badge badge-warn';
    return;
  }
  setup.classList.add('hide');dash.classList.remove('hide');
  badge.textContent=S.connected?'Connected':'Disconnected';
  badge.className='badge '+(S.connected?'badge-ok':'badge-err');

  document.getElementById('app-url').textContent='http://'+S.local_ip+':'+S.port;
  document.getElementById('n-books').textContent=S.indexed_books||0;
  document.getElementById('n-match').textContent=S.matched||0;
  document.getElementById('n-unm').textContent=S.unmatched_count||0;

  // Folders
  const fl=document.getElementById('folders');fl.innerHTML='';
  (S.folders||[]).forEach(f=>{
    const d=document.createElement('div');d.className='folder';
    d.innerHTML='<span class="folder-path">&#128193; '+esc(f.path)+'</span>'
      +'<span class="folder-info">'+f.count+' files</span>'
      +(f.removable?'<button class="btn-xs" data-path="'+esc(f.path)+'">Remove</button>':'<span class="badge-dim">default</span>');
    const btn=d.querySelector('[data-path]');
    if(btn)btn.onclick=function(){rmFolder(f.path)};
    fl.appendChild(d);
  });

  // Matched
  const ml=document.getElementById('matched');
  document.getElementById('mc').textContent='('+S.matched+')';
  if(S.matched_items&&S.matched_items.length){
    ml.innerHTML='';
    S.matched_items.forEach(m=>{
      const d=document.createElement('div');d.className='item';
      d.innerHTML='<span class="f">'+esc(m.file)+'</span><span class="a">&rarr;</span><span class="b">'+esc(m.title)+(m.author?' &mdash; '+esc(m.author):'')+'</span>';
      ml.appendChild(d);
    });
  }else{
    ml.innerHTML='<div class="empty"><b>No spine images yet</b>Drop images into the spines folder or link a folder above</div>';
  }

  // Unmatched
  const uc=document.getElementById('unm-card');
  const ul=document.getElementById('unmatched');
  document.getElementById('uc').textContent='('+(S.unmatched_count||0)+')';
  if(S.unmatched_count>0){
    uc.style.display='';ul.innerHTML='';
    (S.unmatched_items||[]).forEach(f=>{
      const d=document.createElement('div');d.className='item un';
      d.innerHTML='<span class="f">'+esc(f)+'</span>';
      ul.appendChild(d);
    });
  }else{uc.style.display='none'}

  // Settings
  document.getElementById('d-url').value=S.abs_url||'';
  document.getElementById('d-key').placeholder=S.abs_api_key_masked||'';
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

async function doTest(){
  const el=document.getElementById('s-result');
  el.className='result load';el.classList.remove('hide');el.textContent='Testing...';
  const r=await api('/api/ui/action',{action:'test_connection',abs_url:document.getElementById('s-url').value,abs_api_key:document.getElementById('s-key').value});
  el.className='result '+(r.ok?'ok':'err');el.textContent=r.message;
}

async function doSave(){
  await api('/api/ui/action',{action:'save_config',abs_url:document.getElementById('s-url').value,abs_api_key:document.getElementById('s-key').value});
  await load();
}

async function doTestDash(){
  const el=document.getElementById('d-result');
  el.className='result load';el.classList.remove('hide');el.textContent='Testing...';
  const url=document.getElementById('d-url').value;
  const key=document.getElementById('d-key').value;
  const r=await api('/api/ui/action',{action:'test_connection',abs_url:url,abs_api_key:key||S.abs_api_key_raw||''});
  el.className='result '+(r.ok?'ok':'err');el.textContent=r.message;
}

async function doSaveDash(){
  const url=document.getElementById('d-url').value;
  const key=document.getElementById('d-key').value;
  const body={action:'save_config',abs_url:url};
  if(key)body.abs_api_key=key;
  await api('/api/ui/action',body);
  document.getElementById('d-result').classList.add('hide');
  await load();
}

async function browseFolder(){
  const msg=document.getElementById('folder-msg');
  msg.className='result load';msg.textContent='Opening folder picker...';msg.classList.remove('hide');
  try{
    const r=await api('/api/ui/action',{action:'browse_folder'});
    if(r.ok){
      msg.className='result ok';msg.textContent='Linked: '+r.path;
      setTimeout(()=>msg.classList.add('hide'),3000);
      await load();
    }else if(r.cancelled){
      msg.classList.add('hide');
    }else{
      msg.className='result err';msg.textContent=r.message||'Could not link folder.';
    }
  }catch(e){msg.className='result err';msg.textContent='Error: '+e.message}
}

async function addFolder(){
  const inp=document.getElementById('new-folder');
  const msg=document.getElementById('folder-msg');
  const p=inp.value.trim();
  if(!p){msg.className='result err';msg.textContent='Enter a folder path first.';msg.classList.remove('hide');return}
  msg.className='result load';msg.textContent='Checking folder...';msg.classList.remove('hide');
  const r=await api('/api/ui/action',{action:'add_folder',path:p});
  if(!r.ok){msg.className='result err';msg.textContent=r.message||'Folder not found on this computer.';return}
  msg.className='result ok';msg.textContent='Folder linked!';
  inp.value='';
  setTimeout(()=>msg.classList.add('hide'),2000);
  await load();
}

async function rmFolder(p){
  await api('/api/ui/action',{action:'remove_folder',path:p});
  await load();
}

function copyUrl(){
  const t=document.getElementById('app-url').textContent;
  navigator.clipboard.writeText(t).then(()=>{
    const b=event.target;b.textContent='Copied!';setTimeout(()=>b.textContent='Copy',1500);
  });
}

function tog(id){
  const el=document.getElementById(id);
  const ch=document.getElementById('chev-'+id);
  el.classList.toggle('hide');
  if(ch)ch.classList.toggle('open');
}

load();
setInterval(load,5000);
</script>
</body>
</html>"""


# =============================================================================
# HTTP SERVER
# =============================================================================

class SpineHandler(BaseHTTPRequestHandler):
    """Handles spine API requests and the web UI."""

    _spine_files = None
    _manifest = None
    _last_scan = 0
    _scan_interval = 30

    @classmethod
    def refresh_if_needed(cls):
        now = time.time()
        if now - cls._last_scan > cls._scan_interval:
            spines, unmatched = find_spine_files()
            cls._spine_files = spines
            cls._manifest = build_manifest(spines)
            cls._last_scan = now

    def do_GET(self):
        self.refresh_if_needed()
        path = self.path.split("?")[0]

        # Web UI
        if path == "/":
            html = get_ui_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        # Manifest
        if path == "/api/spines/manifest":
            self.send_json(self._manifest or {"items": [], "version": 1, "count": 0})
            return

        # Spine image
        if path.startswith("/api/items/") and path.endswith("/spine"):
            parts = path.split("/")
            if len(parts) >= 4:
                self.serve_spine(parts[3])
                return

        # Health
        if path == "/health":
            self.send_json({
                "status": "ok",
                "spines": len(self._spine_files) if self._spine_files else 0,
                "indexed_books": len(_books_by_id),
                "matchable_keys": len(_title_index),
            })
            return

        # UI status API
        if path == "/api/ui/status":
            self.send_json(self.build_ui_status())
            return

        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/ui/action":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except (json.JSONDecodeError, ValueError):
                self.send_json({"ok": False, "message": "Invalid JSON"})
                return

            action = body.get("action", "")

            if action == "test_connection":
                result = test_connection(
                    body.get("abs_url", ""),
                    body.get("abs_api_key", ""),
                )
                self.send_json(result)

            elif action == "save_config":
                if body.get("abs_url"):
                    _config["abs_url"] = body["abs_url"]
                if body.get("abs_api_key"):
                    _config["abs_api_key"] = body["abs_api_key"]
                save_config(_config)
                # Re-index in background
                threading.Thread(target=reload_index, daemon=True).start()
                self.send_json({"ok": True})

            elif action == "add_folder":
                folder = body.get("path", "").strip()
                if not folder:
                    self.send_json({"ok": False, "message": "No path provided."})
                elif not os.path.isdir(folder):
                    self.send_json({"ok": False, "message": f"Folder not found: {folder}"})
                else:
                    folders = _config.get("linked_folders", [])
                    if folder not in folders:
                        folders.append(folder)
                        _config["linked_folders"] = folders
                        save_config(_config)
                        SpineHandler._last_scan = 0  # force rescan
                    self.send_json({"ok": True})

            elif action == "remove_folder":
                folder = body.get("path", "")
                folders = _config.get("linked_folders", [])
                if folder in folders:
                    folders.remove(folder)
                    _config["linked_folders"] = folders
                    save_config(_config)
                    SpineHandler._last_scan = 0
                self.send_json({"ok": True})

            elif action == "browse_folder":
                folder = pick_folder()
                if folder:
                    if os.path.isdir(folder):
                        folders = _config.get("linked_folders", [])
                        if folder not in folders:
                            folders.append(folder)
                            _config["linked_folders"] = folders
                            save_config(_config)
                            SpineHandler._last_scan = 0
                        self.send_json({"ok": True, "path": folder})
                    else:
                        self.send_json({"ok": False, "message": f"Not a valid folder: {folder}"})
                else:
                    self.send_json({"ok": False, "cancelled": True, "message": "No folder selected."})

            elif action == "rescan":
                threading.Thread(target=reload_index, daemon=True).start()
                self.send_json({"ok": True})

            else:
                self.send_json({"ok": False, "message": f"Unknown action: {action}"})
            return

        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def serve_spine(self, book_id):
        if not self._spine_files or book_id not in self._spine_files:
            self.send_error(404, f"No spine for {book_id}")
            return
        filepath = self._spine_files[book_id]
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except IOError:
            self.send_error(500, "Could not read file")
            return
        ctype = mimetypes.guess_type(filepath)[0] or "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def build_ui_status(self):
        spines, unmatched = find_spine_files()

        # Count images per folder
        folders = []
        for d in get_all_spine_dirs():
            count = 0
            if os.path.isdir(d):
                count = len([
                    f for f in os.listdir(d)
                    if os.path.isfile(os.path.join(d, f))
                    and os.path.splitext(f)[1].lower() in IMAGE_EXTS
                ])
            is_default = (d == _config.get("spines_dir", DEFAULT_SPINES_DIR))
            folders.append({"path": d, "count": count, "removable": not is_default})

        matched_items = []
        for book_id, filepath in sorted(spines.items(), key=lambda x: x[1]):
            book = _books_by_id.get(book_id)
            matched_items.append({
                "file": os.path.basename(filepath),
                "book_id": book_id,
                "title": book["title"] if book else book_id,
                "author": book.get("author", "") if book else "",
            })

        key = _config.get("abs_api_key", "")
        masked = ("*" * min(len(key), 20) + key[-4:]) if len(key) > 4 else "*" * len(key)

        return {
            "configured": bool(_config.get("abs_url") and _config.get("abs_api_key")),
            "connected": _index_loaded and len(_books_by_id) > 0,
            "abs_url": _config.get("abs_url", ""),
            "abs_api_key_masked": masked,
            "port": _config.get("port", DEFAULT_PORT),
            "local_ip": get_local_ip(),
            "indexed_books": len(_books_by_id),
            "matched": len(spines),
            "unmatched_count": len(unmatched),
            "matched_items": matched_items,
            "unmatched_items": sorted(unmatched),
            "folders": folders,
            "spines_dir": _config.get("spines_dir", DEFAULT_SPINES_DIR),
        }

    def log_message(self, format, *args):
        msg = format % args
        # Only log errors and spine/manifest requests (not UI polling)
        if "404" in msg or "/spine" in msg or "manifest" in msg:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# =============================================================================
# CLI COMMANDS
# =============================================================================

def cmd_list_books():
    """Print all books with their IDs."""
    print("Fetching books from ABS...")
    books = get_all_books()
    if not books:
        print("No books found.")
        return
    print(f"\nFound {len(books)} books:\n")
    print(f"{'BOOK ID':<30} {'TITLE':<50} {'AUTHOR'}")
    print("-" * 110)
    for book in sorted(books, key=lambda b: b["title"]):
        print(f"{book['id']:<30} {book['title'][:48]:<50} {book['author'][:30]}")


def cmd_scan_library():
    """Scan ABS library folders for existing spine images."""
    print("Fetching books from ABS...")
    books = get_all_books()
    if not books:
        print("No books found.")
        return
    print(f"Found {len(books)} books. Scanning library for spine images...")
    found = scan_library_for_spines(books)
    print(f"\nDone! Found {found} spine images.")
    if found > 0:
        spines_dir = _config.get("spines_dir", DEFAULT_SPINES_DIR)
        print(f"Copied to: {spines_dir}/")


def cmd_serve(port, open_browser=True):
    """Start the HTTP server."""
    ensure_spines_dir()
    load_book_index()

    # Initial scan
    spine_files, unmatched = find_spine_files()
    SpineHandler._spine_files = spine_files
    SpineHandler._manifest = build_manifest(spine_files)
    SpineHandler._last_scan = time.time()

    local_ip = get_local_ip()
    is_configured = bool(_config.get("abs_url") and _config.get("abs_api_key"))

    print()
    print("=== ABS Spine Server ===")
    print()

    if spine_files:
        print(f"Matched {len(spine_files)} spine images:")
        for book_id, filepath in sorted(spine_files.items(), key=lambda x: x[1]):
            filename = os.path.basename(filepath)
            book = _books_by_id.get(book_id)
            if book:
                print(f"  {filename:<40} -> {book['title']} ({book['author']})")
            else:
                print(f"  {filename:<40} -> {book_id}")
        print()

    if unmatched:
        print(f"Could not match {len(unmatched)} files:")
        for f in sorted(unmatched):
            print(f"  {f}")
        print()

    print(f"Web UI:    http://localhost:{port}")
    print(f"App URL:   http://{local_ip}:{port}")
    print()

    if not is_configured:
        print("Open the web UI to complete setup.")
        print()

    # Open browser
    if open_browser:
        def _open():
            time.sleep(0.5)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=_open, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", port), SpineHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


# =============================================================================
# MAIN
# =============================================================================

def main():
    global _config

    parser = argparse.ArgumentParser(
        description="ABS Spine Server — Custom book spines for AudiobookShelf",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list-books", action="store_true",
                        help="List all books with their IDs")
    parser.add_argument("--scan-library", action="store_true",
                        help="Scan ABS library folders for existing spine images")
    parser.add_argument("--port", type=int, default=None,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--spines-dir", type=str, default=None,
                        help="Folder containing spine images")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't open the browser on startup")

    args = parser.parse_args()

    # Load config (file + env vars)
    _config = load_config()

    # CLI overrides
    if args.port:
        _config["port"] = args.port
    if args.spines_dir:
        _config["spines_dir"] = args.spines_dir

    if args.list_books:
        cmd_list_books()
    elif args.scan_library:
        cmd_scan_library()
    else:
        cmd_serve(_config.get("port", DEFAULT_PORT), open_browser=not args.no_open)


if __name__ == "__main__":
    main()
