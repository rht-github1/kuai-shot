from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from kuai_shot.stitch import frames_similar, overlap_rows, stitch_horizontal, stitch_vertical


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def _band(h: int, color: QColor) -> QImage:
    image = QImage(120, h, QImage.Format_RGB32)
    image.fill(color)
    return image


class StitchTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_overlap_and_stitch(self) -> None:
        top = QImage(80, 100, QImage.Format_RGB32)
        top.fill(QColor(10, 10, 10))
        painter = QPainter(top)
        painter.fillRect(0, 60, 80, 40, QColor(200, 40, 40))
        painter.end()
        bottom = QImage(80, 90, QImage.Format_RGB32)
        bottom.fill(QColor(40, 40, 200))
        painter = QPainter(bottom)
        painter.fillRect(0, 0, 80, 40, QColor(200, 40, 40))
        painter.end()
        ov = overlap_rows(top, bottom)
        self.assertGreaterEqual(ov, 20)
        out = stitch_vertical([top, bottom])
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.width(), 80)
        self.assertLess(out.height(), top.height() + bottom.height())
        self.assertGreater(out.height(), top.height())

    def test_single(self) -> None:
        frame = _band(40, QColor(1, 2, 3))
        out = stitch_vertical([frame])
        self.assertEqual(out.height(), 40)

    def test_black_frames_do_not_eat(self) -> None:
        top = _band(80, QColor(8, 8, 8))
        bottom = _band(80, QColor(4, 4, 4))
        self.assertEqual(overlap_rows(top, bottom), 0)

    def test_similar_skips_duplicate(self) -> None:
        a = _band(60, QColor(30, 40, 50))
        b = _band(60, QColor(31, 40, 50))
        self.assertTrue(frames_similar(a, b))
        self.assertFalse(frames_similar(a, _band(60, QColor(200, 20, 20))))

    def test_horizontal_stitch(self) -> None:
        left = QImage(80, 40, QImage.Format_RGB32)
        left.fill(QColor(10, 10, 10))
        painter = QPainter(left)
        painter.fillRect(50, 0, 30, 40, QColor(20, 180, 20))
        painter.end()
        right = QImage(70, 40, QImage.Format_RGB32)
        right.fill(QColor(20, 20, 180))
        painter = QPainter(right)
        painter.fillRect(0, 0, 30, 40, QColor(20, 180, 20))
        painter.end()
        out = stitch_horizontal([left, right])
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.height(), 40)
        self.assertGreater(out.width(), left.width())
        self.assertLess(out.width(), left.width() + right.width())


if __name__ == "__main__":
    unittest.main()
