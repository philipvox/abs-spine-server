"""Tests for dashboard access control, CORS, and config-change safety.

Run:  python3 -m unittest discover -s tests -v

Every test uses a temporary CONFIG_FILE and spines dir — the real config.json
is never read or written.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import spine_server as ss  # noqa: E402

SECRET_KEY = "zzKEYzzSECRET9876"


class ServerTestBase(unittest.TestCase):
    client_ip = None   # override to simulate a non-loopback client
    token = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.spines = os.path.join(self.tmp, "spines")
        os.makedirs(self.spines)
        self._orig = {
            "CONFIG_FILE": ss.CONFIG_FILE,
            "LEGACY_CONFIG_FILE": ss.LEGACY_CONFIG_FILE,
            "reload_index": ss.reload_index,
            "abs_api_get": ss.abs_api_get,
            "test_connection": ss.test_connection,
        }
        ss.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        ss.LEGACY_CONFIG_FILE = os.path.join(self.tmp, "legacy-config.json")
        ss.reload_index = lambda: None          # never reach out to a real ABS
        self.abs_calls = []

        def fake_get(endpoint, abs_url=None, api_key=None, **kw):
            self.abs_calls.append((endpoint, abs_url, api_key))
            return None
        ss.abs_api_get = fake_get
        ss._config = {
            "abs_url": "http://abs.local:13378",
            "abs_api_key": SECRET_KEY,
            "port": 8786,
            "spines_dir": self.spines,
            "linked_folders": [],
            "dashboard_token": self.token,
        }
        os.environ.pop("DASHBOARD_TOKEN", None)
        ss._books_by_id = {"li_book1": {"id": "li_book1", "title": "Dune", "author": "Frank Herbert"}}
        ss._title_index = {}
        with open(os.path.join(self.spines, "li_book1.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
        ss.SpineHandler._last_scan = 0

        handler = ss.SpineHandler
        if self.client_ip:
            ip = self.client_ip

            class Remote(ss.SpineHandler):
                def client_ip(self):
                    return ip
            handler = Remote
        self.server = ss.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for k, v in self._orig.items():
            setattr(ss, k, v)
        shutil.rmtree(self.tmp)

    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def action(self, body, headers=None):
        r, data = self.req("POST", "/api/ui/action", body, headers)
        try:
            return r, json.loads(data)
        except ValueError:
            return r, {}


class LocalNoTokenTests(ServerTestBase):
    """Loopback client, no DASHBOARD_TOKEN: dashboard works, cross-site doesn't."""

    def test_status_never_contains_key(self):
        r, data = self.req("GET", "/api/ui/status")
        self.assertEqual(r.status, 200)
        text = data.decode()
        self.assertNotIn("9876", text)
        self.assertNotIn("zzKEY", text)
        self.assertNotIn("SECRET", text)
        self.assertTrue(json.loads(text)["abs_api_key_set"])

    def test_no_cors_on_dashboard(self):
        for path in ("/", "/api/ui/status"):
            r, _ = self.req("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertIsNone(r.getheader("Access-Control-Allow-Origin"), path)
        r, _ = self.req("OPTIONS", "/api/ui/action",
                        headers={"Origin": "http://evil.example",
                                 "Access-Control-Request-Method": "POST"})
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_cors_kept_on_public_spine_endpoints(self):
        for path in ("/api/items/li_book1/spine", "/api/spines/manifest"):
            r, _ = self.req("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*", path)

    def test_text_plain_post_rejected(self):
        body = json.dumps({"action": "save_config", "abs_url": "http://evil.example",
                           "abs_api_key": "x"})
        r, _ = self.req("POST", "/api/ui/action", body, {"Content-Type": "text/plain"})
        self.assertIn(r.status, (403, 415))
        self.assertEqual(ss._config["abs_url"], "http://abs.local:13378")

    def test_cross_origin_post_rejected(self):
        r, _ = self.action({"action": "rescan"}, {"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)

    def test_same_origin_post_allowed(self):
        r, d = self.action({"action": "rescan"},
                           {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)
        self.assertTrue(d["ok"])

    def test_dns_rebinding_host_rejected(self):
        r, _ = self.req("GET", "/api/ui/status", headers={"Host": "evil.example:8786"})
        self.assertEqual(r.status, 403)

    def test_changing_url_requires_key(self):
        r, d = self.action({"action": "save_config", "abs_url": "http://evil.example"})
        self.assertFalse(d["ok"])
        self.assertEqual(ss._config["abs_url"], "http://abs.local:13378")
        self.assertEqual(ss._config["abs_api_key"], SECRET_KEY)

        r, d = self.action({"action": "save_config", "abs_url": "http://new.local:13378",
                            "abs_api_key": "NEWKEY"})
        self.assertTrue(d["ok"])
        self.assertEqual(ss._config["abs_url"], "http://new.local:13378")
        self.assertEqual(ss._config["abs_api_key"], "NEWKEY")
        with open(ss.CONFIG_FILE) as f:
            self.assertEqual(json.load(f)["abs_url"], "http://new.local:13378")

    def test_same_url_keeps_stored_key(self):
        r, d = self.action({"action": "save_config", "abs_url": "http://abs.local:13378/"})
        self.assertTrue(d["ok"])
        self.assertEqual(ss._config["abs_api_key"], SECRET_KEY)

    def test_test_connection_new_url_never_gets_stored_key(self):
        r, d = self.action({"action": "test_connection", "abs_url": "http://evil.example"})
        self.assertFalse(d["ok"])
        self.assertFalse(any(k == SECRET_KEY for _, _, k in self.abs_calls))

    def test_test_connection_same_url_uses_stored_key(self):
        r, d = self.action({"action": "test_connection", "abs_url": "http://abs.local:13378"})
        self.assertTrue(self.abs_calls)
        self.assertTrue(all(k == SECRET_KEY for _, _, k in self.abs_calls))

    def test_add_folder_validation(self):
        f = os.path.join(self.tmp, "afile.txt")
        open(f, "w").close()
        for bad in (f, "/etc", "/", "relative/dir", os.path.join(self.tmp, "missing")):
            r, d = self.action({"action": "add_folder", "path": bad})
            self.assertFalse(d["ok"], bad)
        self.assertEqual(ss._config["linked_folders"], [])
        good = os.path.join(self.tmp, "my-spines")
        os.makedirs(good)
        r, d = self.action({"action": "add_folder", "path": good})
        self.assertTrue(d["ok"])
        self.assertEqual(len(ss._config["linked_folders"]), 1)


class RemoteNoTokenTests(ServerTestBase):
    """Non-loopback client with no token: dashboard locked, spines public."""
    client_ip = "192.168.1.66"

    def test_dashboard_locked(self):
        for method, path in (("GET", "/"), ("GET", "/api/ui/status")):
            r, data = self.req(method, path)
            self.assertEqual(r.status, 403, path)
            self.assertIn(b"DASHBOARD_TOKEN", data)
        r, _ = self.action({"action": "save_config", "abs_url": "http://evil.example",
                            "abs_api_key": "x"})
        self.assertEqual(r.status, 403)
        self.assertEqual(ss._config["abs_url"], "http://abs.local:13378")

    def test_public_endpoints_open(self):
        for path in ("/api/items/li_book1/spine", "/api/spines/manifest", "/health"):
            r, _ = self.req("GET", path)
            self.assertEqual(r.status, 200, path)


class TokenTests(ServerTestBase):
    client_ip = "192.168.1.66"
    token = "correct horse battery"

    def login(self, token):
        return self.req("POST", "/login",
                        "token=" + token.replace(" ", "+"),
                        {"Content-Type": "application/x-www-form-urlencoded"})

    def test_requires_login(self):
        r, _ = self.req("GET", "/api/ui/status")
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", "/")
        self.assertIn(r.status, (302, 303))
        self.assertEqual(r.getheader("Location"), "/login")
        r, _ = self.req("GET", "/login")
        self.assertEqual(r.status, 200)

    def test_wrong_token_no_cookie(self):
        r, _ = self.login("wrong")
        self.assertIsNone(r.getheader("Set-Cookie"))

    def test_login_cookie_grants_access(self):
        r, _ = self.login(self.token)
        cookie = r.getheader("Set-Cookie")
        self.assertIsNotNone(cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(self.token.split()[0], cookie)   # raw token not stored
        c = cookie.split(";")[0]
        r, _ = self.req("GET", "/api/ui/status", headers={"Cookie": c})
        self.assertEqual(r.status, 200)
        r, d = self.action({"action": "rescan"}, {"Cookie": c})
        self.assertEqual(r.status, 200)
        # Cookie alone isn't enough cross-site (Origin check still applies).
        r, _ = self.action({"action": "rescan"}, {"Cookie": c, "Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)

    def test_bearer_header(self):
        r, _ = self.req("GET", "/api/ui/status",
                        headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(r.status, 200)

    def test_env_token_overrides(self):
        ss._config["dashboard_token"] = ""
        os.environ["DASHBOARD_TOKEN"] = "envtok"
        try:
            r, _ = self.req("GET", "/api/ui/status")
            self.assertEqual(r.status, 401)
            r, _ = self.req("GET", "/api/ui/status", headers={"Authorization": "Bearer envtok"})
            self.assertEqual(r.status, 200)
        finally:
            os.environ.pop("DASHBOARD_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
