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
# set DASHBOARD_TOKEN in docker-compose.yml
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
- [Security](#security)
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

> **Remote access / Docker:** by default the dashboard only works from the computer the server runs on (`http://localhost:8786`). To use it from another device — or at all when running in Docker — set `DASHBOARD_TOKEN`. See [Security](#security).

---

## Security

The server has two kinds of endpoints:

| | Endpoints | Access |
|---|---|---|
| **Public, read-only** | `/api/spines/manifest`, `/api/items/{id}/spine`, `/health` | Open to anyone who can reach the port (your phone needs them). The manifest and images send `Access-Control-Allow-Origin: *`. |
| **Dashboard** | `/`, `/api/ui/*` | Locked down — it can change which ABS server the API key is sent to. No CORS. |

How the dashboard is protected:

- **`DASHBOARD_TOKEN` not set (default):** the dashboard only answers requests from this computer (`127.0.0.1` / `::1`) addressed to `localhost` or `127.0.0.1`. Other devices on your network get a "Dashboard locked" page. The server prints a note about this on startup.
- **`DASHBOARD_TOKEN` set:** the dashboard works from anywhere that can reach the port, after signing in with the token at `/login` (sets an `HttpOnly`, `SameSite=Strict` cookie; scripts can send `Authorization: Bearer <token>`). Use a long random value, e.g. `openssl rand -hex 24`. It can also be set as `"dashboard_token"` in `config.json`; the env var wins. The cookie is only as private as the connection — over plain HTTP use it on a network you trust, or put the server behind HTTPS.
- **Always:** dashboard actions must be `application/json` POSTs from the same origin, so other websites you visit can't drive the dashboard in the background.
- The API key is never sent back to the browser (not even a masked suffix).
- Changing the ABS URL requires re-entering the API key in the same request, so the stored key is never sent to a newly entered address. **Test** with an empty key field tests the saved URL with the saved key.
- Linked folders must be existing absolute directories; system folders (`/etc`, `/usr`, `/System`, `C:\Windows`, …) and your whole home folder are refused.
- `config.json` is written with mode `600`.

**Docker:** requests reach the container from the Docker bridge network, not from localhost, so the dashboard is **locked unless you set `DASHBOARD_TOKEN`**. Alternatively, skip the dashboard and configure everything with environment variables.

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

The server needs to read your book list from ABS, which it does with an ABS API key. The spine server itself only ever makes `GET` requests to ABS — but **an ABS API key carries all the permissions of the user it belongs to**. A key created for an admin account is an admin key, and anyone who obtains it can do whatever that admin can.

**Recommended:** create a dedicated, low-privilege ABS user for the spine server:

1. In ABS: **Settings** → **Users** → **Add User**, e.g. `spine-server`, account type **User** (not Admin)
2. Give it access to the libraries you want spines for, and untick download/update/delete/upload permissions — it only needs to read the library
3. Log in as (or edit) that user → **API Tokens** → **Create**
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

You can link as many folders as you want. The server scans the default `spines/` folder first, then linked folders in the order you added them, then `spines/community/`. When more than one file matches the same book:

- a file **named by book ID** (`li_abc123.png` / the ABS item UUID) always wins over a title-matched file, whichever folder it is in;
- otherwise the **first folder scanned** wins — so the default `spines/` folder has priority over linked folders;
- files in `spines/community/` (from the community import) never replace a spine from any other folder.

Linked folders must be existing absolute paths; system folders and your whole home folder are refused.

---

## Configuration

Where each setting comes from (highest priority first):

| Setting | Priority |
|---|---|
| ABS URL, API key (editable in the web UI) | **config.json** → `ABS_URL` / `ABS_API_KEY` env vars → empty |
| Port, spines folder | CLI (`--port`, `--spines-dir`) → env (`PORT`, `SPINES_DIR`) → config.json → defaults |
| Library path | `LIBRARY_PATH` env → config.json |
| Dashboard token | `DASHBOARD_TOKEN` env → `dashboard_token` in config.json |

In other words, `ABS_URL` / `ABS_API_KEY` are **defaults**: once you save a URL or key in the web UI it is stored in `config.json` and wins over the env vars on every restart. To go back to the env values, delete those fields from `config.json`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ABS_URL` | *(empty)* | Your ABS server address |
| `ABS_API_KEY` | *(empty)* | ABS API token ([how to get one](#getting-an-api-key)) |
| `SPINES_DIR` | `./spines` | Default folder for spine images |
| `LIBRARY_PATH` | *(empty)* | Path to audiobook files (only for `--scan-library`) |
| `PORT` | `8786` | HTTP port |
| `DASHBOARD_TOKEN` | *(empty)* | Password for the web UI. Required to use the dashboard from another device or in Docker ([Security](#security)) |
| `CONFIG_FILE` | `./config.json` (Docker: `/data/config.json`) | Where settings saved from the web UI are stored |

### CLI

```
python3 spine_server.py [OPTIONS]

  (no args)        Start the server and open the web UI
  --list-books     Print all books with IDs and titles
  --scan-library   Import spine.png files from audiobook folders into spines/
  --port N         Listen on port N (default: 8786)
  --spines-dir D   Use D as the spines folder
  --no-open        Don't open the browser on startup
  --import-community        Download curated community spines (see below)
  --community-url URL       Community server (default: https://spines.mysecretlibrary.com)
  --dry-run                 With --import-community: list what would be downloaded
```

### config.json

Created automatically by the web UI when you save settings (next to `spine_server.py`, or at `$CONFIG_FILE` — `/data/config.json` in Docker). You can also create it by hand:

```json
{
  "abs_url": "http://192.168.1.50:13378",
  "abs_api_key": "your-key-here",
  "port": 8786,
  "linked_folders": [],
  "dashboard_token": "optional-long-random-string"
}
```

> **Note:** `config.json` contains your API key. It is gitignored and written with mode `600`.

### Importing existing spines

If your audiobook folders already contain `spine.png` or `spine.jpg` files:

```bash
LIBRARY_PATH=/path/to/audiobooks \
ABS_URL=http://your-abs:13378 \
ABS_API_KEY=your-key \
python3 spine_server.py --scan-library
```

This walks every book folder, finds spine images, and copies them into `spines/` named by book ID.

### Importing curated community spines

Fill in spines for books you don't have one for yet, using the curated
[Secret Spines](https://spines.mysecretlibrary.com) collection:

```bash
python3 spine_server.py --import-community --dry-run   # preview
python3 spine_server.py --import-community             # download
```

Or click **Import curated community spines** in the web UI.

- Uses the server's curated-only manifest (`/api/spines/manifest/v3`). Community
  uploads are not included. If the server only offers the older v2 manifest, the
  import stops and downloads nothing.
- Matches your ABS books by **ASIN**, then **ISBN**, then a normalized
  title+author hash, then a strict title fallback: the normalized title must
  be identical **and** the author's last name must match (no partial/contained
  titles, so "Silence" is never matched to "Silence in Hanover Close").
- The remote manifest is treated as untrusted: malformed entries are skipped
  (and counted in the summary), downloads are capped at 10 MB and must be real
  PNG/JPEG/WebP images, and files are only ever named after ABS item ids.
- Downloads only for books that have **no spine yet** (in any folder) into
  `spines/community/<ABS item id>.webp`. Existing files are never overwritten, and
  a spine you add yourself later always takes precedence over an imported one.
- Safe to re-run: it only fetches what's still missing.

You can also grab everything at once as a zip from
`https://spines.mysecretlibrary.com/downloads/spines-curated.zip`.

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
      - ./spines:/spines   # spine images
      - ./data:/data       # config.json saved by the web UI — keep this!
    environment:
      DASHBOARD_TOKEN: change-me-to-a-long-random-string   # needed for the web UI
      ABS_URL: http://audiobookshelf:13378                 # default, UI can override
      ABS_API_KEY: your-key-here                           # optional, or enter in the UI
```

- **`/data` volume:** settings saved in the web UI go to `/data/config.json`. Without the volume they are lost whenever the container is recreated (e.g. on image updates). If you are upgrading from a version that stored settings in `/app/config.json`, that file is still read until the first save — but it only survives if the container was never recreated, so re-enter your settings once after adding the volume.
- **`DASHBOARD_TOKEN`:** inside Docker the dashboard sees requests from the bridge network, not localhost, so it stays locked without a token. Open `http://YOUR_IP:8786`, sign in with the token, and finish setup. If you configure everything via env vars you can leave it unset — spine serving works either way.

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
      - ./data:/data
    environment:
      DASHBOARD_TOKEN: change-me-to-a-long-random-string
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

Keep your API key and token out of version control:

```bash
# .env (add to .gitignore)
ABS_URL=http://audiobookshelf:13378
ABS_API_KEY=your-key-here
DASHBOARD_TOKEN=change-me-to-a-long-random-string
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
      - ./data:/data
    env_file: .env
```

### Health check

The Docker image includes a built-in health check (`/health` endpoint on `$PORT`, every 30s). Check status with:

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

The web UI dashboard. Setup wizard on first visit, management dashboard after. Not public: localhost-only, or behind `DASHBOARD_TOKEN` (see [Security](#security)). `/api/ui/*` follows the same rules.

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
- **CORS:** `Access-Control-Allow-Origin: *` (also on the manifest)
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
│  ├─ GET /api/libraries/{id}/items → all books per library (500 per page)
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
   ├─ /login, /logout          → dashboard sign-in (when DASHBOARD_TOKEN is set)
   ├─ /api/ui/status           → dashboard data (JSON, protected)
   ├─ /api/ui/action           → config changes (JSON POST, protected, same-origin)
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
