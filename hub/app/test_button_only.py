"""M.O 14 Oct 2026: after a voice sleep/stop only the back button wakes him."""
import json, os, tempfile, unittest
from session import Session


class ButtonOnly(unittest.TestCase):
    def test_voice_sleep_blocks_voice_wake(self):
        s = Session(enabled=True, idle_s=0)
        self.assertTrue(s.wake("phrase"))  # fresh start: Hey Vector still works
        s.sleep("phrase")
        self.assertTrue(s.button_only)
        for why in ("phrase", "restored after hub restart", "wake"):
            self.assertFalse(s.wake(why))
        self.assertEqual((s.state, s.voice_wakes_blocked), ("asleep", 3))
        self.assertTrue(s.wake("button"))
        self.assertFalse(s.button_only)
        self.assertEqual(s.status()["button_only"], False)

    def test_non_voice_sleep_keeps_voice_wake(self):
        s = Session(enabled=True, idle_s=0)
        s.wake("phrase"); s.sleep("test")
        self.assertTrue(s.wake("phrase"))


class ButtonOnlyHub(unittest.TestCase):
    def setUp(self):
        import main
        self.m = main
        self.tmp = tempfile.mkdtemp()
        self.old = (main.SESSION, main.SESSION_FILE)
        main.SESSION = Session(enabled=True, idle_s=0)
        main.SESSION_FILE = os.path.join(self.tmp, "session.json")

    def tearDown(self):
        self.m.SESSION, self.m.SESSION_FILE = self.old

    def test_restart_keeps_lock(self):
        json.dump({"state": "asleep", "t": 0, "explicit_sleep": True, "button_only": True}, open(self.m.SESSION_FILE, "w"))
        self.assertFalse(self.m.session_restore())
        self.assertTrue(self.m.SESSION.button_only)
        self.assertFalse(self.m.SESSION.wake("phrase"))
        self.assertTrue(self.m.SESSION.wake("button"))

    def test_old_file_explicit_sleep_also_locks(self):
        json.dump({"state": "asleep", "t": 0, "explicit_sleep": True}, open(self.m.SESSION_FILE, "w"))
        self.m.session_restore()
        self.assertTrue(self.m.SESSION.button_only)


if __name__ == "__main__":
    unittest.main()
