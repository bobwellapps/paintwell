"""Effects › Borders. Each works on premultiplied float RGBA (HxWx4, 0..1).

Borders that can go outside the picture declare grow(params) -> (left, top, right, bottom) pixels;
the main window enlarges the canvas by that much first (only when nothing is selected)."""
import math
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QLinearGradient

from . import imaging as im

COLOURS = ["Primary colour", "Secondary colour", "White", "Black"]


def _colour(name, ctx):
    if name.startswith("Primary"):
        return QColor(ctx["primary"])
    if name.startswith("Secondary"):
        return QColor(ctx["secondary"])
    return QColor(255, 255, 255) if name == "White" else QColor(0, 0, 0)


def _qimg(a):
    return im.from_array(np.ascontiguousarray(a), premultiplied=True)


def _arr(img):
    return im.to_array(img, premultiplied=True)


def _rgba(c):
    """QColor -> premultiplied float RGBA."""
    a = c.alphaF()
    return np.array([c.redF() * a, c.greenF() * a, c.blueF() * a, a], np.float32)


def _rounded_cover(h, w, inset, radius):
    """Antialiased coverage (0..1) of a rounded rectangle inset from the edges."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    cx, cy = w / 2.0, h / 2.0
    hx, hy = max(w / 2.0 - inset, 0.0), max(h / 2.0 - inset, 0.0)
    r = max(0.0, min(radius, hx, hy))
    qx = np.abs(xs - cx) - (hx - r)
    qy = np.abs(ys - cy) - (hy - r)
    out = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.clip(0.5 - out, 0, 1).astype(np.float32)


def _fill_outside(a, cover, fill, ctx):
    """Outside the covered area becomes transparent, or the chosen colour."""
    c = cover[..., None]
    if fill == "Transparent":
        return a * c
    col = _rgba(_colour(fill, ctx))
    return a * c + col * (1 - c)


# ---------------------------------------------------------------- simple / double line
def _grow_simple(p):
    if not p["outside"]:
        return (0, 0, 0, 0)
    m = int(p["width"] + p["gap"])
    return (m, m, m, m)


def simple_border(a, p, ctx):
    h, w = a.shape[:2]
    bw, gap, rad = p["width"], p["gap"], p["radius"]
    img = _qimg(a)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    if rad > 0 and p["outside_corners"] != "Leave as they are":
        q.end()
        a2 = _fill_outside(_arr(img), _rounded_cover(h, w, gap, rad + bw / 2.0), p["outside_corners"], ctx)
        img = _qimg(a2)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(_colour(p["colour"], ctx), bw)
    pen.setJoinStyle(Qt.MiterJoin if rad == 0 else Qt.RoundJoin)
    style = p["style"]
    if style == "Dashed":
        pen.setDashPattern([3, 2]); pen.setCapStyle(Qt.FlatCap)
    elif style == "Dotted":
        pen.setDashPattern([0.01, 2]); pen.setCapStyle(Qt.RoundCap)
    q.setPen(pen)
    q.setBrush(Qt.NoBrush)
    r = QRectF(gap + bw / 2.0, gap + bw / 2.0, w - 2 * gap - bw, h - 2 * gap - bw)
    if r.width() > 0 and r.height() > 0:
        if rad > 0:
            q.drawRoundedRect(r, rad, rad)
        else:
            q.drawRect(r)
    q.end()
    return _arr(img)


def _grow_double(p):
    if not p["outside"]:
        return (0, 0, 0, 0)
    m = int(p["outer"] + p["space"] + p["inner"])
    return (m, m, m, m)


def double_border(a, p, ctx):
    h, w = a.shape[:2]
    img = _qimg(a)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    col = _colour(p["colour"], ctx)
    q.setBrush(Qt.NoBrush)
    o, s, i = p["outer"], p["space"], p["inner"]
    if p["space_fill"] != "See-through (keep picture)":
        sc = _colour(p["space_fill"], ctx)
        q.setPen(QPen(sc, s, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        q.drawRect(QRectF(o + s / 2.0, o + s / 2.0, w - 2 * o - s, h - 2 * o - s))
    for off, width in ((0, o), (o + s, i)):
        if width <= 0:
            continue
        q.setPen(QPen(col, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        r = QRectF(off + width / 2.0, off + width / 2.0, w - 2 * off - width, h - 2 * off - width)
        if r.width() > 0 and r.height() > 0:
            q.drawRect(r)
    q.end()
    return _arr(img)


# ---------------------------------------------------------------- picture frame
FRAME_STYLES = ["Moulded", "Bevelled", "Rounded", "Wood", "Gold", "Silver", "Flat", "Instant photo (wide bottom)"]
MOUNT_COLOURS = ["White", "Cream", "Black", "Grey", "Primary colour", "Secondary colour"]


def _frame_sizes(p):
    f = int(p["width"])
    m = int(p.get("mount", 0))
    bottom = int(f * 3.5) if p["style"].startswith("Instant") else f
    return f, m, bottom


def _grow_frame(p):
    if not p["outside"]:
        return (0, 0, 0, 0)
    f, m, fb = _frame_sizes(p)
    return (f + m, f + m, f + m, fb + m)


def _mix(c, t, k):
    k = min(max(k, 0.0), 1.0)
    return QColor(int(c.red() + (t.red() - c.red()) * k), int(c.green() + (t.green() - c.green()) * k),
                  int(c.blue() + (t.blue() - c.blue()) * k))


def _shade(c, k):
    """k > 0 lightens towards white, k < 0 darkens towards black."""
    return _mix(c, QColor(255, 255, 255), k) if k >= 0 else _mix(c, QColor(0, 0, 0), -k)


def _ring_sides(x0, y0, x1, y1, t, b=None):
    """The four mitred sides of a frame ring: outer box (x0,y0)-(x1,y1), width t (bottom b).
    Returns [(name, polygon points, gradient start, gradient end)] going outer -> inner."""
    b = t if b is None else b
    ix0, iy0, ix1, iy1 = x0 + t, y0 + t, x1 - t, y1 - b
    P = QPointF
    return [
        ("top", [P(x0, y0), P(x1, y0), P(ix1, iy0), P(ix0, iy0)], P(0, y0), P(0, iy0)),
        ("left", [P(x0, y0), P(ix0, iy0), P(ix0, iy1), P(x0, y1)], P(x0, 0), P(ix0, 0)),
        ("right", [P(x1, y0), P(x1, y1), P(ix1, iy1), P(ix1, iy0)], P(x1, 0), P(ix1, 0)),
        ("bottom", [P(x0, y1), P(ix0, iy1), P(ix1, iy1), P(x1, y1)], P(0, y1), P(0, iy1)),
    ]


def _poly_path(pts):
    path = QPainterPath(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    return path


LIGHT = {"top": 1.0, "left": 0.5, "right": -0.5, "bottom": -1.0}     # light from the top-left


def picture_frame(a, p, ctx):
    h, w = a.shape[:2]
    W, H = float(w), float(h)
    f, m, fb = _frame_sizes(p)
    f, fb = float(min(f, W / 2.0, H / 2.0)), float(min(fb, H / 2.0))
    style = p["style"]
    k = p.get("shading", 50) / 50.0            # 1 = normal shading, 0 = none, 2 = strong
    img = _qimg(a)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    base = {"Wood": QColor(128, 80, 40), "Gold": QColor(200, 160, 60),
            "Silver": QColor(178, 180, 188)}.get(style, _colour(p["colour"], ctx))
    white, black = QColor(255, 255, 255), QColor(0, 0, 0)
    # cross-section of the moulding, from the outer edge (0) to the picture (1): (pos, lighten/darken)
    profiles = {
        "Moulded": [(0.0, -0.35), (0.12, -0.1), (0.32, 0.38), (0.5, 0.08), (0.82, -0.2), (1.0, -0.42)],
        "Rounded": [(0.0, -0.5), (0.18, -0.12), (0.42, 0.42), (0.6, 0.1), (0.85, -0.25), (1.0, -0.5)],
        "Wood": [(0.0, -0.4), (0.1, -0.05), (0.3, 0.22), (0.55, 0.0), (0.8, -0.18), (1.0, -0.45)],
        "Gold": [(0.0, -0.45), (0.1, 0.1), (0.2, 0.55), (0.3, 0.0), (0.45, 0.35), (0.6, -0.15),
                 (0.75, 0.45), (0.88, -0.1), (1.0, -0.5)],
        "Silver": [(0.0, -0.45), (0.1, 0.1), (0.2, 0.6), (0.3, 0.0), (0.45, 0.35), (0.6, -0.15),
                   (0.75, 0.5), (0.88, -0.1), (1.0, -0.5)],
    }
    instant = style.startswith("Instant")
    sides = _ring_sides(0, 0, W, H, f, fb if instant else f)
    q.setPen(Qt.NoPen)
    for name, pts, g0, g1 in sides:
        path = _poly_path(pts)
        lean = LIGHT[name]
        if style in profiles:
            g = QLinearGradient(g0, g1)
            for pos, v in profiles[style]:
                g.setColorAt(pos, _shade(base, (v + 0.07 * lean) * k))
            q.fillPath(path, QBrush(g))
        elif style == "Bevelled":
            # the original look: each side a plain colour, lit from the top-left
            q.fillPath(path, _shade(base, 0.3 * lean * k))
        else:  # Flat, Instant photo: filled in one piece below (no joins showing)
            pass
        if style == "Wood":
            # grain running along each side
            rnd = np.random.default_rng(7 + len(name))
            q.save(); q.setClipPath(path)
            along_x = name in ("top", "bottom")
            length = W if along_x else H
            for i in range(max(4, int(f / 2.5))):
                off = rnd.uniform(0, f)
                amp, freq, ph = rnd.uniform(0.3, 1.6), rnd.uniform(0.004, 0.02), rnd.uniform(0, 6.28)
                gp = QPainterPath()
                n = max(8, int(length / 12))
                for j in range(n + 1):
                    t = length * j / n
                    o = off + amp * math.sin(t * freq * 6.28 + ph)
                    if name == "top": pt = QPointF(t, o)
                    elif name == "bottom": pt = QPointF(t, H - o)
                    elif name == "left": pt = QPointF(o, t)
                    else: pt = QPointF(W - o, t)
                    gp.moveTo(pt) if j == 0 else gp.lineTo(pt)
                c = QColor(40, 20, 5, int(rnd.uniform(25, 70)))
                q.setPen(QPen(c, rnd.uniform(0.6, 1.6))); q.setBrush(Qt.NoBrush); q.drawPath(gp)
            q.restore(); q.setPen(Qt.NoPen)
    if style not in profiles and style != "Bevelled":
        ring = QPainterPath(); ring.addRect(QRectF(0, 0, W, H))
        hole = QPainterPath(); hole.addRect(QRectF(f, f, W - 2 * f, H - f - (fb if instant else f)))
        q.fillPath(ring.subtracted(hole), base)
    if style in profiles or style == "Bevelled":
        # fine mitre joins at the corners, like a real frame
        q.setPen(QPen(_shade(base, -0.45), max(0.8, f / 60.0)))
        b = fb if instant else f
        for x0, y0, x1, y1 in ((0, 0, f, f), (W, 0, W - f, f), (0, H, f, H - b), (W, H, W - f, H - b)):
            q.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        q.setPen(Qt.NoPen)
    # the opening inside the frame
    ox0, oy0, ox1, oy1 = f, f, W - f, H - (fb if instant else f)
    # mount (mat board) between frame and picture, with a cut bevel round the picture
    if m > 0 and ox1 - ox0 > 2 * m and oy1 - oy0 > 2 * m:
        mc = p.get("mount_colour", "White")
        mcol = {"White": QColor(248, 247, 242), "Cream": QColor(240, 230, 205), "Black": QColor(28, 28, 30),
                "Grey": QColor(150, 150, 152)}.get(mc) or _colour(mc, ctx)
        ring = QPainterPath(); ring.addRect(QRectF(ox0, oy0, ox1 - ox0, oy1 - oy0))
        hole = QPainterPath(); hole.addRect(QRectF(ox0 + m, oy0 + m, ox1 - ox0 - 2 * m, oy1 - oy0 - 2 * m))
        q.fillPath(ring.subtracted(hole), mcol)
        cut = max(1.5, min(m / 8.0, 6.0))       # the bevelled cut edge of the mount
        for name, pts, g0, g1 in _ring_sides(ox0 + m - cut, oy0 + m - cut, ox1 - m + cut, oy1 - m + cut, cut):
            q.fillPath(_poly_path(pts), _shade(_mix(mcol, white, 0.5), -0.12 * (LIGHT[name] < 0) + 0.05 * LIGHT[name]))
        ox0, oy0, ox1, oy1 = ox0 + m, oy0 + m, ox1 - m, oy1 - m
    # shadow the frame (or mount) casts onto the picture, top and left
    if p.get("inner_shadow", True) and not instant and style != "Flat":
        sw = max(2.0, f / 6.0)
        strength = int(min(255, 120 * max(k, 0.3)))
        for g0, g1, rect in ((QPointF(0, oy0), QPointF(0, oy0 + sw), QRectF(ox0, oy0, ox1 - ox0, sw)),
                             (QPointF(ox0, 0), QPointF(ox0 + sw, 0), QRectF(ox0, oy0, sw, oy1 - oy0))):
            g = QLinearGradient(g0, g1)
            g.setColorAt(0, QColor(0, 0, 0, strength)); g.setColorAt(1, QColor(0, 0, 0, 0))
            q.fillRect(rect, QBrush(g))
    q.setPen(QPen(QColor(0, 0, 0, 110), max(1.0, f / 18.0)))
    q.setBrush(Qt.NoBrush)
    q.drawRect(QRectF(ox0, oy0, ox1 - ox0, oy1 - oy0))
    q.end()
    return _arr(img)


# ---------------------------------------------------------------- shaped edges
def rounded_corners(a, p, ctx):
    h, w = a.shape[:2]
    return _fill_outside(a, _rounded_cover(h, w, 0, p["radius"]), p["fill"], ctx)


def soft_edge(a, p, ctx):
    h, w = a.shape[:2]
    fw = max(1.0, float(p["width"]))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    if p["shape"] == "Oval":
        nx = (xs - w / 2.0) / (w / 2.0)
        ny = (ys - h / 2.0) / (h / 2.0)
        r = np.sqrt(nx * nx + ny * ny)                      # 1 at the oval's edge
        inner = 1.0 - fw / (min(w, h) / 2.0)
        t = np.clip((1.0 - r) / max(1e-3, 1.0 - inner), 0, 1)
    else:
        d = np.minimum(np.minimum(xs, w - xs), np.minimum(ys, h - ys))
        t = np.clip(d / fw, 0, 1)
    t = t * t * (3 - 2 * t)                                 # smooth fade
    return _fill_outside(a, t.astype(np.float32), p["fill"], ctx)


def _noise1d(n, scale, rng):
    """Smooth random wobble, length n, roughly in -1..1."""
    out = np.zeros(n, np.float32)
    amp, total, step = 1.0, 0.0, max(2.0, scale)
    while step >= 1.0:
        pts = rng.uniform(-1, 1, int(n / step) + 3).astype(np.float32)
        x = np.arange(n, dtype=np.float32) / step
        i = np.floor(x).astype(int); f = x - i
        f = f * f * (3 - 2 * f)
        out += (pts[i] * (1 - f) + pts[i + 1] * f) * amp
        total += amp; amp *= 0.5; step /= 2.5
    return out / total


def rough_edge(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    depth = float(p["width"])
    rough = p["roughness"] / 100.0
    scale = max(3.0, depth * (2.2 - 1.8 * rough))
    top = depth * (0.55 + 0.45 * _noise1d(w, scale, rng))
    bot = depth * (0.55 + 0.45 * _noise1d(w, scale, rng))
    lef = depth * (0.55 + 0.45 * _noise1d(h, scale, rng))
    rig = depth * (0.55 + 0.45 * _noise1d(h, scale, rng))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    d = np.minimum.reduce([ys - top[None, :], (h - ys) - bot[None, :],
                           xs - lef[:, None], (w - xs) - rig[:, None]])
    cover = np.clip(d + 0.5, 0, 1).astype(np.float32)
    return _fill_outside(a, cover, p["fill"], ctx)


# ---------------------------------------------------------------- drop shadow
def _grow_shadow(p):
    if not p["outside"]:
        return (0, 0, 0, 0)
    b = int(p["blur"])
    dx, dy = int(p["offset_x"]), int(p["offset_y"])
    return (max(0, b - dx) + 2, max(0, b - dy) + 2, max(0, b + dx) + 2, max(0, b + dy) + 2)


def _backdrop(a, p, ctx):
    """What the shadow falls on around the picture."""
    choice = p.get("background", "White")
    out = np.zeros_like(a)
    if choice == "Transparent":
        return out
    if choice == "Secondary colour":
        if ctx.get("background") is None:      # an upper layer: keep it see-through
            return out
        out[...] = _rgba(QColor(ctx["background"]))
        return out
    out[...] = _rgba(QColor(255, 255, 255) if choice == "White" else QColor(0, 0, 0))
    return out


def drop_shadow(a, p, ctx):
    """Shadow behind an object:
    - the whole picture, once the canvas has grown to make room for it;
    - the selected part, when there's a selection (cast onto the rest of the layer);
    - a cut-out object on a see-through layer;
    - a picture that fills the whole layer with no room added: it's shrunk a little to make
      room inside the canvas, otherwise the shadow would be hidden behind it."""
    h, w = a.shape[:2]
    alpha = a[..., 3]
    grown = ctx.get("grown")
    obj = ctx.get("object_mask")
    if obj is not None and obj.shape == alpha.shape:
        src_mask = obj.astype(np.float32)
        shape = src_mask * alpha
        composite_under = True
    elif grown and grown != (0, 0, 0, 0):
        l, t, r, b = grown
        src_mask = np.zeros((h, w), np.float32)
        src_mask[t:h - b, l:w - r] = 1.0
        # the added room gets the chosen background (not whatever the canvas grew with)
        a = a * src_mask[..., None] + _backdrop(a, p, ctx) * (1 - src_mask[..., None])
        shape = src_mask * alpha            # a cut-out object casts its own shape, a photo a rectangle
        src_mask = shape
        composite_under = True
    elif (alpha < 0.5).mean() > 0.002:
        shape = alpha
        composite_under = False
    else:
        # fills the whole layer: shrink it into the room the shadow needs
        l, t, r, b = _grow_shadow(dict(p, outside=True))
        iw, ih = w - l - r, h - t - b
        if iw < 8 or ih < 8:
            return a
        small = im.to_array(_qimg(a).scaled(iw, ih, Qt.IgnoreAspectRatio, Qt.SmoothTransformation), premultiplied=True)
        out = _backdrop(a, p, ctx)
        out[t:t + ih, l:l + iw] = small
        a = out
        src_mask = np.zeros((h, w), np.float32)
        src_mask[t:t + ih, l:l + iw] = 1.0
        shape = src_mask
        composite_under = True
    dx, dy = int(p["offset_x"]), int(p["offset_y"])
    sh = np.zeros_like(shape)
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    sh[ys0:ys1, xs0:xs1] = shape[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    if p["blur"] > 0:
        sh = im.gaussian_blur(sh[..., None], p["blur"])[..., 0]
    sh = sh * (p["opacity"] / 100.0)
    col = _rgba(_colour(p["colour"], ctx))
    shadow = col[None, None, :] * sh[..., None]
    if composite_under:
        m = src_mask[..., None]
        under = shadow + a * (1 - shadow[..., 3:4])      # shadow over whatever is around the object
        return a * m + under * (1 - m)
    return a + shadow * (1 - a[..., 3:4])                 # shadow goes behind the object
