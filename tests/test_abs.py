"""ABS client: pagination, error surfacing, null metadata, dashboard Test button."""
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import spine_server as ss  # noqa: E402


def make_item(n):
    return {"id": f"li_{n}", "path": f"/a/{n}",
            "media": {"metadata": {"title": f"Book {n}", "authorName": "Ann Author"}}}


class FakeABS:
    def __init__(self, n_items=1200, fail_page=None):
        self.n, self.fail_page, self.calls = n_items, fail_page, []

    def __call__(self, endpoint, abs_url=None, api_key=None, **kw):
        self.calls.append(endpoint)
        if endpoint == "/api/libraries":
            return {"libraries": [{"id": "lib1", "name": "Books"}]}
        u = urllib.parse.urlsplit(endpoint)
        q = dict(urllib.parse.parse_qsl(u.query))
        limit, page = int(q["limit"]), int(q.get("page", 0))
        if page == self.fail_page:
            raise ss.ABSError("HTTP 500")
        start = page * limit
        res = [make_item(i) for i in range(start, min(start + limit, self.n))]
        return {"results": res, "total": self.n, "limit": limit, "page": page}


class ABSClientTests(unittest.TestCase):
    def setUp(self):
        self._orig = ss.abs_api_request
        ss._config = {"abs_url": "http://abs.test", "abs_api_key": "k",
                      "spines_dir": "/nonexistent-spines-test", "linked_folders": []}

    def tearDown(self):
        ss.abs_api_request = self._orig

    def test_paginates(self):
        fake = FakeABS(1200)
        ss.abs_api_request = fake
        books = ss.get_all_books()
        self.assertEqual(len(books), 1200)
        self.assertEqual(len({b["id"] for b in books}), 1200)
        item_calls = [c for c in fake.calls if "/items" in c]
        self.assertEqual(len(item_calls), 3)
        self.assertTrue(all("limit=500" in c for c in item_calls))
        self.assertNotIn("100000", " ".join(fake.calls))

    def test_error_is_raised_not_empty(self):
        ss.abs_api_request = FakeABS(1200, fail_page=1)
        with self.assertRaises(ss.ABSError):
            ss.get_all_books()

    def test_index_error_keeps_previous_index_and_is_reported(self):
        ss.abs_api_request = FakeABS(3)
        ss.load_book_index()
        self.assertEqual(len(ss._books_by_id), 3)
        self.assertEqual(ss._index_error, "")
        ss.abs_api_request = FakeABS(3, fail_page=0)
        ss.load_book_index()
        self.assertEqual(len(ss._books_by_id), 3)        # not wiped
        self.assertIn("HTTP 500", ss._index_error)

    def test_null_metadata_coerced(self):
        def fake(endpoint, *a, **kw):
            if endpoint == "/api/libraries":
                return {"libraries": [{"id": "lib1"}]}
            return {"results": [
                {"id": "li_a", "media": {"metadata": {"title": None, "authorName": None,
                                                      "asin": None}}},
                {"id": "li_b", "media": None},
                {"id": "li_c", "media": {"metadata": {"title": 42, "authorName": "X"}}},
                {"media": {"metadata": {"title": "no id"}}},
            ], "total": 4}
        ss.abs_api_request = fake
        books = ss.get_all_books()
        self.assertEqual([b["id"] for b in books], ["li_a", "li_b", "li_c"])
        for b in books:
            self.assertIsInstance(b["title"], str)
            self.assertIsInstance(b["author"], str)
        ss.build_title_index(books)                       # must not crash
        ss.match_community_spines([{"sid": "s", "title": "Unknown", "author": "Unknown"}], books)
        self.assertEqual(ss.normalize(None), "")

    def test_test_connection_reports_error(self):
        def boom(*a, **kw):
            raise ss.ABSError("Could not reach ABS: connection refused")
        ss.abs_api_request = boom
        r = ss.test_connection("http://abs.test", "k")
        self.assertFalse(r["ok"])
        self.assertIn("connection refused", r["message"])

    def test_test_connection_counts_via_total(self):
        fake = FakeABS(1200)
        ss.abs_api_request = fake
        r = ss.test_connection("http://abs.test", "k")
        self.assertTrue(r["ok"])
        self.assertEqual(r["books_count"], 1200)

    def test_dashboard_test_button_sends_blank_key_not_raw(self):
        html = ss.get_ui_html()
        self.assertNotIn("abs_api_key_raw", html)


if __name__ == "__main__":
    unittest.main()
