"""Dashboard community import must not run twice concurrently."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import spine_server as ss  # noqa: E402


class ImportLockTests(unittest.TestCase):
    def setUp(self):
        self._orig = ss.import_community_spines
        self.release = threading.Event()
        self.calls = 0

        def fake(*a, **kw):
            self.calls += 1
            self.release.wait(5)
            return {"ok": True, "message": "done"}
        ss.import_community_spines = fake
        ss._config = {"spines_dir": "/nonexistent-spines-test", "linked_folders": []}

    def tearDown(self):
        self.release.set()
        ss.import_community_spines = self._orig

    def test_second_start_refused_while_running(self):
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(ss.start_ui_import())
        ts = [threading.Thread(target=go) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(results.count(True), 1)
        self.assertTrue(ss._import_state["running"])   # set before the thread runs
        self.assertFalse(ss.start_ui_import())
        self.release.set()
        for _ in range(100):
            if not ss._import_state["running"]:
                break
            threading.Event().wait(0.02)
        self.assertFalse(ss._import_state["running"])
        self.assertEqual(self.calls, 1)
        # Can start again once finished.
        self.release.clear()
        self.assertTrue(ss.start_ui_import())
        self.release.set()


if __name__ == "__main__":
    unittest.main()
