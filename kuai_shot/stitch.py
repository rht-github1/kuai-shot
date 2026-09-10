from __future__ import annotations

from PyQt5.QtGui import QColor, QImage, QPainter


def _row_sig(image: QImage, y: int) -> tuple[int, ...]:
    w = image.width()
    parts: list[int] = []
    for t in range(3):
        x0 = t * w // 3
        x1 = max(x0 + 1, (t + 1) * w // 3)
        step = max(1, (x1 - x0) // 16)
        r = g = b = n = 0
        for x in range(x0, x1, step):
            c = image.pixelColor(x, y)
            r += c.red()
            g += c.green()
            b += c.blue()
            n += 1
        n = max(1, n)
        parts.extend((r // n, g // n, b // n))
    return tuple(parts)


def _sig_diff(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(abs(x - y) for x, y in zip(a, b))


def _luma_of_sig(sig: tuple[int, ...]) -> int:
    n = max(1, len(sig) // 3)
    acc = 0
    for i in range(0, len(sig), 3):
        acc += (sig[i] * 3 + sig[i + 1] * 6 + sig[i + 2]) // 10
    return acc // n


def frame_usable(image: QImage | None) -> bool:
    if image is None or image.isNull() or image.width() < 8 or image.height() < 8:
        return False
    w, h = image.width(), image.height()
    step_x = max(1, w // 20)
    step_y = max(1, h // 16)
    total = dark = 0
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            c = image.pixelColor(x, y)
            total += 1
            if c.red() < 10 and c.green() < 10 and c.blue() < 10:
                dark += 1
    return total > 0 and dark / total <= 0.92


def overlap_rows(top: QImage, bottom: QImage) -> int:
    """How many rows at the bottom of `top` match the top of `bottom`."""
    if top.isNull() or bottom.isNull():
        return 0
    if top.width() < 8 or bottom.width() < 8:
        return 0
    h1, h2 = top.height(), bottom.height()
    lo = min(16, h1 // 6, h2 // 6)
    hi = min(h1, h2) * 2 // 5
    if hi <= lo:
        return 0
    bottom_rows = [_row_sig(bottom, y) for y in range(0, min(hi, h2))]
    best, best_score = 0, 10**9
    for ov in range(hi, lo - 1, -2):
        score = 0
        for i in range(ov):
            score += _sig_diff(_row_sig(top, h1 - ov + i), bottom_rows[i])
        score //= ov
        if score < best_score:
            best, best_score = ov, score
    if best_score > 36:
        return 0
    dark = 0
    samples = 0
    for i in range(0, best, 2):
        samples += 1
        if _luma_of_sig(_row_sig(top, h1 - best + i)) < 18:
            dark += 1
    if samples and dark / samples > 0.85:
        return 0
    return best


def frames_similar(a: QImage | None, b: QImage | None, limit: int = 14) -> bool:
    if a is None or b is None or a.isNull() or b.isNull():
        return False
    if a.width() < 4 or b.width() < 4:
        return False
    aw, ah = a.width(), a.height()
    bw, bh = b.width(), b.height()
    w, h = min(aw, bw), min(ah, bh)
    step_x = max(1, w // 16)
    step_y = max(1, h // 12)
    total = acc = 0
    for y in range(0, h, step_y):
        ya = min(ah - 1, int(y * ah / h))
        yb = min(bh - 1, int(y * bh / h))
        for x in range(0, w, step_x):
            xa = min(aw - 1, int(x * aw / w))
            xb = min(bw - 1, int(x * bw / w))
            ca, cb = a.pixelColor(xa, ya), b.pixelColor(xb, yb)
            acc += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue())
            total += 1
    return total > 0 and (acc // total) <= limit


def _col_sig(image: QImage, x: int) -> tuple[int, ...]:
    h = image.height()
    parts: list[int] = []
    for t in range(3):
        y0 = t * h // 3
        y1 = max(y0 + 1, (t + 1) * h // 3)
        step = max(1, (y1 - y0) // 16)
        r = g = b = n = 0
        for y in range(y0, y1, step):
            c = image.pixelColor(x, y)
            r += c.red()
            g += c.green()
            b += c.blue()
            n += 1
        n = max(1, n)
        parts.extend((r // n, g // n, b // n))
    return tuple(parts)


def overlap_cols(left: QImage, right: QImage) -> int:
    if left.isNull() or right.isNull():
        return 0
    if left.height() < 8 or right.height() < 8:
        return 0
    w1, w2 = left.width(), right.width()
    lo = min(16, w1 // 6, w2 // 6)
    hi = min(w1, w2) * 2 // 5
    if hi <= lo:
        return 0
    right_cols = [_col_sig(right, x) for x in range(0, min(hi, w2))]
    best, best_score = 0, 10**9
    for ov in range(hi, lo - 1, -2):
        score = 0
        for i in range(ov):
            score += _sig_diff(_col_sig(left, w1 - ov + i), right_cols[i])
        score //= ov
        if score < best_score:
            best, best_score = ov, score
    if best_score > 36:
        return 0
    return best


def stitch_vertical(frames: list[QImage]) -> QImage | None:
    valid = [f.convertToFormat(QImage.Format_RGB32) for f in frames if frame_usable(f)]
    if not valid:
        valid = [f.convertToFormat(QImage.Format_RGB32) for f in frames if f is not None and not f.isNull()]
    if not valid:
        return None
    acc = valid[0]
    for nxt in valid[1:]:
        if nxt.width() != acc.width():
            nxt = nxt.scaled(acc.width(), max(1, int(nxt.height() * acc.width() / max(1, nxt.width()))))
        ov = overlap_rows(acc, nxt)
        add = nxt.copy(0, ov, nxt.width(), max(1, nxt.height() - ov))
        out = QImage(acc.width(), acc.height() + add.height(), QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        painter = QPainter(out)
        painter.drawImage(0, 0, acc)
        painter.drawImage(0, acc.height(), add)
        painter.end()
        acc = out
    return acc


def stitch_horizontal(frames: list[QImage]) -> QImage | None:
    valid = [f.convertToFormat(QImage.Format_RGB32) for f in frames if frame_usable(f)]
    if not valid:
        valid = [f.convertToFormat(QImage.Format_RGB32) for f in frames if f is not None and not f.isNull()]
    if not valid:
        return None
    acc = valid[0]
    for nxt in valid[1:]:
        if nxt.height() != acc.height():
            nxt = nxt.scaled(max(1, int(nxt.width() * acc.height() / max(1, nxt.height()))), acc.height())
        ov = overlap_cols(acc, nxt)
        add = nxt.copy(ov, 0, max(1, nxt.width() - ov), nxt.height())
        out = QImage(acc.width() + add.width(), acc.height(), QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        painter = QPainter(out)
        painter.drawImage(0, 0, acc)
        painter.drawImage(acc.width(), 0, add)
        painter.end()
        acc = out
    return acc


def stitch_long(frames: list[QImage], axis: str = "v") -> QImage | None:
    if axis == "h":
        return stitch_horizontal(frames)
    return stitch_vertical(frames)
