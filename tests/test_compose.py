from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from kuai_shot.compose import compose_monitor_frames, compose_pieces, compose_shots
from kuai_shot.display import Monitor, ScreenShot


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def _solid(w: int, h: int, color: QColor) -> QImage:
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(color)
    return image


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = _app().primaryScreen()

    def test_single_screen(self) -> None:
        shot = ScreenShot(self.screen, _solid(1920, 1080, QColor(10, 20, 30)), QRect(0, 0, 1920, 1080), "HDMI-1")
        out = compose_shots([shot], QRect(100, 80, 400, 200))
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.width(), 400)
        self.assertEqual(out.height(), 200)
        self.assertEqual(out.pixelColor(8, 8), QColor(10, 20, 30))

    def test_two_screens_span(self) -> None:
        left = ScreenShot(self.screen, _solid(2880, 1800, QColor(200, 20, 20)), QRect(0, 0, 2880, 1800), "eDP-1")
        right = ScreenShot(self.screen, _solid(2560, 1440, QColor(20, 40, 200)), QRect(2880, 360, 2560, 1440), "DP-3")
        out = compose_shots([left, right], QRect(2780, 400, 300, 200))
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.size().width(), 300)
        self.assertEqual(out.pixelColor(10, 10), QColor(200, 20, 20))
        self.assertEqual(out.pixelColor(150, 10), QColor(20, 40, 200))

    def test_three_screens_span(self) -> None:
        shots = [
            ScreenShot(self.screen, _solid(1920, 1080, QColor(9, 9, 9)), QRect(-1920, 360, 1920, 1080), "HDMI-2"),
            ScreenShot(self.screen, _solid(2880, 1800, QColor(80, 80, 80)), QRect(0, 0, 2880, 1800), "eDP-1"),
            ScreenShot(self.screen, _solid(2560, 1440, QColor(180, 180, 180)), QRect(2880, 360, 2560, 1440), "DP-3"),
        ]
        out = compose_shots(shots, QRect(-100, 400, 200, 120))
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.width(), 200)
        self.assertEqual(out.pixelColor(10, 10), QColor(9, 9, 9))
        self.assertEqual(out.pixelColor(150, 10), QColor(80, 80, 80))

    def test_compose_pieces(self) -> None:
        a = _solid(80, 40, QColor(1, 2, 3))
        b = _solid(80, 40, QColor(4, 5, 6))
        out = compose_pieces(
            [(QRect(0, 0, 80, 40), a), (QRect(80, 0, 80, 40), b)],
            QRect(0, 0, 160, 40),
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.width(), 160)
        self.assertEqual(out.pixelColor(10, 10), QColor(1, 2, 3))
        self.assertEqual(out.pixelColor(100, 10), QColor(4, 5, 6))

    def test_compose_monitor_frames(self) -> None:
        geo = QRect(0, 0, 1920, 1080)
        mon = Monitor(self.screen, "eDP-1", geo, 1.0)
        frames = [
            {
                "name": "eDP-1",
                "image": _solid(1920, 1080, QColor(11, 22, 33)),
                "x": 0,
                "y": 0,
            }
        ]
        out = compose_monitor_frames(frames, QRect(12, 16, 48, 36), [mon])
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.width(), 48)
        self.assertEqual(out.height(), 36)
        self.assertEqual(out.pixelColor(4, 4), QColor(11, 22, 33))


if __name__ == "__main__":
    unittest.main()
