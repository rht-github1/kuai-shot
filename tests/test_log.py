from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from kuai_shot.log import event, is_enabled, rotate_if_needed, set_enabled


class LogTests(unittest.TestCase):
    def test_rotate_when_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kuai-shot.log"
            path.write_bytes(b"x" * 64)
            rotate_if_needed(path, max_bytes=32)
            self.assertFalse(path.exists())
            self.assertTrue((Path(tmp) / "kuai-shot.log.1").exists())
            self.assertEqual((Path(tmp) / "kuai-shot.log.1").stat().st_size, 64)

    def test_keep_small_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kuai-shot.log"
            path.write_text("ok\n", encoding="utf-8")
            rotate_if_needed(path, max_bytes=1024)
            self.assertTrue(path.exists())
            self.assertFalse((Path(tmp) / "kuai-shot.log.1").exists())

    def test_default_off(self) -> None:
        set_enabled(False, persist=False)
        self.assertFalse(is_enabled())
        event("test.should.skip")

    def test_can_turn_on(self) -> None:
        set_enabled(True, persist=False)
        self.assertTrue(is_enabled())
        set_enabled(False, persist=False)
        self.assertFalse(is_enabled())


if __name__ == "__main__":
    unittest.main()
