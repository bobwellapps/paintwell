"""Effects › Render: lens flare, lighting, fractals, plasma, textures, stars, rain/snow,
sunburst, fibres and difference clouds.  Each takes and returns premultiplied float RGBA
(HxWx4, 0..1), like the other effects."""
import math
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QRadialGradient, QBrush

from . import imaging as im


def _check(ctx):
    c = ctx.get("cancelled")
    if c is not None and c():
        from .effects import Cancelled
        raise Cancelled()


def _rgb(c):
    return np.array([c.redF(), c.greenF(), c.blueF()], np.float32)


def _pick(name, ctx):
    if name.startswith("Primary"):
        return QColor(ctx["primary"])
    if name.startswith("Secondary"):
        return QColor(ctx["secondary"])
    return QColor(name)


def _opaque(rgb, a=None):
    """RGB (0..1) -> premultiplied RGBA; keeps the layer's see-through areas if `a` given."""
    h, w = rgb.shape[:2]
    out = np.empty((h, w, 4), np.float32)
    out[..., :3] = np.clip(rgb, 0, 1)
    out[..., 3] = 1.0
    return out


def _screen(a, light):
    """Add light (RGB, 0..1+) over premultiplied a, screen-style, never above the pixel's alpha."""
    out = a.copy()
    al = a[..., 3:4]
    L = np.clip(light, 0, 1)
    out[..., :3] = a[..., :3] + L * (al - a[..., :3])
    return out


def _straight(a):
    al = a[..., 3:4]
    return np.where(al > 1e-6, a[..., :3] / np.maximum(al, 1e-6), 0.0).astype(np.float32)


def _from_straight(rgb, a):
    out = a.copy()
    out[..., :3] = np.clip(rgb, 0, 1) * a[..., 3:4]
    return out


def _over(a, top):
    """Premultiplied top over premultiplied a."""
    return top + a * (1 - top[..., 3:4])


def _qimage(h, w):
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def _coords(h, w):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return xs + 0.5, ys + 0.5


# ------------------------------------------------------------------ noise
def fbm(h, w, scale, rough, rng, sx=1.0, sy=1.0, ctx=None, min_size=1.0):
    """Smooth fractal value noise, 0..1.  sx/sy stretch it (sy > 1 = long vertical streaks)."""
    total = np.zeros((h, w), np.float32)
    amp, norm = 1.0, 0.0
    size = max(2.0, float(scale))
    while size >= max(1.0, min_size):
        if ctx is not None:
            _check(ctx)
        szx, szy = size * sx, size * sy
        gh, gw = int(h / szy) + 3, int(w / szx) + 3
        grid = rng.random((gh, gw)).astype(np.float32)
        ys = (np.arange(h, dtype=np.float32) / szy)
        xs = (np.arange(w, dtype=np.float32) / szx)
        y0 = np.floor(ys).astype(np.int32); x0 = np.floor(xs).astype(np.int32)
        fy = ys - y0; fx = xs - x0
        fy = (fy * fy * (3 - 2 * fy))[:, None]; fx = (fx * fx * (3 - 2 * fx))[None, :]
        # separable: smooth along x on the small grid first, then along y (much less work)
        rows = grid[:, x0] * (1 - fx) + grid[:, x0 + 1] * fx          # gh x w
        layer = rows[y0] * (1 - fy) + rows[y0 + 1] * fy                 # h x w
        if amp != 1.0:
            layer *= amp
        total += layer
        norm += amp
        amp *= rough
        size /= 2
    t = total / norm
    return np.clip((t - t.min()) / max(float(t.max() - t.min()), 1e-6), 0, 1)


# ------------------------------------------------------------------ difference clouds
def difference_clouds(a, p, ctx):
    h, w = a.shape[:2]
    t = fbm(h, w, p["scale"], p["roughness"] / 100.0, np.random.default_rng(p["seed"]), ctx=ctx)[..., None]
    if p["colours"] == "Black and white":
        c0, c1 = np.zeros(3, np.float32), np.ones(3, np.float32)
    else:
        c0, c1 = _rgb(ctx["primary"]), _rgb(ctx["secondary"])
    cl = c0 * (1 - t) + c1 * t
    return _from_straight(np.abs(_straight(a) - cl), a)


