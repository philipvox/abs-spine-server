#!/usr/bin/env python3
"""
Spine Server for AudiobookShelf

A tiny HTTP server that serves book spine images and a manifest file.
Designed to run alongside your AudiobookShelf instance.

WHAT THIS DOES:
  1. Connects to your ABS server to learn about your books
  2. Looks in a local folder for spine images
  3. Auto-matches images to books by TITLE or by ID
  4. Serves those images over HTTP so the app can display them
  5. Generates a "manifest" (a list of which books have spines)

NAMING YOUR FILES:
  You can name spine images however you want. The server figures it out:

    By title:           Dune.png
    By title (spaces):  The Hobbit.png
    With author:        Frank Herbert - Dune.png
    With underscores:   The_Great_Gatsby.png
    By book ID:         li_8f7bd2c8.png           (always works, even without ABS)

SETUP:
  1. Set your ABS server URL and API key (see below)
  2. Put spine images in the "spines/" folder
  3. Run this script — it auto-matches files to books
  4. In the app, go to Settings > Display > Spine Server URL
     and enter this server's address (e.g. http://192.168.1.100:8786)

ZERO DEPENDENCIES - just Python 3.6+, nothing to install.

Usage:
  python3 spine_server.py                    # Start the server
  python3 spine_server.py --list-books       # Show all books with their IDs
  python3 spine_server.py --scan-library     # Find spine.png files in your ABS library
  python3 spine_server.py --port 9000        # Use a different port
"""

import os
import re
import sys
import json
import time
import unicodedata
import argparse
import mimetypes
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

# =============================================================================
# CONFIGURATION — Change these to match YOUR setup
# =============================================================================

# Your AudiobookShelf server address (where you access it in a browser)
ABS_URL = os.environ.get("ABS_URL", "http://localhost:13378")

# Your ABS API key (get this from ABS > Settings > API Tokens)
ABS_API_KEY = os.environ.get("ABS_API_KEY", "")

# Port this spine server will listen on
DEFAULT_PORT = 8786

# Folder where you put your spine images
SPINES_DIR = os.environ.get("SPINES_DIR", os.path.join(os.path.dirname(__file__) or ".", "spines"))

# If your audiobook files are accessible locally, set this to the root path.
# This lets --scan-library find spine.png files inside book folders.
# Example: /mnt/audiobooks  or  /audiobooks  or  C:\Audiobooks
LIBRARY_PATH = os.environ.get("LIBRARY_PATH", "")


# =============================================================================
# ABS API HELPERS
# =============================================================================

def abs_api_get(endpoint):
    """Make a GET request to the ABS API. Returns parsed JSON or None on error."""
    if not ABS_API_KEY:
        print("ERROR: No ABS_API_KEY set. Get one from ABS > Settings > API Tokens")
        print("       Set it with: export ABS_API_KEY='your-key-here'")
        sys.exit(1)

    url = f"{ABS_URL.rstrip('/')}{endpoint}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {ABS_API_KEY}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.reason}")
        if e.code == 401:
            print("  -> Your API key is invalid. Check ABS > Settings > API Tokens")
        return None
    except urllib.error.URLError as e:
        print(f"Cannot reach ABS at {ABS_URL}: {e.reason}")
        print("  -> Is ABS running? Is the URL correct?")
        return None


def get_all_libraries():
    """Get list of all libraries from ABS."""
    data = abs_api_get("/api/libraries")
    if not data:
        return []
    return data.get("libraries", [])


def get_library_items(library_id):
    """Get all items from a library."""
    data = abs_api_get(f"/api/libraries/{library_id}/items?limit=100000")
    if not data:
        return []
    return data.get("results", [])


