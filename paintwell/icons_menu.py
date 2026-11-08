"""Icons for the menu entries (File, Edit, View, Adjustments, Effects...), drawn in the same
24x24 style as the tool icons.  Most effect icons are a small landscape picture shown with
that effect applied, so they read as a family."""
import math, random
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QPainterPath, QColor, QBrush, QLinearGradient, QRadialGradient,
                           QConicalGradient, QPolygonF, QFont, QPen)
from . import icons as I

SKY = QColor(150, 200, 245)
HILL = QColor(70, 160, 80)
SUN = QColor(245, 195, 50)
WOOD = QColor(170, 115, 60)
GUIDE = QColor(0, 170, 210)


def P(x, y):
    return QPointF(x, y)


def _photo(p, r=QRectF(3, 4, 18, 16), sky=SKY, hill=HILL, sun=SUN, frame=True, round_=0.0):
    """Little landscape: sky, a sun and a hill, inside rectangle r."""
    p.save()
    clip = QPainterPath(); clip.addRoundedRect(r, round_, round_)
    p.setClipPath(clip)
    p.setPen(Qt.NoPen); p.setBrush(sky); p.drawRect(r)
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    p.setBrush(sun); p.drawEllipse(P(x + w * 0.72, y + h * 0.3), w * 0.12, w * 0.12)
    p.setBrush(hill)
    p.drawPolygon(QPolygonF([P(x, y + h), P(x, y + h * 0.75), P(x + w * 0.38, y + h * 0.42),
                             P(x + w * 0.62, y + h * 0.68), P(x + w * 0.78, y + h * 0.56),
                             P(x + w, y + h * 0.78), P(x + w, y + h)]))
    p.restore()
    if frame:
        p.setPen(I._pen(I.OUT, 1.2)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r, round_, round_)


def _grey(c):
    v = int(c.red() * 0.3 + c.green() * 0.59 + c.blue() * 0.11)
    return QColor(v, v, v)


def _sepia(c):
    v = c.red() * 0.3 + c.green() * 0.59 + c.blue() * 0.11
    return QColor(min(255, int(v * 1.07 + 20)), min(255, int(v * 0.86 + 8)), int(v * 0.62))


def _magnifier(p, cx, cy, r, mark=None, glass=None):
    p.setPen(I._pen(I.OUT, 3.2)); p.drawLine(P(cx + r * 0.7, cy + r * 0.7), P(cx + r * 1.55, cy + r * 1.55))
    p.setPen(I._pen(I.OUT, 2.0)); p.setBrush(QColor(200, 225, 250) if glass is None else glass)
    p.drawEllipse(P(cx, cy), r, r)
    if mark:
        p.setPen(I._pen(QColor(25, 70, 140), 1.8)); d = r * 0.5
        p.drawLine(P(cx - d, cy), P(cx + d, cy))
        if mark == "+":
            p.drawLine(P(cx, cy - d), P(cx, cy + d))


def _diamond(p, cx, cy, w=9, h=4.5, fill=QColor(170, 205, 245)):
    p.setPen(I._pen(I.OUT, 1.2)); p.setBrush(fill)
    p.drawPolygon(QPolygonF([P(cx, cy - h), P(cx + w, cy), P(cx, cy + h), P(cx - w, cy)]))


def _plus(p, cx, cy, r=3.5, c=None):
    p.setPen(I._pen(I.GREEN if c is None else c, 2.3))
    p.drawLine(P(cx - r, cy), P(cx + r, cy)); p.drawLine(P(cx, cy - r), P(cx, cy + r))


def _cross(p, cx, cy, r=3.2):
    p.setPen(I._pen(I.RED, 2.3))
    p.drawLine(P(cx - r, cy - r), P(cx + r, cy + r)); p.drawLine(P(cx + r, cy - r), P(cx - r, cy + r))


