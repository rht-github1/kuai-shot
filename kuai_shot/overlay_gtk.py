from __future__ import annotations

import math
from datetime import datetime
from dataclasses import dataclass, field

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from PyQt5.QtCore import QObject, QRect, pyqtSignal
from PyQt5.QtGui import QImage

from .display import ScreenShot
from .paths import pictures_dir

_GTK_READY = False


def ensure_gtk() -> None:
    global _GTK_READY
    if _GTK_READY:
        return
    Gtk.init([])
    _GTK_READY = True


def pump_gtk() -> None:
    ctx = GLib.MainContext.default()
    n = 0
    while ctx.pending() and n < 128:
        ctx.iteration(False)
        n += 1


def qimage_to_pixbuf(image: QImage) -> GdkPixbuf.Pixbuf:
    if image.format() != QImage.Format_RGBA8888:
        image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    bits = image.constBits()
    raw = bits.asstring(stride * height) if hasattr(bits, "asstring") else bytes(bits)
    src = GdkPixbuf.Pixbuf.new_from_data(
        raw, GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
    )
    return src.copy()


def pixbuf_to_qimage(pixbuf: GdkPixbuf.Pixbuf) -> QImage:
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    raw = bytes(pixbuf.get_pixels())
    fmt = QImage.Format_RGBA8888 if pixbuf.get_has_alpha() else QImage.Format_RGB888
    return QImage(raw, width, height, stride, fmt).copy()


def monitor_index_for(geo: QRect) -> tuple[int, object]:
    display = Gdk.Display.get_default()
    best, best_d, best_mon = 0, 10**9, None
    n = display.get_n_monitors()
    cx = geo.x() + geo.width() // 2
    cy = geo.y() + geo.height() // 2
    for i in range(n):
        mon = display.get_monitor(i)
        g = mon.get_geometry()
        d = abs(g.x - geo.x()) + abs(g.y - geo.y()) + abs(g.width - geo.width()) + abs(g.height - geo.height())
        if g.x <= cx < g.x + g.width and g.y <= cy < g.y + g.height:
            d -= 10000
        if d < best_d:
            best, best_d, best_mon = i, d, mon
    return best, best_mon


COLORS = [
    (245, 74, 69),
    (255, 141, 26),
    (255, 198, 10),
    (52, 199, 36),
    (51, 112, 255),
    (123, 103, 238),
    (255, 255, 255),
    (31, 35, 41),
]


@dataclass
class Stroke:
    kind: str
    color: tuple[int, int, int]
    width: int
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""