def get_all_books():
    """
    Get ALL books from ALL libraries.
    Returns a list of dicts: [{id, title, author, path, libraryId}, ...]
    """
    books = []
    libraries = get_all_libraries()

    if not libraries:
        print("No libraries found. Is ABS set up?")
        return books

    for lib in libraries:
        lib_id = lib["id"]
        lib_name = lib.get("name", lib_id)
        items = get_library_items(lib_id)

        for item in items:
            media = item.get("media", {})
            metadata = media.get("metadata", {})

            book = {
                "id": item["id"],
                "title": metadata.get("title", "Unknown"),
                "author": metadata.get("authorName", "Unknown"),
                "path": item.get("path", ""),
                "libraryId": lib_id,
                "libraryName": lib_name,
            }
            books.append(book)

    return books


# =============================================================================
# TITLE MATCHING
# =============================================================================

def normalize(text):
    """
    Normalize a string for fuzzy comparison.

    "The Hitchhiker's Guide to the Galaxy" → "hitchhikers guide to the galaxy"
    "Frank Herbert - Dune"                 → "frank herbert dune"
    "The_Great_Gatsby"                     → "the great gatsby"
    "Ender\u2019s Game"                    → "enders game"
    """
    # Unicode normalize (curly quotes → straight, accents → base)
    text = unicodedata.normalize("NFKD", text)
    # Lowercase
    text = text.lower()
    # Replace underscores, hyphens, dots with spaces
    text = re.sub(r"[_.\-]+", " ", text)
    # Remove all punctuation (apostrophes, colons, commas, etc.)
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_title_index(books):
    """
    Build lookup tables for matching filenames to books.

    Returns a dict of normalized_string → book_id.
    Multiple keys can point to the same book to increase match chances.

    For each book we index:
      - title                        ("dune")
      - author - title               ("frank herbert dune")
      - title - author               ("dune frank herbert")
      - title without leading "the"  ("hobbit" for "The Hobbit")
      - title without subtitle       ("dune" for "Dune: Part One")
    """
    index = {}
    collisions = {}  # track normalized keys that map to multiple books

    def add(key, book_id, description):
        if not key:
            return
        if key in index and index[key] != book_id:
            # Collision — two books normalize to the same key. Mark as ambiguous.
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

        # Title alone
        add(nt, bid, "title")

        # Author - Title  and  Title - Author
        if na and na != "unknown":
            add(f"{na} {nt}", bid, "author title")
            add(f"{nt} {na}", bid, "title author")

        # Without leading article
        for article in ("the ", "a ", "an "):
            if nt.startswith(article):
                add(nt[len(article):], bid, "title without article")
                break

        # Without subtitle (split on colon or dash-surrounded-by-spaces)
        for sep in [":", " - "]:
            if sep in title:
                base = normalize(title.split(sep)[0])
                add(base, bid, "title without subtitle")

    # Remove ambiguous keys
    for key in collisions:
        if key in index:
            del index[key]

    return index, collisions


def match_filename_to_book(filename, title_index):
    """
    Try to match a filename (without extension) to a book ID.

    Tries progressively looser matching:
      1. Exact book ID (starts with "li_")
      2. Exact normalized match
      3. "Author - Title" split on " - "
      4. Partial match (filename contained in a title key, or vice versa)

    Returns (book_id, match_type) or (None, None).
    """
    # 1. Already a book ID
    if filename.startswith("li_"):
        return filename, "id"

    nf = normalize(filename)
    if not nf:
        return None, None

    # 2. Exact match
    if nf in title_index:
        return title_index[nf], "exact"

    # 3. Try splitting "Author - Title" or "Title - Author"
    for sep in [" - ", " — ", " – "]:
        if sep in filename:
            parts = filename.split(sep, 1)
            for combo in [parts, reversed(parts)]:
                combo = list(combo)
                joined = normalize(" ".join(combo))
                if joined in title_index:
                    return title_index[joined], "author-title split"
                # Try each part alone
                for part in combo:
                    np = normalize(part)
                    if np in title_index:
                        return title_index[np], "part match"

    # 4. Containment — filename is a substring of a key or vice versa
    #    Only if the shorter side is at least 5 chars (avoid false positives)
    best_match = None
    best_len = 0
    for key, book_id in title_index.items():
        if len(key) < 5 or len(nf) < 5:
            continue
        if nf in key or key in nf:
            # Prefer the tighter match (less leftover)
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

