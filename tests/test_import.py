"""Tests for --import-community: matching order and the no-overwrite rule.

Run:  python3 -m unittest discover -s tests -v
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import spine_server as ss  # noqa: E402

BASE = "https://community.test"

BOOKS = [
    {"id": "uuid-asin", "title": "Some Book", "author": "Ann Author", "asin": "b00asin1", "isbn": ""},
    {"id": "uuid-isbn", "title": "Isbn Book", "author": "Ian Isbn", "asin": "", "isbn": "978-1-23456-789-0"},
    {"id": "uuid-hash", "title": "Éclair: The Story!", "author": "Zoë Ödegaard", "asin": "", "isbn": ""},
    {"id": "uuid-title", "title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin", "asin": "", "isbn": ""},
    {"id": "uuid-local", "title": "Already Spined", "author": "Local Person", "asin": "BLOCAL", "isbn": ""},
    {"id": "uuid-none", "title": "Nothing Matches", "author": "Nobody", "asin": "", "isbn": ""},
]

MANIFEST = [
    # ASIN must win even though the hash points at uuid-hash.
    {"sid": "s-asin", "title": "Éclair: The Story!", "author": "Zoë Ödegaard", "asin": "B00ASIN1",
     "hash": ss.community_hash("Éclair: The Story!", "Zoë Ödegaard")},
    {"sid": "s-isbn", "title": "Different Title", "author": "X", "isbn": "9781234567890",
     "hash": ss.community_hash("Different Title", "X")},
    # Hash-only match (accents/punctuation normalised away).
    {"sid": "s-hash", "title": "Eclair The Story", "author": "Zoe Odegaard",
     "hash": ss.community_hash("Eclair The Story", "Zoe Odegaard")},
    # Fuzzy title only: author spelled differently so the hash misses.
    {"sid": "s-title", "title": "The Left Hand of Darkness", "author": "Ursula Le Guin",
     "hash": ss.community_hash("The Left Hand of Darkness", "Ursula Le Guin")},
    {"sid": "s-local", "title": "Already Spined", "author": "Local Person", "asin": "BLOCAL",
     "hash": ss.community_hash("Already Spined", "Local Person")},
    {"sid": "s-stranger", "title": "Not In Library", "author": "Someone",
     "hash": ss.community_hash("Not In Library", "Someone")},
]


class FakeHTTP:
    def __init__(self, manifest=MANIFEST, v3=True):
        self.manifest, self.v3, self.calls = manifest, v3, []

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        if url == BASE + "/api/spines/manifest/v3":
            if not self.v3:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return json.dumps({"version": 3, "spines": self.manifest}).encode(), "application/json"
        if url == BASE + "/api/spines/manifest/v2":
            return b'{"version": 2, "books": []}', "application/json"
        if url.startswith(BASE + "/api/spine/"):
            return b"RIFFxxxxWEBP" + url.rsplit("/", 1)[1].encode(), "image/webp"
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.spines = os.path.join(self.tmp, "spines")
        os.makedirs(self.spines)
        ss._config = {"spines_dir": self.spines, "linked_folders": []}
        ss._title_index, ss._books_by_id = {}, {}
        # A spine the user already has, named by title.
        with open(os.path.join(self.spines, "Already Spined.png"), "wb") as f:
            f.write(b"LOCAL")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_import(self, **kw):
        http = kw.pop("http", FakeHTTP())
        r = ss.import_community_spines(BASE, books=BOOKS, http_get=http, log=lambda *_: None, **kw)
        return r, http

    def community(self, name):
        return os.path.join(self.spines, "community", name)

    def test_hash_matches_community_server(self):
        # Reference values produced by spine_community.manifest_title_author_hash.
        self.assertEqual(ss.community_hash("Éclair: The Story!", "Zoë Ödegaard"), "a317176ecd7ff142")
        self.assertEqual(ss.community_hash("Dune", "Frank Herbert"), "af9ebc5bf554c551")

    def test_matching_order(self):
        matches, unmatched = ss.match_community_spines(MANIFEST, BOOKS)
        got = {bid: (e["sid"], t) for bid, (e, t) in matches.items()}
        self.assertEqual(got["uuid-asin"], ("s-asin", "asin"))
        self.assertEqual(got["uuid-isbn"], ("s-isbn", "isbn"))
        self.assertEqual(got["uuid-hash"], ("s-hash", "hash"))
        self.assertEqual(got["uuid-title"], ("s-title", "title"))
        self.assertNotIn("uuid-none", got)
        self.assertEqual([e["sid"] for e in unmatched], ["s-stranger"])

    def test_stronger_match_wins_for_same_book(self):
        entries = [
            {"sid": "weak", "title": "Some Book", "author": "Ann Author",
             "hash": ss.community_hash("Some Book", "Ann Author")},
            {"sid": "strong", "title": "zzz", "author": "zzz", "asin": "B00ASIN1", "hash": "0" * 16},
        ]
        matches, _ = ss.match_community_spines(entries, BOOKS)
        self.assertEqual(matches["uuid-asin"][0]["sid"], "strong")

    def test_downloads_only_missing_and_never_overwrites(self):
        os.makedirs(self.community(""))
        with open(self.community("uuid-isbn.webp"), "wb") as f:
            f.write(b"PREVIOUS")

        r, http = self.run_import()
        self.assertTrue(r["ok"])
        self.assertEqual(r["downloaded"], 3)          # asin, hash, title
        self.assertEqual(r["skipped_existing"], 2)    # local title spine + existing community file
        self.assertEqual(r["unmatched"], 1)
        with open(self.community("uuid-isbn.webp"), "rb") as f:
            self.assertEqual(f.read(), b"PREVIOUS")
        with open(os.path.join(self.spines, "Already Spined.png"), "rb") as f:
            self.assertEqual(f.read(), b"LOCAL")
        self.assertFalse(os.path.exists(self.community("uuid-local.webp")))
        self.assertFalse(any(u.endswith("/s-local") for u in http.calls))
        self.assertTrue(os.path.exists(self.community("uuid-asin.webp")))

        # Second run downloads nothing.
        r2, _ = self.run_import()
        self.assertEqual(r2["downloaded"], 0)

    def test_dry_run_writes_nothing(self):
        r, http = self.run_import(dry_run=True)
        self.assertEqual(r["would_download"], 4)
        self.assertFalse(os.path.exists(self.community("")))
        self.assertFalse(any("/api/spine/" in u for u in http.calls))

    def test_no_v3_skips_without_falling_back_to_v2(self):
        r, http = self.run_import(http=FakeHTTP(v3=False))
        self.assertFalse(r["ok"])
        self.assertIn("v2", r["message"])
        self.assertFalse(any("/api/spine/" in u for u in http.calls))

    def test_scanner_picks_up_community_files_but_local_wins(self):
        self.run_import()
        # Later the user adds their own spine for a book that got a community one.
        with open(os.path.join(self.spines, "Some Book.png"), "wb") as f:
            f.write(b"MINE")
        spines, unmatched = ss.find_spine_files()
        self.assertEqual(spines["uuid-hash"], self.community("uuid-hash.webp"))
        self.assertEqual(spines["uuid-title"], self.community("uuid-title.webp"))
        self.assertEqual(spines["uuid-asin"], os.path.join(self.spines, "Some Book.png"))
        self.assertEqual(unmatched, [])


if __name__ == "__main__":
    unittest.main()
