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


def _prep_pair(prev: QImage, nxt: QImage, axis: str) -> tuple[QImage, QImage] | None:
    if prev is None or nxt is None or prev.isNull() or nxt.isNull():
        return None
    a = prev.convertToFormat(QImage.Format_RGB32)
    b = nxt.convertToFormat(QImage.Format_RGB32)
    if axis == "h":
        if a.height() != b.height() and a.height() > 0:
            b = b.scaled(max(1, int(b.width() * a.height() / max(1, b.height()))), a.height())
    elif a.width() != b.width() and a.width() > 0:
        b = b.scaled(a.width(), max(1, int(b.height() * a.width() / max(1, b.width()))))
    if a.width() < 8 or a.height() < 8 or b.width() < 8 or b.height() < 8:
        return None
    return a, b


def estimate_shift(prev: QImage, nxt: QImage, axis: str = "v") -> int | None:
    """How far content moved from prev to nxt.

    Vertical: +shift means content moved up, new rows are at the bottom of nxt.
    Horizontal: +shift means content moved left, new columns are at the right of nxt.
    None means the two frames cannot be aligned.
    """
    pair = _prep_pair(prev, nxt, axis)
    if pair is None:
        return None
    a, b = pair
    kind = "h" if axis == "h" else "v"
    candidates = _profile_candidates(_edges(_luma_profile(a, kind)), _edges(_luma_profile(b, kind)))
    if not candidates:
        candidates = _profile_candidates(_luma_profile(a, kind), _luma_profile(b, kind))
    if not candidates:
        return None
    floor = candidates[0][1]
    for delta, score in candidates:
        if score > floor + 3:
            break
        if verify_shift(a, b, delta, kind):
            return delta
    return None


def canvas_viewport(canvas: QImage | None, frame: QImage, axis: str = "v") -> QImage | None:
    if canvas is None or canvas.isNull() or frame is None or frame.isNull():
        return None
    if axis == "h":
        if canvas.height() != frame.height() and canvas.height() > 0:
            frame = frame.scaled(
                max(1, int(frame.width() * canvas.height() / max(1, frame.height()))),
                canvas.height(),
            )
        if canvas.width() < frame.width():
            return canvas.copy()
        return canvas.copy(canvas.width() - frame.width(), 0, frame.width(), canvas.height())
    if canvas.width() != frame.width() and canvas.width() > 0:
        frame = frame.scaled(canvas.width(), max(1, int(frame.height() * canvas.width() / max(1, frame.width()))))
    if canvas.height() < frame.height():
        return canvas.copy()
    return canvas.copy(0, canvas.height() - frame.height(), canvas.width(), frame.height())


def verify_shift(prev: QImage, nxt: QImage, shift: int, axis: str = "v") -> bool:
    pair = _prep_pair(prev, nxt, axis)
    if pair is None:
        return False
    a, b = pair
    if shift == 0:
        return _band_diff(a, b, 0, 0, a.height() if axis != "h" else a.width(), axis) <= 14
    if axis == "h":
        w = min(a.width(), b.width())
        if shift > 0:
            ov = w - shift
            return ov >= 12 and _band_diff(a, b, shift, 0, ov, "h") <= 16
        ov = w + shift
        return ov >= 12 and _band_diff(a, b, 0, -shift, ov, "h") <= 16
    h = min(a.height(), b.height())
    if shift > 0:
        ov = h - shift
        return ov >= 12 and _band_diff(a, b, shift, 0, ov, "v") <= 16
    ov = h + shift
    return ov >= 12 and _band_diff(a, b, 0, -shift, ov, "v") <= 16


def _band_diff(prev: QImage, nxt: QImage, prev_off: int, nxt_off: int, length: int, axis: str) -> int:
    pa = _luma_profile(prev, axis)
    pb = _luma_profile(nxt, axis)
    acc = n = 0
    for i in range(max(0, length)):
        ia, ib = prev_off + i, nxt_off + i
        if 0 <= ia < len(pa) and 0 <= ib < len(pb):
            acc += abs(pa[ia] - pb[ib])
            n += 1
    return acc // max(1, n)