# Global book index (populated on startup if ABS is reachable)
_title_index = {}
_books_by_id = {}  # id → {title, author, ...} for logging
_index_loaded = False
_collisions = {}


def load_book_index():
    """
    Fetch books from ABS and build the title matching index.
    Called once on startup. If ABS is unreachable, falls back to ID-only mode.
    """
    global _title_index, _books_by_id, _index_loaded, _collisions

    if not ABS_API_KEY:
        print("No ABS_API_KEY set — running in ID-only mode.")
        print("  Files must be named by book ID (e.g. li_abc123.png)")
        print("  Set ABS_API_KEY to enable auto-matching by title.")
        _index_loaded = True
        return

    print("Connecting to ABS to build book index...")
    books = get_all_books()

    if not books:
        print("WARNING: Could not load books from ABS. Running in ID-only mode.")
        _index_loaded = True
        return

    _title_index, _collisions = build_title_index(books)
    _books_by_id = {b["id"]: b for b in books}
    _index_loaded = True

    print(f"Indexed {len(books)} books ({len(_title_index)} matchable keys)")

    if _collisions:
        print(f"  {len(_collisions)} ambiguous titles (skipped, use book IDs for these):")
        for key in sorted(list(_collisions.keys())[:5]):
            titles = [_books_by_id[bid]["title"] for bid in _collisions[key] if bid in _books_by_id]
            print(f"    \"{key}\" matches: {', '.join(titles)}")
        if len(_collisions) > 5:
            print(f"    ... and {len(_collisions) - 5} more")


def ensure_spines_dir():
    """Create the spines folder if it doesn't exist."""
    os.makedirs(SPINES_DIR, exist_ok=True)


def find_spine_files():
    """
    Scan the spines/ folder for image files.
    Returns a dict: {book_id: file_path, ...}

    Files can be named:
      - By book ID:    li_abc123.png         (always works)
      - By title:      Dune.png              (matched via ABS)
      - Author-title:  Frank Herbert - Dune.png
      - Underscores:   The_Hobbit.png
    """
    spines = {}
    unmatched = []

    if not os.path.isdir(SPINES_DIR):
        return spines

    for filename in os.listdir(SPINES_DIR):
        filepath = os.path.join(SPINES_DIR, filename)
        if not os.path.isfile(filepath):
            continue

        name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()

        if ext_lower not in (".png", ".jpg", ".jpeg", ".webp"):
            continue

        # Try to match this filename to a book
        book_id, match_type = match_filename_to_book(name, _title_index)

        if book_id:
            if book_id in spines:
                # Already have a spine for this book — prefer ID-named files
                existing_name = os.path.basename(spines[book_id])
                if match_type != "id" and existing_name.startswith("li_"):
                    continue  # Keep the ID-named one
            spines[book_id] = filepath
        else:
            unmatched.append(filename)

    return spines, unmatched


def scan_library_for_spines(books):
    """
    Walk the ABS library folders looking for spine.png/spine.jpg files.
    Copies found spines into the spines/ folder named by book ID.
    Returns count of spines found.
    """
    if not LIBRARY_PATH:
        print("ERROR: LIBRARY_PATH not set.")
        print("  Set it to where your audiobook files live on THIS machine.")
        print("  Example: export LIBRARY_PATH='/mnt/audiobooks'")
        return 0

    if not os.path.isdir(LIBRARY_PATH):
        print(f"ERROR: LIBRARY_PATH '{LIBRARY_PATH}' doesn't exist or isn't a directory.")
        return 0

    ensure_spines_dir()
    found = 0

    for book in books:
        abs_path = book["path"]  # e.g. /audiobooks/Author/Title

        # Try to find the book folder relative to LIBRARY_PATH
        candidates = [abs_path]

        parts = Path(abs_path).parts
        if len(parts) >= 2:
            candidates.append(os.path.join(LIBRARY_PATH, *parts[-2:]))
        if len(parts) >= 3:
            candidates.append(os.path.join(LIBRARY_PATH, *parts[-3:]))
        candidates.append(os.path.join(LIBRARY_PATH, abs_path.lstrip("/")))

        for folder in candidates:
            if not os.path.isdir(folder):
                continue

            for spine_name in ["spine.png", "spine.jpg", "spine.jpeg", "spine.webp"]:
                spine_path = os.path.join(folder, spine_name)
                if os.path.isfile(spine_path):
                    ext = os.path.splitext(spine_name)[1]
                    dest = os.path.join(SPINES_DIR, f"{book['id']}{ext}")

                    if not os.path.exists(dest):
                        import shutil
                        shutil.copy2(spine_path, dest)
                        print(f"  Found: {book['title']}")

                    found += 1
                    break
            else:
                continue
            break

    return found


