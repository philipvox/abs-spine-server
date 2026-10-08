<p align="center">
  <h1 align="center">ABS Spine Server</h1>
  <p align="center">
    Custom book spine images for <a href="https://www.audiobookshelf.org/">AudiobookShelf</a>.<br>
    Drop in images. Name them by title. The server does the rest.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.6+-3776AB?logo=python&logoColor=white" alt="Python 3.6+">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/dependencies-zero-brightgreen" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

---

```
spines/
├── Dune.png                        ← matched by title
├── Frank Herbert - Dune.png        ← author + title
├── The_Hobbit.png                  ← underscores are fine
├── Ender's Game.jpg                ← punctuation is fine
└── li_8f7bd2c8.png                 ← book ID works too
```

The server connects to your ABS instance, learns your books, and auto-matches filenames to the right book. No manual ID lookups. Just name the image something reasonable and it figures it out.

**New:** Built-in web UI for setup and management — no terminal required after first launch.

---

## Quick Start

### Option A: Double-click and go (recommended for beginners)

1. [Download](https://github.com/philipvox/abs-spine-server/archive/refs/heads/main.zip) and unzip
2. Put spine images in the `spines/` folder (name them by book title)
3. Run `python3 spine_server.py` — your browser opens automatically
4. Enter your ABS URL and API key in the setup page, click **Save & Start**
5. Copy the app URL shown on the dashboard into your phone app

### Option B: Environment variables (Docker / advanced)

<table>
<tr><th>Docker</th><th>Python</th></tr>
<tr><td>

```bash
docker compose up -d
```

</td><td>

```bash
ABS_URL=http://your-abs:13378 \
ABS_API_KEY=your-key \
python3 spine_server.py --no-open
```

</td></tr>
</table>

### Option C: Docker Desktop Extension

See [`docker-extension/README.md`](docker-extension/README.md) for one-click install via Docker Desktop.

---

## Table of Contents

- [Web UI](#web-ui)
- [How It Works](#how-it-works)
- [Getting an API Key](#getting-an-api-key)
- [File Naming](#file-naming)
- [Linked Folders](#linked-folders)
- [Configuration](#configuration)
- [Docker](#docker)
- [Docker Desktop Extension](#docker-desktop-extension)
- [API Endpoints](#api-endpoints)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)

---

## Web UI

The server includes a built-in web dashboard at `http://localhost:8786`. No extra software needed.

**First launch** — opens a setup wizard:
- Enter your ABS URL and API key
- Test the connection
- Click Save & Start

**After setup** — shows a management dashboard:
- **App URL** with one-click copy — paste into your phone app
- **Live stats** — books indexed, spines matched, unmatched files
- **Linked folders** — point to any folder on your computer containing spine images
- **Match list** — see exactly which file mapped to which book
- **Settings** — update ABS connection at any time

The dashboard auto-refreshes every 5 seconds. Drop new images into any linked folder and watch them appear.

---

## How It Works

```
                          On startup
                              │
                              ▼
                    ┌───────────────────┐       ┌──────────────┐
                    │   Spine Server    │──────▶│  ABS Server  │
                    │                   │ fetch │              │
                    │  1. Gets book list│ books │  "Here are   │
                    │  2. Scans folders │◀──────│   247 books" │
                    │  3. Matches names │       └──────────────┘
                    │     to books      │
                    │  4. Serves images │
                    └─────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        /api/spines/    /api/items/     / (Web UI)
        manifest        {id}/spine
        (JSON list)     (image file)    (dashboard)
```

1. **Startup** — connects to ABS, fetches every book's title, author, and ID. Builds a matching index.
2. **Matching** — scans all spine folders and fuzzy-matches each filename to a book. `Dune.png` → finds the book titled "Dune" → maps it to `li_8f7bd2c8`.
3. **Serving** — the app asks for spines by book ID. The server looks up the matched file and sends it back.
4. **Live reload** — re-scans folders every 30 seconds. Drop in new images anytime.

---

## Getting an API Key

The server needs to read your book list from ABS. It uses an API key — a read-only token that never modifies anything.

1. Open ABS in your browser
2. **Settings** (gear icon) → **Users** → click your username
3. Scroll to **API Tokens** → click **Create**
4. Copy the token

You can enter it in the web UI setup page, or pass it via the `ABS_API_KEY` environment variable.

> Without an API key, the server still runs — but files must be named by book ID (`li_abc123.png`) since it can't look up titles.

---

## File Naming

Name your images however is natural. The server normalizes both filenames and book titles, then matches them.

### What works

| You name the file | It matches the book |
|---|---|
| `Dune.png` | Dune |
| `dune.png` | Dune |
| `DUNE.PNG` | Dune |
| `The_Hobbit.jpg` | The Hobbit |
| `Hobbit.png` | The Hobbit (strips "The") |
| `Ender's Game.png` | Ender's Game |
| `Enders Game.png` | Ender's Game (punctuation ignored) |
| `Frank Herbert - Dune.png` | Dune by Frank Herbert |
| `Dune.png` | Dune: Part One (subtitle ignored) |
| `Gatsby.webp` | The Great Gatsby (contained match) |
| `li_8f7bd2c8.png` | Direct ID lookup (always works) |

### Matching priority

The server tries these strategies in order and uses the first match:

1. **Book ID** — filename starts with `li_`
2. **Exact title** — normalized filename equals normalized title
3. **Author-title split** — splits on ` - `, tries both orderings
4. **Part match** — each part of an author-title split checked individually
5. **Without subtitle** — title before the first `:` or ` - `
6. **Without article** — strips leading The/A/An
7. **Containment** — filename is a substring of a title or vice versa (min 5 chars)

### Ambiguous titles

If two books normalize to the same title (e.g., two editions of "Dune"), that key is marked ambiguous and skipped — neither book matches by title. The web UI shows which titles are ambiguous. Use the book ID for those.

### Finding your books

```bash
python3 spine_server.py --list-books
# or
docker compose run --rm spine-server --list-books
```

---

## Linked Folders

Instead of copying images into `spines/`, you can point the server at any folder on your computer.

**Via the web UI:**
1. Open the dashboard (`http://localhost:8786`)
2. Under **Spine Folders**, paste a folder path and click **Link Folder**
3. The server immediately scans it and matches any images it finds

**Via config.json:**
```json
{
  "linked_folders": [
    "/Users/me/Art/book-spines",
    "/mnt/nas/spines"
  ]
}
```

You can link as many folders as you want. The server scans all of them plus the default `spines/` folder. If the same book is matched in multiple folders, the first match wins (default folder has priority).

---

## Configuration

Configuration is loaded in this priority order (higher overrides lower):

1. **CLI arguments** (`--port`, `--spines-dir`)
2. **Environment variables** (`ABS_URL`, `ABS_API_KEY`, etc.)
3. **config.json** (created by the web UI, or by hand)
4. **Defaults**

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ABS_URL` | *(empty)* | Your ABS server address |
| `ABS_API_KEY` | *(empty)* | ABS API token ([how to get one](#getting-an-api-key)) |
| `SPINES_DIR` | `./spines` | Default folder for spine images |
| `LIBRARY_PATH` | *(empty)* | Path to audiobook files (only for `--scan-library`) |
| `PORT` | `8786` | HTTP port |

### CLI

```
python3 spine_server.py [OPTIONS]

  (no args)        Start the server and open the web UI
  --list-books     Print all books with IDs and titles
  --scan-library   Import spine.png files from audiobook folders into spines/
  --port N         Listen on port N (default: 8786)
  --spines-dir D   Use D as the spines folder
  --no-open        Don't open the browser on startup
```

### config.json

Created automatically by the web UI when you save settings. You can also create it by hand:

```json
{
  "abs_url": "http://192.168.1.50:13378",
  "abs_api_key": "your-key-here",
  "port": 8786,
  "linked_folders": []
}
```

> **Note:** `config.json` is gitignored since it contains your API key.

### Importing existing spines

If your audiobook folders already contain `spine.png` or `spine.jpg` files:

```bash
LIBRARY_PATH=/path/to/audiobooks \
ABS_URL=http://your-abs:13378 \
ABS_API_KEY=your-key \
python3 spine_server.py --scan-library
```

This walks every book folder, finds spine images, and copies them into `spines/` named by book ID.

---

## Docker

### Basic

```yaml
services:
  spine-server:
    build: .
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: your-key-here
```

### Same network as ABS

If ABS runs in Docker too, they need a shared network:

```yaml
services:
  spine-server:
    build: .
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: your-key-here
    networks:
      - audiobookshelf_default

networks:
  audiobookshelf_default:
    external: true
```

Find your ABS network name with `docker network ls`.

### With .env file

Keep your API key out of version control:

```bash
# .env (add to .gitignore)
ABS_URL=http://audiobookshelf:13378
ABS_API_KEY=your-key-here
```

```yaml
services:
  spine-server:
    build: .
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
    env_file: .env
```

### Health check

The Docker image includes a built-in health check (`/health` endpoint, every 30s). Check status with:

```bash
docker inspect --format='{{.State.Health.Status}}' abs-spine-server
```

---

## Docker Desktop Extension

For Docker Desktop users, the spine server is available as a one-click extension.

### Install

```bash
# Build from source
docker build -t abs-spine-server-extension ./docker-extension
docker extension install abs-spine-server-extension
```

### Usage

1. Open Docker Desktop
2. Click **ABS Spine Server** in the left sidebar
3. Click **Open Spine Server UI** — the web setup wizard opens
4. Enter your ABS URL and API key, link your spine folders, done

See [`docker-extension/README.md`](docker-extension/README.md) for full details.

---

## API Endpoints

Three public endpoints (no authentication required), plus the web UI.

### `GET /`

The web UI dashboard. Setup wizard on first visit, management dashboard after.

### `GET /api/spines/manifest`

Returns which books have spine images.

```json
{
  "items": ["li_abc123", "li_def456"],
  "version": 1,
  "count": 2,
  "generated": "2026-03-23T14:30:00.000000"
}
```

### `GET /api/items/{bookId}/spine`

Returns the spine image for a book. Responds with the image file and appropriate `Content-Type`.

- **Cache:** `Cache-Control: public, max-age=604800` (1 week)
- **CORS:** `Access-Control-Allow-Origin: *`
- **404** if no spine exists for that book

### `GET /health`

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

<details>
<summary><strong>"Cannot reach ABS" on startup</strong></summary>

The server can't connect to your ABS instance.

- Verify `ABS_URL` — open it in a browser, does ABS load?
- **Docker → Docker:** both containers must share a Docker network. Check with `docker network ls`.
- **Docker → host ABS:** use `http://host.docker.internal:13378` as the URL.
- **Wrong port:** ABS defaults to `13378` unless you changed it.

The server still starts without ABS — it just can't match by title. Files must be named by book ID.
</details>

<details>
<summary><strong>"Could not match N files"</strong></summary>

The server found images it can't match to any book.

- **Typo?** Run `--list-books` and compare exact titles.
- **Different title in ABS?** ABS might store "The Lord of the Rings: The Fellowship of the Ring" while you named the file `Fellowship.png`. Try the full title or add more of it.
- **Ambiguous?** Two books with the same title. The web UI shows these. Use the book ID.
</details>

<details>
<summary><strong>No spines showing in the app</strong></summary>

1. **Is the server reachable?** Open `http://YOUR_IP:8786/health` in your phone's browser. You should see JSON.
   - **No?** Same WiFi? Firewall blocking port 8786? Docker port exposed?
2. **Does the manifest have books?** Check `http://YOUR_IP:8786/api/spines/manifest` — `count` should be > 0.
3. **App configured?** Settings → Display → Server Spines is **ON** and the URL is exactly `http://YOUR_IP:8786` (with `http://`, no trailing slash).
4. **Refresh the app** — pull down on the library screen to trigger a reload.
</details>

<details>
<summary><strong>App shows generated spines instead of my images</strong></summary>

The app falls back to procedurally generated spines when it can't load server spines.

1. Check the manifest endpoint — is your book listed?
2. Try loading a spine directly: `http://YOUR_IP:8786/api/items/BOOK_ID/spine` — does the image load?
3. New images take up to 30 seconds to appear (the server rescans periodically).
</details>

<details>
<summary><strong>How do I update a spine image?</strong></summary>

Replace the file in your spines folder. The server picks it up within 30 seconds. Pull down to refresh in the app.
</details>

<details>
<summary><strong>Can I run this on a different machine than ABS?</strong></summary>

Yes. Set `ABS_URL` to your ABS server's address (public URL or private IP). The spine server just needs HTTP access to the ABS API on startup. It can run anywhere — same machine, different machine, a VPS, a Raspberry Pi.
</details>

---

## Architecture

Single Python file. Zero dependencies. Built-in web UI.

```
spine_server.py
│
├─ Startup
│  ├─ Load config.json (or env vars)
│  ├─ GET /api/libraries          → list of libraries
│  ├─ GET /api/libraries/{id}/items → all books per library
│  ├─ Build title index            → normalized keys → book IDs
│  └─ Open browser to web UI
│
├─ Every 30 seconds
│  ├─ Scan all spine folders (default + linked)
│  ├─ Match each filename → title index → book ID
│  └─ Rebuild manifest
│
└─ HTTP Server (stdlib ThreadingHTTPServer)
   ├─ /                        → web UI (setup wizard / dashboard)
   ├─ /api/spines/manifest     → JSON manifest
   ├─ /api/items/{id}/spine    → image file from disk
   ├─ /api/ui/status           → dashboard data (JSON)
   ├─ /api/ui/action           → config changes (POST)
   └─ /health                  → status JSON
```

### Why separate from ABS?

ABS has no spine feature. Modifying ABS would break on updates. This runs alongside it as a stateless sidecar — ABS handles audio and metadata, this handles one thing: spine images.

### URL compatibility

The endpoints mirror what the mobile app expects (`/api/items/{id}/spine` and `/api/spines/manifest`), so the app just swaps the base URL and everything works.

### Title matching internals

For each book, the index stores multiple normalized keys:

```
"The Hobbit" by J.R.R. Tolkien
  → the hobbit
  → jrr tolkien the hobbit
  → the hobbit jrr tolkien
  → hobbit                        (without article)

"Dune: Part One" by Frank Herbert
  → dune part one
  → frank herbert dune part one
  → dune part one frank herbert
  → dune                          (without subtitle)
```

Filenames are normalized the same way. Exact match is tried first, then progressively fuzzier strategies. Ambiguous keys (matching 2+ books) are excluded.

---

## License

[MIT](LICENSE)