def _luma_profile(image: QImage, axis: str) -> list[int]:
    w, h = image.width(), image.height()
    profile: list[int] = []
    if axis == "h":
        y0, y1 = h // 8, h - h // 8
        step_y = max(1, (y1 - y0) // 20)
        for x in range(w):
            acc = n = 0
            for y in range(y0, max(y0 + 1, y1), step_y):
                c = image.pixelColor(x, y)
                acc += (c.red() * 3 + c.green() * 6 + c.blue()) // 10
                n += 1
            profile.append(acc // max(1, n))
        return profile
    x0, x1 = w // 8, w - w // 8
    step_x = max(1, (x1 - x0) // 20)
    for y in range(h):
        acc = n = 0
        for x in range(x0, max(x0 + 1, x1), step_x):
            c = image.pixelColor(x, y)
            acc += (c.red() * 3 + c.green() * 6 + c.blue()) // 10
            n += 1
        profile.append(acc // max(1, n))
    return profile


def _edges(profile: list[int]) -> list[int]:
    if len(profile) < 3:
        return profile
    return [profile[i] - profile[i - 1] for i in range(1, len(profile))]


def _profile_candidates(prev: list[int], nxt: list[int]) -> list[tuple[int, int]]:
    n = min(len(prev), len(nxt))
    if n < 16:
        return []
    min_ov = max(24, n // 3)
    found: list[tuple[int, int]] = []
    for delta in range(-(n - min_ov), n - min_ov + 1):
        score = count = 0
        for i in range(n):
            j = i + delta
            if 0 <= j < n:
                score += abs(nxt[i] - prev[j])
                count += 1
        if count < min_ov:
            continue
        score //= count
        if score <= 24:
            found.append((delta, score))
    found.sort(key=lambda item: (item[1], abs(item[0])))
    return found


def _stack(first: QImage, second: QImage, axis: str) -> QImage:
    if axis == "h":
        out = QImage(first.width() + second.width(), first.height(), QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        painter = QPainter(out)
        painter.drawImage(0, 0, first)
        painter.drawImage(first.width(), 0, second)
        painter.end()
        return out
    out = QImage(first.width(), first.height() + second.height(), QImage.Format_RGB32)
    out.fill(QColor(0, 0, 0))
    painter = QPainter(out)
    painter.drawImage(0, 0, first)
    painter.drawImage(0, first.height(), second)
    painter.end()
    return out


def _gap_strip(image: QImage, axis: str, size: int = 4) -> QImage:
    if axis == "h":
        strip = QImage(size, image.height(), QImage.Format_RGB32)
    else:
        strip = QImage(image.width(), size, QImage.Format_RGB32)
    strip.fill(QColor(255, 141, 26))
    return strip


def extend_unwrapped(canvas: QImage, nxt: QImage, shift: int | None, axis: str = "v") -> QImage:
    """Grow canvas by the newly revealed strip of nxt. None shift inserts a visible gap."""
    if canvas is None or canvas.isNull():
        return nxt.convertToFormat(QImage.Format_RGB32) if nxt is not None and not nxt.isNull() else canvas
    if nxt is None or nxt.isNull():
        return canvas
    frame = nxt.convertToFormat(QImage.Format_RGB32)
    if axis == "h" and frame.height() != canvas.height() and frame.height() > 0:
        frame = frame.scaled(max(1, int(frame.width() * canvas.height() / frame.height())), canvas.height())
    elif axis != "h" and frame.width() != canvas.width() and frame.width() > 0:
        frame = frame.scaled(canvas.width(), max(1, int(frame.height() * canvas.width() / frame.width())))
    if shift is None:
        return _stack(_stack(canvas, _gap_strip(frame, axis), axis), frame, axis)
    if shift == 0:
        return canvas
    if axis == "h":
        if shift > 0:
            add = frame.copy(max(0, frame.width() - shift), 0, min(shift, frame.width()), frame.height())
            return _stack(canvas, add, "h")
        add = frame.copy(0, 0, min(-shift, frame.width()), frame.height())
        return _stack(add, canvas, "h")
    if shift > 0:
        add = frame.copy(0, max(0, frame.height() - shift), frame.width(), min(shift, frame.height()))
        return _stack(canvas, add, "v")
    add = frame.copy(0, 0, frame.width(), min(-shift, frame.height()))
    return _stack(add, canvas, "v")
