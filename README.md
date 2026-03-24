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

The server connects to your ABS instance, learns your books, and auto-matches filenames to the right book. No manual ID lookups. No config files. Just name the image something reasonable and it figures it out.

---

## 3-Step Quick Start

**1. Clone and add images**
```bash
git clone https://github.com/philipvox/abs-spine-server.git
cd abs-spine-server
mkdir spines
# Drop your spine images into spines/ — name them by book title
```

**2. Start the server**

<table>
<tr><th>Docker</th><th>Python</th></tr>
<tr><td>

```bash
# Set your ABS_API_KEY in docker-compose.yml, then:
docker compose up -d
```

</td><td>

```bash
ABS_URL=http://your-abs:13378 \
ABS_API_KEY=your-key \
python3 spine_server.py
```

</td></tr>
</table>

**3. Tell the app**

Settings → Display → Server Spines → **ON** → enter `http://YOUR_IP:8786`

That's it. No `pip install`, no config files, no requirements.txt.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Getting an API Key](#getting-an-api-key)
- [File Naming](#file-naming)
- [Server Output](#server-output)
- [Creating Spine Images](#creating-spine-images)
- [Configuration](#configuration)
- [Docker](#docker)
- [API Endpoints](#api-endpoints)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)

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
                    │  2. Scans spines/ │◀──────│   247 books" │
                    │  3. Matches names │       └──────────────┘
                    │     to books      │
                    │  4. Serves images │
                    └─────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        /api/spines/    /api/items/     /health
        manifest        {id}/spine
        (JSON list)     (image file)    (status)
```

1. **Startup** — connects to ABS, fetches every book's title, author, and ID. Builds a matching index.
2. **Matching** — scans `spines/` folder and fuzzy-matches each filename to a book. `Dune.png` → finds the book titled "Dune" → maps it to `li_8f7bd2c8`.
3. **Serving** — the app asks for spines by book ID. The server looks up the matched file and sends it back.
4. **Live reload** — re-scans the folder every 30 seconds. Drop in new images anytime.

---

## Getting an API Key

The server needs to read your book list from ABS. It uses an API key — a read-only token that never modifies anything.

1. Open ABS in your browser
2. **Settings** (gear icon) → **Users** → click your username
3. Scroll to **API Tokens** → click **Create**
4. Copy the token

Pass it to the server via the `ABS_API_KEY` environment variable.

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

### Normalization

Applied to both filenames and book titles before comparison:

- Lowercased
- `_`, `-`, `.` → space
- All punctuation removed (`'`, `:`, `,`, `!`, etc.)
- Unicode normalized (curly quotes → straight, `é` → `e`)
- Whitespace collapsed and trimmed

### Ambiguous titles

If two books normalize to the same title (e.g., two editions of "Dune"), that key is marked ambiguous and skipped — neither book matches by title. The server logs which titles are ambiguous. Use the book ID for those.

### Finding your books

```bash
python3 spine_server.py --list-books
# or
docker compose run --rm spine-server --list-books
```

Prints every book with its ID, title, and author.

---

## Server Output

On startup, the server prints a match report:

```
Connecting to ABS to build book index...
Indexed 247 books (493 matchable keys)

=== Spine Server ===

Matched 5 spine images:
  Dune.png                                 → Dune (Frank Herbert)
  The Hobbit.jpg                           → The Hobbit (J.R.R. Tolkien)
  Project Hail Mary.webp                   → Project Hail Mary (Andy Weir)
  Frank Herbert - Children of Dune.png     → Children of Dune (Frank Herbert)
  li_c9d0e1f2.png                          → li_c9d0e1f2

Could not match 1 files:
  vacation_photo.png
  Tip: use --list-books to see valid titles, or rename to a book ID

Spines folder: /spines
Server running at: http://0.0.0.0:8786

--- App Setup ---
In the app, go to:
  Settings > Display > Spine Server URL
Enter: http://YOUR_IP_ADDRESS:8786
```

Every matched file shows exactly which book it mapped to. Unmatched files are listed with a hint.

---

## Creating Spine Images

This server serves images — it doesn't create them. Here are some approaches.

### Recommended specs

| | Value |
|---|---|
| **Size** | 60–120 × 400–800 px (narrow and tall, like a real spine) |
| **Format** | PNG or WebP preferred. JPG works. |
| **Orientation** | Vertical — title reads top-to-bottom |

### Approaches

**By hand** — Photoshop, GIMP, Figma, Canva. Create a tall narrow canvas (80×600 works well), add the title vertically, pick colors from the book's cover.

**Batch with Python + Pillow** — Loop through your library and render each title programmatically:

```python
from PIL import Image, ImageDraw, ImageFont

def make_spine(title, width=80, height=600, bg="#2C1810", fg="#D4C5A9"):
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("Georgia.ttf", 14)
    # Draw title vertically rotated
    txt_img = Image.new("RGB", (height, 40), bg)
    txt_draw = ImageDraw.Draw(txt_img)
    txt_draw.text((20, 8), title.upper(), fill=fg, font=font)
    img.paste(txt_img.rotate(90, expand=True), (10, 0))
    return img
```

**AI image generation** — Prompt: *"book spine for [title], narrow vertical image, library aesthetic, dark leather texture"*. Crop to the right aspect ratio.

### Design tips

- **Contrast matters** — the app may render spines as narrow as 40px. If you can't read it at that size, simplify.
- **Match the cover** — pull 1–2 colors from the book's cover art for the spine background.
- **Consistent heights** — books look best on the shelf when spines are similar heights. Pick a standard (e.g., 600px) and stick to it.
- **Less is more** — title and maybe author name. No need for publisher logos or ISBNs.

---

## Configuration

All via environment variables. No config files.

| Variable | Default | Purpose |
|---|---|---|
| `ABS_URL` | `http://localhost:13378` | Your ABS server address |
| `ABS_API_KEY` | *(empty)* | ABS API token ([how to get one](#getting-an-api-key)) |
| `SPINES_DIR` | `./spines` | Folder with your spine images |
| `LIBRARY_PATH` | *(empty)* | Local path to audiobook files (only for `--scan-library`) |
| `PORT` | `8786` | HTTP port |

### CLI

```
python3 spine_server.py [OPTIONS]

  (no args)        Start the server
  --list-books     Print all books with IDs and titles
  --scan-library   Import spine.png files from audiobook folders into spines/
  --port N         Listen on port N (default: 8786)
  --spines-dir D   Use D as the spines folder
```

### Importing existing spines

If your audiobook folders already contain `spine.png` or `spine.jpg` files:

```bash
# Set LIBRARY_PATH to where your audiobook files live on this machine
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

### With library scanning

Mount your audiobook library to import existing spine files:

```yaml
services:
  spine-server:
    build: .
    restart: unless-stopped
    ports:
      - "8786:8786"
    volumes:
      - ./spines:/spines
      - /path/to/audiobooks:/audiobooks:ro   # read-only
    environment:
      ABS_URL: http://audiobookshelf:13378
      ABS_API_KEY: your-key-here
      LIBRARY_PATH: /audiobooks
```

Then run: `docker compose run --rm spine-server --scan-library`

### Health check

The Docker image includes a built-in health check (`/health` endpoint, every 30s). Check status with:

```bash
docker inspect --format='{{.State.Health.Status}}' abs-spine-server
```

---

## API Endpoints

Three endpoints, no authentication required.

### `GET /api/spines/manifest`

Returns which books have spine images. The app calls this once at startup so it only requests spines for books that have them.

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
- **Ambiguous?** Two books with the same title. The server logs these. Use the book ID.
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

Replace the file in `spines/`. The server picks it up within 30 seconds. Pull down to refresh in the app.
</details>

<details>
<summary><strong>Can I run this on a different machine than ABS?</strong></summary>

Yes. Set `ABS_URL` to your ABS server's address (public URL or private IP). The spine server just needs HTTP access to the ABS API on startup. It can run anywhere — same machine, different machine, a VPS, a Raspberry Pi.
</details>

---

## Architecture

Single Python file. Zero dependencies. ~500 lines.

```
spine_server.py
│
├─ Startup
│  ├─ GET /api/libraries          → list of libraries
│  ├─ GET /api/libraries/{id}/items → all books per library
│  └─ Build title index            → normalized keys → book IDs
│
├─ Every 30 seconds
│  ├─ Scan spines/ folder
│  ├─ Match each filename → title index → book ID
│  └─ Rebuild manifest
│
└─ HTTP Server (stdlib http.server)
   ├─ /api/spines/manifest   → JSON manifest
   ├─ /api/items/{id}/spine  → image file from disk
   └─ /health                → status JSON
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
