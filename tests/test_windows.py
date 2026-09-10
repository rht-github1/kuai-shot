from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from kuai_shot.windows import TopWindow, candidate_rects, window_at


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


class WindowMapTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.laptop = QRect(0, 0, 2880, 1800)
        self.ext = QRect(2880, 360, 2560, 1440)
        self.mons = [self.laptop, self.ext]

    def test_near_max_external(self) -> None:
        rects = candidate_rects(QRect(2938, 392, 2502, 1408), self.mons)
        self.assertEqual(rects, [self.ext])

    def test_origin_size_maps_to_external(self) -> None:
        rects = candidate_rects(QRect(0, 0, 2502, 1408), self.mons)
        self.assertEqual(rects, [self.ext])

    def test_unknown_windowed_ignored(self) -> None:
        self.assertEqual(candidate_rects(QRect(0, 0, 1920, 1200), self.mons), [])

    def test_real_small_window(self) -> None:
        raw = QRect(3581, 649, 922, 868)
        self.assertEqual(candidate_rects(raw, self.mons), [raw])

    def test_click_disambiguate(self) -> None:
        wins = [
            TopWindow("ChatGPT", "Codex", QRect(2938, 392, 2502, 1408), QRect(2938, 392, 2502, 1408)),
            TopWindow("微信", "WeChat", QRect(3581, 649, 922, 868), QRect(3581, 649, 922, 868)),
        ]
        hit = window_at(3600, 700, wins, self.mons)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.title, "微信")
        self.assertIsNone(window_at(1440, 900, wins, self.mons))


if __name__ == "__main__":
    unittest.main()
