# ABS Spine Server

A lightweight image server that serves custom book spine images for [AudiobookShelf](https://www.audiobookshelf.org/) mobile apps. Drop in spine images, run the server, and your books get custom spines on the shelf view.

**Name your files however you want.** The server auto-matches them to your books:

```
spines/
├── Dune.png                          ← matched by title
├── Frank Herbert - Dune.png          ← matched by author + title
├── The_Hobbit.png                    ← underscores work too
├── 1984.jpg                          ← numbers, symbols, all fine
└── li_8f7bd2c8-0146-41be-8207.png    ← or use the book ID directly
```

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Your Phone App         Spine Server         ABS Server    │
│                                                             │
│   ┌───────────┐      ┌───────────────┐     ┌───────────┐   │
│   │           │      │               │     │           │   │
│   │  "Do you  │─────>│  On startup:  │     │           │   │
│   │  have any │      │  1. Asks ABS  │────>│  Book     │   │
│   │  spines?" │      │     for books │     │  list     │   │
│   │           │      │  2. Matches   │     │           │   │
│   │  "Give me │─────>│     filenames │     │           │   │
│   │  spine for│      │     to books  │     │  Audio,   │   │
│   │  book X"  │      │  3. Serves    │     │  covers,  │   │
│   │           │      │     images    │     │  metadata │   │
│   └───────────┘      └───────────────┘     └───────────┘   │
│                        port 8786            port 13378      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

- [What This Does](#what-this-does)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Quick Start (No Docker)](#quick-start-no-docker)
- [Step-by-Step Guide](#step-by-step-guide)
  - [Step 1: Get Your ABS API Key](#step-1-get-your-abs-api-key)
  - [Step 2: Add Spine Images](#step-2-add-spine-images)
  - [Step 3: Start the Server](#step-3-start-the-server)
  - [Step 4: Connect the App](#step-4-connect-the-app)
- [File Naming Guide](#file-naming-guide)
- [Creating Spine Images](#creating-spine-images)
- [Configuration Reference](#configuration-reference)
- [Docker Compose Examples](#docker-compose-examples)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [How It Works](#how-it-works)

---

## What This Does

AudiobookShelf doesn't have a built-in "book spine" feature. This server fills that gap by serving spine images to the mobile app over HTTP.

**The server does three things:**

1. **Connects to ABS on startup** to learn your book titles, authors, and IDs
2. **Auto-matches your image filenames** to the right books (by title, author, or ID)
3. **Serves spine images** to the app when requested

**What it does NOT do:**
- It does not modify your ABS server or library
- It does not generate spine images for you (you create them)
- It does not require any changes to ABS itself

---

## Prerequisites

- An [AudiobookShelf](https://www.audiobookshelf.org/) server (any version)
- An ABS API key (instructions below)
- Spine images you've created (PNG, JPG, or WebP)
- **Docker** OR **Python 3.6+** (the script has zero dependencies)

---

## Quick Start (Docker)

```bash
# Clone
git clone https://github.com/philipvox/abs-spine-server.git
cd abs-spine-server

# Create a folder for your spine images
mkdir spines

# Put spine images in spines/ — name them by title, e.g. "Dune.png"

# Edit docker-compose.yml → set your ABS_API_KEY

# Start the server
docker compose up -d

# Verify it's working
curl http://localhost:8786/health
```

## Quick Start (No Docker)

```bash
# Clone
git clone https://github.com/philipvox/abs-spine-server.git
cd abs-spine-server

# Put spine images in spines/ — name them by title, e.g. "Dune.png"

# Start the server
ABS_URL=http://your-abs-server:13378 ABS_API_KEY=your-key python3 spine_server.py
```

No `pip install`, no virtual environment, no requirements.txt. Just Python.

---

## Step-by-Step Guide

### Step 1: Get Your ABS API Key

The spine server needs to talk to your ABS server to learn about your books. It uses an API key to authenticate.

1. Open your AudiobookShelf web interface in a browser
2. Click the **gear icon** (Settings) in the top-right
3. Go to **Users** and click your username
4. Scroll down to **API Tokens**
5. Click **Create** to generate a new token
6. Copy the token — you'll need it in the next step

> **What is an API key?** It's like a password that lets programs talk to your ABS server. The spine server uses it to get your book list. It only reads data — it never changes anything.

### Step 2: Add Spine Images

Create a `spines/` folder and drop your spine images into it. **Name them whatever you want** — the server figures out which book each one belongs to.

```
spines/
├── Dune.png
├── The Hobbit.jpg
├── Project Hail Mary.webp
└── Frank Herbert - Children of Dune.png
```

The server matches filenames to books using your ABS library. It handles:
- Exact titles: `Dune.png`
- Titles with spaces, punctuation, apostrophes: `Ender's Game.png`
- Underscores instead of spaces: `The_Great_Gatsby.png`
- Author + title: `Frank Herbert - Dune.png`
- Leading articles: `Hobbit.png` matches "The Hobbit"
- Subtitles: `Dune.png` matches "Dune: Part One"

On startup, the server tells you exactly what it matched:

```
Indexed 247 books (493 matchable keys)

Matched 4 spine images:
  Dune.png                                 → Dune (Frank Herbert)
  The Hobbit.jpg                           → The Hobbit (J.R.R. Tolkien)
  Project Hail Mary.webp                   → Project Hail Mary (Andy Weir)
  Frank Herbert - Children of Dune.png     → Children of Dune (Frank Herbert)

Could not match 1 files:
  random_image.png
  Tip: use --list-books to see valid titles, or rename to a book ID
```

> **Already have spine images in your audiobook folders?** If your book folders contain files named `spine.png` or `spine.jpg`, the server can find and import them automatically:
> ```bash
> # Docker (mount your library read-only):
> # Add to docker-compose.yml volumes: - /path/to/audiobooks:/audiobooks:ro
> # Add to environment: LIBRARY_PATH: /audiobooks
> docker compose run --rm spine-server --scan-library
>
> # Without Docker:
> LIBRARY_PATH=/path/to/audiobooks ABS_URL=... ABS_API_KEY=... python3 spine_server.py --scan-library
> ```

### Step 3: Start the Server

**With Docker:**
```bash
docker compose up -d
```

**Without Docker:**
```bash
ABS_URL=http://your-abs-server:13378 \
ABS_API_KEY=your-key \
python3 spine_server.py
```

Verify it's running:
```bash
# Health check
curl http://localhost:8786/health
# → {"status": "ok", "spines": 4, "indexed_books": 247, "matchable_keys": 493}

# Get the manifest (list of books with spines)
curl http://localhost:8786/api/spines/manifest
# → {"items": ["li_8f7bd2c8-...", "li_a2c3d4e5-...", ...], "count": 4, ...}
```

### Step 4: Connect the App

1. Open the app on your phone
2. Go to **Settings** (profile tab)
3. Tap **Display Settings**
4. Toggle **Server Spines** on
5. Enter your **Spine Server URL**: `http://YOUR_SERVER_IP:8786`
6. Tap the checkmark to save

> **Finding your server IP:** This is the IP address of the machine running the spine server. On Linux/Mac, run `hostname -I` or `ifconfig`. On Windows, run `ipconfig`. It'll be something like `192.168.1.100`.

> **Important:** Your phone must be able to reach this IP. If the spine server is on your home network, your phone needs to be on the same network (same WiFi).

---

## File Naming Guide

The server matches filenames to books using multiple strategies, tried in order:

| Priority | Naming Style | Example | How It Matches |
|----------|-------------|---------|----------------|
| 1 | Book ID | `li_8f7bd2c8.png` | Direct ID lookup (always works, even without ABS) |
| 2 | Exact title | `Dune.png` | Normalized text comparison |
| 3 | Author - Title | `Frank Herbert - Dune.png` | Splits on ` - ` and matches both parts |
| 4 | Title with underscores | `The_Great_Gatsby.png` | Underscores treated as spaces |
| 5 | Without subtitle | `Dune.png` | Matches "Dune: Part One" |
| 6 | Without "The/A/An" | `Hobbit.png` | Matches "The Hobbit" |
| 7 | Fuzzy containment | `Gatsby.png` | Matches if filename is contained in a title |

**Normalization rules** (applied to both filenames and book titles):
- Lowercased
- Underscores, hyphens, dots → spaces
- Punctuation removed (apostrophes, colons, etc.)
- Unicode normalized (curly quotes → straight, accents → base letters)
- Whitespace collapsed

**When two books have the same title** (e.g., two different editions), the server skips the ambiguous match and logs a warning. Use the book ID for those.

**To see what matched:** The server prints a full match report on startup.

**To see all your books and valid names:**
```bash
python3 spine_server.py --list-books
# or
docker compose run --rm spine-server --list-books
```

---

## Creating Spine Images

The spine server doesn't generate images — it serves images you provide. Here are some ways to create them:

### Image Specifications

| Property | Recommended | Notes |
|----------|-------------|-------|
| Format | PNG or WebP | JPG works but doesn't support transparency |
| Width | 60–120 pixels | Narrow, like a real book spine |
| Height | 400–800 pixels | Tall, like a real book spine |
| Orientation | Vertical | Text should read top-to-bottom or bottom-to-top |

### Methods for Creating Spines

**Manual (Photoshop, GIMP, Canva, etc.):**
- Create a tall, narrow image (e.g., 80x600px)
- Add the book title vertically
- Use colors/textures that match the book's cover
- Export as PNG

**Batch Generation (Python + Pillow):**
```python
from PIL import Image, ImageDraw, ImageFont

def make_spine(title, author, width=80, height=600, bg_color="#2C1810"):
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    # Add rotated text, decorations, etc.
    # ...
    return img
```

**AI-Generated:**
- Use image generation tools with prompts like "book spine for [title], narrow vertical image, library aesthetic"
- Crop/resize to the right proportions

### Tips

- Keep text readable at small sizes — the app may display spines as narrow as 40px wide
- Use high contrast between text and background
- Consider matching the spine color to the book's cover art
- Consistent heights look best when books are displayed side-by-side on a shelf

---

## Configuration Reference

All configuration is done via environment variables. No config files needed.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ABS_URL` | Yes (for title matching) | `http://localhost:13378` | Your AudiobookShelf server URL |
| `ABS_API_KEY` | Yes (for title matching) | (empty) | ABS API token |
| `SPINES_DIR` | No | `./spines` | Folder containing your spine images |
| `LIBRARY_PATH` | For `--scan-library` only | (empty) | Local path to your audiobook files |
| `PORT` | No | `8786` | Port the server listens on |

> **Without ABS_API_KEY:** The server still works, but files must be named by book ID (e.g., `li_abc123.png`). Title matching requires ABS access to know your book titles.

### Command-Line Arguments

```
python3 spine_server.py [OPTIONS]

Options:
  --list-books       Connect to ABS and print all books with their IDs/titles
  --scan-library     Find spine.png/jpg files in your audiobook folders
  --port PORT        Port to listen on (default: 8786)
  --spines-dir DIR   Folder containing spine images (default: ./spines)
  -h, --help         Show help
```

---

## Docker Compose Examples

### Minimal Setup

```yaml
services:
  spine-server:
    build: .
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: "your-key-here"
```

### Alongside ABS (Same Docker Network)

If your ABS also runs in Docker, they need to be on the same network to talk:

```yaml
services:
  spine-server:
    build: .
    container_name: abs-spine-server
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: "your-key-here"
    networks:
      - audiobookshelf_default

networks:
  audiobookshelf_default:
    external: true
```

> **Finding your ABS network name:** Run `docker network ls` and look for the one your ABS container uses. It's usually named something like `audiobookshelf_default` or `abs_default`.

### With Library Scanning

If your audiobook files are accessible and you want `--scan-library` to find existing `spine.png` files:

```yaml
services:
  spine-server:
    build: .
    container_name: abs-spine-server
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
      - /path/to/your/audiobooks:/audiobooks:ro
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: "your-key-here"
      LIBRARY_PATH: /audiobooks
```

### Using .env File

Create a `.env` file to keep secrets out of docker-compose.yml:

```bash
# .env
ABS_URL=http://audiobookshelf:13378
ABS_API_KEY=your-key-here
```

```yaml
# docker-compose.yml
services:
  spine-server:
    build: .
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    env_file:
      - .env
```

---

## API Reference

The spine server exposes three endpoints. No authentication required.

### GET /api/spines/manifest

Returns a JSON list of all book IDs that have spine images available.

**Response:**
```json
{
  "items": ["li_abc123", "li_def456", "li_ghi789"],
  "version": 1,
  "count": 3,
  "generated": "2026-03-23T14:30:00.000000"
}
```

The app calls this once at startup. It uses the list to know which books it should request spines for, so it doesn't make unnecessary requests for books that don't have spines.

### GET /api/items/{bookId}/spine

Returns the spine image for a specific book.

**Parameters:**
- `bookId` (path) — the ABS library item ID (e.g., `li_abc123`)

**Response:** The image file (PNG, JPG, or WebP) with appropriate `Content-Type` header.

**Cache:** Response includes `Cache-Control: public, max-age=604800` (1 week).

**Errors:**
- `404` — No spine image exists for this book ID

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "spines": 42,
  "indexed_books": 247,
  "matchable_keys": 493
}
```

---

## Troubleshooting

### "Cannot reach ABS" when starting the server

**Cause:** The spine server can't connect to your ABS server to build the book index.

**Fix:**
- Check that `ABS_URL` is correct — open it in a browser to verify
- If both run in Docker, they must be on the same Docker network
- If ABS runs on the host and the spine server runs in Docker, use `http://host.docker.internal:13378` as the URL
- The server still works without ABS, but files must be named by book ID

### "Could not match N files"

The server found image files it couldn't match to any book in your library.

**Common causes:**
- Typo in the filename (check spelling against `--list-books` output)
- The book title in ABS is different from what you expected
- Two books have the same title (ambiguous — use book ID instead)

**Fix:** Run `--list-books` to see exact titles, then rename your files to match.

### No spines showing in the app

**Check the server is reachable from your phone:**
1. Open a browser on your phone
2. Go to `http://YOUR_SERVER_IP:8786/health`
3. You should see `{"status": "ok", "spines": N}`

**If you can't reach it:**
- Make sure your phone is on the same WiFi network as the server
- Check your firewall allows port 8786
- If using Docker, make sure the port is exposed (`ports: "8786:8786"`)

**If you can reach it but spines don't show:**
- Check the manifest: `http://YOUR_SERVER_IP:8786/api/spines/manifest` — does it list book IDs?
- In the app, toggle Server Spines off and back on
- Make sure the Spine Server URL in the app matches exactly (including `http://`)

### App shows procedural (generated) spines instead of my images

This means the app either can't reach the spine server or the manifest is empty.

1. Check `http://YOUR_SERVER_IP:8786/api/spines/manifest` — the `count` should be > 0
2. Check the server startup log — did your files match?
3. The server re-scans the folder every 30 seconds — wait a moment after adding new images

### How do I update spine images?

Just replace the file in the `spines/` folder. The server picks up changes within 30 seconds. In the app, pull down to refresh the library to see updated spines.

### Running on a VPS / remote server

The spine server works anywhere — it doesn't need to be on the same machine as ABS. Set `ABS_URL` to your ABS server's public URL (or private IP if they're on the same network). Make sure port 8786 is accessible from wherever your phone will connect.

---

## How It Works

### Architecture

The spine server is a single Python file with zero dependencies.

```
spine_server.py
      │
      ├── On startup: connects to ABS API
      │   ├── Fetches all books (titles, authors, IDs)
      │   └── Builds a title-matching index
      │
      ├── Scans spines/ folder every 30 seconds
      │   ├── Matches filenames to books (by title, author, or ID)
      │   └── Builds a manifest (list of matched book IDs)
      │
      ├── GET /api/spines/manifest
      │   └── Returns the manifest as JSON
      │
      ├── GET /api/items/{id}/spine
      │   └── Reads the image file from disk and sends it back
      │
      └── GET /health
          └── Returns OK + spine count + index stats
```

### Title Matching

On startup, the server builds an index from your ABS library. For each book, it creates multiple lookup keys:

| Book | Keys generated |
|------|---------------|
| "The Hobbit" by J.R.R. Tolkien | `the hobbit`, `jrr tolkien the hobbit`, `the hobbit jrr tolkien`, `hobbit` |
| "Dune: Part One" by Frank Herbert | `dune part one`, `frank herbert dune part one`, `dune` |

When scanning the spines folder, each filename is normalized the same way and looked up in the index. Normalization strips punctuation, lowercases, and collapses whitespace — so `The_Hobbit.png`, `the hobbit.png`, and `THE HOBBIT.PNG` all match the same book.

If two books produce the same key (e.g., two editions of "Dune"), that key is marked ambiguous and skipped. Use the book ID for those cases.

### Why a Separate Server?

AudiobookShelf doesn't have a spine image feature. Rather than modifying ABS (which would break on updates), this runs alongside it as a separate service. The mobile app is configured to know about both servers:

- **ABS server** — audio, metadata, covers, playback sessions, progress
- **Spine server** — just spine images (this project)

### URL Compatibility

The spine server uses the same URL patterns that the app already expects:

| Endpoint | App expects | Server provides |
|----------|-------------|-----------------|
| Manifest | `GET /api/spines/manifest` | JSON list of book IDs |
| Spine image | `GET /api/items/{id}/spine` | Image file |

This means the app doesn't need special logic — it just talks to a different base URL.

### Auto-Refresh

The server re-scans the `spines/` folder every 30 seconds. You can add, remove, or replace spine images at any time without restarting the server. New files are automatically matched against the book index.

---

## License

MIT
