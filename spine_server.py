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
  python3 spine_server.py --import-community [--dry-run] [--community-url URL]
                                         # Download curated community spines
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
import hashlib
import hmac
import ipaddress
import urllib.parse
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
LEGACY_CONFIG_FILE = CONFIG_FILE
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
    if config.get("dashboard_token"):
        saveable["dashboard_token"] = config["dashboard_token"]
    # 0600: the file holds the ABS API key.
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
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

# Dashboard-triggered community import state
_import_state = {"running": False, "message": "", "ok": True}


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
                "asin": meta.get("asin") or "",
                "isbn": meta.get("isbn") or "",
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


def match_filename_to_book(filename, title_index, books_by_id=None):
    """
    Match a filename (without extension) to a book ID.
    Returns (book_id, match_type) or (None, None).
    """
    if books_by_id is None:
        books_by_id = _books_by_id
    # Old ABS item ids look like li_xxxx; current ABS uses UUIDs.
    if filename.startswith("li_") or filename in books_by_id:
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


def get_community_dir():
    """Folder that --import-community downloads into (<abs item id>.webp)."""
    return os.path.join(_config.get("spines_dir", DEFAULT_SPINES_DIR), "community")


def get_all_spine_dirs():
    """Get list of all directories to scan for spine images.
    The community folder is always last so local spines take precedence."""
    dirs = [_config.get("spines_dir", DEFAULT_SPINES_DIR)]
    for folder in _config.get("linked_folders", []):
        if folder not in dirs:
            dirs.append(folder)
    community = get_community_dir()
    if community not in dirs:
        dirs.append(community)
    return dirs


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def find_spine_files():
    """Scan all spine directories for image files. Returns (spines, unmatched)."""
    spines = {}
    unmatched = []
    community = get_community_dir()

    for spine_dir in get_all_spine_dirs():
        if not os.path.isdir(spine_dir):
            continue
        is_community = (spine_dir == community)

        for filename in os.listdir(spine_dir):
            filepath = os.path.join(spine_dir, filename)
            if not os.path.isfile(filepath):
                continue

            name, ext = os.path.splitext(filename)
            if ext.lower() not in IMAGE_EXTS:
                continue

            book_id, match_type = match_filename_to_book(name, _title_index)

            if book_id:
                if is_community:
                    # Imported spines only fill gaps — never shadow a local one.
                    spines.setdefault(book_id, filepath)
                elif book_id not in spines or match_type == "id":
                    spines[book_id] = filepath
            else:
                unmatched.append(filename)

    return spines, unmatched


# =============================================================================
# COMMUNITY IMPORT (bulk curated spines from spines.mysecretlibrary.com)
# =============================================================================

DEFAULT_COMMUNITY_URL = "https://spines.mysecretlibrary.com"
USER_AGENT = "abs-spine-server (+https://github.com/philipvox/abs-spine-server)"

# Match strength, strongest first.
MATCH_RANKS = {"asin": 0, "isbn": 1, "hash": 2, "title": 3}


def community_hash(title, author):
    """Replicates the community server's manifest hash exactly
    (spine_community.py: manifest_title_author_hash / _normalize_for_hash):
    lowercase -> strip -> NFKD -> drop combining marks -> keep only [a-z0-9],
    then sha256("title|author")[:16]."""
    def _norm(s):
        s = (s or "").lower().strip()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]", "", s)
    key = _norm(title) + "|" + _norm(author)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _norm_asin(v):
    return (v or "").strip().upper()


def _norm_isbn(v):
    # Same cleaning the community server applies before storing ISBN keys.
    return re.sub(r"[^0-9X]", "", (v or "").strip().upper())


def author_last_names(author):
    """Normalized last names for an author string ("A Smith, B. Jones & C Lee")."""
    names = set()
    for part in re.split(r",|;|&|\band\b", author or "", flags=re.IGNORECASE):
        tokens = normalize(part).split()
        if tokens:
            names.add(tokens[-1])
    names.discard("unknown")
    return names


