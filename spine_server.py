#!/usr/bin/env python3
"""
Spine Server for AudiobookShelf

A tiny HTTP server that serves book spine images and a manifest file.
Designed to run alongside your AudiobookShelf instance.

WHAT THIS DOES:
  1. Connects to your ABS server to learn about your books
  2. Looks in a local folder for spine images you've created
  3. Serves those images over HTTP so the app can display them
  4. Generates a "manifest" (a list of which books have spines)

SETUP:
  1. Set your ABS server URL and API key (see below)
  2. Put spine images in the "spines/" folder, named by book ID
     (run with --list-books to see your book IDs)
  3. Run this script
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
import sys
import json
import time
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
SPINES_DIR = os.environ.get("SPINES_DIR", os.path.join(os.path.dirname(__file__), "spines"))

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
# SPINE IMAGE MANAGEMENT
# =============================================================================

def ensure_spines_dir():
    """Create the spines folder if it doesn't exist."""
    os.makedirs(SPINES_DIR, exist_ok=True)


def find_spine_files():
    """
    Scan the spines/ folder for image files.
    Returns a dict: {book_id: file_path, ...}

    Supported naming:
      - li_abc123.png       (full ABS item ID)
      - li_abc123.jpg
      - li_abc123.webp
    """
    spines = {}
    if not os.path.isdir(SPINES_DIR):
        return spines

    for filename in os.listdir(SPINES_DIR):
        filepath = os.path.join(SPINES_DIR, filename)
        if not os.path.isfile(filepath):
            continue

        # Strip extension to get the book ID
        name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()

        if ext_lower in (".png", ".jpg", ".jpeg", ".webp"):
            spines[name] = filepath

    return spines


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
        # ABS stores paths like /audiobooks/Author/Title
        # We need to map that to the local filesystem
        # Strategy: try the path as-is, then try just the last 2-3 segments
        candidates = [abs_path]

        parts = Path(abs_path).parts
        if len(parts) >= 2:
            # Try last 2 parts (Author/Title)
            candidates.append(os.path.join(LIBRARY_PATH, *parts[-2:]))
        if len(parts) >= 3:
            # Try last 3 parts (Library/Author/Title)
            candidates.append(os.path.join(LIBRARY_PATH, *parts[-3:]))
        # Try full path under LIBRARY_PATH
        candidates.append(os.path.join(LIBRARY_PATH, abs_path.lstrip("/")))

        for folder in candidates:
            if not os.path.isdir(folder):
                continue

            # Look for spine image in this folder
            for spine_name in ["spine.png", "spine.jpg", "spine.jpeg", "spine.webp"]:
                spine_path = os.path.join(folder, spine_name)
                if os.path.isfile(spine_path):
                    # Copy to spines/ folder named by book ID
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

    The manifest is just a list:
      {
        "items": ["li_abc123", "li_def456", ...],
        "version": 1,
        "count": 2,
        "generated": "2026-03-23T12:00:00"
      }
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
            cls._spine_files = find_spine_files()
            cls._manifest = build_manifest(cls._spine_files)
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
            # Extract book ID from the path
            # /api/items/li_abc123/spine -> li_abc123
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

        # Figure out the content type from the file extension
        content_type = mimetypes.guess_type(filepath)[0] or "image/png"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Let the app cache this for a long time (1 week)
        self.send_header("Cache-Control", "public, max-age=604800")
        # Allow requests from any origin (the app needs this)
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

    print(f"\n--- How to use these IDs ---")
    print(f"Name your spine image files using the BOOK ID above.")
    print(f"Put them in: {SPINES_DIR}/")
    print(f"Example: {SPINES_DIR}/{books[0]['id']}.png")


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

    # Do initial scan
    spine_files = find_spine_files()
    SpineHandler._spine_files = spine_files
    SpineHandler._manifest = build_manifest(spine_files)
    SpineHandler._last_scan = time.time()

    print(f"=== Spine Server ===")
    print(f"")
    print(f"Serving {len(spine_files)} spine images")
    print(f"Spines folder: {SPINES_DIR}")
    print(f"")
    print(f"Server running at: http://0.0.0.0:{port}")
    print(f"")
    print(f"--- App Setup ---")
    print(f"In the app, go to:")
    print(f"  Settings > Display > Spine Server URL")
    print(f"Enter: http://YOUR_IP_ADDRESS:{port}")
    print(f"")
    print(f"--- Endpoints ---")
    print(f"  GET /api/spines/manifest      - List of books with spines")
    print(f"  GET /api/items/{{id}}/spine     - Get a spine image")
    print(f"  GET /health                    - Server status")
    print(f"")

    if len(spine_files) == 0:
        print(f"WARNING: No spine images found in {SPINES_DIR}/")
        print(f"  Run with --list-books to see your book IDs")
        print(f"  Then put images named by ID in {SPINES_DIR}/")
        print(f"")

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
