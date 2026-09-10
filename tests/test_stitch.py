from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from kuai_shot.stitch import (
    _pick_row_shift,
    estimate_shift,
    extend_unwrapped,
    frames_similar,
    overlap_rows,
    pick_verified_shift,
    stitch_horizontal,
    stitch_vertical,
)


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

    def test_estimate_and_unwrap_vertical(self) -> None:
        doc = QImage(64, 180, QImage.Format_RGB32)
        painter = QPainter(doc)
        for y in range(180):
            painter.fillRect(
                0,
                y,
                64,
                1,
                QColor(20 + (y * 13) % 200, 30 + (y * 7) % 180, 40 + (y * 3) % 150),
            )
        painter.end()
        prev = doc.copy(0, 24, 64, 56)
        nxt = doc.copy(0, 24 + 17, 64, 56)
        dy = estimate_shift(prev, nxt, "v")
        self.assertIsNotNone(dy)
        assert dy is not None
        self.assertGreaterEqual(dy, 15)
        self.assertLessEqual(dy, 19)
        out = extend_unwrapped(prev, nxt, dy, "v")
        self.assertEqual(out.width(), 64)
        self.assertGreaterEqual(out.height(), 56 + 15)

    def test_jitter_does_not_beat_real_scroll(self) -> None:
        self.assertEqual(pick_verified_shift([2, -2, 50, 100], 50, 300), 50)
        self.assertEqual(pick_verified_shift([2, -2], 2, 193), 0)
        self.assertEqual(pick_verified_shift([50, 250], 250, 376), 50)
        self.assertEqual(pick_verified_shift([50, 285], 285, 380), 50)
        self.assertEqual(pick_verified_shift([12, 13, 50], None, 296), 50)
        self.assertEqual(pick_verified_shift([-26, 24, 50], None, 324), 50)
        self.assertEqual(_pick_row_shift([(20, 94), (22, 50), (30, 76), (80, 40)]), 50)

    def test_same_viewport_is_not_a_jump(self) -> None:
        frame = QImage(64, 80, QImage.Format_RGB32)
        painter = QPainter(frame)
        for y in range(80):
            painter.fillRect(0, y, 64, 1, QColor(30 + y, 40, 80))
        painter.end()
        dy = estimate_shift(frame, frame.copy(), "v")
        self.assertTrue(dy is None or abs(dy) <= 1)

    def test_unwrap_gap_when_unaligned(self) -> None:
        a = _band(40, QColor(20, 80, 20))
        b = _band(40, QColor(180, 30, 30))
        out = extend_unwrapped(a, b, None, "v")
        self.assertEqual(out.width(), a.width())
        self.assertGreater(out.height(), a.height() + b.height())


if __name__ == "__main__":
    unittest.main()