# ------------------------------------------------------------------ fibres
def fibres(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    var = max(1, p["variance"])
    strength = max(1, p["strength"])
    t = fbm(h, w, var, 0.55, rng, sx=0.25, sy=strength / 4.0, ctx=ctx)
    fine = fbm(h, w, 6, 0.5, rng, sx=0.25, sy=strength / 2.0, ctx=ctx)
    t = np.clip(t * 0.75 + fine * 0.25, 0, 1)
    t = np.clip((t - t.mean()) * 1.8 + 0.5, 0, 1)[..., None]
    c0, c1 = _rgb(ctx["primary"]), _rgb(ctx["secondary"])
    cl = c0 * (1 - t) + c1 * t
    if p["blend"] == "Multiply":
        return _from_straight(_straight(a) * cl, a)
    return _opaque(cl)


# ------------------------------------------------------------------ fractals
PALETTES = ["Rainbow", "Fire", "Ice", "Primary to secondary colour"]


def _palette(t, name, ctx):
    """t: 0..1 (cycles) -> RGB."""
    t = t[..., None]
    if name == "Rainbow":
        return 0.5 + 0.5 * np.cos(2 * np.pi * (t + np.array([0.0, 0.33, 0.67], np.float32)))
    if name == "Fire":
        s = (0.5 - 0.5 * np.cos(2 * np.pi * t))           # 0 -> 1 -> 0 so the cycle is seamless
        return np.clip(np.concatenate([s * 3, s * 3 - 1, s * 3 - 2], -1), 0, 1)
    if name == "Ice":
        s = (0.5 - 0.5 * np.cos(2 * np.pi * t))
        return np.clip(np.concatenate([s * 3 - 2, s * 3 - 1, s * 3], -1), 0, 1)
    s = 0.5 - 0.5 * np.cos(2 * np.pi * t)
    return _rgb(ctx["primary"]) * (1 - s) + _rgb(ctx["secondary"]) * s


def _escape(zr, zi, cr, ci, iters, ctx):
    """Iterate z = z^2 + c; returns smooth escape counts (NaN = never escaped)."""
    zr = zr.ravel().astype(np.float64); zi = zi.ravel().astype(np.float64)
    n = zr.size
    cr = np.broadcast_to(np.asarray(cr, np.float64), (n,)).copy() if np.ndim(cr) else np.full(n, cr)
    ci = np.broadcast_to(np.asarray(ci, np.float64), (n,)).copy() if np.ndim(ci) else np.full(n, ci)
    out = np.full(n, np.nan, np.float64)
    idx = np.arange(n)
    for k in range(int(iters)):
        if k % 16 == 0:
            _check(ctx)
        zr, zi = zr * zr - zi * zi + cr, 2 * zr * zi + ci
        m2 = zr * zr + zi * zi
        done = m2 > 256.0
        if done.any():
            out[idx[done]] = k + 1 - np.log2(np.log(np.sqrt(m2[done])))
            keep = ~done
            idx, zr, zi, cr, ci = idx[keep], zr[keep], zi[keep], cr[keep], ci[keep]
            if idx.size == 0:
                break
    return out


def _fractal_plane(h, w, p, cx, cy, span):
    xs, ys = _coords(h, w)
    zoom = max(p["zoom"], 1) / 100.0
    s = span / zoom / max(w, h)
    ang = math.radians(p["angle"])
    x = (xs - w / 2) * s; y = (ys - h / 2) * s
    xr = x * math.cos(ang) - y * math.sin(ang)
    yr = x * math.sin(ang) + y * math.cos(ang)
    return xr + cx, yr + cy


def _colour_fractal(a, cnt, h, w, p, ctx):
    cnt = cnt.reshape(h, w)
    inside = np.isnan(cnt)
    t = np.nan_to_num(cnt) * (p["colour_cycle"] / 100.0) / 20.0
    rgb = _palette(t.astype(np.float32), p["palette"], ctx)
    rgb[inside] = 0.0
    if p["blend"] == "Screen":
        return _screen(a, rgb)
    return _opaque(rgb)


def julia(a, p, ctx):
    h, w = a.shape[:2]
    zr, zi = _fractal_plane(h, w, p, 0.0, 0.0, 3.2)
    cnt = _escape(zr, zi, p["c_real"], p["c_imag"], p["iterations"], ctx)
    return _colour_fractal(a, cnt, h, w, p, ctx)


def mandelbrot(a, p, ctx):
    h, w = a.shape[:2]
    cr, ci = _fractal_plane(h, w, p, -0.6 + p["offset_x"] / 100.0, p["offset_y"] / 100.0, 3.2)
    cr, ci = cr.ravel().astype(np.float64), ci.ravel().astype(np.float64)
    q = (cr - 0.25) ** 2 + ci * ci
    inside = (q * (q + (cr - 0.25)) <= 0.25 * ci * ci) | ((cr + 1) ** 2 + ci * ci <= 0.0625)
    cnt = np.full(cr.size, np.nan)
    todo = ~inside
    if todo.any():
        z = np.zeros(int(todo.sum()))
        cnt[todo] = _escape(z, z, cr[todo], ci[todo], p["iterations"], ctx)
    return _colour_fractal(a, cnt, h, w, p, ctx)


# ------------------------------------------------------------------ plasma
def plasma(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    turb = p["turbulence"] / 100.0
    rough = 0.35 + 0.45 * turb
    chans = [fbm(h, w, p["scale"], rough, rng, ctx=ctx) for _ in range(3)]
    k = 0.8 + 2.5 * turb                  # more turbulence = more colour bands
    rgb = np.stack([0.5 + 0.5 * np.sin(2 * np.pi * (c * k + i / 3.0)) for i, c in enumerate(chans)], -1)
    if p["blend"] == "Screen":
        return _screen(a, rgb)
    if p["blend"] == "Multiply":
        return _from_straight(_straight(a) * rgb, a)
    return _opaque(rgb)


# ------------------------------------------------------------------ lens flare
LENSES = ["Zoom lens", "Prime lens", "Soft glow"]


def lens_flare(a, p, ctx):
    h, w = a.shape[:2]
    xs, ys = _coords(h, w)
    D = math.hypot(w, h)
    sx, sy = w * p["x"] / 100.0, h * p["y"] / 100.0
    bright = p["brightness"] / 100.0
    size = p["size"] / 100.0
    lens = p["lens"]
    tint = np.array([1.0, 0.92, 0.8], np.float32)
    d = np.hypot(xs - sx, ys - sy)
    r0 = D * 0.035 * size
    light = np.zeros((h, w, 3), np.float32)
    # bright core and wide glow
    light += (np.exp(-(d / r0) ** 2) * 1.6)[..., None] * np.array([1, 1, 1], np.float32)
    light += (np.exp(-(d / (r0 * 5)) ** 2) * 0.55)[..., None] * tint
    _check(ctx)
    if lens != "Soft glow":
        # rays: a long horizontal streak and fainter spokes
        ang = np.arctan2(ys - sy, xs - sx)
        n = 12 if lens == "Zoom lens" else 6
        spokes = (np.abs(np.cos(ang * n / 2)) ** 40) * np.exp(-d / (r0 * 9))
        streak = np.exp(-((ys - sy) / (r0 * 0.12)) ** 2) * np.exp(-np.abs(xs - sx) / (D * 0.35 * size))
        light += (spokes * 0.5 + streak * 0.6)[..., None] * tint
        # halo ring
        R = r0 * 6.5
        ring = np.exp(-((d - R) / (r0 * 0.5)) ** 2) * 0.18
        light += ring[..., None] * np.array([0.7, 0.85, 1.0], np.float32)
        _check(ctx)
        # ghosts along the line from the light through the centre of the picture
        vx, vy = w / 2 - sx, h / 2 - sy
        ghosts = [(0.45, 0.35, (0.4, 0.7, 1.0), 0.18), (0.75, 0.15, (0.6, 1.0, 0.6), 0.22),
                  (1.1, 0.6, (1.0, 0.6, 0.3), 0.12), (1.35, 0.25, (0.8, 0.5, 1.0), 0.2),
                  (1.7, 1.0, (0.5, 0.8, 1.0), 0.09), (2.0, 0.4, (1.0, 0.8, 0.4), 0.16)]
        if lens == "Prime lens":
            ghosts = ghosts[::2]
        for t, rs, col, strength in ghosts:
            gx, gy = sx + vx * t, sy + vy * t
            gr = r0 * 2.2 * rs
            gd = np.hypot(xs - gx, ys - gy)
            disc = np.clip((gr - gd) / max(gr * 0.25, 1.0), 0, 1) * (0.6 + 0.4 * (gd / gr) ** 2)
            light += (disc * strength)[..., None] * np.array(col, np.float32)
    return _screen(a, light * bright)


# ------------------------------------------------------------------ lighting effects
LIGHT_TYPES = ["Spotlight", "Round light", "Light from one side"]
LIGHT_COLOURS = ["White", "Warm", "Cool", "Primary colour"]


def lighting(a, p, ctx):
    h, w = a.shape[:2]
    xs, ys = _coords(h, w)
    D = math.hypot(w, h)
    lx, ly = w * p["x"] / 100.0, h * p["y"] / 100.0
    ang = math.radians(p["angle"])
    rad = D * p["size"] / 100.0 * 0.5
    kind = p["type"]
    if kind == "Spotlight":
        dx, dy = xs - lx, ys - ly
        u = dx * math.cos(ang) + dy * math.sin(ang)           # along the beam
        v = -dx * math.sin(ang) + dy * math.cos(ang)
        # an oval pool of light, centred a little along the beam
        e = ((u - rad * 0.6) / (rad * 1.3)) ** 2 + (v / (rad * 0.75)) ** 2
        fall = np.clip(1 - e, 0, 1) ** 1.5
    elif kind == "Round light":
        fall = np.clip(1 - (np.hypot(xs - lx, ys - ly) / rad) ** 2, 0, 1) ** 1.5
    else:
        proj = (xs - w / 2) * math.cos(ang) + (ys - h / 2) * math.sin(ang)
        span = (abs(w * math.cos(ang)) + abs(h * math.sin(ang))) / 2 + 1
        fall = np.clip(0.5 - proj / (2 * span), 0, 1)
    intensity = p["intensity"] / 100.0
    ambient = p["ambient"] / 100.0
    cols = {"White": (1.0, 1.0, 1.0), "Warm": (1.0, 0.85, 0.62), "Cool": (0.75, 0.88, 1.0)}
    col = np.array(cols[p["colour"]], np.float32) if p["colour"] in cols else _rgb(ctx["primary"])
    shade = np.ones((h, w), np.float32)
    rough = p["texture"] / 100.0
    if rough > 0:
        _check(ctx)
        y = im.luminance(_straight(a))
        y = im.gaussian_blur(y[..., None].astype(np.float32), 1.0)[..., 0]
        gy, gx = np.gradient(y)
        ddx, ddy = lx - xs, ly - ys
        n = np.hypot(ddx, ddy) + 1e-3
        if kind == "Light from one side":
            ddx, ddy, n = -math.cos(ang), -math.sin(ang), 1.0
        shade = np.clip(1 + rough * 12 * (gx * ddx + gy * ddy) / n, 0.3, 1.8).astype(np.float32)
    lightmap = (ambient + intensity * fall * shade)[..., None] * (ambient + (1 - ambient) * col)
    return _from_straight(_straight(a) * lightmap, a)


# ------------------------------------------------------------------ textures
TEXTURES = ["Wood", "Marble", "Granite"]


def texture(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    xs, ys = _coords(h, w)
    sc = max(5, p["scale"])
    kind = p["type"]
    natural = p["colours"] == "Natural"
    c0, c1 = _rgb(ctx["primary"]), _rgb(ctx["secondary"])
    if kind == "Wood":
        n = fbm(h, w, sc * 2, 0.5, rng, ctx=ctx)
        cx, cy = -w * 0.3, h * (0.3 + 0.4 * rng.random())        # tree centre off to one side
        r = np.hypot((xs - cx) * 0.35, ys - cy) / sc * 6
        ring = (r + n * 3.0) % 1.0
        v = ring ** 3 * 0.6 + (fbm(h, w, 3, 0.5, rng, sx=6, sy=0.6, ctx=ctx) * 0.4)
        v = np.clip(v, 0, 1)[..., None]
        light, dark = (np.array([0.80, 0.58, 0.36], np.float32), np.array([0.45, 0.27, 0.12], np.float32)) \
            if natural else (c1, c0)
        rgb = light * (1 - v) + dark * v
    elif kind == "Marble":
        # veins follow a gently warped wave; warp noise is kept smooth so veins flow, not zig-zag
        warp = fbm(h, w, sc * 3, 0.55, rng, ctx=ctx, min_size=sc / 16.0)
        warp2 = fbm(h, w, sc * 1.5, 0.5, rng, ctx=ctx, min_size=sc / 10.0)
        u = (xs * 0.8 + ys * 0.45) / (sc * 2.5)
        v = np.sin(2 * np.pi * (u + warp * 2.0 + warp2 * 0.35))
        vein = (1 - np.abs(v)) ** 9
        v2 = np.sin(2 * np.pi * (u * 2.6 + warp2 * 1.6 + 0.37))
        vein2 = (1 - np.abs(v2)) ** 14 * 0.35
        detail = fbm(h, w, max(4, sc / 6), 0.5, rng, ctx=ctx)       # a little texture inside the veins
        thick = fbm(h, w, sc * 2, 0.5, rng, ctx=ctx, min_size=sc / 8.0)
        veins = np.clip((vein * (0.25 + 1.1 * thick) + vein2) * (0.6 + 0.6 * detail), 0, 0.9)
        cloud = fbm(h, w, sc * 0.8, 0.5, rng, ctx=ctx, min_size=sc / 12.0)
        if natural:
            base = np.array([0.95, 0.94, 0.91], np.float32) - np.array([0.07, 0.07, 0.06], np.float32) * cloud[..., None]
            vc = np.array([0.46, 0.46, 0.5], np.float32)
        else:
            base, vc = c1 * (0.92 + 0.08 * cloud[..., None]), c0
        rgb = base * (1 - veins[..., None]) + vc * veins[..., None]
    else:  # Granite
        speck = max(2, sc // 12)
        b = fbm(h, w, speck * 2, 0.55, rng, ctx=ctx)
        d1 = fbm(h, w, speck, 0.5, rng, ctx=ctx)
        d2 = fbm(h, w, speck, 0.5, rng, ctx=ctx)
        grain = rng.random((h, w)).astype(np.float32)
        if natural:
            rgb = np.array([0.66, 0.58, 0.56], np.float32) * (0.85 + 0.3 * b[..., None])
            dark, pale, pink = np.array([0.12, 0.11, 0.12]), np.array([0.92, 0.92, 0.9]), np.array([0.78, 0.55, 0.5])
        else:
            rgb = (c0 * 0.5 + c1 * 0.5) * (0.85 + 0.3 * b[..., None])
            dark, pale, pink = c0, c1, c0 * 0.5 + c1 * 0.5
        m_dark = np.clip((d1 - 0.68) * 12, 0, 1)[..., None]
        m_pale = np.clip((d2 - 0.7) * 12, 0, 1)[..., None]
        m_pink = np.clip((b - 0.72) * 8, 0, 1)[..., None] * 0.6
        rgb = rgb * (1 - m_pink) + np.array(pink, np.float32) * m_pink
        rgb = rgb * (1 - m_dark) + np.array(dark, np.float32) * m_dark
        rgb = rgb * (1 - m_pale) + np.array(pale, np.float32) * m_pale
        rgb = rgb * (0.94 + 0.12 * grain[..., None])
    if p["blend"] == "Multiply":
        return _from_straight(_straight(a) * rgb, a)
    return _opaque(rgb)


# ------------------------------------------------------------------ starfield
STAR_BACKGROUNDS = ["Black space", "Deep blue night", "Keep the picture"]


def starfield(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    bgname = p["background"]
    if bgname == "Black space":
        base = _opaque(np.zeros((h, w, 3), np.float32))
    elif bgname == "Deep blue night":
        yy = (np.arange(h, dtype=np.float32) / max(h - 1, 1))[:, None, None]
        base = _opaque(np.array([0.01, 0.02, 0.07], np.float32) * (1 - yy) + np.array([0.05, 0.08, 0.2], np.float32) * yy
                       + np.zeros((h, w, 3), np.float32))
    else:
        base = a
    if p["nebula"]:
        n1 = fbm(h, w, max(w, h) / 3, 0.55, rng, ctx=ctx)
        n2 = fbm(h, w, max(w, h) / 4, 0.55, rng, ctx=ctx)
        neb = (np.clip(n1 - 0.45, 0, 1)[..., None] ** 1.5 * np.array([0.9, 0.3, 0.7], np.float32)[None, None, :] * 1.6
               + np.clip(n2 - 0.5, 0, 1)[..., None] ** 1.5 * np.array([0.2, 0.5, 1.0], np.float32)[None, None, :] * 1.6)
        base = _screen(base, neb)
    _check(ctx)
    img = _qimage(h, w)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing)
    q.setCompositionMode(QPainter.CompositionMode_Plus)
    count = int(w * h / 1e4 * p["density"] * 0.6)
    size = p["size"] / 100.0
    bright = p["brightness"] / 100.0
    tints = [QColor(255, 255, 255), QColor(200, 220, 255), QColor(255, 240, 210), QColor(255, 220, 190), QColor(180, 200, 255)]
    xs = rng.random(count) * w; ys = rng.random(count) * h
    mags = rng.random(count) ** 6                       # many faint stars, a few bright ones
    q.setPen(Qt.NoPen)
    for i in range(count):
        if i % 2000 == 0:
            _check(ctx)
        m = mags[i]
        r = (0.5 + 2.8 * m) * size
        c = QColor(tints[int(rng.integers(len(tints)))])
        alpha = int(min(255, (60 + 195 * min(1.0, m * 3)) * bright))
        g = QRadialGradient(QPointF(xs[i], ys[i]), r * 2.2)
        c.setAlpha(alpha); g.setColorAt(0, c)
        c2 = QColor(c); c2.setAlpha(int(alpha * 0.35)); g.setColorAt(0.35, c2)
        c3 = QColor(c); c3.setAlpha(0); g.setColorAt(1, c3)
        q.setBrush(QBrush(g)); q.drawEllipse(QPointF(xs[i], ys[i]), r * 2.2, r * 2.2)
        if p["sparkle"] and m > 0.8:
            from PySide6.QtGui import QLinearGradient
            L = r * (5 + 9 * m)
            for dx, dy in ((1, 0), (0, 1)):
                g2 = QLinearGradient(QPointF(xs[i] - dx * L, ys[i] - dy * L), QPointF(xs[i] + dx * L, ys[i] + dy * L))
                c4 = QColor(c); c4.setAlpha(0); g2.setColorAt(0, c4); g2.setColorAt(1, c4)
                c5 = QColor(c); c5.setAlpha(int(alpha * 0.8)); g2.setColorAt(0.5, c5)
                pen = QPen(QBrush(g2), max(0.6, r * 0.3)); pen.setCapStyle(Qt.RoundCap); q.setPen(pen)
                q.drawLine(QPointF(xs[i] - dx * L, ys[i] - dy * L), QPointF(xs[i] + dx * L, ys[i] + dy * L))
            q.setPen(Qt.NoPen)
    q.end()
    stars = im.to_array(img, premultiplied=True)
    return _screen(base, stars[..., :3])


# ------------------------------------------------------------------ rain and snow
def rain_snow(a, p, ctx):
    h, w = a.shape[:2]
    rng = np.random.default_rng(p["seed"])
    amount = p["amount"] / 100.0
    size = p["size"] / 100.0
    ang = math.radians(90 + p["wind"])
    out = a
    scale = min(w, h) / 600.0 + 0.4
    if p["type"] == "Rain":
        img = _qimage(h, w)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        n = int(w * h / 1e4 * 30 * amount)
        dx, dy = math.cos(ang), math.sin(ang)
        for i in range(n):
            if i % 3000 == 0:
                _check(ctx)
            x, y = rng.random() * (w + h * 0.5) - h * 0.25, rng.random() * h
            near = rng.random() ** 2
            L = (14 + 40 * near) * size * scale
            c = QColor(235, 240, 250, int(70 + 110 * near))
            pen = QPen(c, (0.7 + 1.5 * near) * size * max(1.0, scale * 0.8)); pen.setCapStyle(Qt.RoundCap); q.setPen(pen)
            q.drawLine(QPointF(x, y), QPointF(x + dx * L, y + dy * L))
        q.end()
        drops = im.gaussian_blur(im.to_array(img, premultiplied=True), 0.6 * size + 0.2)
        # rainy days are a little greyer and flatter
        haze = np.array([0.72, 0.75, 0.8, 1.0], np.float32) * a[..., 3:4] * 0.12
        out = a * 0.88 + haze
        return _over(out, drops)
    # snow: far flakes small and sharp, near flakes bigger and soft
    layers = []
    for layer, (count, rmin, rmax, blur) in enumerate(((60, 0.6, 1.6, 0.0), (25, 1.5, 3.2, 0.8), (6, 3.5, 7.0, 2.5))):
        img = _qimage(h, w)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        q.setPen(Qt.NoPen)
        n = int(w * h / 1e5 * count * amount * 4)
        for i in range(n):
            if i % 3000 == 0:
                _check(ctx)
            x, y = rng.random() * w, rng.random() * h
            r = (rmin + (rmax - rmin) * rng.random()) * size * scale * 1.3
            g = QRadialGradient(QPointF(x, y), r)
            g.setColorAt(0, QColor(255, 255, 255, 235)); g.setColorAt(0.6, QColor(250, 252, 255, 190))
            g.setColorAt(1, QColor(250, 252, 255, 0))
            q.setBrush(QBrush(g)); q.drawEllipse(QPointF(x, y), r, r)
        q.end()
        arr = im.to_array(img, premultiplied=True)
        if blur > 0:
            arr = im.gaussian_blur(arr, blur * size)
        layers.append(arr)
    for arr in layers:
        out = _over(out, arr)
    return out


# ------------------------------------------------------------------ sunburst
SUNBURST_COLOURS = ["Sunny yellow", "Retro orange", "Sky blue", "Primary and secondary colour"]


def sunburst(a, p, ctx):
    h, w = a.shape[:2]
    xs, ys = _coords(h, w)
    cx, cy = w * p["x"] / 100.0, h * p["y"] / 100.0
    n = max(2, int(p["rays"]))
    theta = np.arctan2(ys - cy, xs - cx) + math.radians(p["angle"])
    r = np.hypot(xs - cx, ys - cy)
    phase = (theta / (2 * np.pi) * n) % 1.0
    width = p["ray_width"] / 100.0
    # antialiased edges: one pixel measured around the circle at this distance
    px = n / (2 * np.pi * np.maximum(r, 1.0))
    edge = np.minimum(phase, width - phase)
    inside = np.where(phase < width, np.clip(edge / px + 0.5, 0, 1), 0.0)
    out_edge = np.minimum(phase - width, 1.0 - phase)
    inside = np.where(phase >= width, 1 - np.clip(out_edge / px + 0.5, 0, 1), inside).astype(np.float32)
    schemes = {"Sunny yellow": ((1.0, 0.86, 0.3), (1.0, 0.95, 0.65)),
               "Retro orange": ((0.95, 0.45, 0.15), (1.0, 0.7, 0.35)),
               "Sky blue": ((0.25, 0.55, 0.9), (0.55, 0.78, 1.0))}
    if p["colours"] in schemes:
        ca, cb = (np.array(c, np.float32) for c in schemes[p["colours"]])
    else:
        ca, cb = _rgb(ctx["primary"]), _rgb(ctx["secondary"])
    rgb = cb * (1 - inside[..., None]) + ca * inside[..., None]
    if p["glow"]:
        D = math.hypot(w, h)
        glow = np.exp(-(r / (D * 0.12)) ** 2)[..., None]
        rgb = rgb * (1 - glow * 0.8) + np.array([1.0, 1.0, 0.95], np.float32) * glow * 0.8
    if p["fade"]:
        D = math.hypot(w, h)
        f = np.clip(r / (D * 0.6), 0, 1)[..., None] ** 1.5
        rgb = rgb * (1 - f * 0.45)
    if p["blend"] == "Screen":
        return _screen(a, rgb)
    return _opaque(rgb)