def match_community_spines(entries, books):
    """
    Match community manifest entries to ABS books.
    Order: ASIN, then ISBN, then title|author hash, then a strict title
    fallback (exact normalized title AND a matching author last name — no
    containment, never an id taken from manifest text).
    Returns ({book_id: (entry, match_type)}, unmatched_entries).
    When several entries hit one book the strongest match wins.
    """
    by_asin, by_isbn, by_hash = {}, {}, {}
    by_title = {}  # normalized title -> [(book_id, last_names)]
    for b in books:
        a = _norm_asin(b.get("asin"))
        if a:
            by_asin.setdefault(a, b["id"])
        i = _norm_isbn(b.get("isbn"))
        if i:
            by_isbn.setdefault(i, b["id"])
        by_hash.setdefault(community_hash(b.get("title", ""), b.get("author", "")), b["id"])
        nt = normalize(b.get("title"))
        if nt:
            by_title.setdefault(nt, []).append((b["id"], author_last_names(b.get("author"))))

    def title_match(e):
        nt = normalize(e.get("title"))
        last = author_last_names(e.get("author"))
        if not nt or not last:
            return None
        hits = {bid for bid, names in by_title.get(nt, []) if names & last}
        return hits.pop() if len(hits) == 1 else None  # ambiguous -> skip

    matches = {}
    unmatched = []
    for e in entries:
        hit = None
        a = _norm_asin(e.get("asin"))
        i = _norm_isbn(e.get("isbn"))
        if a and a in by_asin:
            hit = (by_asin[a], "asin")
        elif i and i in by_isbn:
            hit = (by_isbn[i], "isbn")
        elif e.get("hash") and e["hash"] in by_hash:
            hit = (by_hash[e["hash"]], "hash")
        else:
            bid = title_match(e)
            if bid:
                hit = (bid, "title")
        if not hit:
            unmatched.append(e)
            continue
        bid, mtype = hit
        prev = matches.get(bid)
        if prev is None or MATCH_RANKS[mtype] < MATCH_RANKS[prev[1]]:
            matches[bid] = (e, mtype)
    return matches, unmatched


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def safe_dest_path(directory, book_id, ext):
    """<directory>/<book_id><ext>, or None if the id isn't a plain ABS item id
    or the result would land outside <directory>."""
    if not isinstance(book_id, str) or not SAFE_ID_RE.match(book_id):
        return None
    base = os.path.realpath(directory)
    dest = os.path.realpath(os.path.join(base, book_id + ext))
    if os.path.dirname(dest) != base:
        return None
    return dest


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def fetch_community_manifest(base_url, http_get=_http_get):
    """Return the v3 (curated-only) spine list, or None with a message if the
    server does not offer v3. v2 is deliberately NOT used: it includes
    community uploads that have no licence terms."""
    base = base_url.rstrip("/")
    try:
        body, _ = http_get(f"{base}/api/spines/manifest/v3")
        data = json.loads(body.decode("utf-8"))
        return data.get("spines", []), None
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return None, f"Manifest v3 request failed: HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"Could not fetch manifest v3 from {base}: {e}"
    # v3 missing — check whether this is an older server with only v2.
    try:
        http_get(f"{base}/api/spines/manifest/v2")
        return None, (f"{base} only offers manifest v2, which mixes in community "
                      "uploads without licence terms. Bulk import needs the "
                      "curated-only manifest v3 — skipping, nothing imported.")
    except Exception:
        return None, f"{base} has no spine manifest (v3 or v2) — nothing imported."