class GtkOverlay:
    def __init__(self, shot: ScreenShot, session: "OverlaySession"):
        ensure_gtk()
        self.session = session
        self.pixbuf = qimage_to_pixbuf(shot.image)
        self.img_w = self.pixbuf.get_width()
        self.img_h = self.pixbuf.get_height()
        self.tool = "rect"
        self.color = COLORS[0]
        self.pen_w = 4
        self.strokes: list[Stroke] = []
        self.redo: list[Stroke] = []
        self.draft: Stroke | None = None
        self.rect = [0.0, 0.0, 0.0, 0.0]
        self.selecting = False
        self.closed = False
        self._seat = None
        self._last_ev = None

        screen = Gdk.Screen.get_default()
        idx, gdk_mon = monitor_index_for(shot.geometry)
        self.mon_geo = screen.get_monitor_geometry(idx)
        self.mon_idx = idx
        self.mon_sf = float(gdk_mon.get_scale_factor() if gdk_mon is not None else 1)

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_accept_focus(True)
        self.win.set_resizable(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.da = Gtk.DrawingArea()
        self.da.set_can_focus(True)
        self.da.set_hexpand(True)
        self.da.set_vexpand(True)
        self.win.add(self.da)
        self.win.add_events(mask)
        self.da.add_events(mask)
        self.da.connect("draw", self._on_draw)
        self.win.connect("button-press-event", self._on_press)
        self.win.connect("button-release-event", self._on_release)
        self.win.connect("motion-notify-event", self._on_motion)
        self.da.connect("button-press-event", self._on_press)
        self.da.connect("button-release-event", self._on_release)
        self.da.connect("motion-notify-event", self._on_motion)
        self.win.connect("key-press-event", self._on_key)
        self.win.connect("delete-event", self._on_delete)
        self.win.connect("realize", self._on_realize)

        self.win.move(self.mon_geo.x, self.mon_geo.y)
        self.win.set_default_size(self.mon_geo.width, self.mon_geo.height)
        self.win.resize(self.mon_geo.width, self.mon_geo.height)
        self.win.fullscreen_on_monitor(screen, idx)
        self.win.show_all()
        GLib.idle_add(self._prep_window)

    def _on_realize(self, *_a) -> None:
        self.win.move(self.mon_geo.x, self.mon_geo.y)
        self.win.resize(self.mon_geo.width, self.mon_geo.height)

    def _win_sf(self) -> float:
        return max(1.0, float(self.win.get_scale_factor() or 1))

    def _view_size(self) -> tuple[float, float]:
        alloc = self.da.get_allocation()
        aw, ah = float(max(1, alloc.width)), float(max(1, alloc.height))
        ratio = self.mon_sf / self._win_sf()
        vis_w = min(aw, max(1.0, self.mon_geo.width * ratio))
        vis_h = min(ah, max(1.0, self.mon_geo.height * ratio))
        return vis_w, vis_h

    def _event_xy(self, widget, ev) -> tuple[float, float]:
        x, y = float(ev.x), float(ev.y)
        if widget is not self.da:
            try:
                ok, tx, ty = widget.translate_coordinates(self.da, int(x), int(y))
                if ok:
                    return float(tx), float(ty)
            except Exception:
                pass
        return x, y

    def _prep_window(self) -> bool:
        gdk_win = self.win.get_window()
        if gdk_win is None:
            return False
        gdk_win.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        try:
            gdk_win.set_cursor(Gdk.Cursor.new_from_name(self.win.get_display(), "crosshair"))
        except Exception:
            pass
        self.win.present()
        self.da.grab_focus()
        return False

    def _norm(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.rect
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)

    def _on_draw(self, _w, cr):
        vis_w, vis_h = self._view_size()
        sx = vis_w / max(1, self.img_w)
        sy = vis_h / max(1, self.img_h)
        cr.save()
        cr.scale(sx, sy)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        x, y, w, h = self._norm()
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, self.img_w, self.img_h)
        if w > 2 and h > 2:
            cr.rectangle(x, y, w, h)
            cr.set_fill_rule(1)  # EVEN_ODD
        cr.fill()
        if w > 2 and h > 2:
            cr.set_source_rgb(0.20, 0.44, 1)
            cr.set_line_width(2)
            cr.rectangle(x, y, w, h)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            for hx, hy in (
                (x, y),
                (x + w / 2, y),
                (x + w, y),
                (x + w, y + h / 2),
                (x + w, y + h),
                (x + w / 2, y + h),
                (x, y + h),
                (x, y + h / 2),
            ):
                cr.rectangle(hx - 4, hy - 4, 8, 8)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("Sans")
            cr.set_font_size(16)
            cr.move_to(x, max(18, y - 8))
            cr.show_text(f"{int(w)} × {int(h)}")
        for stroke in self.strokes + ([self.draft] if self.draft else []):
            self._draw_stroke(cr, stroke)
        cr.restore()
        self._draw_hint(cr, vis_w)
        if w > 2:
            self._draw_bar(cr, vis_w, vis_h, sx, sy, x, y, w, h)
        return False

    def _draw_hint(self, cr, win_w):
        cr.set_source_rgba(0.17, 0.18, 0.21, 0.92)
        cr.rectangle(16, 16, 420, 28)
        cr.fill()
        cr.set_source_rgb(0.91, 0.92, 0.93)
        cr.set_font_size(13)
        cr.move_to(24, 35)
        cr.show_text(f"本屏 {self.img_w}×{self.img_h}  ·  在此屏拖拽  Enter复制  保存才写文件  Esc取消")

    def _round_rect(self, cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
        cr.close_path()

    def _draw_bar(self, cr, win_w, win_h, sx, sy, x, y, w, h):
        kinds = ["rect", "ellipse", "arrow", "pen", "mosaic", "text", "undo", "save", "ok", "cancel"]
        bw, bh, pad = 40, 40, 6
        seps = {6, 8}
        total = pad * 2 + len(kinds) * bw + len(seps) * 10
        px = min(win_w - total - 10, max(10, (x + w) * sx - total))
        py = min(win_h - bh - 10, max(10, (y + h) * sy + 10))
        self._bar = (px, py, bw, kinds, pad, seps)
        self._round_rect(cr, px, py, total, bh, 12)
        cr.set_source_rgba(0.07, 0.09, 0.12, 0.94)
        cr.fill_preserve()
        cr.set_source_rgba(0.28, 0.34, 0.42, 0.55)
        cr.set_line_width(1)
        cr.stroke()
        cursor = px + pad
        slots = []
        for i, kind in enumerate(kinds):
            if i in seps:
                cr.set_source_rgba(1, 1, 1, 0.12)
                cr.set_line_width(1)
                cr.move_to(cursor + 5, py + 10)
                cr.line_to(cursor + 5, py + bh - 10)
                cr.stroke()
                cursor += 10
            slots.append((kind, cursor))
            active = kind == self.tool
            if active:
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.18, 0.46, 1.0, 0.95)
                cr.fill()
            elif kind == "ok":
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.13, 0.72, 0.47, 0.18)
                cr.fill()
            elif kind == "cancel":
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.96, 0.32, 0.36, 0.16)
                cr.fill()
            self._draw_icon(cr, kind, cursor + (bw - 20) / 2, py + (bh - 20) / 2, active)
            cursor += bw
        self._bar_slots = (py, bh, slots, bw)

    def _draw_icon(self, cr, kind: str, x: float, y: float, active: bool = False) -> None:
        cr.save()
        cr.set_line_width(1.7)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        if kind == "ok":
            cr.set_source_rgb(0.35, 0.95, 0.62) if not active else cr.set_source_rgb(1, 1, 1)
        elif kind == "cancel":
            cr.set_source_rgb(1.0, 0.45, 0.48) if not active else cr.set_source_rgb(1, 1, 1)
        else:
            cr.set_source_rgb(1, 1, 1) if active else cr.set_source_rgb(0.86, 0.90, 0.94)
        if kind == "rect":
            self._round_rect(cr, x + 2, y + 3.5, 16, 13, 2.5)
            cr.stroke()
        elif kind == "ellipse":
            cr.arc(x + 10, y + 10, 7.2, 0, math.tau)
            cr.stroke()
        elif kind == "arrow":
            cr.move_to(x + 4, y + 16)
            cr.line_to(x + 14.5, y + 5.5)
            cr.stroke()
            cr.move_to(x + 8.2, y + 4.6)
            cr.line_to(x + 16.2, y + 4.2)
            cr.line_to(x + 15.6, y + 12.2)
            cr.close_path()
            cr.fill()
        elif kind == "pen":
            cr.move_to(x + 13.8, y + 2.6)
            cr.line_to(x + 17.2, y + 6.0)
            cr.line_to(x + 7.4, y + 15.8)
            cr.line_to(x + 3.2, y + 17.0)
            cr.line_to(x + 4.4, y + 12.8)
            cr.close_path()
            cr.stroke()
            cr.move_to(x + 6.2, y + 14.2)
            cr.line_to(x + 14.6, y + 5.8)
            cr.stroke()
        elif kind == "mosaic":
            cells = ((0, 0, 0.95), (9, 0, 0.45), (0, 9, 0.45), (9, 9, 0.95))
            for cx, cy, a in cells:
                cr.set_source_rgba(0.86, 0.90, 0.94, a) if not active else cr.set_source_rgba(1, 1, 1, a)
                self._round_rect(cr, x + 2 + cx, y + 2 + cy, 7, 7, 1.6)
                cr.fill()
        elif kind == "text":
            cr.set_line_width(1.9)
            cr.move_to(x + 4, y + 4.2)
            cr.line_to(x + 16, y + 4.2)
            cr.move_to(x + 10, y + 4.2)
            cr.line_to(x + 10, y + 16.4)
            cr.stroke()
            cr.set_line_width(1.5)
            cr.move_to(x + 6.5, y + 16.4)
            cr.line_to(x + 13.5, y + 16.4)
            cr.stroke()
        elif kind == "undo":
            cr.arc_negative(x + 10.2, y + 10.6, 6.2, math.radians(28), math.radians(-145))
            cr.stroke()
            cr.move_to(x + 2.2, y + 6.4)
            cr.line_to(x + 2.0, y + 12.6)
            cr.line_to(x + 7.8, y + 11.4)
            cr.close_path()
            cr.fill()
        elif kind == "save":
            cr.move_to(x + 10, y + 2.8)
            cr.line_to(x + 10, y + 12.2)
            cr.stroke()
            cr.move_to(x + 5.6, y + 8.4)
            cr.line_to(x + 10, y + 13.2)
            cr.line_to(x + 14.4, y + 8.4)
            cr.stroke()
            cr.move_to(x + 3.4, y + 15.6)
            cr.line_to(x + 16.6, y + 15.6)
            cr.stroke()
        elif kind == "ok":
            cr.set_line_width(2.1)
            cr.move_to(x + 3.4, y + 10.4)
            cr.line_to(x + 8.2, y + 15.2)
            cr.line_to(x + 16.8, y + 5.0)
            cr.stroke()
        elif kind == "cancel":
            cr.set_line_width(2.0)
            cr.move_to(x + 4.4, y + 4.4)
            cr.line_to(x + 15.6, y + 15.6)
            cr.move_to(x + 15.6, y + 4.4)
            cr.line_to(x + 4.4, y + 15.6)
            cr.stroke()
        cr.restore()

    def _draw_stroke(self, cr, stroke: Stroke):
        if len(stroke.points) < 1:
            return
        r, g, b = [c / 255 for c in stroke.color]
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(stroke.width)
        pts = stroke.points
        if stroke.kind == "pen" and len(pts) >= 2:
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.stroke()
        elif stroke.kind in {"rect", "ellipse", "highlight"} and len(pts) >= 2:
            x1, y1 = pts[0]
            x2, y2 = pts[-1]
            box = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            if stroke.kind == "highlight":
                cr.set_source_rgba(r, g, b, 0.35)
                cr.rectangle(*box)
                cr.fill()
            elif stroke.kind == "rect":
                cr.rectangle(*box)
                cr.stroke()
            else:
                cr.save()
                cr.translate(box[0] + box[2] / 2, box[1] + box[3] / 2)
                cr.scale(max(box[2] / 2, 0.1), max(box[3] / 2, 0.1))
                cr.arc(0, 0, 1, 0, 6.283)
                cr.restore()
                cr.stroke()
        elif stroke.kind == "arrow" and len(pts) >= 2:
            x1, y1 = pts[0]
            x2, y2 = pts[-1]
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()
            ang = math.atan2(y2 - y1, x2 - x1)
            size = max(10, stroke.width * 3)
            cr.move_to(x2, y2)
            cr.line_to(x2 - size * math.cos(ang - 0.4), y2 - size * math.sin(ang - 0.4))
            cr.line_to(x2 - size * math.cos(ang + 0.4), y2 - size * math.sin(ang + 0.4))
            cr.close_path()
            cr.fill()
        elif stroke.kind == "mosaic" and len(pts) >= 2:
            self._draw_mosaic(cr, pts)
        elif stroke.kind == "text" and stroke.text:
            cr.set_font_size(18)
            cr.move_to(*pts[0])
            cr.show_text(stroke.text)

    def _to_img(self, widget, ev) -> tuple[float, float]:
        px, py = self._event_xy(widget, ev)
        vis_w, vis_h = self._view_size()
        ix = px * self.img_w / vis_w
        iy = py * self.img_h / vis_h
        return max(0, min(self.img_w, ix)), max(0, min(self.img_h, iy))

    def _hit_bar(self, x, y) -> int | None:
        slots = getattr(self, "_bar_slots", None)
        if not slots:
            return None
        py, bh, items, bw = slots
        if y < py or y > py + bh:
            return None
        for i, (_kind, sx) in enumerate(items):
            if sx <= x <= sx + bw:
                return i
        return None

    def _dup_event(self, ev) -> bool:
        key = (int(ev.time), int(ev.type), int(getattr(ev, "button", 0)), round(ev.x, 1), round(ev.y, 1))
        if key == self._last_ev:
            return True
        self._last_ev = key
        return False

    def _on_press(self, widget, ev):
        if self._dup_event(ev):
            return True
        self.session.note_active(self)
        if ev.button == 3:
            self._cancel()
            return True
        if ev.button != 1:
            return False
        vx, vy = self._event_xy(widget, ev)
        hit = self._hit_bar(vx, vy)
        if hit is not None:
            self._bar_action(hit)
            return True
        ix, iy = self._to_img(widget, ev)
        x, y, w, h = self._norm()
        if w > 8 and x <= ix <= x + w and y <= iy <= y + h and self.tool in {"rect", "ellipse", "arrow", "pen", "mosaic", "text"}:
            if self.tool == "text":
                self._add_text(ix, iy)
                return True
            self.draft = Stroke(self.tool, self.color, self.pen_w, [(ix, iy)])
            self.da.queue_draw()
            return True
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if w > 4:
                self._copy()
            else:
                self.rect = [0, 0, self.img_w, self.img_h]
                self._copy()
            return True
        self.selecting = True
        self.rect = [ix, iy, ix, iy]
        self.da.queue_draw()
        return True

    def _on_motion(self, widget, ev):
        if self._dup_event(ev):
            return True
        ix, iy = self._to_img(widget, ev)
        if self.selecting:
            self.rect[2], self.rect[3] = ix, iy
            self.da.queue_draw()
            return True
        if self.draft is not None:
            if self.draft.kind == "pen":
                self.draft.points.append((ix, iy))
            elif len(self.draft.points) == 1:
                self.draft.points.append((ix, iy))
            else:
                self.draft.points[-1] = (ix, iy)
            self.da.queue_draw()
        return True

    def _on_release(self, _w, ev):
        if self._dup_event(ev):
            return True
        if ev.button != 1:
            return False
        if self.draft is not None:
            if len(self.draft.points) >= 2:
                self.strokes.append(self.draft)
                self.redo.clear()
            self.draft = None
        self.selecting = False
        x, y, w, h = self._norm()
        self.rect = [x, y, x + w, y + h]
        self.da.queue_draw()
        return True

    def _mosaic_box(self, pts: list[tuple[float, float]]) -> tuple[int, int, int, int] | None:
        x1, y1 = pts[0]
        x2, y2 = pts[-1]
        x = max(0, int(min(x1, x2)))
        y = max(0, int(min(y1, y2)))
        w = min(self.img_w - x, int(abs(x2 - x1)))
        h = min(self.img_h - y, int(abs(y2 - y1)))
        if w < 2 or h < 2:
            return None
        return x, y, w, h

    def _draw_mosaic(self, cr, pts: list[tuple[float, float]]) -> None:
        box = self._mosaic_box(pts)
        if box is None:
            return
        x, y, w, h = box
        block = 14
        small_w = max(1, (w + block - 1) // block)
        small_h = max(1, (h + block - 1) // block)
        try:
            src = self.pixbuf.new_subpixbuf(x, y, w, h)
            tiny = src.scale_simple(small_w, small_h, GdkPixbuf.InterpType.NEAREST)
            pixelated = tiny.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)
        except Exception:
            cr.set_source_rgb(0.35, 0.36, 0.38)
            cr.rectangle(x, y, w, h)
            cr.fill()
            return
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, pixelated, x, y)
        cr.paint()
        cr.restore()

    def _bar_action(self, i: int) -> None:
        tools = ["rect", "ellipse", "arrow", "pen", "mosaic", "text"]
        if i < 6:
            self.tool = tools[i]
        elif i == 6:
            if self.strokes:
                self.redo.append(self.strokes.pop())
            self.da.queue_draw()
        elif i == 7:
            self._save()
        elif i == 8:
            self._copy()
        else:
            self._cancel()

    def _add_text(self, x, y) -> None:
        dialog = Gtk.Dialog(title="文字", transient_for=self.win, flags=0)
        dialog.add_button("确定", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        dialog.get_content_area().pack_start(entry, True, True, 8)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            text = entry.get_text().strip()
            if text:
                self.strokes.append(Stroke("text", self.color, self.pen_w, [(x, y)], text=text))
                self.redo.clear()
        dialog.destroy()
        self.da.queue_draw()

    def _export_pixbuf(self) -> GdkPixbuf.Pixbuf:
        x, y, w, h = self._norm()
        if w < 2 or h < 2:
            x, y, w, h = 0, 0, self.img_w, self.img_h
        x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
        x = max(0, min(x, self.img_w - 1))
        y = max(0, min(y, self.img_h - 1))
        w = min(w, self.img_w - x)
        h = min(h, self.img_h - y)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.img_w, self.img_h)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        for stroke in self.strokes:
            self._draw_stroke(cr, stroke)
        full = Gdk.pixbuf_get_from_surface(surface, 0, 0, self.img_w, self.img_h)
        if full is None:
            return self.pixbuf.new_subpixbuf(x, y, w, h).copy()
        return full.new_subpixbuf(x, y, w, h).copy()

    def _copy_to_clipboard(self, pix: GdkPixbuf.Pixbuf) -> QImage:
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_image(pix)
        clip.store()
        return pixbuf_to_qimage(pix)

    def _copy(self) -> None:
        image = self._copy_to_clipboard(self._export_pixbuf())
        self._finish(image)

    def _save(self) -> None:
        pix = self._export_pixbuf()
        dialog = Gtk.FileChooserNative.new(
            "保存截图",
            self.win,
            Gtk.FileChooserAction.SAVE,
            "保存",
            "取消",
        )
        dialog.set_current_folder(str(pictures_dir()))
        dialog.set_current_name(datetime.now().strftime("截图-%Y%m%d-%H%M%S.png"))
        dialog.set_do_overwrite_confirmation(True)
        filt = Gtk.FileFilter()
        filt.set_name("PNG 图片")
        filt.add_pattern("*.png")
        dialog.add_filter(filt)
        if dialog.run() != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        path = dialog.get_filename() or ""
        dialog.destroy()
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        pix.savev(path, "png", [], [])
        image = self._copy_to_clipboard(pix)
        self._finish(image)

    def _cancel(self) -> None:
        self._finish(None)

    def destroy_quiet(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._seat is not None:
            try:
                self._seat.ungrab()
            except Exception:
                pass
            self._seat = None
        try:
            self.win.hide()
            self.win.destroy()
        except Exception:
            pass

    def _finish(self, image: QImage | None) -> None:
        self.session.finish(image)

    def _on_key(self, _w, ev):
        key = Gdk.keyval_name(ev.keyval) or ""
        if key in {"Escape"}:
            self.session.finish(None)
            return True
        if key in {"Return", "KP_Enter"}:
            chosen = [ov for ov in self.session.overlays if ov._norm()[2] > 2]
            target = self.session.active if self.session.active in chosen else (chosen[-1] if chosen else self)
            target._copy()
            return True
        if key in {"s", "S"} and ev.state & Gdk.ModifierType.CONTROL_MASK:
            self._save()
            return True
        if key == "z" and ev.state & Gdk.ModifierType.CONTROL_MASK:
            if self.strokes:
                self.redo.append(self.strokes.pop())
                self.da.queue_draw()
            return True
        return False

    def _on_delete(self, *_a):
        self._cancel()
        return True


class OverlaySession(QObject):
    finished = pyqtSignal()

    def __init__(self, shots: list[ScreenShot]):
        super().__init__()
        self.pins: list = []
        self.result: QImage | None = None
        self._emitted = False
        self.active: GtkOverlay | None = None
        self.overlays = [GtkOverlay(shot, self) for shot in shots]
        if self.overlays:
            self.active = self.overlays[0]

    def note_active(self, overlay: GtkOverlay) -> None:
        self.active = overlay

    def finish(self, image: QImage | None) -> None:
        if self._emitted:
            return
        self._emitted = True
        self.result = image
        for overlay in self.overlays:
            overlay.destroy_quiet()
        self.finished.emit()

    def close_all(self) -> None:
        self.finish(None)