def _arrow_round(p, cx, cy, r, c=None, start=200, span=-250):
    """Circular arrow (refresh / redo style)."""
    c = I.BLUE if c is None else c
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath(); path.arcMoveTo(rect, start); path.arcTo(rect, start, span)
    p.setPen(I._pen(c, 2)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    end = path.currentPosition()
    a = math.radians(start + span)
    # tangent direction of travel (span negative = clockwise on screen)
    sgn = -1 if span < 0 else 1
    tx, ty = -math.sin(a) * sgn, -math.cos(a) * sgn
    nx, ny = -ty, tx
    s = 3.2
    tip = P(end.x() + tx * s * 0.9, end.y() + ty * s * 0.9)
    p.setPen(Qt.NoPen); p.setBrush(c)
    p.drawPolygon(QPolygonF([tip, P(end.x() + nx * s * 0.8, end.y() + ny * s * 0.8),
                             P(end.x() - nx * s * 0.8, end.y() - ny * s * 0.8)]))


def _floppy(p, x, y, s=16):
    k = s / 16.0
    p.setPen(I._pen(QColor(25, 70, 140), 1.3)); p.setBrush(I.BLUE); p.drawRoundedRect(QRectF(x, y, s, s), 2 * k, 2 * k)
    p.setBrush(I.PAPER); p.drawRect(QRectF(x + 3 * k, y, 10 * k, 6 * k))
    p.setBrush(QColor(220, 225, 235)); p.drawRect(QRectF(x + 3 * k, y + 9 * k, 10 * k, 7 * k))


def _pencil(p, x, y, ang=45, length=12):
    p.save(); p.translate(x, y); p.rotate(ang)
    p.setPen(I._pen(I.OUT, 1.1)); p.setBrush(I.YEL)
    p.drawRect(QRectF(-1.7, -length, 3.4, length - 3.5))
    p.setBrush(QColor(240, 210, 170))
    p.drawPolygon(QPolygonF([P(-1.7, -3.5), P(1.7, -3.5), P(0, 0)]))
    p.setBrush(QColor(240, 140, 150)); p.drawRect(QRectF(-1.7, -length - 2, 3.4, 2))
    p.restore()


def _clipboard(p):
    p.setPen(I._pen(QColor(120, 85, 40), 1.3)); p.setBrush(QColor(195, 150, 90)); p.drawRoundedRect(QRectF(3, 4, 15, 18), 2, 2)
    p.setBrush(QColor(110, 110, 120)); p.drawRect(QRectF(7, 2.5, 7, 4))


def _font(px, bold=True):
    f = QFont(); f.setPixelSize(px); f.setBold(bold); return f


def _histogram(p, r, c=None):
    vals = [0.25, 0.45, 0.7, 0.95, 0.8, 0.55, 0.65, 0.4, 0.2]
    bw = r.width() / len(vals)
    p.setPen(Qt.NoPen); p.setBrush(I.OUT if c is None else c)
    for i, v in enumerate(vals):
        p.drawRect(QRectF(r.x() + i * bw, r.bottom() - r.height() * v, bw * 0.8, r.height() * v))


def _dots(p, r, n, seed, colours, size=(0.7, 1.4)):
    rnd = random.Random(seed)
    p.setPen(Qt.NoPen)
    for i in range(n):
        p.setBrush(colours[i % len(colours)])
        s = rnd.uniform(*size)
        p.drawEllipse(P(rnd.uniform(r.left() + 1, r.right() - 1), rnd.uniform(r.top() + 1, r.bottom() - 1)), s, s)


def _blob(p, cx, cy, rx, ry, seed=1, n=9, jitter=0.25):
    rnd = random.Random(seed)
    path = QPainterPath()
    pts = []
    for i in range(n):
        a = i * 2 * math.pi / n
        k = 1 + rnd.uniform(-jitter, jitter)
        pts.append(P(cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    path.moveTo((pts[0] + pts[-1]) / 2)
    for i in range(n):
        q = pts[i]; nxt = pts[(i + 1) % n]
        path.quadTo(q, (q + nxt) / 2)
    return path


def draw(name, p):
    """Draw menu icon `name`; returns False if the name is unknown."""
    O = I.OUT
    # ------------------------------------------------------------------ File
    if name == "recent":
        I._draw("open", p)
        p.setPen(I._pen(O, 1.3)); p.setBrush(I.PAPER); p.drawEllipse(P(17.5, 17.5), 5.2, 5.2)
        p.setPen(I._pen(I.BLUE, 1.5)); p.drawLine(P(17.5, 17.5), P(17.5, 14.3)); p.drawLine(P(17.5, 17.5), P(19.8, 18.8))
    elif name == "save_as":
        _floppy(p, 2.5, 2.5, 15)
        _pencil(p, 13.5, 22, 40, 13)
    elif name == "save_all":
        _floppy(p, 7, 2.5, 14.5); _floppy(p, 2.5, 7, 14.5)
    elif name == "close":
        I._page(p, 4, 3, 13, 17)
        p.setPen(I._pen(I.RED, 1.3)); p.setBrush(I.RED.lighter(125)); p.drawEllipse(P(17, 17), 5, 5)
        p.setPen(I._pen(QColor(255, 255, 255), 1.7))
        p.drawLine(P(15.2, 15.2), P(18.8, 18.8)); p.drawLine(P(18.8, 15.2), P(15.2, 18.8))
    elif name == "quit":
        p.setPen(I._pen(O, 1.3)); p.setBrush(WOOD.lighter(125)); p.drawRect(QRectF(4, 3, 10, 18))
        p.setBrush(O); p.drawEllipse(P(11.5, 12.5), 0.9, 0.9)
        p.setPen(I._pen(I.RED, 2.1)); p.drawLine(P(12, 12), P(21, 12))
        p.drawPolyline(QPolygonF([P(17.5, 8.5), P(21, 12), P(17.5, 15.5)]))
    # ------------------------------------------------------------------ Edit
    elif name == "copy_merged":
        _diamond(p, 9.5, 11, 7, 3.5, QColor(150, 190, 240)); _diamond(p, 9.5, 7, 7, 3.5, I.PAPER)
        I._page(p, 11, 10, 10, 12, QColor(225, 238, 252))
    elif name == "paste_layer":
        _clipboard(p)
        _diamond(p, 15.5, 16, 6.5, 3.5, QColor(170, 205, 245)); _diamond(p, 15.5, 13, 6.5, 3.5, I.PAPER)
    elif name == "paste_image":
        _clipboard(p)
        _photo(p, QRectF(10, 11, 12, 10))
    elif name == "erase_selection":
        p.setPen(I._dash(O, 1.5)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(2.5, 2.5, 15, 15))
        p.save(); p.translate(16, 16); p.rotate(-40)
        p.setPen(I._pen(O, 1.2)); p.setBrush(QColor(240, 130, 150)); p.drawRect(QRectF(-6, -3.2, 6, 6.4))
        p.setBrush(I.PAPER); p.drawRect(QRectF(0, -3.2, 6, 6.4)); p.restore()
    elif name == "fill_selection":
        p.setPen(I._dash(O, 1.5)); p.setBrush(QColor(40, 120, 215, 150)); p.drawRect(QRectF(2.5, 2.5, 15, 15))
        p.setPen(I._pen(I.BLUE.darker(120), 1.1)); p.setBrush(I.BLUE)
        path = QPainterPath(P(18.5, 12)); path.cubicTo(21.5, 16.5, 22, 19.5, 18.5, 20.5)
        path.cubicTo(15, 19.5, 15.5, 16.5, 18.5, 12); p.drawPath(path)
    elif name == "invert_selection":
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(40, 120, 215, 110)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setBrush(I.PAPER); p.setPen(I._dash(O, 1.4)); p.drawRect(QRectF(8, 8, 8, 8))
    # ------------------------------------------------------------------ View
    elif name in ("zoom_in", "zoom_out"):
        _magnifier(p, 10, 10, 6.5, "+" if name == "zoom_in" else "-")
    elif name == "zoom_window":
        p.setPen(I._pen(O, 1.6)); p.setBrush(Qt.NoBrush)
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            cx, cy = 12 - sx * 9, 12 - sy * 9
            p.drawPolyline(QPolygonF([P(cx, cy + sy * 5), P(cx, cy), P(cx + sx * 5, cy)]))
        _magnifier(p, 11, 11, 4)
    elif name == "zoom_selection":
        p.setPen(I._dash(O, 1.4)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(2.5, 2.5, 19, 19))
        _magnifier(p, 10.5, 10.5, 4.6)
    elif name == "actual_size":
        p.setPen(I._pen(O, 1.2)); p.setBrush(I.PAPER); p.drawRoundedRect(QRectF(2.5, 5, 19, 14), 2, 2)
        p.setFont(_font(9)); p.setPen(O); p.drawText(QRectF(2.5, 5, 19, 14), Qt.AlignCenter, "1:1")
    elif name in ("show_guides", "new_guide", "clear_guides", "snap_guides"):
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(I._pen(GUIDE, 1.5))
        p.drawLine(P(9, 3), P(9, 21))
        if name != "snap_guides":
            p.drawLine(P(3, 9), P(21, 9))
        if name == "new_guide":
            _plus(p, 17, 17, 3.5)
        elif name == "clear_guides":
            _cross(p, 17, 17, 3.2)
        elif name == "snap_guides":
            p.save(); p.translate(15.5, 13); p.rotate(90)
            mag = QPainterPath(); mag.moveTo(-3.5, -4); mag.lineTo(-3.5, 0.5); mag.arcTo(QRectF(-3.5, -3, 7, 7), 180, 180)
            mag.lineTo(3.5, -4)
            p.setPen(QPen(I.RED, 2.6, Qt.SolidLine, Qt.FlatCap)); p.setBrush(Qt.NoBrush); p.drawPath(mag)
            p.setPen(QPen(QColor(225, 225, 230), 2.6, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(P(-3.5, -4), P(-3.5, -2.5)); p.drawLine(P(3.5, -4), P(3.5, -2.5))
            p.restore()
    elif name == "theme":
        p.setPen(I._pen(O, 1.3)); p.setBrush(I.SUN if hasattr(I, "SUN") else SUN); p.drawEllipse(QRectF(4, 4, 16, 16))
        p.setBrush(QColor(60, 70, 110))
        path = QPainterPath(P(12, 4)); path.arcTo(QRectF(4, 4, 16, 16), 90, -180); path.closeSubpath(); p.drawPath(path)
    elif name == "theme_system":
        p.setPen(I._pen(O, 1.3)); p.setBrush(QColor(60, 70, 90)); p.drawRoundedRect(QRectF(3, 4, 18, 12), 1.5, 1.5)
        g = QLinearGradient(4, 5, 20, 15); g.setColorAt(0.49, QColor(240, 240, 245)); g.setColorAt(0.51, QColor(70, 80, 110))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(g)); p.drawRect(QRectF(4.5, 5.5, 15, 9))
        p.setPen(I._pen(O, 1.6)); p.drawLine(P(12, 16), P(12, 19.5)); p.drawLine(P(8, 20), P(16, 20))
    elif name == "theme_light":
        p.setPen(I._pen(SUN.darker(130), 1.6))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(P(12 + 6.5 * math.cos(a), 12 + 6.5 * math.sin(a)), P(12 + 9.5 * math.cos(a), 12 + 9.5 * math.sin(a)))
        p.setPen(I._pen(SUN.darker(130), 1.2)); p.setBrush(SUN); p.drawEllipse(P(12, 12), 4.6, 4.6)
    elif name == "theme_dark":
        moon = QPainterPath(); moon.addEllipse(P(12, 12), 8, 8)
        cut = QPainterPath(); cut.addEllipse(P(16.5, 8.5), 7, 7)
        p.setPen(I._pen(QColor(60, 70, 110), 1.2)); p.setBrush(QColor(110, 125, 180)); p.drawPath(moon.subtracted(cut))
        p.setPen(Qt.NoPen); p.setBrush(SUN); p.drawEllipse(P(18.5, 15.5), 1.1, 1.1); p.drawEllipse(P(15.5, 19.5), 0.8, 0.8)
    # ------------------------------------------------------------------ Layers / Window / Help
    elif name == "import_layer":
        _diamond(p, 11, 15.5, 9, 4.5)
        p.setPen(I._pen(I.GREEN, 2.2)); p.drawLine(P(18, 2), P(18, 9))
        p.setPen(Qt.NoPen); p.setBrush(I.GREEN); p.drawPolygon(QPolygonF([P(14, 8), P(22, 8), P(18, 12.5)]))
    elif name == "layer_rotate_zoom":
        p.save(); p.translate(10, 14); p.rotate(-20)
        p.setPen(I._pen(O, 1.2)); p.setBrush(QColor(170, 205, 245)); p.drawRect(QRectF(-6, -5, 12, 10)); p.restore()
        path = QPainterPath(P(9, 4)); path.cubicTo(14, 1.5, 19.5, 3.5, 20.5, 10)
        p.setPen(I._pen(I.BLUE, 2)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        p.setPen(Qt.NoPen); p.setBrush(I.BLUE)
        p.drawPolygon(QPolygonF([P(17.3, 9.2), P(23.5, 8.8), P(20.8, 14)]))
    elif name == "reset_layout":
        p.setPen(I._pen(O, 1.2)); p.setBrush(I.PAPER); p.drawRect(QRectF(2.5, 3.5, 16, 13))
        p.setBrush(QColor(200, 210, 225)); p.drawRect(QRectF(2.5, 3.5, 16, 3))
        p.drawRect(QRectF(2.5, 6.5, 4.5, 10)); p.drawRect(QRectF(14, 6.5, 4.5, 10))
        p.setPen(I._pen(O, 1.2)); p.setBrush(I.PAPER); p.drawEllipse(P(17, 17), 5.5, 5.5)
        _arrow_round(p, 17, 17, 3.3, start=150, span=-270)
    elif name == "keyboard":
        p.setPen(I._pen(O, 1.3)); p.setBrush(QColor(215, 218, 226)); p.drawRoundedRect(QRectF(1.5, 6, 21, 13), 2, 2)
        p.setPen(Qt.NoPen); p.setBrush(QColor(90, 92, 102))
        for row, (y, n, x0) in enumerate(((8, 6, 3.3), (11.3, 6, 4.3))):
            for i in range(n):
                p.drawRoundedRect(QRectF(x0 + i * 3, y, 2.2, 2.2), 0.4, 0.4)
        p.drawRoundedRect(QRectF(6.5, 14.8, 11, 2.2), 0.4, 0.4)
    # ------------------------------------------------------------------ Adjustments
    elif name == "adj_auto_level":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 6, 18, 15))
        _histogram(p, QRectF(4.5, 8, 15, 12), I.BLUE)
        p.setPen(I._pen(I.YEL.darker(120), 1)); p.setBrush(I.YEL)
        star = QPolygonF()
        for i in range(10):
            r = 4.2 if i % 2 == 0 else 1.8
            a = -math.pi / 2 + i * math.pi / 5
            star.append(P(18.5 + r * math.cos(a), 5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "adj_black_and_white":
        _photo(p, sky=_grey(SKY), hill=_grey(HILL), sun=_grey(SUN))
    elif name == "adj_sepia":
        _photo(p, sky=_sepia(SKY), hill=_sepia(HILL), sun=_sepia(SUN))
    elif name == "adj_brightness_contrast":
        p.setPen(I._pen(O, 1.5))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(P(12 + 7 * math.cos(a), 12 + 7 * math.sin(a)), P(12 + 10 * math.cos(a), 12 + 10 * math.sin(a)))
        p.setPen(I._pen(O, 1.3)); p.setBrush(I.PAPER); p.drawEllipse(P(12, 12), 5.2, 5.2)
        p.setBrush(O); path = QPainterPath(P(12, 6.8)); path.arcTo(QRectF(6.8, 6.8, 10.4, 10.4), 90, 180); path.closeSubpath()
        p.drawPath(path)
    elif name == "adj_curves":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(I._pen(I.GREY, 0.9))
        for v in (7.5, 12, 16.5):
            p.drawLine(P(v, 3), P(v, 21)); p.drawLine(P(3, v), P(21, v))
        path = QPainterPath(P(3, 21)); path.cubicTo(12, 21, 12, 3, 21, 3)
        p.setPen(I._pen(I.BLUE, 1.9)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    elif name == "adj_hue_saturation":
        g = QConicalGradient(12, 12, 90)
        for i, c in enumerate(("#ff3b30", "#ffcc00", "#34c759", "#00c7be", "#007aff", "#af52de", "#ff3b30")):
            g.setColorAt(i / 6, QColor(c))
        p.setPen(I._pen(O, 1.2)); p.setBrush(QBrush(g)); p.drawEllipse(P(12, 12), 9, 9)
        rg = QRadialGradient(12, 12, 9); rg.setColorAt(0, QColor(255, 255, 255, 255)); rg.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(rg)); p.drawEllipse(P(12, 12), 8.4, 8.4)
    elif name == "adj_invert_colours":
        p.setPen(Qt.NoPen); p.setBrush(QColor(250, 250, 252)); p.drawRect(QRectF(3, 3, 9, 18))
        p.setBrush(QColor(40, 40, 50)); p.drawRect(QRectF(12, 3, 9, 18))
        p.setBrush(I.BLUE); path = QPainterPath(P(12, 6.5)); path.arcTo(QRectF(6.5, 6.5, 11, 11), 90, 180); path.closeSubpath(); p.drawPath(path)
        p.setBrush(I.ORANGE); path = QPainterPath(P(12, 6.5)); path.arcTo(QRectF(6.5, 6.5, 11, 11), 90, -180); path.closeSubpath(); p.drawPath(path)
        p.setPen(I._pen(O, 1.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "adj_levels":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 13))
        _histogram(p, QRectF(4.5, 5, 15, 10))
        for x, c in ((4.5, QColor(30, 30, 35)), (12, QColor(140, 140, 145)), (19.5, QColor(255, 255, 255))):
            p.setPen(I._pen(O, 1)); p.setBrush(c)
            p.drawPolygon(QPolygonF([P(x, 17), P(x + 2.6, 21.5), P(x - 2.6, 21.5)]))
    elif name == "adj_posterise":
        cols = [QColor(30, 70, 140), QColor(40, 120, 215), QColor(110, 175, 240), QColor(200, 228, 252)]
        p.setPen(Qt.NoPen)
        for i, c in enumerate(cols):
            p.setBrush(c); p.drawRect(QRectF(3 + i * 4.5, 3, 4.5, 18))
        p.setPen(I._pen(O, 1.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    # ------------------------------------------------------------------ Effect categories
    elif name == "repeat_effect":
        I._draw("effects", p)
        p.setPen(I._pen(O, 1.2)); p.setBrush(I.PAPER); p.drawEllipse(P(17.5, 17.5), 5.5, 5.5)
        _arrow_round(p, 17.5, 17.5, 3.3, I.GREEN.darker(110), start=150, span=-270)
    elif name == "cat_artistic":
        pal = _blob(p, 12, 12.5, 9.5, 8, seed=3, n=10, jitter=0.1)
        hole = QPainterPath(); hole.addEllipse(P(15.5, 16.5), 2, 2)
        p.setPen(I._pen(WOOD.darker(130), 1.2)); p.setBrush(QColor(225, 190, 140)); p.drawPath(pal.subtracted(hole))
        p.setPen(Qt.NoPen)
        for (x, y), c in zip(((7.5, 10), (10.5, 6.8), (14.8, 7), (18, 10.5), (8, 15)),
                             (I.RED, I.YEL, I.GREEN, I.BLUE, QColor(150, 90, 200))):
            p.setBrush(c); p.drawEllipse(P(x, y), 1.8, 1.8)
    elif name == "cat_blurs":
        I._draw("blur", p)
    elif name == "cat_borders":
        p.setPen(I._pen(O, 1.2)); p.setBrush(QColor(40, 120, 215)); p.drawRect(QRectF(2.5, 2.5, 19, 19))
        _photo(p, QRectF(6, 6, 12, 12))
    elif name == "cat_distort":
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush)
        for i in range(5):
            y = 4 + i * 4
            path = QPainterPath(P(3, y))
            for k in range(1, 13):
                x = 3 + k * 1.5
                path.lineTo(x, y + 1.6 * math.sin(k * 0.7 + i * 0.4))
            p.drawPath(path)
        for i in range(5):
            x = 4 + i * 4
            path = QPainterPath(P(x, 3))
            for k in range(1, 13):
                yy = 3 + k * 1.5
                path.lineTo(x + 1.6 * math.sin(k * 0.7 + i * 0.4), yy)
            p.drawPath(path)
    elif name == "cat_noise":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        _dots(p, QRectF(3, 3, 18, 18), 34, 7, [O, I.GREY, I.BLUE])
    elif name == "cat_object":
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 70)); p.drawEllipse(QRectF(7, 17, 14, 4))
        p.setPen(I._pen(I.ORANGE.darker(130), 1.2)); p.setBrush(I.ORANGE)
        star = QPolygonF()
        for i in range(10):
            r = 8 if i % 2 == 0 else 3.6
            a = -math.pi / 2 + i * math.pi / 5
            star.append(P(12 + r * math.cos(a), 10.5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "cat_photo":
        p.setPen(I._pen(O, 1.3)); p.setBrush(QColor(80, 85, 98)); p.drawRoundedRect(QRectF(2.5, 7, 19, 13), 2, 2)
        p.drawRect(QRectF(8, 4.5, 8, 3)); p.setBrush(QColor(180, 215, 250)); p.drawEllipse(P(12, 13.5), 4.3, 4.3)
        p.setBrush(QColor(60, 110, 180)); p.drawEllipse(P(12, 13.5), 2.2, 2.2)
        p.setPen(Qt.NoPen); p.setBrush(I.YEL); p.drawRect(QRectF(17, 9, 2.5, 1.5))
    elif name in ("cat_render", "fx_clouds"):
        if name == "fx_clouds":
            p.setPen(Qt.NoPen); p.setBrush(SKY); p.drawRect(QRectF(3, 4, 18, 16))
        path = QPainterPath(); path.addRoundedRect(QRectF(5, 13, 15, 5), 2.5, 2.5)
        for cx, cy, r in ((8, 14, 4), (12.5, 11, 5), (17, 14, 3.8)):
            c = QPainterPath(); c.addEllipse(P(cx, cy), r, r); path = path.united(c)
        p.setPen(I._pen(QColor(120, 130, 150), 1.3)); p.setBrush(QColor(250, 250, 255)); p.drawPath(path)
        if name == "fx_clouds":
            p.setPen(I._pen(O, 1.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "cat_stylise":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(245, 245, 250)); p.drawPolygon(QPolygonF([P(3, 3), P(21, 3), P(17, 7), P(7, 7), P(7, 17), P(3, 21)]))
        p.setBrush(QColor(90, 95, 110)); p.drawPolygon(QPolygonF([P(21, 3), P(21, 21), P(3, 21), P(7, 17), P(17, 17), P(17, 7)]))
        p.setBrush(QColor(170, 175, 190)); p.drawRect(QRectF(7, 7, 10, 10))
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    # ------------------------------------------------------------------ Artistic
    elif name == "fx_ink_sketch":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 4, 18, 16))
        p.setPen(I._pen(O, 1.2))
        p.drawPolyline(QPolygonF([P(3.5, 16), P(9.5, 10), P(13.5, 14.5), P(16, 12.5), P(20.5, 16)]))
        p.drawEllipse(P(16, 8), 1.8, 1.8)
        p.setPen(I._pen(O, 0.8))
        for x in (6, 8, 10, 12):
            p.drawLine(P(x, 19), P(x + 2, 16))
    elif name == "fx_oil_painting":
        _photo(p, frame=False)
        p.setPen(Qt.NoPen)
        rnd = random.Random(5)
        for i in range(14):
            x, y = 3.5 + rnd.random() * 16, 5 + rnd.random() * 14
            c = HILL if y > 13 else SKY
            p.setBrush(c.lighter(rnd.choice((115, 90, 130))))
            p.save(); p.translate(x, y); p.rotate(rnd.uniform(-40, 40)); p.drawEllipse(QRectF(-2.4, -1, 4.8, 2)); p.restore()
        p.setPen(I._pen(O, 1.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "fx_cartoon":
        _photo(p, sky=QColor(120, 200, 255), hill=QColor(40, 190, 70), sun=QColor(255, 210, 0), frame=False)
        p.setPen(I._pen(QColor(15, 15, 20), 1.6)); p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([P(3, 16), P(9.84, 10.72), P(14.16, 14.88), P(17.04, 12.96), P(21, 16.48)]))
        p.drawEllipse(P(15.96, 8.8), 2.16, 2.16)
        p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "fx_halftone":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(Qt.NoPen); p.setBrush(O)
        for i in range(5):
            for j in range(5):
                r = 0.35 + (i + j) * 0.2
                p.drawEllipse(P(5.2 + i * 3.4, 5.2 + j * 3.4), r, r)
    elif name == "fx_pencil_sketch":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(2.5, 3, 15, 17))
        p.setPen(I._pen(I.GREY, 0.8))
        for k in range(6):
            p.drawLine(P(4 + k * 2, 18.5), P(8 + k * 2, 11))
        _pencil(p, 13, 21, 35, 13)
    elif name == "fx_watercolour":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(Qt.NoPen)
        for (x, y, rx, ry, s), c in zip(((9, 9, 5, 4, 1), (15, 12, 5, 4.5, 2), (10, 16, 5.5, 3.5, 3)),
                                        (QColor(40, 120, 215, 110), QColor(215, 60, 50, 100), QColor(245, 195, 50, 130))):
            p.setBrush(c); p.drawPath(_blob(p, x, y, rx, ry, seed=s))
    # ------------------------------------------------------------------ Blurs
    elif name == "fx_fragment":
        p.setOpacity(0.45)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            _photo(p, QRectF(5 + dx, 6 + dy, 14, 12), frame=False)
        p.setOpacity(1); p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "fx_gaussian_blur":
        rg = QRadialGradient(12, 12, 9.5); rg.setColorAt(0, I.BLUE); rg.setColorAt(0.45, I.BLUE.lighter(130))
        rg.setColorAt(1, QColor(150, 195, 245, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(rg)); p.drawEllipse(P(12, 12), 10, 10)
    elif name == "fx_motion_blur":
        for k, a in ((0, 255), (3, 120), (6, 60)):
            c = QColor(I.BLUE); c.setAlpha(a)
            p.setPen(Qt.NoPen); p.setBrush(c); p.drawEllipse(P(15 - k, 12), 5, 5)
        p.setPen(I._pen(O, 1.4))
        for y, x0 in ((8.5, 2), (12, 1.5), (15.5, 2)):
            p.drawLine(P(x0, y), P(x0 + 5, y))
    elif name == "fx_radial_blur":
        p.setBrush(Qt.NoBrush)
        for r, a in ((3, 255), (5.5, 190), (8, 120), (10, 70)):
            c = QColor(I.BLUE); c.setAlpha(a); p.setPen(I._pen(c, 1.6))
            p.drawArc(QRectF(12 - r, 12 - r, 2 * r, 2 * r), 20 * 16, 110 * 16)
            p.drawArc(QRectF(12 - r, 12 - r, 2 * r, 2 * r), 200 * 16, 110 * 16)
        p.setPen(Qt.NoPen); p.setBrush(I.BLUE); p.drawEllipse(P(12, 12), 1.5, 1.5)
    elif name == "fx_unfocus":
        for (x, y, r), c in zip(((8, 9, 4.5), (15.5, 10, 4), (11.5, 16, 4.2)),
                                (QColor(245, 195, 50, 150), QColor(40, 120, 215, 130), QColor(215, 60, 50, 120))):
            p.setPen(I._pen(c.darker(110), 1)); p.setBrush(c); p.drawEllipse(P(x, y), r, r)
    elif name == "fx_zoom_blur":
        p.setPen(Qt.NoPen); p.setBrush(I.BLUE); p.drawEllipse(P(12, 12), 3, 3)
        for i in range(12):
            a = i * math.pi / 6
            c = QColor(I.BLUE); c.setAlpha(170); p.setPen(I._pen(c, 1.3))
            p.drawLine(P(12 + 5 * math.cos(a), 12 + 5 * math.sin(a)), P(12 + 10 * math.cos(a), 12 + 10 * math.sin(a)))
    # ------------------------------------------------------------------ Borders
    elif name == "fx_simple_border":
        _photo(p, QRectF(3, 4, 18, 16), frame=False)
        p.setPen(QPen(I.OUT, 2.4, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)); p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(4.2, 5.2, 15.6, 13.6))
    elif name == "fx_double_border":
        _photo(p, QRectF(3, 4, 18, 16), frame=False)
        p.setPen(QPen(I.OUT, 1.3, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)); p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(3.6, 4.6, 16.8, 14.8)); p.drawRect(QRectF(6.2, 7.2, 11.6, 9.6))
    elif name == "fx_picture_frame":
        p.setPen(I._pen(WOOD.darker(140), 1.2)); p.setBrush(WOOD); p.drawRect(QRectF(2, 3, 20, 18))
        p.setBrush(WOOD.lighter(130)); p.drawRect(QRectF(4, 5, 16, 14))
        _photo(p, QRectF(6, 7, 12, 10))
    elif name == "fx_rounded_corners":
        _photo(p, QRectF(3, 4, 18, 16), round_=5)
    elif name == "fx_soft_edge":
        _photo(p, frame=False)
        g = QRadialGradient(12, 12, 12.5); c0 = QColor(I.PAPER); c0.setAlpha(0); g.setColorAt(0.55, c0); g.setColorAt(1, I.PAPER)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(g)); p.drawRect(QRectF(3, 4, 18, 16))
        p.setPen(I._pen(I.GREY, 1, Qt.DotLine)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "fx_rough_edge":
        rnd = random.Random(11)
        pts = []
        for i in range(10):
            pts.append(P(3 + i * 2, 4 + rnd.uniform(-1, 1)))
        for i in range(9):
            pts.append(P(21 + rnd.uniform(-1, 1), 4 + i * 2))
        for i in range(10):
            pts.append(P(21 - i * 2, 20 + rnd.uniform(-1, 1)))
        for i in range(9):
            pts.append(P(3 + rnd.uniform(-1, 1), 20 - i * 2))
        poly = QPolygonF(pts); path = QPainterPath(); path.addPolygon(poly); path.closeSubpath()
        p.save(); p.setClipPath(path); _photo(p, QRectF(1.5, 2.5, 21, 19), frame=False); p.restore()
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    elif name == "fx_drop_shadow":
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 90)); p.drawRoundedRect(QRectF(6, 7, 16, 14), 1.5, 1.5)
        _photo(p, QRectF(2.5, 3, 16, 14))
    # ------------------------------------------------------------------ Distort
    elif name == "fx_bulge":
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(225, 238, 252)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setBrush(Qt.NoBrush); p.setPen(I._pen(I.BLUE, 1.1))
        def warp(x, y):
            dx, dy = x - 12, y - 12; d = math.hypot(dx, dy) / 9
            k = 1 + 0.35 * max(0, 1 - d * d) if d < 1 else 1
            return P(12 + dx * k, 12 + dy * k)
        for v in (6.6, 9.3, 12, 14.7, 17.4):
            a = QPainterPath(warp(v, 3)); b = QPainterPath(warp(3, v))
            for k in range(1, 19):
                t = 3 + k
                a.lineTo(warp(v, t)); b.lineTo(warp(t, v))
            p.drawPath(a); p.drawPath(b)
    elif name == "fx_frosted_glass":
        g = QLinearGradient(3, 3, 21, 21); g.setColorAt(0, QColor(220, 238, 252)); g.setColorAt(1, QColor(170, 205, 240))
        p.setPen(I._pen(O, 1.1)); p.setBrush(QBrush(g)); p.drawRect(QRectF(3, 3, 18, 18))
        _dots(p, QRectF(3, 3, 18, 18), 40, 3, [QColor(255, 255, 255, 230), QColor(120, 160, 210, 160)], (0.4, 0.9))
    elif name == "fx_pixelate":
        rnd = random.Random(2)
        p.setPen(Qt.NoPen)
        for i in range(4):
            for j in range(4):
                c = SKY if j < 2 else HILL
                if (i, j) == (2, 0): c = SUN
                p.setBrush(c.lighter(rnd.choice((100, 112, 90))))
                p.drawRect(QRectF(3 + i * 4.5, 3 + j * 4.5, 4.5, 4.5))
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_tile_reflection":
        _photo(p, QRectF(3, 3, 18, 18), frame=False)
        p.setPen(I._pen(QColor(255, 255, 255, 200), 1.1))
        for v in (7.5, 12, 16.5):
            p.drawLine(P(v, 3), P(v, 21)); p.drawLine(P(3, v), P(21, v))
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_twist":
        path = QPainterPath(P(12, 12))
        for k in range(1, 80):
            a = k * 0.17; r = k * 0.12
            path.lineTo(12 + r * math.cos(a), 12 + r * math.sin(a))
        p.setPen(I._pen(I.BLUE, 1.7)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    # ------------------------------------------------------------------ Noise
    elif name == "fx_add_noise":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        _dots(p, QRectF(3, 3, 18, 18), 30, 4, [O, I.RED, I.GREEN, I.BLUE])
        _plus(p, 18.5, 18.5, 3.5)
    elif name == "fx_median":
        p.setPen(Qt.NoPen); p.setBrush(QColor(200, 225, 250)); p.drawRect(QRectF(12, 3, 9, 18))
        p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 9, 18))
        _dots(p, QRectF(3, 3, 9, 18), 16, 9, [O, I.BLUE])
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(I._pen(O, 0.9, Qt.DashLine)); p.drawLine(P(12, 3), P(12, 21))
    elif name == "fx_reduce_noise":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setOpacity(0.55); _dots(p, QRectF(3, 3, 18, 18), 16, 4, [O, I.BLUE]); p.setOpacity(1)
        p.setPen(I._pen(I.RED, 2.3)); p.drawLine(P(15, 18.5), P(22, 18.5))
    # ------------------------------------------------------------------ Object
    elif name == "fx_align_object":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(I._pen(I.ORANGE.darker(130), 1.1)); p.setBrush(I.ORANGE); p.drawRect(QRectF(9, 9, 6, 6))
        p.setPen(I._pen(GUIDE, 1.1, Qt.DashLine)); p.drawLine(P(12, 3), P(12, 21)); p.drawLine(P(3, 12), P(21, 12))
    elif name == "fx_feather_object":
        rg = QRadialGradient(12, 12, 9); rg.setColorAt(0.45, I.ORANGE); rg.setColorAt(1, QColor(235, 130, 40, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(rg)); p.drawEllipse(P(12, 12), 9.5, 9.5)
        p.save(); p.translate(12, 12); p.rotate(-35)
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER)
        f = QPainterPath(P(0, -9)); f.cubicTo(4.5, -4, 3.5, 4, 0, 8); f.cubicTo(-3.5, 4, -4.5, -4, 0, -9)
        p.drawPath(f); p.drawLine(P(0, -6), P(0, 10)); p.restore()
    # ------------------------------------------------------------------ Photo
    elif name == "fx_glow":
        rg = QRadialGradient(12, 12, 10.5); rg.setColorAt(0, QColor(255, 245, 190)); rg.setColorAt(0.5, QColor(255, 220, 100, 170))
        rg.setColorAt(1, QColor(255, 210, 80, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(rg)); p.drawEllipse(P(12, 12), 11, 11)
        p.setPen(I._pen(SUN.darker(130), 1.1)); p.setBrush(SUN); p.drawEllipse(P(12, 12), 4.5, 4.5)
    elif name == "fx_old_photo":
        _photo(p, QRectF(3, 4, 18, 16), sky=_sepia(SKY), hill=_sepia(HILL), sun=_sepia(SUN))
        p.setPen(I._pen(QColor(255, 250, 235, 210), 0.8))
        p.drawLine(P(6, 5), P(9, 19)); p.drawLine(P(15, 5), P(14, 12))
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(245, 235, 210))
        p.drawPolygon(QPolygonF([P(16, 20), P(21, 15), P(21, 20)]))
    elif name == "fx_sharpen":
        I._draw("sharpen", p)
    elif name == "fx_soften_portrait":
        rg = QRadialGradient(12, 12, 10); rg.setColorAt(0.6, QColor(250, 225, 200)); rg.setColorAt(1, QColor(250, 225, 200, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(rg)); p.drawEllipse(P(12, 12), 10, 10)
        p.setPen(I._pen(QColor(150, 100, 70), 1.2)); p.setBrush(QColor(248, 222, 196)); p.drawEllipse(P(12, 12), 6.5, 7.5)
        p.setPen(Qt.NoPen); p.setBrush(QColor(90, 60, 45)); p.drawEllipse(P(9.6, 10.5), 0.9, 0.9); p.drawEllipse(P(14.4, 10.5), 0.9, 0.9)
        p.setPen(I._pen(QColor(150, 80, 70), 1.1)); p.setBrush(Qt.NoBrush); p.drawArc(QRectF(9.5, 11.5, 5, 4), 200 * 16, 140 * 16)
    elif name == "fx_vignette":
        _photo(p, frame=False)
        g = QRadialGradient(12, 12, 12.5); g.setColorAt(0.45, QColor(0, 0, 0, 0)); g.setColorAt(1, QColor(0, 0, 0, 210))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(g)); p.drawRect(QRectF(3, 4, 18, 16))
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    # ------------------------------------------------------------------ Render (new)
    elif name == "fx_difference_clouds":
        p.setPen(Qt.NoPen); p.setBrush(QColor(30, 30, 40)); p.drawRect(QRectF(3, 4, 18, 16))
        p.setBrush(QColor(250, 250, 255))
        p.drawPolygon(QPolygonF([P(3, 4), P(21, 4), P(3, 20)]))
        path = QPainterPath(); path.addRoundedRect(QRectF(6, 12, 13, 4.5), 2.2, 2.2)
        for cx, cy, r in ((9, 12.5, 3), (13, 10.5, 3.8), (16.5, 12.8, 2.6)):
            c = QPainterPath(); c.addEllipse(P(cx, cy), r, r); path = path.united(c)
        p.setBrush(QColor(120, 140, 200)); p.drawPath(path)
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 4, 18, 16))
    elif name == "fx_fibres":
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(225, 235, 250)); p.drawRect(QRectF(3, 3, 18, 18))
        rnd = random.Random(6)
        for i in range(9):
            x = 4.5 + i * 1.9 + rnd.uniform(-0.4, 0.4)
            p.setPen(I._pen(QColor(40, 80, 160, rnd.randint(110, 230)), rnd.uniform(0.9, 1.6)))
            p.drawLine(P(x, 3.6), P(x + rnd.uniform(-0.8, 0.8), 20.4))
    elif name in ("fx_julia_fractal", "fx_mandelbrot_fractal"):
        g = QLinearGradient(3, 3, 21, 21)
        if name == "fx_julia_fractal":
            g.setColorAt(0, QColor(230, 60, 140)); g.setColorAt(1, QColor(120, 60, 220))
        else:
            g.setColorAt(0, QColor(240, 200, 40)); g.setColorAt(1, QColor(200, 40, 20))
        p.setPen(I._pen(O, 1.1)); p.setBrush(QBrush(g)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(Qt.NoPen); p.setBrush(QColor(15, 15, 20))
        if name == "fx_mandelbrot_fractal":
            p.drawEllipse(P(14, 12), 4.2, 4.2); p.drawEllipse(P(8.6, 12), 2.2, 2.2); p.drawEllipse(P(5.6, 12), 0.9, 0.9)
            p.drawEllipse(P(14, 7.3), 1.2, 1.2); p.drawEllipse(P(14, 16.7), 1.2, 1.2)
        else:
            for cx, cy, r in ((12, 12, 2.6), (8, 10.5, 1.8), (16, 13.5, 1.8), (5.5, 9, 1), (18.5, 15, 1)):
                p.drawEllipse(P(cx, cy), r, r)
    elif name == "fx_lens_flare":
        p.setPen(Qt.NoPen); p.setBrush(QColor(30, 50, 100)); p.drawRect(QRectF(3, 3, 18, 18))
        rg = QRadialGradient(8, 8, 6); rg.setColorAt(0, QColor(255, 255, 240)); rg.setColorAt(0.4, QColor(255, 220, 140, 200))
        rg.setColorAt(1, QColor(255, 200, 100, 0)); p.setBrush(QBrush(rg)); p.drawEllipse(P(8, 8), 6, 6)
        p.setPen(I._pen(QColor(255, 245, 210), 0.9)); p.drawLine(P(3, 8), P(14, 8)); p.drawLine(P(8, 3), P(8, 13))
        p.setPen(Qt.NoPen)
        for (cx, cy, r), c in zip(((13.5, 13.5, 1.6), (16.5, 16.5, 2.4), (19, 19, 1.2)),
                                  (QColor(120, 200, 255, 170), QColor(140, 255, 160, 120), QColor(255, 170, 90, 170))):
            p.setBrush(c); p.drawEllipse(P(cx, cy), r, r)
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_lighting_effects":
        p.setPen(Qt.NoPen); p.setBrush(QColor(40, 40, 50)); p.drawRect(QRectF(3, 3, 18, 18))
        rg = QRadialGradient(13, 15, 7.5); rg.setColorAt(0, QColor(255, 245, 200)); rg.setColorAt(1, QColor(255, 235, 170, 0))
        p.setBrush(QBrush(rg)); p.drawEllipse(P(13, 15), 7.5, 5)
        p.setBrush(QColor(255, 240, 180, 60))
        p.drawPolygon(QPolygonF([P(5, 4.5), P(7.5, 4), P(19, 13), P(9, 19.5)]))
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(200, 200, 210)); p.drawRoundedRect(QRectF(3.5, 3.5, 4, 3), 0.8, 0.8)
        p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_plasma":
        p.setPen(Qt.NoPen)
        for (cx, cy, r), c in zip(((8, 8, 7), (16, 9, 6.5), (9, 16, 6.5), (16, 16, 6)),
                                  (QColor(255, 60, 120), QColor(255, 220, 40), QColor(40, 200, 120), QColor(70, 110, 255))):
            rg = QRadialGradient(cx, cy, r); rg.setColorAt(0, c); c2 = QColor(c); c2.setAlpha(0); rg.setColorAt(1, c2)
            p.setBrush(QBrush(rg)); p.drawEllipse(P(cx, cy), r, r)
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_rain_and_snow":
        path = QPainterPath(); path.addRoundedRect(QRectF(4, 7, 15, 5), 2.5, 2.5)
        for cx, cy, r in ((8, 7.5, 3.3), (12.5, 5.8, 4), (16, 8, 2.8)):
            c = QPainterPath(); c.addEllipse(P(cx, cy), r, r); path = path.united(c)
        p.setPen(I._pen(QColor(120, 130, 150), 1.2)); p.setBrush(QColor(235, 238, 245)); p.drawPath(path)
        p.setPen(I._pen(I.BLUE, 1.4))
        for x in (6.5, 10.5):
            p.drawLine(P(x, 15), P(x - 1.2, 19.5))
        p.setPen(Qt.NoPen); p.setBrush(QColor(150, 190, 240))
        for x, y in ((15, 16), (18, 19.5), (14, 21)):
            p.drawEllipse(P(x, y), 1.2, 1.2)
    elif name == "fx_starfield":
        p.setPen(Qt.NoPen); p.setBrush(QColor(15, 20, 45)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setBrush(QColor(255, 255, 255))
        for x, y, r in ((6, 6, 0.7), (17, 5.5, 0.6), (8, 17, 0.6), (19, 15, 0.8), (13, 19, 0.5), (5, 12, 0.5)):
            p.drawEllipse(P(x, y), r, r)
        p.setPen(I._pen(QColor(255, 250, 220), 1.1))
        p.drawLine(P(12.5, 7), P(12.5, 15)); p.drawLine(P(8.5, 11), P(16.5, 11))
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 250, 220)); p.drawEllipse(P(12.5, 11), 1.6, 1.6)
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_sunburst":
        p.save(); clip = QPainterPath(); clip.addRect(QRectF(3, 3, 18, 18)); p.setClipPath(clip)
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 236, 160)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setBrush(QColor(250, 190, 50))
        for i in range(8):
            a0 = i * math.pi / 4; a1 = a0 + math.pi / 8
            p.drawPolygon(QPolygonF([P(12, 12), P(12 + 16 * math.cos(a0), 12 + 16 * math.sin(a0)),
                                     P(12 + 16 * math.cos(a1), 12 + 16 * math.sin(a1))]))
        p.restore()
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    elif name == "fx_textures":
        p.save(); clip = QPainterPath(); clip.addRect(QRectF(3, 3, 18, 18)); p.setClipPath(clip)
        p.setPen(Qt.NoPen); p.setBrush(QColor(200, 145, 90)); p.drawRect(QRectF(3, 3, 18, 18))
        p.setBrush(Qt.NoBrush)
        for r in (4, 7, 10, 13, 16):
            p.setPen(I._pen(QColor(120, 70, 30), 1.1)); p.drawEllipse(P(1, 12), r * 1.3, r)
        p.restore()
        p.setPen(I._pen(O, 1.1)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(3, 3, 18, 18))
    # ------------------------------------------------------------------ Stylise
    elif name == "fx_edge_detect":
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(40, 40, 48)); p.drawRect(QRectF(3, 4, 18, 16))
        p.setPen(I._pen(QColor(120, 230, 255), 1.3)); p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([P(3.5, 16), P(9.8, 10.7), P(14.2, 14.9), P(17, 13), P(20.5, 16.4)]))
        p.drawEllipse(P(16, 8.8), 2.1, 2.1)
    elif name == "fx_emboss":
        p.setPen(I._pen(O, 1.1)); p.setBrush(QColor(160, 162, 170)); p.drawRect(QRectF(3, 3, 18, 18))
        path = QPainterPath(P(5, 17)); path.lineTo(10, 8); path.lineTo(14, 14); path.lineTo(16.5, 11); path.lineTo(19.5, 17)
        p.setBrush(Qt.NoBrush)
        p.setPen(I._pen(QColor(250, 250, 255), 1.8)); p.drawPath(path.translated(-0.7, -0.7))
        p.setPen(I._pen(QColor(70, 72, 80), 1.8)); p.drawPath(path.translated(0.7, 0.7))
    elif name == "fx_outline":
        p.setPen(I._pen(O, 1.1)); p.setBrush(I.PAPER); p.drawRect(QRectF(3, 3, 18, 18))
        p.setPen(I._pen(I.BLUE, 2.6)); p.setBrush(Qt.NoBrush)
        star = QPolygonF()
        for i in range(10):
            r = 6.5 if i % 2 == 0 else 3
            a = -math.pi / 2 + i * math.pi / 5
            star.append(P(12 + r * math.cos(a), 12.5 + r * math.sin(a)))
        p.drawPolygon(star)
    elif name == "fx_relief":
        _photo(p, frame=False)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.6)); p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([P(3.5, 19.5), P(3.5, 4.5), P(20.5, 4.5)]))
        p.setPen(QPen(QColor(0, 0, 0, 140), 1.6))
        p.drawPolyline(QPolygonF([P(20.5, 4.5), P(20.5, 19.5), P(3.5, 19.5)]))
        p.setPen(I._pen(O, 1.1)); p.drawRect(QRectF(3, 4, 18, 16))
    else:
        return False
    return True


def slug(text):
    import re
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