def import_community_spines(community_url=DEFAULT_COMMUNITY_URL, dry_run=False,
                            books=None, http_get=_http_get, log=print):
    """
    Download curated community spines for ABS books that have no local spine.
    Files go to spines/community/<abs item id>.<ext>; nothing is overwritten.
    Returns a summary dict.
    """
    global _title_index, _books_by_id, _collisions
    summary = {"ok": False, "manifest": 0, "matched": 0, "downloaded": 0,
               "would_download": 0, "skipped_existing": 0, "failed": 0,
               "unmatched": 0, "by_type": {}, "message": ""}

    if books is None:
        books = get_all_books()
    if not books:
        summary["message"] = "No books from ABS — check abs_url / abs_api_key."
        log(summary["message"])
        return summary

    entries, err = fetch_community_manifest(community_url, http_get)
    if entries is None:
        summary["message"] = err
        log(err)
        return summary
    summary["manifest"] = len(entries)

    # Index ABS books so the existing scanner can tell which books already
    # have a spine (any folder, any naming scheme).
    with _lock:
        _title_index, _collisions = build_title_index(books)
        _books_by_id = {b["id"]: b for b in books}
    existing, _ = find_spine_files()

    matches, unmatched = match_community_spines(entries, books)
    summary["matched"] = len(matches)
    summary["unmatched"] = len(unmatched)

    community_dir = get_community_dir()
    if not dry_run:
        os.makedirs(community_dir, exist_ok=True)
    base = community_url.rstrip("/")

    for bid, (entry, mtype) in sorted(matches.items()):
        summary["by_type"][mtype] = summary["by_type"].get(mtype, 0) + 1
        already = bid in existing or any(
            os.path.exists(os.path.join(community_dir, bid + ext)) for ext in IMAGE_EXTS)
        if already:
            summary["skipped_existing"] += 1
            continue
        book = _books_by_id.get(bid, {})
        if safe_dest_path(community_dir, bid, ".webp") is None:
            summary["failed"] += 1
            log(f"  SKIPPED unsafe ABS item id: {bid!r}")
            continue
        if dry_run:
            summary["would_download"] += 1
            log(f"  [dry-run] {book.get('title', bid)} <- {entry.get('title')} ({mtype})")
            continue
        try:
            data, ctype = http_get(f"{base}/api/spine/{urllib.parse.quote(str(entry['sid']), safe='')}")
        except Exception as e:
            summary["failed"] += 1
            log(f"  FAILED {book.get('title', bid)}: {e}")
            continue
        ext = mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ".webp"
        if ext not in IMAGE_EXTS:
            ext = ".webp"
        dest = safe_dest_path(community_dir, bid, ext)
        if dest is None:
            summary["failed"] += 1
            continue
        if os.path.exists(dest):  # never overwrite
            summary["skipped_existing"] += 1
            continue
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        summary["downloaded"] += 1
        log(f"  + {book.get('title', bid)} ({mtype})")

    summary["ok"] = True
    bt = ", ".join(f"{k} {v}" for k, v in sorted(summary["by_type"].items(),
                                                     key=lambda kv: MATCH_RANKS[kv[0]]))
    action = (f"would download {summary['would_download']}" if dry_run
              else f"downloaded {summary['downloaded']}")
    summary["message"] = (
        f"Community manifest: {summary['manifest']} curated spines. "
        f"Matched {summary['matched']} of your books ({bt or 'none'}); "
        f"{action}, skipped {summary['skipped_existing']} that already have a spine, "
        f"{summary['unmatched']} community spines not in your library"
        + (f", {summary['failed']} failed" if summary["failed"] else "") + ".")
    log(summary["message"])
    return summary


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

    <!-- Community import -->
    <div class="card">
      <h2>Community Spines</h2>
      <span class="help" style="margin-top:0;margin-bottom:12px">Download the curated Secret Library spines for books that don't have a spine yet. Your own spines are never replaced.</span>
      <button class="btn btn-sec" id="imp-btn" onclick="importCommunity()" style="width:100%">Import curated community spines</button>
      <div id="imp-msg" class="result hide" style="margin-top:8px"></div>
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
  if(r.status===401){location.href='/login';return {ok:false,message:'Login required.'}}
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

  // Community import
  const ib=document.getElementById('imp-btn'),im=document.getElementById('imp-msg');
  ib.disabled=!!S.import_running;
  if(S.import_message){im.className='result '+(S.import_running?'load':(S.import_ok?'ok':'err'));im.textContent=S.import_message}

  // Settings
  document.getElementById('d-url').value=S.abs_url||'';
  document.getElementById('d-key').placeholder=S.abs_api_key_set?'Saved (leave blank to keep; required if you change the URL)':'Paste your API key';
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

async function doTest(){
  const el=document.getElementById('s-result');
  el.className='result load';el.classList.remove('hide');el.textContent='Testing...';
  const r=await api('/api/ui/action',{action:'test_connection',abs_url:document.getElementById('s-url').value,abs_api_key:document.getElementById('s-key').value});
  el.className='result '+(r.ok?'ok':'err');el.textContent=r.message;
}

