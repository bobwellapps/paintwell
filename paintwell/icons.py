"""Vector icons drawn with QPainter at runtime, so there are no image files to ship.

Stroke widths go through _thin() so detail lines stay crisp at toolbar size, and
_tone_down() mutes the colours slightly after drawing.
"""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QIcon, QPixmap, QPainter, QPen, QColor, QBrush, QPainterPath,
                           QLinearGradient, QPolygonF, QFont, QConicalGradient, QRadialGradient)

_cache = {}

OUT = QColor(52, 52, 60)
BLUE = QColor(40, 120, 215)
LBLUE = QColor(150, 195, 245)
RED = QColor(215, 60, 50)
GREEN = QColor(60, 160, 70)
YEL = QColor(245, 195, 50)
ORANGE = QColor(235, 130, 40)
PAPER = QColor(250, 250, 252)
GREY = QColor(150, 150, 158)


_dark = False


def set_dark(dark):
    """Use light outlines on dark themes. Call before any icon is created."""
    global OUT, PAPER, _dark
    _dark = bool(dark)
    OUT = QColor(218, 218, 225) if dark else QColor(52, 52, 60)
    # white "paper" fills would glare (and hide the light outlines) on a dark theme
    PAPER = QColor(74, 74, 82) if dark else QColor(250, 250, 252)
    _cache.clear()
    _disk.clear()
    _disk_state["loaded"] = False


# --- Icon cache on disk ---
# Drawing every icon at four sizes takes a noticeable part of startup, so the finished
# pixels are saved once and reloaded on later starts. The file name includes a hash of the
# drawing code, the theme and the logo, so any change simply produces a new cache.
_disk = {}
_disk_state = {"loaded": False, "dirty": False}


