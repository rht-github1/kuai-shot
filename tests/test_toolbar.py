from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("KUAI_SHOT_HOME", ROOT)

from kuai_shot.overlay_gtk import BAR_KINDS, BAR_TIPS, inspect_toolbar_hit, toolbar_tip_text


class ToolbarTipTests(unittest.TestCase):
    def test_every_button_has_tip(self) -> None:
        self.assertEqual(set(BAR_TIPS), set(BAR_KINDS))
        for kind in BAR_KINDS:
            self.assertTrue(BAR_TIPS[kind].strip())

    def test_hit_button_and_color(self) -> None:
        slots = [(kind, i * 34, 34) for i, kind in enumerate(BAR_KINDS)]
        bar = (10, 20, 34 * len(BAR_KINDS), 40, slots)
        colors = (66, [((245, 74, 69), 10, 66), ((255, 255, 255), 30, 66)])
        hit = inspect_toolbar_hit(bar, colors, 10 + 17, 30)
        self.assertEqual(hit[0], "btn")
        self.assertEqual(hit[1], "rect")
        self.assertEqual(toolbar_tip_text(hit), "矩形")
        color = inspect_toolbar_hit(bar, colors, 14, 70)
        self.assertEqual(color[0], "color")
        self.assertEqual(toolbar_tip_text(color), "颜色  #F54A45")
        self.assertIsNone(inspect_toolbar_hit(bar, colors, 0, 0))


if __name__ == "__main__":
    unittest.main()