async function doSave(){
  const el=document.getElementById('s-result');
  const r=await api('/api/ui/action',{action:'save_config',abs_url:document.getElementById('s-url').value,abs_api_key:document.getElementById('s-key').value});
  if(!r.ok){el.className='result err';el.classList.remove('hide');el.textContent=r.message;return}
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
  const r=await api('/api/ui/action',body);
  const el=document.getElementById('d-result');
  if(!r.ok){el.className='result err';el.classList.remove('hide');el.textContent=r.message;return}
  el.classList.add('hide');
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

async function importCommunity(){
  const im=document.getElementById('imp-msg');
  im.className='result load';im.textContent='Starting import...';
  const r=await api('/api/ui/action',{action:'import_community'});
  if(!r.ok){im.className='result err';im.textContent=r.message}
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
# DASHBOARD ACCESS CONTROL
# =============================================================================
#
# Two kinds of endpoints:
#   * Public, read-only spine endpoints (manifest, spine images, /health) that
#     the ABS apps fetch from phones/browsers. No auth; manifest + images send
#     permissive CORS so web clients can use them.
#   * The dashboard ("/" and /api/ui/*), which can change the ABS URL — and the
#     server sends the ABS API key to that URL. It is locked down:
#       - DASHBOARD_TOKEN set  -> login required (HttpOnly SameSite=Strict
#                                 cookie or "Authorization: Bearer <token>").
#       - no token             -> loopback clients only, via a loopback Host
#                                 name (blocks LAN hosts and DNS rebinding).
#       - POSTs must be application/json and same-origin (blocks cross-site
#         form/fetch requests from other web pages).

SESSION_COOKIE = "spine_session"
SESSION_MAX_AGE = 30 * 24 * 3600
MAX_POST_BYTES = 64 * 1024

REMOTE_LOCKED_MSG = (
    "The dashboard is only available from this computer (http://localhost:PORT). "
    "To manage it from another device or from Docker, set the DASHBOARD_TOKEN "
    "environment variable (or \"dashboard_token\" in config.json) and restart.")

# Directories that are never valid spine folders.
SYSTEM_DIRS = [
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc",
    "/root", "/run", "/sbin", "/sys", "/usr", "/var/lib", "/var/log", "/var/run",
    "/System", "/Library", "/private/etc", "/private/var/db", "/private/var/log",
    "/private/var/root",
]
WINDOWS_SYSTEM_DIRS = ["c:\\windows", "c:\\program files", "c:\\program files (x86)"]


def get_dashboard_token():
    return os.environ.get("DASHBOARD_TOKEN") or _config.get("dashboard_token") or ""


def session_value(token):
    """Cookie value derived from the token (the raw token is never stored)."""
    return hashlib.sha256(("abs-spine-server-session:" + token).encode("utf-8")).hexdigest()


def is_public_path(path):
    return (path in ("/api/spines/manifest", "/health")
            or (path.startswith("/api/items/") and path.endswith("/spine")))


def is_cors_path(path):
    return path != "/health" and is_public_path(path)


def is_loopback_ip(ip):
    try:
        addr = ipaddress.ip_address((ip or "").strip("[]").split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _split_host(host):
    """'Host' header / netloc -> (hostname, port or None), lowercased."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        name, _, rest = host[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else None
    elif host.count(":") == 1:
        name, port = host.split(":")
    else:
        name, port = host, None
    return name, (port or None)


def host_is_loopback(host_header):
    name, _ = _split_host(host_header)
    return name == "localhost" or is_loopback_ip(name)


def origin_matches_host(origin, host_header):
    """True if an Origin header is the same origin as this server's Host."""
    try:
        u = urllib.parse.urlsplit(origin or "")
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    default = "443" if u.scheme == "https" else "80"
    o_port = str(u.port) if u.port else default
    h_name, h_port = _split_host(host_header)
    return u.hostname.lower() == h_name and o_port == (h_port or default)


def same_abs_url(a, b):
    return (a or "").strip().rstrip("/").lower() == (b or "").strip().rstrip("/").lower()


def validate_abs_url(url):
    u = urllib.parse.urlsplit(url)
    return u.scheme in ("http", "https") and bool(u.netloc)


def validate_spine_folder(path):
    """Return (ok, normalized_path_or_error_message)."""
    path = (path or "").strip()
    if not path:
        return False, "No path provided."
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        return False, "Use a full (absolute) folder path."
    if not os.path.isdir(path):
        return False, f"Folder not found: {path}"
    real = os.path.realpath(path)
    if platform.system() == "Windows" or re.match(r"^[A-Za-z]:\\", real):
        low = real.lower().rstrip("\\")
        if len(low) <= 3 or any(low == d or low.startswith(d + "\\") for d in WINDOWS_SYSTEM_DIRS):
            return False, f"Refusing to link a system folder: {path}"
    else:
        for d in SYSTEM_DIRS:
            if real == d or (d != "/" and real.startswith(d + "/")):
                return False, f"Refusing to link a system folder: {path}"
    if real == os.path.realpath(os.path.expanduser("~")):
        return False, "Pick the folder that holds your spine images, not your whole home folder."
    return True, os.path.abspath(path)


def get_login_html(error=""):
    err = f'<p class="err">{error}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ABS Spine Server — Sign in</title>
<style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#09090b;color:#fafafa;display:flex;justify-content:center;padding-top:15vh}}
form{{background:#18181b;border:1px solid #27272a;border-radius:12px;padding:24px;width:320px}}
h1{{font-size:16px;margin:0 0 16px}}
input{{width:100%;box-sizing:border-box;padding:10px;background:#09090b;border:1px solid #27272a;border-radius:8px;color:#fafafa;font-size:14px}}
button{{margin-top:12px;width:100%;padding:10px;border:none;border-radius:8px;background:#F3B60C;font-weight:600;cursor:pointer}}
.err{{color:#ef4444;font-size:13px}}
</style></head><body>
<form method="POST" action="/login">
<h1>ABS Spine Server</h1>
{err}
<label for="t" style="font-size:13px">Dashboard token</label>
<input id="t" name="token" type="password" autofocus autocomplete="current-password">
<button type="submit">Sign in</button>
</form></body></html>"""


def get_message_html(title, message):
    import html as _html
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{_html.escape(title)}</title></head>
<body style="font-family:system-ui,sans-serif;background:#09090b;color:#fafafa;padding:15vh 24px;max-width:560px;margin:0 auto">
<h1 style="font-size:18px">{_html.escape(title)}</h1>
<p style="color:#a1a1aa;line-height:1.5">{_html.escape(message)}</p>
</body></html>"""


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

    # ---- access control -------------------------------------------------

    def client_ip(self):
        return self.client_address[0]

    def _has_valid_session(self, token):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            if hmac.compare_digest(auth[7:].strip().encode("utf-8"), token.encode("utf-8")):
                return True
        raw = self.headers.get("Cookie", "")
        if raw:
            from http.cookies import SimpleCookie, CookieError
            try:
                c = SimpleCookie()
                c.load(raw)
            except CookieError:
                return False
            m = c.get(SESSION_COOKIE)
            if m and hmac.compare_digest(m.value.encode("utf-8"),
                                         session_value(token).encode("utf-8")):
                return True
        return False

    def ui_access_error(self):
        """None if this request may use the dashboard, else (status, message)."""
        token = get_dashboard_token()
        if token:
            if self._has_valid_session(token):
                return None
            return 401, "Login required."
        if not is_loopback_ip(self.client_ip()):
            return 403, REMOTE_LOCKED_MSG
        if not host_is_loopback(self.headers.get("Host")):
            return 403, ("Open the dashboard via http://localhost:PORT (or 127.0.0.1). "
                         + REMOTE_LOCKED_MSG.split(". ", 1)[1])
        return None

    def cross_site_error(self):
        """Reject POSTs that come from another web origin."""
        origin = self.headers.get("Origin")
        if origin is not None and not origin_matches_host(origin, self.headers.get("Host")):
            return "Cross-origin request blocked."
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return "Cross-site request blocked."
        return None

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_POST_BYTES:
            return None
        return self.rfile.read(length) if length else b""

    # ---- routing --------------------------------------------------------

    def do_GET(self):
        self.refresh_if_needed()
        path = self.path.split("?")[0]

        if path == "/login":
            if not get_dashboard_token():
                self.redirect("/")
            else:
                self.send_html(get_login_html())
            return

        if path == "/logout":
            self.redirect("/login", cookie=f"{SESSION_COOKIE}=; Max-Age=0; Path=/; "
                                            "HttpOnly; SameSite=Strict")
            return

        # Web UI
        if path == "/":
            err = self.ui_access_error()
            if err and err[0] == 401:
                self.redirect("/login")
            elif err:
                self.send_html(get_message_html("Dashboard locked", err[1]), status=err[0])
            else:
                self.send_html(get_ui_html())
            return

        # Manifest
        if path == "/api/spines/manifest":
            self.send_json(self._manifest or {"items": [], "version": 1, "count": 0}, cors=True)
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
            err = self.ui_access_error()
            if err:
                self.send_json({"ok": False, "message": err[1]}, status=err[0])
                return
            self.send_json(self.build_ui_status())
            return

        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/login":
            token = get_dashboard_token()
            if not token:
                self.send_error(404)
                return
            if self.cross_site_error():
                self.send_html(get_login_html("Cross-site sign-in blocked."), status=403)
                return
            raw = self.read_body()
            given = ""
            if raw is not None:
                form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
                given = (form.get("token") or [""])[0]
            if given and hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
                self.redirect("/", cookie=(f"{SESSION_COOKIE}={session_value(token)}; "
                                           f"Max-Age={SESSION_MAX_AGE}; Path=/; "
                                           "HttpOnly; SameSite=Strict"))
            else:
                time.sleep(1)  # slow down guessing
                self.send_html(get_login_html("Wrong token."), status=401)
            return

        if path.startswith("/api/ui/"):
            err = self.ui_access_error()
            if err:
                self.send_json({"ok": False, "message": err[1]}, status=err[0])
                return
            xs = self.cross_site_error()
            if xs:
                self.send_json({"ok": False, "message": xs}, status=403)
                return
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self.send_json({"ok": False, "message": "Content-Type must be application/json"},
                               status=415)
                return
            if path != "/api/ui/action":
                self.send_error(404)
                return
            raw = self.read_body()
            if raw is None:
                self.send_json({"ok": False, "message": "Request too large"}, status=413)
                return
            try:
                body = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, ValueError):
                body = None
            if not isinstance(body, dict):
                self.send_json({"ok": False, "message": "Invalid JSON"}, status=400)
                return
            self.send_json(self.handle_action(body))
            return

        self.send_error(404)

    def handle_action(self, body):
        action = body.get("action", "")

        if action == "test_connection":
            url = str(body.get("abs_url") or "").strip() or _config.get("abs_url", "")
            key = str(body.get("abs_api_key") or "").strip()
            if not key:
                if not same_abs_url(url, _config.get("abs_url", "")):
                    return {"ok": False, "message": "Enter the API key to test a different ABS URL."}
                key = _config.get("abs_api_key", "")
            return test_connection(url, key)

        if action == "save_config":
            new_url = str(body.get("abs_url") or "").strip()
            new_key = str(body.get("abs_api_key") or "").strip()
            if new_url and not validate_abs_url(new_url):
                return {"ok": False, "message": "ABS URL must start with http:// or https://"}
            if new_url and not same_abs_url(new_url, _config.get("abs_url", "")) and not new_key:
                # Never send the stored key to a newly supplied URL.
                return {"ok": False, "message": "Re-enter the API key when changing the ABS URL."}
            if new_url:
                _config["abs_url"] = new_url
            if new_key:
                _config["abs_api_key"] = new_key
            save_config(_config)
            threading.Thread(target=reload_index, daemon=True).start()
            return {"ok": True}

        if action in ("add_folder", "browse_folder"):
            if action == "browse_folder":
                picked = pick_folder()
                if not picked:
                    return {"ok": False, "cancelled": True, "message": "No folder selected."}
            else:
                picked = str(body.get("path") or "")
            ok, result = validate_spine_folder(picked)
            if not ok:
                return {"ok": False, "message": result}
            folders = _config.get("linked_folders", [])
            if result not in folders:
                folders.append(result)
                _config["linked_folders"] = folders
                save_config(_config)
                SpineHandler._last_scan = 0  # force rescan
            return {"ok": True, "path": result}

        if action == "remove_folder":
            folder = body.get("path", "")
            folders = _config.get("linked_folders", [])
            if folder in folders:
                folders.remove(folder)
                _config["linked_folders"] = folders
                save_config(_config)
                SpineHandler._last_scan = 0
            return {"ok": True}

        if action == "import_community":
            if _import_state["running"]:
                return {"ok": False, "message": "Import already running."}
            threading.Thread(target=_run_ui_import, daemon=True).start()
            return {"ok": True, "message": "Import started."}

        if action == "rescan":
            threading.Thread(target=reload_index, daemon=True).start()
            return {"ok": True}

        return {"ok": False, "message": f"Unknown action: {action}"}

    def do_OPTIONS(self):
        path = self.path.split("?")[0]
        self.send_response(204)
        if is_cors_path(path):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Content-Length", "0")
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

    def send_json(self, data, status=200, cors=False):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
            is_community = (d == get_community_dir())
            if is_community and not os.path.isdir(d):
                continue
            folders.append({"path": d, "count": count,
                            "removable": not (is_default or is_community)})

        matched_items = []
        for book_id, filepath in sorted(spines.items(), key=lambda x: x[1]):
            book = _books_by_id.get(book_id)
            matched_items.append({
                "file": os.path.basename(filepath),
                "book_id": book_id,
                "title": book["title"] if book else book_id,
                "author": book.get("author", "") if book else "",
            })

        return {
            "configured": bool(_config.get("abs_url") and _config.get("abs_api_key")),
            "connected": _index_loaded and len(_books_by_id) > 0,
            "abs_url": _config.get("abs_url", ""),
            "abs_api_key_set": bool(_config.get("abs_api_key")),
            "auth_enabled": bool(get_dashboard_token()),
            "port": _config.get("port", DEFAULT_PORT),
            "local_ip": get_local_ip(),
            "indexed_books": len(_books_by_id),
            "matched": len(spines),
            "unmatched_count": len(unmatched),
            "matched_items": matched_items,
            "unmatched_items": sorted(unmatched),
            "folders": folders,
            "spines_dir": _config.get("spines_dir", DEFAULT_SPINES_DIR),
            "import_running": _import_state["running"],
            "import_message": _import_state["message"],
            "import_ok": _import_state["ok"],
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


def _run_ui_import():
    """Run the community import from the dashboard (background thread)."""
    _import_state.update(running=True, message="Importing curated community spines...")
    try:
        r = import_community_spines(_config.get("community_url", DEFAULT_COMMUNITY_URL))
        _import_state.update(message=r["message"], ok=r["ok"])
    except Exception as e:
        _import_state.update(message=f"Import failed: {e}", ok=False)
    finally:
        _import_state["running"] = False
        SpineHandler._last_scan = 0


def cmd_import_community(community_url, dry_run=False):
    """Bulk-import curated community spines into spines/community/."""
    print("Fetching books from ABS...")
    books = get_all_books()
    if not books:
        print("No books found — check abs_url / abs_api_key (config.json or env).")
        sys.exit(1)
    print(f"Found {len(books)} books. Fetching curated manifest from {community_url} ...")
    result = import_community_spines(community_url, dry_run=dry_run, books=books)
    if not result["ok"]:
        sys.exit(1)


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

    if get_dashboard_token():
        print("Dashboard login: enabled (DASHBOARD_TOKEN).")
    else:
        print("NOTE: DASHBOARD_TOKEN is not set, so the dashboard only works from this")
        print(f"      computer at http://localhost:{port}. Spine images stay public on the")
        print("      network. To manage the server from another device or from Docker")
        print("      (where requests come from the bridge network), set DASHBOARD_TOKEN")
        print("      to a long random string, e.g. DASHBOARD_TOKEN=$(openssl rand -hex 24)")
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
    parser.add_argument("--import-community", action="store_true",
                        help="Download curated community spines for books with no local spine")
    parser.add_argument("--community-url", type=str, default=DEFAULT_COMMUNITY_URL,
                        help=f"Community spine server (default: {DEFAULT_COMMUNITY_URL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --import-community: show what would be downloaded")

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
    elif args.import_community:
        cmd_import_community(args.community_url, args.dry_run)
    else:
        cmd_serve(_config.get("port", DEFAULT_PORT), open_browser=not args.no_open)


if __name__ == "__main__":
    main()
