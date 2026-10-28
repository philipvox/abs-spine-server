"""Config file location, precedence and the Docker packaging.

Uses temporary config paths only — never the real config.json.
"""
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import spine_server as ss  # noqa: E402

ENV_KEYS = ("ABS_URL", "ABS_API_KEY", "PORT", "SPINES_DIR", "LIBRARY_PATH",
            "CONFIG_FILE", "DASHBOARD_TOKEN")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (ss.CONFIG_FILE, ss.LEGACY_CONFIG_FILE)
        ss.CONFIG_FILE = os.path.join(self.tmp, "data", "config.json")
        ss.LEGACY_CONFIG_FILE = os.path.join(self.tmp, "app", "config.json")
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        ss.CONFIG_FILE, ss.LEGACY_CONFIG_FILE = self._orig
        shutil.rmtree(self.tmp)

    def write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def test_config_file_location_from_env(self):
        self.assertEqual(ss.resolve_config_file({"CONFIG_FILE": "/data/config.json"}),
                         "/data/config.json")
        self.assertEqual(ss.resolve_config_file({}), ss.LEGACY_DEFAULT_CONFIG_FILE)

    def test_saved_config_beats_env_defaults(self):
        self.write(ss.CONFIG_FILE, {"abs_url": "http://saved:13378", "abs_api_key": "SAVED"})
        os.environ["ABS_URL"] = "http://audiobookshelf:13378"
        os.environ["ABS_API_KEY"] = "ENVKEY"
        c = ss.load_config()
        self.assertEqual(c["abs_url"], "http://saved:13378")
        self.assertEqual(c["abs_api_key"], "SAVED")

    def test_env_fills_missing_values(self):
        self.write(ss.CONFIG_FILE, {"abs_url": "", "linked_folders": []})
        os.environ["ABS_URL"] = "http://audiobookshelf:13378"
        os.environ["ABS_API_KEY"] = "ENVKEY"
        c = ss.load_config()
        self.assertEqual(c["abs_url"], "http://audiobookshelf:13378")
        self.assertEqual(c["abs_api_key"], "ENVKEY")

    def test_deployment_env_still_overrides(self):
        self.write(ss.CONFIG_FILE, {"port": 8786})
        os.environ["PORT"] = "9999"
        os.environ["SPINES_DIR"] = "/spines"
        c = ss.load_config()
        self.assertEqual(c["port"], 9999)
        self.assertEqual(c["spines_dir"], "/spines")

    def test_legacy_config_read_when_new_missing(self):
        self.write(ss.LEGACY_CONFIG_FILE, {"abs_url": "http://old:13378", "abs_api_key": "OLD"})
        c = ss.load_config()
        self.assertEqual(c["abs_url"], "http://old:13378")
        ss.save_config(c)   # saves go to the new location
        self.assertTrue(os.path.exists(ss.CONFIG_FILE))
        with open(ss.CONFIG_FILE) as f:
            self.assertEqual(json.load(f)["abs_api_key"], "OLD")
        # new file now wins over the legacy one
        self.write(ss.LEGACY_CONFIG_FILE, {"abs_url": "http://older:13378"})
        self.assertEqual(ss.load_config()["abs_url"], "http://old:13378")

    def test_save_creates_dir_with_private_mode(self):
        ss.save_config({"abs_url": "u", "abs_api_key": "k"})
        mode = stat.S_IMODE(os.stat(ss.CONFIG_FILE).st_mode)
        self.assertEqual(mode, 0o600)


class DockerPackagingTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, name)) as f:
            return f.read()

    def test_dockerfile(self):
        d = self.read("Dockerfile")
        self.assertIn("CONFIG_FILE=/data/config.json", d)
        hc = d[d.index("HEALTHCHECK"):]
        self.assertNotIn(":8786", hc)
        self.assertRegex(hc, r"\$\{?PORT")
        self.assertNotRegex(d, r"ENV ABS_API_KEY")

    def test_compose_mounts_data(self):
        c = self.read("docker-compose.yml")
        self.assertRegex(c, r"-\s+\./data:/data")
        self.assertIn("DASHBOARD_TOKEN", c)


if __name__ == "__main__":
    unittest.main()
