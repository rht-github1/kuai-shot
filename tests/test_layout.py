from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtCore import QRect, QSize
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("KUAI_SHOT_HOME", ROOT)

from kuai_shot.display import Monitor, best_layout, monitor_names_for_rect, place_rect_away, split_by_screens


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def _mon(name: str, x: int, y: int, w: int, h: int, scale: float, mw: int, mh: int) -> Monitor:
    return Monitor(_app().primaryScreen(), name, QRect(x, y, w, h), scale, QSize(mw, mh))


def _img(w: int, h: int) -> QImage:
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(20, 80, 140))
    return image


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_single_1080(self) -> None:
        mons = [_mon("HDMI-1", 0, 0, 1920, 1080, 1.0, 1920, 1080)]
        shots = split_by_screens(_img(1920, 1080), mons)
        self.assertEqual(len(shots), 1)
        self.assertEqual(best_layout(_img(1920, 1080), mons).score, 0.0)

    def test_single_4k_scaled(self) -> None:
        mons = [_mon("eDP-1", 0, 0, 1920, 1080, 2.0, 3840, 2160)]
        shots = split_by_screens(_img(3840, 2160), mons)
        self.assertEqual(len(shots), 1)

    def test_office_two_screens(self) -> None:
        mons = [
            _mon("eDP-1", 0, 0, 2880, 1800, 2.0, 2880, 1800),
            _mon("DP-3", 2880, 360, 2560, 1440, 1.0, 2560, 1440),
        ]
        shots = split_by_screens(_img(5440, 1800), mons)
        self.assertEqual(len(shots), 2)
        self.assertEqual(shots[0].image.width(), 2880)
        self.assertEqual(shots[1].image.width(), 2560)

    def test_reject_partial_canvas(self) -> None:
        mons = [
            _mon("eDP-1", 0, 0, 2880, 1800, 2.0, 2880, 1800),
            _mon("DP-3", 2880, 360, 2560, 1440, 1.0, 2560, 1440),
        ]
        self.assertEqual(split_by_screens(_img(2880, 1800), mons), [])

    def test_triple(self) -> None:
        mons = [
            _mon("HDMI-2", -1920, 360, 1920, 1080, 1.0, 1920, 1080),
            _mon("eDP-1", 0, 0, 2880, 1800, 2.0, 2880, 1800),
            _mon("DP-3", 2880, 360, 2560, 1440, 1.0, 2560, 1440),
        ]
        shots = split_by_screens(_img(7360, 1800), mons)
        self.assertEqual(len(shots), 3)

    def test_monitor_names_for_rect(self) -> None:
        mons = [
            _mon("eDP-1", 0, 0, 2880, 1800, 2.0, 2880, 1800),
            _mon("DP-3", 2880, 360, 2560, 1440, 1.0, 2560, 1440),
        ]
        self.assertEqual(monitor_names_for_rect(QRect(3572, 732, 762, 279), mons), ["DP-3"])
        self.assertEqual(monitor_names_for_rect(QRect(10, 10, 100, 100), mons), ["eDP-1"])
        self.assertEqual(monitor_names_for_rect(QRect(2800, 400, 200, 200), mons), ["eDP-1", "DP-3"])

    def test_place_rect_away_from_region(self) -> None:
        region = QRect(100, 80, 240, 160)
        bounds = QRect(0, 0, 1280, 800)
        pos = place_rect_away(region, QSize(200, 120), bounds)
        box = QRect(pos, QSize(200, 120))
        self.assertTrue(bounds.contains(box))
        self.assertTrue(box.intersected(region).isEmpty())


if __name__ == "__main__":
    unittest.main()