def build_manifest(spine_files):
    """
    Build the manifest JSON that tells the app which books have spines.
    """
    return {
        "items": sorted(spine_files.keys()),
        "version": 1,
        "count": len(spine_files),
        "generated": datetime.now().isoformat(),
    }


# =============================================================================
# HTTP SERVER
# =============================================================================

class SpineHandler(BaseHTTPRequestHandler):
    """
    Handles two types of requests:

    1. GET /api/spines/manifest
       Returns the manifest JSON (list of book IDs that have spines)

    2. GET /api/items/{bookId}/spine
       Returns the actual spine image file

    These URLs match exactly what the app expects, so the app
    just needs to know this server's address.
    """

    # Class-level cache (shared across requests)
    _spine_files = None
    _manifest = None
    _last_scan = 0
    _scan_interval = 30  # Re-scan folder every 30 seconds

    @classmethod
    def refresh_if_needed(cls):
        """Re-scan the spines folder periodically to pick up new images."""
        now = time.time()
        if now - cls._last_scan > cls._scan_interval:
            spines, unmatched = find_spine_files()
            cls._spine_files = spines
            cls._manifest = build_manifest(spines)
            cls._last_scan = now

    def do_GET(self):
        self.refresh_if_needed()

        # Strip query params for matching (app sends ?v=1&t=123 for cache busting)
        path = self.path.split("?")[0]

        # --- Manifest ---
        if path == "/api/spines/manifest":
            self.send_json(self._manifest)
            return

        # --- Spine image ---
        # Path: /api/items/{bookId}/spine
        if path.startswith("/api/items/") and path.endswith("/spine"):
            parts = path.split("/")
            if len(parts) >= 4:
                book_id = parts[3]
                self.serve_spine_image(book_id)
                return

        # --- Health check ---
        if path == "/health":
            self.send_json({
                "status": "ok",
                "spines": len(self._spine_files) if self._spine_files else 0,
                "indexed_books": len(_books_by_id),
                "matchable_keys": len(_title_index),
            })
            return

        # --- Not found ---
        self.send_error(404, "Not found")

    def serve_spine_image(self, book_id):
        """Send back a spine image file."""
        if not self._spine_files or book_id not in self._spine_files:
            self.send_error(404, f"No spine for book {book_id}")
            return

        filepath = self._spine_files[book_id]

        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except IOError:
            self.send_error(500, "Could not read spine file")
            return

        content_type = mimetypes.guess_type(filepath)[0] or "image/png"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data):
        """Send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Quieter logging - only show errors and spine requests."""
        msg = format % args
        if "404" in msg or "/spine" in msg or "manifest" in msg:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# =============================================================================
# CLI COMMANDS
# =============================================================================

def cmd_list_books():
    """Print all books with their IDs so users know what to name their files."""
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

    print()
    print("--- How to name your spine files ---")
    print()
    print("You can use ANY of these naming styles:")
    print(f"  {SPINES_DIR}/Dune.png                       (just the title)")
    print(f"  {SPINES_DIR}/Frank Herbert - Dune.png       (author - title)")
    print(f"  {SPINES_DIR}/The_Hobbit.png                 (underscores for spaces)")
    print(f"  {SPINES_DIR}/{books[0]['id']}.png   (book ID — always works)")
    print()
    print("The server auto-matches filenames to books when it starts.")


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
        print(f"Copied to: {SPINES_DIR}/")
        print("Start the server to serve them: python3 spine_server.py")