def _cache_path():
    import hashlib
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.md5()
    for name in ("icons.py", "icons_menu.py", os.path.join("resources", "paintwell_icon.png")):
        try:
            with open(os.path.join(here, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(name.encode())
    h.update(b"dark" if _dark else b"light")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "paintwell", f"icons-{h.hexdigest()[:12]}.bin")


def _load_disk():
    _disk_state["loaded"] = True
    import pickle
    import zlib
    try:
        with open(_cache_path(), "rb") as fh:
            data = pickle.loads(zlib.decompress(fh.read()))
        if isinstance(data, dict):
            _disk.update(data)
    except Exception:
        pass                                    # no cache yet, or an unreadable one


def save_cache():
    """Write newly drawn icons to the disk cache (called once the window is up)."""
    if not _disk_state["dirty"]:
        return
    import os
    import pickle
    import zlib
    path = _cache_path()
    try:
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(zlib.compress(pickle.dumps(dict(_disk), protocol=pickle.HIGHEST_PROTOCOL), 1))
        os.replace(tmp, path)
        _disk_state["dirty"] = False
        # keep the newest few (light and dark themes), remove ones left by older versions
        old = sorted((os.path.join(folder, n) for n in os.listdir(folder)
                      if n.startswith("icons-") and n.endswith(".bin")), key=os.path.getmtime)
        for p in old[:-4]:
            os.remove(p)
    except OSError:
        pass


def _to_disk(key, pm):
    img = pm.toImage()
    _disk[key] = (img.width(), img.height(), int(img.format().value), img.bytesPerLine(),
                  bytes(img.constBits()), pm.devicePixelRatio())
    _disk_state["dirty"] = True


def _from_disk(key):
    from PySide6.QtGui import QImage
    w, h, fmt, bpl, data, dpr = _disk[key]
    img = QImage(data, w, h, bpl, QImage.Format(fmt))
    pm = QPixmap.fromImage(img.copy())
    pm.setDevicePixelRatio(dpr)
    return pm


def _thin(w):
    """Fine line weight: detail lines about half the original weight, bold strokes 60 %."""
    return max(0.7, w * 0.5) if w < 2.0 else w * 0.6


def _pen(c=None, w=1.6, style=Qt.SolidLine):
    p = QPen(OUT if c is None else c, _thin(w), style)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _dash(c=None, w=1.5):
    t = _thin(w)
    p = QPen(OUT if c is None else c, t)
    p.setCapStyle(Qt.FlatCap)
    # dash lengths are in multiples of the pen width: keep the same dash size as before
    p.setDashPattern([2.2 * w / t, 1.8 * w / t])
    return p


def _arrow4(p, cx, cy, r, c=None):
    c = OUT if c is None else c
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    a = 2.6
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tip = QPointF(cx + dx * r, cy + dy * r)
        if dx:
            pts = [tip, QPointF(tip.x() - dx * a, tip.y() - a), QPointF(tip.x() - dx * a, tip.y() + a)]
        else:
            pts = [tip, QPointF(tip.x() - a, tip.y() - dy * a), QPointF(tip.x() + a, tip.y() - dy * a)]
        p.setBrush(c)
        p.drawPolygon(QPolygonF(pts))


def _pointer(p, tx, ty, scale=0.72):
    """Mouse pointer (arrow cursor) with its tip at (tx, ty): white with a dark outline."""
    pts = [(0, 0), (0, 14), (3.4, 10.8), (5.9, 16.2), (8.2, 15.2), (5.8, 9.9), (10.2, 9.9)]
    poly = QPolygonF([QPointF(tx + x * scale, ty + y * scale) for x, y in pts])
    pen = _pen(OUT, 1.5)
    pen.setJoinStyle(Qt.MiterJoin)
    p.setPen(pen); p.setBrush(QColor(255, 255, 255))
    p.drawPolygon(poly)


def _move_marks(p, cx, cy, c):
    """Small four-way arrow marks in the middle of a box (shows it can be moved)."""
    r = 3.6
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    a = 1.6
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tx, ty = cx + dx * r, cy + dy * r
        if dx:
            p.drawLine(QPointF(tx, ty), QPointF(tx - dx * a, ty - a)); p.drawLine(QPointF(tx, ty), QPointF(tx - dx * a, ty + a))
        else:
            p.drawLine(QPointF(tx, ty), QPointF(tx - a, ty - dy * a)); p.drawLine(QPointF(tx, ty), QPointF(tx + a, ty - dy * a))


def _page(p, x, y, w, h, fill=None):
    path = QPainterPath()
    f = 4
    path.moveTo(x, y)
    path.lineTo(x + w - f, y)
    path.lineTo(x + w, y + f)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.setPen(_pen(OUT, 1.3))
    p.setBrush(PAPER if fill is None else fill)
    p.drawPath(path)


def _draw(name, p):
    """All icons drawn in a 24x24 box."""
    if name == "rect_select":
        p.setPen(_dash(OUT, 1.6)); p.setBrush(QColor(40, 120, 215, 40))
        p.drawRect(QRectF(3.5, 5.5, 17, 13))
    elif name == "ellipse_select":
        p.setPen(_dash(OUT, 1.6)); p.setBrush(QColor(40, 120, 215, 40))
        p.drawEllipse(QRectF(3, 5, 18, 14))
    elif name == "lasso":
        path = QPainterPath(QPointF(6, 18))
        path.cubicTo(1, 12, 4, 4, 12, 4)
        path.cubicTo(21, 4, 23, 12, 16, 15)
        path.cubicTo(12, 17, 9, 15, 8, 18)
        p.setPen(_dash(OUT, 1.6)); p.setBrush(QColor(40, 120, 215, 40))
        p.drawPath(path)
        p.setPen(_pen(OUT, 1.4)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(8, 18), QPointF(9, 22))
    elif name == "magnetic_lasso":
        path = QPainterPath(QPointF(6, 18))
        path.cubicTo(1, 12, 4, 4, 12, 4)
        path.cubicTo(21, 4, 23, 12, 16, 15)
        path.cubicTo(12, 17, 9, 15, 8, 18)
        p.setPen(_dash(OUT, 1.6)); p.setBrush(QColor(40, 120, 215, 40))
        p.drawPath(path)
        p.setPen(_pen(OUT, 1.4)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(8, 18), QPointF(9, 22))
        # small horseshoe magnet, bottom right
        p.save(); p.translate(17.5, 18.5); p.rotate(-35)
        mag = QPainterPath()
        mag.moveTo(-4, -4.5); mag.lineTo(-4, 0.5); mag.arcTo(QRectF(-4, -3.5, 8, 8), 180, 180)
        mag.lineTo(4, -4.5)
        p.setPen(QPen(RED, 3, Qt.SolidLine, Qt.FlatCap)); p.drawPath(mag)
        p.setPen(QPen(QColor(225, 225, 230), 3, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QPointF(-4, -4.5), QPointF(-4, -2.8)); p.drawLine(QPointF(4, -4.5), QPointF(4, -2.8))
        p.restore()
    elif name == "magic_wand":
        p.setPen(_pen(OUT, 3)); p.drawLine(QPointF(4, 20), QPointF(14, 10))
        p.setPen(_pen(PAPER, 1.4)); p.drawLine(QPointF(12.5, 11.5), QPointF(14, 10))
        p.setPen(_pen(YEL.darker(115), 1.3)); p.setBrush(YEL)
        star = QPolygonF()
        for i in range(10):
            r = 5.2 if i % 2 == 0 else 2.2
            a = -math.pi / 2 + i * math.pi / 5
            star.append(QPointF(17.5 + r * math.cos(a), 6.5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "move_pixels":
        # a block of pixels with the pointer dragging it
        p.setPen(_pen(BLUE.darker(115), 1.4)); p.setBrush(LBLUE)
        p.drawRoundedRect(QRectF(3.5, 3.5, 13, 13), 1.5, 1.5)
        _pointer(p, 10.5, 10.5, 0.8)
    elif name == "move_selection":
        # just the outline, with the pointer dragging it
        p.setPen(_dash(OUT, 1.5)); p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(3.5, 3.5, 13, 13))
        _pointer(p, 10.5, 10.5, 0.8)
    elif name == "zoom":
        p.setPen(_pen(OUT, 2.2)); p.setBrush(QColor(200, 225, 250))
        p.drawEllipse(QRectF(3, 3, 13, 13))
        p.setPen(_pen(OUT, 3.4)); p.drawLine(QPointF(14.5, 14.5), QPointF(20.5, 20.5))
        p.setPen(_pen(OUT, 1.5)); p.drawLine(QPointF(6.5, 9.5), QPointF(12.5, 9.5))
        p.drawLine(QPointF(9.5, 6.5), QPointF(9.5, 12.5))
    elif name == "pan":
        # open hand: palm, four slim fingers and a thumb angled up from the side of the palm
        from PySide6.QtGui import QTransform

        def rr(x, y, w, h, r):
            q = QPainterPath(); q.addRoundedRect(QRectF(x, y, w, h), r, r); return q
        hand = rr(7.2, 10.5, 10.6, 10.5, 4)
        for x, top in ((7.3, 6.0), (10.0, 3.9), (12.7, 3.6), (15.3, 5.4)):
            hand = hand.united(rr(x, top, 2.45, 9.0, 1.22))
        thumb = rr(-1.3, -6.4, 2.6, 7.4, 1.3)
        hand = hand.united(QTransform().translate(7.9, 16.8).rotate(-34).map(thumb))
        p.setPen(_pen(OUT, 1.35)); p.setBrush(QColor(248, 222, 196))
        p.drawPath(hand)
        p.setPen(_pen(OUT, 1.1))                       # the gaps between the fingers
        for x, top in ((9.87, 6.6), (12.6, 5.6), (15.28, 6.6)):
            p.drawLine(QPointF(x, top), QPointF(x, 11.2))
    elif name == "bucket":
        p.save(); p.translate(11, 13); p.rotate(-30)
        p.setPen(_pen(OUT, 1.4)); p.setBrush(QColor(210, 214, 222))
        p.drawRect(QRectF(-6, -5, 12, 11))
        p.setBrush(QColor(170, 175, 185)); p.drawEllipse(QRectF(-6, -7, 12, 4))
        p.setBrush(Qt.NoBrush); p.drawArc(QRectF(-4, -12, 8, 10), 0, 180 * 16)
        p.restore()
        p.setPen(Qt.NoPen); p.setBrush(BLUE)
        path = QPainterPath(QPointF(19.5, 12)); path.cubicTo(22, 16, 22.5, 19, 19.5, 19.5)
        path.cubicTo(16.5, 19, 17, 16, 19.5, 12); p.drawPath(path)
    elif name == "gradient":
        g = QLinearGradient(3, 0, 21, 0); g.setColorAt(0, QColor(30, 30, 40)); g.setColorAt(1, PAPER)
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QBrush(g)); p.drawRect(QRectF(3.5, 5.5, 17, 13))
    elif name == "brush":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(170, 115, 60))
        p.save(); p.translate(12, 12); p.rotate(45)
        p.drawRoundedRect(QRectF(-1.8, -11, 3.6, 11), 1.5, 1.5)
        p.setBrush(QColor(190, 190, 200)); p.drawRect(QRectF(-2.4, -1, 4.8, 3.5))
        p.setBrush(BLUE)
        path = QPainterPath(QPointF(-2.4, 2.5)); path.lineTo(2.4, 2.5)
        path.cubicTo(2.6, 7, 1, 9.5, 0, 11); path.cubicTo(-1.2, 9, -2.6, 7, -2.4, 2.5)
        p.drawPath(path); p.restore()
    elif name == "eraser":
        p.save(); p.translate(12, 12); p.rotate(-40)
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(240, 130, 150)); p.drawRect(QRectF(-8, -4, 8, 8))
        p.setBrush(PAPER); p.drawRect(QRectF(0, -4, 8, 8)); p.restore()
        p.setPen(_pen(GREY, 1.2)); p.drawLine(QPointF(4, 21), QPointF(20, 21))
    elif name == "remove":
        # brush tip sweeping away a dotted blemish, with a small sparkle
        p.setPen(_pen(GREY, 1.1)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(3, 13, 8, 8))
        p.setPen(_dash(GREY, 1.0)); p.drawLine(QPointF(7, 13.5), QPointF(7, 20.5))
        p.save(); p.translate(15, 9); p.rotate(40)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(170, 115, 60)); p.drawRoundedRect(QRectF(-1.7, -9, 3.4, 9), 1.5, 1.5)
        p.setBrush(QColor(110, 110, 120)); p.drawRect(QRectF(-2.3, -0.5, 4.6, 2.5))
        p.setBrush(QColor(240, 120, 150)); p.drawRoundedRect(QRectF(-2.6, 2, 5.2, 6), 2.4, 2.4)
        p.restore()
        p.setPen(_pen(YEL.darker(115), 1.0)); p.setBrush(YEL)
        star = QPolygonF()
        for i in range(8):
            r = 3.2 if i % 2 == 0 else 1.1
            a = -math.pi / 2 + i * math.pi / 4
            star.append(QPointF(19.5 + r * math.cos(a), 18.5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "red_eye":
        eye = QPainterPath(QPointF(2.5, 12))
        eye.quadTo(12, 3, 21.5, 12)
        eye.quadTo(12, 21, 2.5, 12)
        p.setPen(_pen(OUT, 1.3)); p.setBrush(PAPER); p.drawPath(eye)
        p.setBrush(QColor(90, 140, 190)); p.drawEllipse(QPointF(12, 12), 4.6, 4.6)
        p.setPen(Qt.NoPen); p.setBrush(RED); p.drawEllipse(QPointF(12, 12), 2.4, 2.4)
        p.setBrush(QColor(255, 255, 255)); p.drawEllipse(QPointF(10.8, 10.8), 0.9, 0.9)
        p.setPen(_pen(OUT, 1.6))
        p.drawLine(QPointF(17.5, 17.5), QPointF(22, 22)); p.drawLine(QPointF(22, 17.5), QPointF(17.5, 22))
    elif name == "dodge":
        # dodging paddle held up to the light: a pale disc with light rays round it
        p.setPen(_pen(OUT, 1.5)); p.drawLine(QPointF(8.5, 15.5), QPointF(4, 22))
        p.setPen(_pen(YEL.darker(110), 1.2))
        for i in range(8):
            ang = i * math.pi / 4
            p.drawLine(QPointF(13 + 6.6 * math.cos(ang), 9.5 + 6.6 * math.sin(ang)),
                       QPointF(13 + 8.6 * math.cos(ang), 9.5 + 8.6 * math.sin(ang)))
        g = QRadialGradient(QPointF(12, 8.5), 6)
        g.setColorAt(0, QColor(255, 255, 250)); g.setColorAt(1, QColor(250, 225, 140))
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QBrush(g)); p.drawEllipse(QPointF(13, 9.5), 4.8, 4.8)
    elif name == "burn":
        # a flame: orange outside, yellow heart
        outer = QPainterPath(QPointF(12, 2.5))
        outer.cubicTo(13.5, 6.5, 19.5, 9, 19, 15)
        outer.cubicTo(18.6, 19.5, 15.5, 22, 12, 22)
        outer.cubicTo(8.5, 22, 5.2, 19.5, 5, 15.5)
        outer.cubicTo(4.8, 12, 7, 10.2, 8.2, 8)
        outer.cubicTo(8.6, 10, 9.5, 11, 10.3, 11.4)
        outer.cubicTo(10, 8, 10.6, 5, 12, 2.5)
        g = QLinearGradient(QPointF(12, 3), QPointF(12, 22))
        g.setColorAt(0.0, QColor(240, 120, 40)); g.setColorAt(1.0, QColor(215, 60, 35))
        p.setPen(_pen(QColor(150, 50, 30), 1.1)); p.setBrush(QBrush(g)); p.drawPath(outer)
        inner = QPainterPath(QPointF(12.3, 11))
        inner.cubicTo(13.2, 13.5, 16, 15, 15.6, 17.6)
        inner.cubicTo(15.3, 19.6, 13.8, 20.5, 12, 20.5)
        inner.cubicTo(10.2, 20.5, 8.6, 19.3, 8.7, 17.4)
        inner.cubicTo(8.8, 15.6, 10.4, 14.8, 11, 13.4)
        inner.cubicTo(11.3, 14.3, 11.9, 14.8, 12.3, 15)
        inner.cubicTo(11.9, 13.5, 11.9, 12.2, 12.3, 11)
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 215, 80)); p.drawPath(inner)
    elif name == "sponge":
        sp = QPainterPath()
        sp.moveTo(4, 9); sp.cubicTo(7, 6.5, 12, 7.5, 15, 6); sp.cubicTo(18, 4.8, 20, 6, 20.5, 8)
        sp.lineTo(20.5, 17); sp.cubicTo(17, 19.5, 12, 18, 8, 19.5); sp.cubicTo(6, 20.3, 4.3, 19.5, 4, 18)
        sp.closeSubpath()
        p.setPen(_pen(OUT, 1.2)); p.setBrush(YEL); p.drawPath(sp)
        p.setPen(Qt.NoPen); p.setBrush(YEL.darker(135))
        for (hx, hy, hr) in ((8, 11, 1.3), (12.5, 10, 1.0), (16.5, 11.5, 1.4), (10, 15.5, 1.1), (15, 15.5, 0.9), (18.5, 15, 0.8)):
            p.drawEllipse(QPointF(hx, hy), hr, hr)
    elif name == "magic_eraser":
        p.save(); p.translate(11, 13); p.rotate(-40)
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(240, 130, 150)); p.drawRect(QRectF(-8, -4, 8, 8))
        p.setBrush(PAPER); p.drawRect(QRectF(0, -4, 8, 8)); p.restore()
        p.setPen(_pen(GREY, 1.2)); p.drawLine(QPointF(3, 21.5), QPointF(15, 21.5))
        p.setPen(_pen(YEL.darker(115), 1.1)); p.setBrush(YEL)
        star = QPolygonF()
        for i in range(10):
            r = 4.4 if i % 2 == 0 else 1.9
            a = -math.pi / 2 + i * math.pi / 5
            star.append(QPointF(18.5 + r * math.cos(a), 5.5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "pencil":
        p.save(); p.translate(12, 12); p.rotate(45)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(YEL); p.drawRect(QRectF(-2.5, -10, 5, 13))
        p.setBrush(QColor(240, 150, 160)); p.drawRect(QRectF(-2.5, -12, 5, 2.5))
        p.setBrush(QColor(240, 215, 170))
        p.drawPolygon(QPolygonF([QPointF(-2.5, 3), QPointF(2.5, 3), QPointF(0, 9)]))
        p.setBrush(OUT); p.drawPolygon(QPolygonF([QPointF(-0.9, 7), QPointF(0.9, 7), QPointF(0, 9)]))
        p.restore()
    elif name == "picker":
        p.save(); p.translate(12, 12); p.rotate(45)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(80, 80, 90)); p.drawRoundedRect(QRectF(-3, -11, 6, 5), 2, 2)
        p.setBrush(QColor(210, 230, 250)); p.drawRect(QRectF(-1.6, -6, 3.2, 12))
        p.setBrush(BLUE); p.drawRect(QRectF(-1.6, 1, 3.2, 5))
        p.drawPolygon(QPolygonF([QPointF(-1.6, 6), QPointF(1.6, 6), QPointF(0, 10)]))
        p.restore()
    elif name == "clone":
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(170, 115, 60))
        p.drawRoundedRect(QRectF(9, 3, 6, 8), 2.5, 2.5)
        p.setBrush(QColor(110, 110, 120)); p.drawRect(QRectF(5, 11, 14, 4))
        p.setBrush(RED); p.drawRect(QRectF(4, 15, 16, 3))
        p.setPen(_pen(RED, 1.5)); p.drawLine(QPointF(5, 21), QPointF(19, 21))
    elif name == "recolor":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(170, 115, 60))
        p.save(); p.translate(15, 9); p.rotate(45)
        p.drawRoundedRect(QRectF(-1.6, -9, 3.2, 9), 1.5, 1.5)
        p.setBrush(RED); p.drawEllipse(QRectF(-2.5, 0, 5, 6)); p.restore()
        p.setPen(Qt.NoPen); p.setBrush(GREEN); p.drawEllipse(QRectF(3, 13, 8, 8))
        p.setPen(_pen(OUT, 1.4)); p.drawLine(QPointF(12, 18), QPointF(16, 18))
    elif name == "blur":
        # a water drop, soft towards the bottom, like the usual blur-tool symbol
        drop = QPainterPath(QPointF(12, 2.5))
        drop.cubicTo(QPointF(13.5, 7), QPointF(19, 11.5), QPointF(19, 15.5))
        drop.cubicTo(QPointF(19, 19.5), QPointF(15.9, 22), QPointF(12, 22))
        drop.cubicTo(QPointF(8.1, 22), QPointF(5, 19.5), QPointF(5, 15.5))
        drop.cubicTo(QPointF(5, 11.5), QPointF(10.5, 7), QPointF(12, 2.5))
        g = QLinearGradient(QPointF(12, 3), QPointF(12, 22))
        g.setColorAt(0.0, LBLUE.lighter(112)); g.setColorAt(1.0, BLUE)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QBrush(g)); p.drawPath(drop)
        p.setPen(_pen(QColor(255, 255, 255, 200), 1.6)); p.setBrush(Qt.NoBrush)
        hl = QPainterPath(QPointF(8.4, 16)); hl.cubicTo(QPointF(8.4, 18), QPointF(9.6, 19.2), QPointF(11, 19.4))
        p.drawPath(hl)
    elif name == "sharpen":
        # a sharp cone, point down, like the usual sharpen-tool symbol
        g = QLinearGradient(QPointF(6, 0), QPointF(18, 0))
        g.setColorAt(0.0, LBLUE.lighter(110)); g.setColorAt(1.0, BLUE)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QBrush(g))
        p.drawPolygon(QPolygonF([QPointF(5.5, 4), QPointF(18.5, 4), QPointF(12, 21.5)]))
        p.setBrush(QColor(255, 255, 255, 120)); p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(8, 5.5), QPointF(11, 5.5), QPointF(12, 17)]))
        p.setPen(_pen(OUT, 1.2)); p.setBrush(LBLUE.lighter(118))
        p.drawEllipse(QRectF(5.5, 2.3, 13, 3.4))
    elif name == "smudge":
        # a finger dragging a smear of colour
        trail = QPainterPath(QPointF(3.5, 20.5)); trail.cubicTo(6, 18, 9, 18.5, 12, 15.5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(BLUE.red(), BLUE.green(), BLUE.blue(), 110), 4.2, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(trail)
        p.setPen(QPen(BLUE, 2.2, Qt.SolidLine, Qt.RoundCap)); p.drawPath(trail)
        p.save(); p.translate(15.5, 10.5); p.rotate(45)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(240, 200, 165))
        p.drawRoundedRect(QRectF(-3.2, -9, 6.4, 15), 3.2, 3.2)
        p.setBrush(QColor(255, 236, 225)); p.drawRoundedRect(QRectF(-2, 1.2, 4, 3.6), 1.6, 1.6)
        p.restore()
    elif name == "text":
        f = QFont("Serif"); f.setPixelSize(20); f.setBold(True); p.setFont(f)
        p.setPen(OUT); p.drawText(QRectF(0, 0, 24, 24), Qt.AlignCenter, "T")
    elif name == "line":
        path = QPainterPath(QPointF(4, 19)); path.cubicTo(8, 4, 15, 22, 20, 5)
        p.setPen(_pen(BLUE, 2.2)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        p.setPen(_pen(OUT, 1)); p.setBrush(PAPER)
        p.drawEllipse(QPointF(4, 19), 2, 2); p.drawEllipse(QPointF(20, 5), 2, 2)
    elif name == "shapes":
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(130, 185, 245)); p.drawRect(QRectF(3, 9, 10, 10))
        p.setBrush(QColor(250, 180, 90, 230)); p.drawEllipse(QRectF(9, 4, 12, 12))
    # ---- actions
    elif name == "new":
        _page(p, 5, 3, 14, 18)
        p.setPen(_pen(GREEN, 2.2)); p.drawLine(QPointF(17, 14), QPointF(17, 22)); p.drawLine(QPointF(13, 18), QPointF(21, 18))
    elif name == "open":
        p.setPen(_pen(QColor(170, 120, 30), 1.3)); p.setBrush(QColor(240, 195, 90))
        p.drawRect(QRectF(3, 6, 17, 14))
        p.setBrush(QColor(252, 220, 130))
        p.drawPolygon(QPolygonF([QPointF(3, 20), QPointF(7, 11), QPointF(22, 11), QPointF(19.5, 20)]))
    elif name == "save":
        p.setPen(_pen(QColor(25, 70, 140), 1.3)); p.setBrush(BLUE); p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.setBrush(PAPER); p.drawRect(QRectF(7, 4, 10, 6))
        p.setBrush(QColor(220, 225, 235)); p.drawRect(QRectF(7, 13, 10, 7))
    elif name == "cut":
        p.setPen(_pen(OUT, 1.6)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(4, 14, 6, 6)); p.drawEllipse(QRectF(14, 14, 6, 6))
        p.drawLine(QPointF(9, 15), QPointF(17, 3)); p.drawLine(QPointF(15, 15), QPointF(7, 3))
    elif name == "copy":
        _page(p, 3, 3, 12, 15); _page(p, 9, 7, 12, 15, QColor(225, 238, 252))
    elif name == "paste":
        p.setPen(_pen(QColor(120, 85, 40), 1.3)); p.setBrush(QColor(195, 150, 90)); p.drawRoundedRect(QRectF(4, 4, 15, 18), 2, 2)
        p.setBrush(QColor(110, 110, 120)); p.drawRect(QRectF(8, 2.5, 7, 4))
        _page(p, 10, 10, 11, 12)
    elif name == "undo" or name == "redo":
        if name == "redo":
            p.translate(24, 0); p.scale(-1, 1)
        path = QPainterPath(QPointF(7, 10)); path.lineTo(15, 10)
        path.cubicTo(22, 10, 22, 20, 15, 20); path.lineTo(10, 20)
        p.setPen(_pen(BLUE.darker(115), 2.4)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        p.setPen(Qt.NoPen); p.setBrush(BLUE.darker(115))
        p.drawPolygon(QPolygonF([QPointF(2.5, 10), QPointF(9, 4.5), QPointF(9, 15.5)]))
    elif name == "crop":
        p.setPen(_pen(OUT, 2)); p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(7, 2), QPointF(7, 17), QPointF(22, 17)]))
        p.drawPolyline(QPolygonF([QPointF(2, 7), QPointF(17, 7), QPointF(17, 22)]))
    elif name == "deselect":
        p.setPen(_dash(OUT, 1.5)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3.5, 3.5, 14, 14))
        p.setPen(_pen(RED, 2.2)); p.drawLine(QPointF(13, 13), QPointF(21, 21)); p.drawLine(QPointF(21, 13), QPointF(13, 21))
    elif name == "select_all":
        p.setPen(_dash(OUT, 1.5)); p.setBrush(QColor(40, 120, 215, 60)); p.drawRect(QRectF(3.5, 3.5, 17, 17))
    elif name == "grid":
        p.setPen(_pen(OUT, 1.1)); p.setBrush(PAPER); p.drawRect(QRectF(3.5, 3.5, 17, 17))
        for i in (9.17, 14.83):
            p.drawLine(QPointF(i, 3.5), QPointF(i, 20.5)); p.drawLine(QPointF(3.5, i), QPointF(20.5, i))
    elif name == "rulers":
        p.setPen(_pen(OUT, 1.1)); p.setBrush(QColor(245, 225, 160))
        p.drawRect(QRectF(3, 3, 18, 5)); p.drawRect(QRectF(3, 8, 5, 13))
        for i in range(10, 21, 3):
            p.drawLine(QPointF(i, 3), QPointF(i, 6)); p.drawLine(QPointF(3, i), QPointF(6, i))
    elif name == "layer_add" or name == "layers":
        for i, c in enumerate((QColor(150, 190, 240), QColor(190, 215, 248), PAPER)):
            off = 8 - i * 4
            p.setPen(_pen(OUT, 1.2)); p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(12, 2 + off), QPointF(21, 6.5 + off), QPointF(12, 11 + off), QPointF(3, 6.5 + off)]))
        if name == "layer_add":
            p.setPen(_pen(GREEN, 2.4)); p.drawLine(QPointF(19, 15), QPointF(19, 23)); p.drawLine(QPointF(15, 19), QPointF(23, 19))
    elif name in ("layer_delete", "layer_dup", "layer_merge", "layer_props", "layer_up", "layer_down"):
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(170, 205, 245))
        p.drawPolygon(QPolygonF([QPointF(10, 6), QPointF(19, 10.5), QPointF(10, 15), QPointF(1, 10.5)]))
        if name == "layer_delete":
            p.setPen(_pen(RED, 2.4)); p.drawLine(QPointF(15, 15), QPointF(22, 22)); p.drawLine(QPointF(22, 15), QPointF(15, 22))
        elif name == "layer_dup":
            p.setBrush(PAPER); p.drawPolygon(QPolygonF([QPointF(14, 10), QPointF(23, 14.5), QPointF(14, 19), QPointF(5, 14.5)]))
        elif name == "layer_merge":
            p.setPen(_pen(BLUE, 2)); p.drawLine(QPointF(10, 14), QPointF(10, 22))
            p.setBrush(BLUE); p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(6, 18), QPointF(14, 18), QPointF(10, 23)]))
        elif name == "layer_props":
            p.setPen(_pen(OUT, 1)); p.setBrush(PAPER); p.drawRect(QRectF(13, 13, 9, 9))
            p.setPen(_pen(OUT, 1.2))
            for y in (15.5, 17.5, 19.5):
                p.drawLine(QPointF(14.5, y), QPointF(20.5, y))
        else:
            up = name == "layer_up"
            p.setPen(Qt.NoPen); p.setBrush(GREEN if up else ORANGE)
            if up:
                p.drawPolygon(QPolygonF([QPointF(19, 13), QPointF(23.5, 19), QPointF(14.5, 19)]))
                p.drawRect(QRectF(17.5, 18, 3, 5))
            else:
                p.drawPolygon(QPolygonF([QPointF(19, 23.5), QPointF(23.5, 17.5), QPointF(14.5, 17.5)]))
                p.drawRect(QRectF(17.5, 13, 3, 5))
    elif name == "history":
        p.setPen(_pen(OUT, 1.5)); p.setBrush(PAPER); p.drawEllipse(QRectF(3, 3, 18, 18))
        p.setPen(_pen(BLUE, 1.8)); p.drawLine(QPointF(12, 12), QPointF(12, 6.5)); p.drawLine(QPointF(12, 12), QPointF(16, 14))
    elif name == "colors":
        g = QConicalGradient(12, 12, 0)
        for i, c in enumerate(("#f00", "#ff0", "#0f0", "#0ff", "#00f", "#f0f", "#f00")):
            g.setColorAt(i / 6, QColor(c))
        p.setPen(_pen(OUT, 1)); p.setBrush(QBrush(g)); p.drawEllipse(QRectF(3, 3, 18, 18))
    elif name == "tools":
        # a pencil and a paintbrush crossed: the drawing tools
        # pencil: bottom-left to top-right
        p.save(); p.translate(12, 12); p.rotate(45)
        p.setPen(_pen(OUT, 1.1)); p.setBrush(YEL); p.drawRect(QRectF(-2.2, -9.5, 4.4, 13))
        p.setBrush(QColor(240, 150, 160)); p.drawRect(QRectF(-2.2, -11.5, 4.4, 2.2))
        p.setBrush(QColor(240, 215, 170))
        p.drawPolygon(QPolygonF([QPointF(-2.2, 3.5), QPointF(2.2, 3.5), QPointF(0, 9)]))
        p.setBrush(OUT); p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(-0.8, 7.2), QPointF(0.8, 7.2), QPointF(0, 9)]))
        p.restore()
        # paintbrush: bottom-right to top-left
        p.save(); p.translate(12, 12); p.rotate(-45)
        p.setPen(_pen(OUT, 1.1)); p.setBrush(QColor(170, 115, 60))
        p.drawRoundedRect(QRectF(-1.7, -11, 3.4, 10), 1.5, 1.5)
        p.setBrush(QColor(175, 180, 190)); p.drawRect(QRectF(-2.3, -1.5, 4.6, 3))
        tip = QPainterPath(QPointF(-2.3, 1.5))
        tip.cubicTo(-2.6, 5, -1.2, 8, 0, 10); tip.cubicTo(1.2, 8, 2.6, 5, 2.3, 1.5); tip.closeSubpath()
        p.setBrush(BLUE); p.drawPath(tip)
        p.restore()
    elif name == "print":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(PAPER); p.drawRect(QRectF(7, 3, 10, 6))          # paper in
        p.setBrush(QColor(150, 155, 165)); p.drawRoundedRect(QRectF(3, 8.5, 18, 8.5), 2, 2)     # body
        p.setBrush(PAPER); p.drawRect(QRectF(7, 13.5, 10, 7.5))                              # paper out
        p.setPen(_pen(GREY, 1)); p.drawLine(QPointF(9, 16.5), QPointF(15, 16.5)); p.drawLine(QPointF(9, 18.5), QPointF(13.5, 18.5))
        p.setPen(Qt.NoPen); p.setBrush(GREEN); p.drawEllipse(QPointF(18, 11), 0.9, 0.9)
    elif name == "scan":
        # flatbed scanner with its lid up and the scan light
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(150, 155, 165))
        p.drawRoundedRect(QRectF(2.5, 13, 19, 7), 1.5, 1.5)
        p.setBrush(QColor(200, 225, 250)); p.drawRect(QRectF(4.5, 14.5, 15, 2.2))
        p.setBrush(QColor(120, 125, 135))
        p.drawPolygon(QPolygonF([QPointF(3, 12.5), QPointF(21, 12.5), QPointF(18, 4), QPointF(6, 4)]))
        p.setPen(_pen(LBLUE.darker(110), 1.4)); p.drawLine(QPointF(5, 18.5), QPointF(19, 18.5))
    elif name == "navigator":
        # a little landscape picture with a red view box on it
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(160, 200, 240)); p.drawRect(QRectF(2.5, 4.5, 19, 15))
        p.setPen(Qt.NoPen); p.setBrush(QColor(110, 170, 90))
        hill = QPainterPath(QPointF(3, 19)); hill.lineTo(3, 15); hill.cubicTo(7, 11, 11, 13, 14, 15.5)
        hill.cubicTo(17, 13, 20, 13, 21, 14); hill.lineTo(21, 19); hill.closeSubpath(); p.drawPath(hill)
        p.setBrush(YEL); p.drawEllipse(QPointF(7, 8.5), 1.8, 1.8)
        p.setBrush(Qt.NoBrush); p.setPen(_pen(RED, 1.8)); p.drawRect(QRectF(11, 7.5, 8, 7))
    elif name == "settings":
        p.setPen(_pen(OUT, 1.4)); p.setBrush(QColor(200, 205, 215))
        path = QPainterPath()
        for i in range(16):
            r = 9.5 if i % 2 == 0 else 7
            a = i * math.pi / 8
            pt = QPointF(12 + r * math.cos(a), 12 + r * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath(); path.addEllipse(QPointF(12, 12), 3, 3)
        p.drawPath(path)
    elif name == "image":
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(180, 215, 250)); p.drawRect(QRectF(3, 4, 18, 16))
        p.setPen(Qt.NoPen); p.setBrush(GREEN)
        p.drawPolygon(QPolygonF([QPointF(3.6, 19.4), QPointF(10, 11), QPointF(15, 16), QPointF(17, 14), QPointF(20.4, 19.4)]))
        p.setBrush(YEL); p.drawEllipse(QPointF(16, 8.5), 2.2, 2.2)
    elif name == "resize":
        p.setPen(_pen(OUT, 1.3)); p.setBrush(QColor(200, 225, 250)); p.drawRect(QRectF(3, 9, 12, 12))
        p.setPen(_pen(BLUE, 2)); p.drawLine(QPointF(11, 13), QPointF(20, 4))
        p.drawPolyline(QPolygonF([QPointF(14, 4), QPointF(20, 4), QPointF(20, 10)]))
    elif name == "split_image":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(200, 225, 250))
        for (x, y) in ((2.5, 2.5), (13, 2.5), (2.5, 13), (13, 13)):
            p.drawRect(QRectF(x, y, 8.5, 8.5))
        p.setPen(_dash(RED, 1.3))
        p.drawLine(QPointF(12, 1), QPointF(12, 23)); p.drawLine(QPointF(1, 12), QPointF(23, 12))
    elif name == "canvas_size":
        p.setPen(_dash(OUT, 1.3)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(2.5, 2.5, 19, 19))
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(200, 225, 250)); p.drawRect(QRectF(7, 7, 10, 10))
    elif name in ("flip_h", "flip_v"):
        if name == "flip_v":
            p.translate(12, 12); p.rotate(90); p.translate(-12, -12)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(BLUE)
        p.drawPolygon(QPolygonF([QPointF(10, 4), QPointF(10, 20), QPointF(2, 20)]))
        p.setBrush(PAPER); p.drawPolygon(QPolygonF([QPointF(14, 4), QPointF(14, 20), QPointF(22, 20)]))
        p.setPen(_dash(OUT, 1.1)); p.drawLine(QPointF(12, 2), QPointF(12, 22))
    elif name in ("rot_cw", "rot_ccw", "rot_180"):
        if name == "rot_ccw":
            p.translate(24, 0); p.scale(-1, 1)
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(200, 225, 250)); p.drawRect(QRectF(4, 9, 11, 11))
        path = QPainterPath(QPointF(8, 6)); path.cubicTo(13, 1, 20, 3, 20, 11)
        p.setPen(_pen(BLUE, 2)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        p.setPen(Qt.NoPen); p.setBrush(BLUE)
        p.drawPolygon(QPolygonF([QPointF(16.5, 10), QPointF(23.5, 10), QPointF(20, 15)]))
        if name == "rot_180":
            f = QFont(); f.setPixelSize(7); f.setBold(True); p.setFont(f); p.setPen(OUT)
            p.drawText(QRectF(4, 9, 11, 11), Qt.AlignCenter, "2")
    elif name == "flatten":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(QColor(170, 205, 245))
        p.drawPolygon(QPolygonF([QPointF(12, 12), QPointF(21, 16.5), QPointF(12, 21), QPointF(3, 16.5)]))
        p.setPen(_pen(BLUE, 2)); p.drawLine(QPointF(12, 2), QPointF(12, 10))
        p.setPen(Qt.NoPen); p.setBrush(BLUE); p.drawPolygon(QPolygonF([QPointF(8, 7), QPointF(16, 7), QPointF(12, 12)]))
    elif name == "adjust":
        p.setPen(_pen(OUT, 1.2)); p.setBrush(Qt.NoBrush); p.drawEllipse(QRectF(3, 3, 18, 18))
        p.setBrush(OUT); path = QPainterPath(QPointF(12, 3)); path.arcTo(QRectF(3, 3, 18, 18), 90, 180); path.closeSubpath(); p.drawPath(path)
    elif name == "effects":
        p.setPen(_pen(QColor(150, 90, 200), 1.3)); p.setBrush(QColor(200, 160, 245))
        star = QPolygonF()
        for i in range(8):
            r = 9.5 if i % 2 == 0 else 3.5
            a = -math.pi / 2 + i * math.pi / 4
            star.append(QPointF(12 + r * math.cos(a), 12 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "swap":
        p.setPen(_pen(OUT, 1.3)); p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(4, 8)); path.cubicTo(4, 4, 8, 4, 16, 4); p.drawPath(path)
        path = QPainterPath(QPointF(20, 8)); path.lineTo(20, 16); p.drawPath(path)
        p.setBrush(OUT); p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(16, 1), QPointF(20, 4), QPointF(16, 7)]))
        p.drawPolygon(QPolygonF([QPointF(17, 14), QPointF(23, 14), QPointF(20, 19)]))
    elif name == "info":
        p.setPen(_pen(BLUE, 1.6)); p.setBrush(QColor(220, 235, 252)); p.drawEllipse(QRectF(3, 3, 18, 18))
        f = QFont("Serif"); f.setPixelSize(14); f.setBold(True); f.setItalic(True); p.setFont(f)
        p.setPen(BLUE); p.drawText(QRectF(3, 3, 18, 18), Qt.AlignCenter, "i")
    elif name == "app":
        p.save(); p.scale(24 / 256.0, 24 / 256.0); _app_icon(p); p.restore()
    elif name == "eye":
        p.setPen(_pen(OUT, 1.4)); p.setBrush(PAPER)
        path = QPainterPath(QPointF(2, 12)); path.cubicTo(7, 4, 17, 4, 22, 12); path.cubicTo(17, 20, 7, 20, 2, 12)
        p.drawPath(path); p.setBrush(OUT); p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    else:
        from . import icons_menu
        if not icons_menu.draw(name, p):
            p.setPen(_pen(OUT, 1.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(4, 4, 16, 16))


def _app_icon(p):
    """Fallback logo drawn on a 256 x 256 grid, used if resources/paintwell_icon.png is missing.
    Fine detail is skipped at small sizes.
    """
    import random
    C = QColor
    P = QPointF
    detail = abs(p.transform().m11()) * 256 >= 90          # pixels across the whole icon
    rnd = random.Random(4)
    PUR, PUR_D, PUR_L = C(128, 46, 172), C(78, 22, 116), C(178, 98, 222)
    WOOD, WOOD_D, WOOD_L = C(150, 110, 74), C(92, 62, 38), C(196, 158, 116)
    MORTAR = C(92, 82, 78)

    def wood_grad(x0, x1, base=WOOD):
        g = QLinearGradient(P(x0, 0), P(x1, 0))
        g.setColorAt(0, base.darker(135)); g.setColorAt(0.4, base.lighter(118)); g.setColorAt(1, base.darker(145))
        return QBrush(g)

    def paint_grad(y0, y1):
        g = QLinearGradient(P(0, y0), P(0, y1))
        g.setColorAt(0, PUR_L); g.setColorAt(0.45, PUR); g.setColorAt(1, PUR_D)
        return QBrush(g)

    def drip_path(x, y0, length, wdt):
        d = QPainterPath(P(x - wdt / 2, y0 - 3))
        d.lineTo(x - wdt * 0.4, y0 + length - wdt * 0.45)
        d.arcTo(QRectF(x - wdt * 0.55, y0 + length - wdt * 0.95, wdt * 1.1, wdt * 1.1), 180, 180)
        d.lineTo(x + wdt / 2, y0 - 3); d.closeSubpath()
        return d

    # ---- painted purple ring behind everything
    for k, (r, wdt, a) in enumerate(((112, 7.5, 230), (109, 3, 150), (115.5, 2.2, 120))):
        pen = QPen(C(PUR.red(), PUR.green(), PUR.blue(), a), wdt, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(128 - r, 134 - r, 2 * r, 2 * r), (100 + k * 25) * 16, (330 - k * 40) * 16)
    if detail:
        p.setPen(QPen(C(PUR.red(), PUR.green(), PUR.blue(), 170), 2.2, Qt.SolidLine, Qt.RoundCap))
        for ang in (200, 215, 330, 345):
            a = math.radians(ang)
            x, y = 128 + 112 * math.cos(a), 134 - 112 * math.sin(a)
            p.drawLine(P(x, y), P(x, y + rnd.uniform(8, 16)))

    # ---- ground, grass and flowers
    p.setPen(Qt.NoPen); p.setBrush(C(120, 96, 70, 150)); p.drawEllipse(P(128, 228), 92, 9)
    if detail:
        for gx, flip in ((52, -1), (204, 1)):
            for i in range(7):
                bx = gx + flip * rnd.uniform(-10, 10)
                h = rnd.uniform(14, 30)
                path = QPainterPath(P(bx - 2, 228)); path.quadTo(P(bx + flip * 4, 228 - h * 0.6), P(bx + flip * rnd.uniform(2, 9), 228 - h))
                path.quadTo(P(bx + flip * 2, 228 - h * 0.5), P(bx + 2, 228)); path.closeSubpath()
                p.setBrush(C(70, 120, 60) if i % 2 else C(96, 150, 72)); p.drawPath(path)
            for fx, fy in ((gx + flip * 6, 202), (gx - flip * 8, 214)):
                p.setBrush(C(170, 100, 210))
                for k in range(5):
                    a = k * 2 * math.pi / 5
                    p.drawEllipse(P(fx + 3.2 * math.cos(a), fy + 3.2 * math.sin(a)), 2.6, 2.6)
                p.setBrush(C(250, 220, 120)); p.drawEllipse(P(fx, fy), 1.6, 1.6)

    # ---- the stone well
    cx, top, bot, rx, ry = 128.0, 156.0, 222.0, 64.0, 15.0

    def ey(a, y):        # a: -pi/2 (left edge) .. pi/2 (right edge) across the front
        return y + ry * math.cos(a)
    body = QPainterPath(P(cx - rx, top))
    body.lineTo(cx - rx, bot); body.arcTo(QRectF(cx - rx, bot - ry, 2 * rx, 2 * ry), 180, 180)
    body.lineTo(cx + rx, top); body.arcTo(QRectF(cx - rx, top - ry, 2 * rx, 2 * ry), 0, -180); body.closeSubpath()
    p.setBrush(MORTAR); p.setPen(Qt.NoPen); p.drawPath(body)
    stones = [C(200, 192, 176), C(178, 170, 156), C(214, 206, 190), C(160, 152, 140), C(190, 180, 168)]
    stained = [C(150, 82, 188), C(128, 60, 168), C(168, 112, 200)]
    p.save(); p.setClipPath(body)
    rows = 4 if detail else 3
    rh = (bot - top) / rows
    for r in range(rows):
        y0 = top + r * rh
        step = 0.5 if detail else 0.75
        a = -math.pi / 2 - (step / 2 if r % 2 else 0)
        while a < math.pi / 2:
            a1 = min(a + step, math.pi / 2 + 0.01)
            gap = 1.4 if detail else 0.0
            pts = []
            n = 6
            for k in range(n + 1):
                t = a + (a1 - a) * k / n
                pts.append(P(cx + rx * math.sin(t) + gap * 0.5, ey(t, y0) + gap))
            for k in range(n, -1, -1):
                t = a + (a1 - a) * k / n
                pts.append(P(cx + rx * math.sin(t) + gap * 0.5, ey(t, y0 + rh)))
            col = rnd.choice(stained) if rnd.random() < (0.28 if r > 0 else 0.15) else rnd.choice(stones)
            p.setBrush(col); p.drawPolygon(QPolygonF(pts))
            a = a1
    # roundness: darker towards the sides
    sh = QLinearGradient(P(cx - rx, 0), P(cx + rx, 0))
    sh.setColorAt(0, C(0, 0, 0, 120)); sh.setColorAt(0.35, C(0, 0, 0, 0)); sh.setColorAt(0.65, C(0, 0, 0, 0)); sh.setColorAt(1, C(0, 0, 0, 140))
    p.setBrush(QBrush(sh)); p.drawRect(QRectF(cx - rx, top - ry, 2 * rx, bot - top + 2 * ry + 2))
    p.restore()
    p.setPen(QPen(C(60, 50, 48), 1.6)); p.setBrush(Qt.NoBrush); p.drawPath(body)

    # ---- posts (stand on the rim, behind the brush)
    for px in (cx - 50, cx + 50):
        p.setPen(QPen(WOOD_D, 1.4)); p.setBrush(wood_grad(px - 7, px + 7))
        p.drawRect(QRectF(px - 7, 52, 14, top - 52))
        if detail:
            p.setPen(QPen(C(92, 62, 38, 120), 1)); p.drawLine(P(px - 2, 64), P(px - 1, top - 10))

    # ---- rim: chunky top stones round a dark opening
    rim_o = QPainterPath(); rim_o.addEllipse(P(cx, top), rx + 5, ry + 4)
    hole = QPainterPath(); hole.addEllipse(P(cx, top - 1), rx - 13, ry - 5)
    rg = QLinearGradient(P(0, top - ry), P(0, top + ry + 4))
    rg.setColorAt(0, C(226, 218, 204)); rg.setColorAt(1, C(170, 160, 146))
    p.setBrush(QBrush(rg)); p.setPen(QPen(C(60, 50, 48), 1.6)); p.drawPath(rim_o.subtracted(hole))
    if detail:
        p.setPen(QPen(C(110, 100, 94), 1.4))
        for k in range(14):
            a = k * 2 * math.pi / 14 + 0.1
            p.drawLine(P(cx + (rx - 13) * math.cos(a), top - 1 + (ry - 5) * math.sin(a)),
                       P(cx + (rx + 5) * math.cos(a), top + (ry + 4) * math.sin(a)))
    hg = QLinearGradient(P(0, top - ry), P(0, top + ry))
    hg.setColorAt(0, C(30, 10, 46)); hg.setColorAt(1, C(84, 30, 120))
    p.setPen(Qt.NoPen); p.setBrush(QBrush(hg)); p.drawPath(hole)

    # ---- purple paint over the front of the rim and down the stones
    def outer_y(x):
        return top + (ry + 4) * math.sqrt(max(0.0, 1 - ((x - cx) / (rx + 5)) ** 2))

    def inner_y(x):
        return top - 1 + (ry - 5) * math.sqrt(max(0.0, 1 - ((x - cx) / (rx - 13)) ** 2))
    x0, x1, n = cx - 8, cx + 44, 30
    band = QPainterPath(P(x0, inner_y(x0) + 2))
    for k in range(n + 1):
        x = x0 + (x1 - x0) * k / n
        band.lineTo(x, inner_y(x) + 2)
    for k in range(n, -1, -1):
        x = x0 + (x1 - x0) * k / n
        taper = math.sin(math.pi * k / n) ** 0.6
        yi, yo = inner_y(x) + 2, outer_y(x) + 3 + 1.5 * math.sin(k * 1.7)
        band.lineTo(x, yi + (yo - yi) * taper)
    band.closeSubpath()
    spill = QPainterPath(band)
    drips = ((cx + 0, 22, 5.5), (cx + 12, 44, 6.5), (cx + 24, 16, 5), (cx + 34, 32, 5.5)) if detail else \
            ((cx + 6, 28, 8), (cx + 28, 38, 8))
    for x, length, wdt in drips:
        spill = spill.united(drip_path(x, outer_y(x) + 1, length, wdt))
    p.setBrush(paint_grad(top, top + 60)); p.setPen(QPen(PUR_D, 1.2)); p.drawPath(spill)
    if detail:
        p.setPen(QPen(C(230, 200, 255, 190), 2.4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(cx + 11, outer_y(cx + 12) + 6), P(cx + 11, outer_y(cx + 12) + 36))

    # ---- the roller, rope and crank
    ay = 88.0
    p.setPen(QPen(WOOD_D, 1.4))
    rg2 = QLinearGradient(P(0, ay - 6), P(0, ay + 6))
    rg2.setColorAt(0, WOOD_L); rg2.setColorAt(1, WOOD_D)
    p.setBrush(QBrush(rg2)); p.drawRoundedRect(QRectF(cx - 50, ay - 6, 100, 12), 3, 3)
    rope = C(186, 150, 96)
    p.setBrush(rope); p.setPen(QPen(C(120, 90, 50), 1.1))
    p.drawRoundedRect(QRectF(cx - 12, ay - 8, 24, 16), 3, 3)
    if detail:
        for k in range(5):
            x = cx - 9 + k * 4.5
            p.drawLine(P(x, ay - 8), P(x + 3, ay + 8))
    # crank on the right
    crank = QPainterPath(P(cx + 57, ay)); crank.lineTo(cx + 66, ay); crank.lineTo(cx + 66, ay + 26); crank.lineTo(cx + 72, ay + 26)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(C(40, 40, 48), 6.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.drawPath(crank)
    p.setPen(QPen(C(150, 156, 170), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.drawPath(crank)
    p.setPen(QPen(WOOD_D, 1.2)); p.setBrush(wood_grad(cx + 72, cx + 90))
    p.drawRoundedRect(QRectF(cx + 71, ay + 21, 20, 10), 4, 4)

    # ---- the brush hanging from the rope
    p.setPen(QPen(rope, 2.6)); p.drawLine(P(cx, ay + 8), P(cx + 1, 98))
    p.save(); p.translate(cx + 1, 98); p.rotate(-12); p.scale(0.85, 0.85)
    p.setPen(QPen(WOOD_D, 1.2)); p.setBrush(wood_grad(-4.5, 4.5, C(196, 128, 64)))
    p.drawRoundedRect(QRectF(-4.5, 0, 9, 22), 4, 4)
    p.setBrush(C(60, 40, 30)); p.setPen(Qt.NoPen); p.drawEllipse(P(0, 4), 1.6, 1.6)     # hanging hole
    fg = QLinearGradient(P(-10, 0), P(10, 0))
    fg.setColorAt(0, C(130, 136, 150)); fg.setColorAt(0.4, C(240, 242, 248)); fg.setColorAt(1, C(110, 116, 130))
    p.setPen(QPen(C(70, 74, 88), 1.2)); p.setBrush(QBrush(fg)); p.drawRect(QRectF(-10, 21, 20, 8))
    bristles = QPainterPath(P(-10, 29)); bristles.lineTo(-11, 44); bristles.lineTo(11, 44); bristles.lineTo(10, 29); bristles.closeSubpath()
    p.setPen(QPen(C(150, 120, 80), 1)); p.setBrush(C(226, 206, 166)); p.drawPath(bristles)
    wet = QPainterPath(P(-11, 37)); wet.cubicTo(P(-4, 34), P(4, 40), P(11, 36)); wet.lineTo(11, 44); wet.lineTo(-11, 44); wet.closeSubpath()
    wet = wet.united(drip_path(-5, 44, 6, 3.6)).united(drip_path(4, 44, 10, 4))
    p.setPen(Qt.NoPen); p.setBrush(paint_grad(34, 56)); p.drawPath(wet)
    p.restore()
    # drops falling into the well
    p.setPen(Qt.NoPen)
    for x, y, r in ((cx + 7, 145, 3.2), (cx + 8, 155, 2.4)):
        d = QPainterPath(P(x, y - r * 2.3))
        d.cubicTo(P(x + r * 0.4, y - r * 1.2), P(x + r, y - r * 0.6), P(x + r, y))
        d.arcTo(QRectF(x - r, y - r, 2 * r, 2 * r), 0, -180)
        d.cubicTo(P(x - r, y - r * 0.6), P(x - r * 0.4, y - r * 1.2), P(x, y - r * 2.3))
        p.setBrush(paint_grad(y - 2 * r, y + r)); p.drawPath(d)

    # ---- the roof: shingle planks, some splashed with paint
    roof = QPolygonF([P(cx - 58, 30), P(cx + 58, 30), P(cx + 86, 64), P(cx - 86, 64)])
    p.setPen(QPen(WOOD_D, 1.6)); p.setBrush(QBrush(QColor(140, 112, 86))); p.drawPolygon(roof)
    p.save(); rp = QPainterPath(); rp.addPolygon(roof); p.setClipPath(rp)
    plank_rows = ((30, 47), (47, 64))
    for ri, (y0, y1) in enumerate(plank_rows):
        cols = 6 if detail else 4
        for k in range(cols + 1):
            t0 = (k - (0.5 if ri else 0)) / cols
            t1 = t0 + 1 / cols

            def xat(t, y):
                half = 58 + 28 * (y - 30) / 34
                return cx - half + 2 * half * t
            poly = QPolygonF([P(xat(t0, y0), y0), P(xat(t1, y0), y0), P(xat(t1, y1), y1), P(xat(t0, y1), y1)])
            base = C(150, 120, 92) if (k + ri) % 2 else C(128, 102, 78)
            if rnd.random() < 0.38:
                base = C(140, 80, 168) if rnd.random() < 0.5 else C(118, 64, 150)
            p.setBrush(base); p.setPen(QPen(WOOD_D, 1.3)); p.drawPolygon(poly)
            if detail:
                p.setPen(QPen(C(255, 255, 255, 40), 1)); p.drawLine(P(xat(t0, y0) + 3, y0 + 4), P(xat(t1, y0) - 3, y0 + 4))
    p.restore()
    p.setPen(QPen(WOOD_D, 1.6)); p.setBrush(wood_grad(cx - 60, cx + 60, C(160, 126, 92)))
    p.drawRoundedRect(QRectF(cx - 62, 25, 124, 7), 2, 2)        # ridge board
    # paint dripping off the roof edge
    p.setPen(Qt.NoPen); p.setBrush(paint_grad(60, 84))
    for x, length in ((cx - 54, 12), (cx + 26, 16), (cx + 70, 9)) if detail else ((cx + 26, 14),):
        p.drawPath(drip_path(x, 64, length, 5.5))


def pixmap(name, size=24):
    key = (name, size)
    if key in _cache:
        return _cache[key]
    if not _disk_state["loaded"]:
        _load_disk()
    if key in _disk:
        try:
            _cache[key] = _from_disk(key)
            return _cache[key]
        except Exception:
            _disk.pop(key, None)
    _draw_pixmap(name, size, key)
    _to_disk(key, _cache[key])
    return _cache[key]


def _draw_pixmap(name, size, key):
    if key not in _cache and name == "app" and _logo():
        pm = QPixmap.fromImage(_logo_scaled(size * 2))
        pm.setDevicePixelRatio(2.0)
        _cache[key] = pm
    if key not in _cache:
        dpr = 2.0
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.scale(size * dpr / 24.0, size * dpr / 24.0)
        _draw(name, p)
        p.end()
        if name not in ("colors", "app"):
            pm = _tone_down(pm)
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm


TONE = 0.74    # how much colour icons keep (1 = full strength); softens the accents


def _tone_down(pm):
    """Soften an icon's colours: pull each pixel part of the way towards its own grey."""
    import numpy as np
    from PySide6.QtGui import QImage
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    a = np.frombuffer(img.bits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
    rgb = a[..., :3].astype(np.float32)                     # premultiplied B, G, R
    grey = rgb[..., 0:1] * 0.114 + rgb[..., 1:2] * 0.587 + rgb[..., 2:3] * 0.299
    out = grey + (rgb - grey) * TONE
    a[..., :3] = np.clip(out + 0.5, 0, a[..., 3:4]).astype(np.uint8)
    return QPixmap.fromImage(img)


def icon(name):
    ic = QIcon()
    for s in ((16, 24, 32, 48, 64, 128, 256) if name == "app" else (16, 24, 32, 48)):
        ic.addPixmap(pixmap(name, s))
    return ic


_LOGO = None


def _logo():
    """The Paintwell logo picture (resources/paintwell_icon.png), loaded once."""
    global _LOGO
    if _LOGO is None:
        import os
        from PySide6.QtGui import QImage
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "paintwell_icon.png")
        img = QImage(path)
        _LOGO = _on_tile(img.convertToFormat(QImage.Format_ARGB32_Premultiplied)) if not img.isNull() else False
    return _LOGO


def _on_tile(art, size=1024):
    """The logo picture (which has its own parchment background) as a rounded-square tile
    with a thin edge, like most desktop app icons."""
    from PySide6.QtGui import QImage
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
    m = size * 0.03
    tile = QRectF(m, m, size - 2 * m, size - 2 * m)
    path = QPainterPath(); path.addRoundedRect(tile, size * 0.2, size * 0.2)
    p.setClipPath(path)
    p.drawImage(tile, art)
    p.setClipping(False)
    p.setPen(QPen(QColor(110, 78, 46, 200), size * 0.01)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    p.end()
    return img


def _logo_scaled(px):
    """Logo at px x px, shrunk in halving steps so small sizes stay crisp, not grainy."""
    img = _logo()
    while img.width() >= px * 2:
        img = img.scaled(img.width() // 2, img.height() // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return img.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def app_icon_image(size=256):
    if _logo():
        return _logo_scaled(size)
    from PySide6.QtGui import QImage
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    _draw("app", p)
    p.end()
    return img