def cmd_serve(port):
    """Start the HTTP server."""
    ensure_spines_dir()

    # Build the title matching index from ABS
    load_book_index()

    # Do initial scan and match
    spine_files, unmatched = find_spine_files()
    SpineHandler._spine_files = spine_files
    SpineHandler._manifest = build_manifest(spine_files)
    SpineHandler._last_scan = time.time()

    print()
    print("=== Spine Server ===")
    print()

    # Show what matched
    if spine_files:
        print(f"Matched {len(spine_files)} spine images:")
        for book_id, filepath in sorted(spine_files.items(), key=lambda x: x[1]):
            filename = os.path.basename(filepath)
            book = _books_by_id.get(book_id)
            if book:
                print(f"  {filename:<40} → {book['title']} ({book['author']})")
            else:
                print(f"  {filename:<40} → {book_id}")
        print()

    # Show what didn't match
    if unmatched:
        print(f"Could not match {len(unmatched)} files:")
        for f in sorted(unmatched):
            print(f"  {f}")
        print("  Tip: use --list-books to see valid titles, or rename to a book ID")
        print()

    if not spine_files and not unmatched:
        print(f"No spine images found in {SPINES_DIR}/")
        print(f"  Add images named by title (e.g. Dune.png) or by book ID")
        print(f"  Run with --list-books to see your books")
        print()

    print(f"Spines folder: {SPINES_DIR}")
    print(f"Server running at: http://0.0.0.0:{port}")
    print()
    print("--- App Setup ---")
    print("In the app, go to:")
    print("  Settings > Display > Spine Server URL")
    print(f"Enter: http://YOUR_IP_ADDRESS:{port}")
    print()
    print("--- Endpoints ---")
    print("  GET /api/spines/manifest      - List of books with spines")
    print("  GET /api/items/{id}/spine      - Get a spine image")
    print("  GET /health                    - Server status")
    print()

    server = HTTPServer(("0.0.0.0", port), SpineHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


# =============================================================================
# MAIN
# =============================================================================

def _override_spines_dir(new_dir):
    global SPINES_DIR
    SPINES_DIR = new_dir


def main():
    parser = argparse.ArgumentParser(
        description="Spine Server for AudiobookShelf",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
File Naming:
  Name your spine images however you like. The server matches them
  to your ABS books automatically:

    Dune.png                         (title)
    Frank Herbert - Dune.png         (author - title)
    The_Hobbit.png                   (underscores = spaces)
    li_8f7bd2c8.png                  (book ID — always works)

Examples:
  # See your books and their IDs:
  python3 spine_server.py --list-books

  # If you already have spine.png files in your book folders:
  python3 spine_server.py --scan-library

  # Start serving spines:
  python3 spine_server.py

  # Use environment variables for config:
  ABS_URL=http://my-abs:13378 ABS_API_KEY=my-key python3 spine_server.py

  # Use a different port:
  python3 spine_server.py --port 9000
        """,
    )

    parser.add_argument(
        "--list-books",
        action="store_true",
        help="List all books with their IDs (so you know what to name your spine files)",
    )
    parser.add_argument(
        "--scan-library",
        action="store_true",
        help="Scan your ABS library folders for existing spine.png/jpg files",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", DEFAULT_PORT)),
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--spines-dir",
        type=str,
        default=SPINES_DIR,
        help=f"Folder containing spine images (default: ./spines)",
    )

    args = parser.parse_args()

    # Override spines dir if specified
    if args.spines_dir != SPINES_DIR:
        _override_spines_dir(args.spines_dir)

    if args.list_books:
        cmd_list_books()
    elif args.scan_library:
        cmd_scan_library()
    else:
        cmd_serve(args.port)


if __name__ == "__main__":
    main()
