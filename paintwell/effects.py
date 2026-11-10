"""Adjustments and effects. Each works on float32 RGBA arrays in 0..1."""
import math
import os
import numpy as np
from PySide6.QtCore import Qt

from . import imaging as im
from . import borders as bd
from . import render_fx as rf


class Effect:
    def __init__(self, name, fn, params=(), premult=False, shortcut=None, custom=None, category=None,
                 needs_dialog=True, grow=None, pointwise=False, per_channel=None, image_fn=None, halo=None):
        self.name = name
        self.fn = fn
        self.params = list(params)
        self.premult = premult            # whether fn expects premultiplied input
        self.shortcut = shortcut
        self.custom = custom              # 'curves' / 'levels' for special dialogs
        self.category = category
        self.needs_dialog = needs_dialog and bool(self.params or custom)
        self.grow = grow                  # params -> (l, t, r, b): enlarge the canvas first
        self.pointwise = pointwise        # each pixel depends only on itself: run in strips on all cores
        self.per_channel = per_channel    # params -> True when R, G, B are independent: 256-entry lookup
        self.image_fn = image_fn          # optional whole-QImage version (used instead of fn when set)
        self.halo = halo                  # params -> how far (px) a pixel's result can look: run in bands

    def defaults(self):
        return {p[0]: p[3] for p in self.params}

    def render(self, src, params, ctx):
        """Apply to a Source and return a QImage (the fast path used by the app)."""
        if self.image_fn is not None:
            return self.image_fn(src.img, params, ctx)
        if self.per_channel is not None and self.per_channel(params):
            return self._render_lut(src, params, ctx)
        if self.pointwise:
            return self._render_strips(src, params, ctx)
        if self.halo is not None and src.img.height() >= 256:
            return self._render_bands(src, params, ctx, int(self.halo(params)) + 1)
        if self.premult:
            return im.from_array(self.fn(src.premult, params, ctx), True)
        out = self.fn(src.straight, params, ctx)
        return im.from_array(out, False)

    def _render_lut(self, src, params, ctx):
        ramp = np.zeros((1, 256, 4), np.float32)
        ramp[0, :, :3] = (np.arange(256, dtype=np.float32) / 255.0)[:, None]
        ramp[0, :, 3] = 1.0
        lut = im.float_to_u8(np.clip(self.fn(ramp, params, ctx), 0, 1))[0]      # 256 x 4
        u8 = src.u8_straight
        out = np.empty_like(u8)

        def job(y0, y1):
            _check(ctx)
            for c in range(3):
                np.take(lut[:, c], u8[y0:y1, :, c], out=out[y0:y1, :, c])
            out[y0:y1, :, 3] = u8[y0:y1, :, 3]
        _strips(u8.shape[0], job)
        return im.u8_to_image(out, False)

    def _render_strips(self, src, params, ctx):
        u8 = src.u8_straight
        out = np.empty_like(u8)

        def job(y0, y1):
            _check(ctx)
            a = u8[y0:y1].astype(np.float32)
            a *= np.float32(1.0 / 255.0)
            out[y0:y1] = im.float_to_u8(self.fn(a, params, ctx))
        _strips(u8.shape[0], job)
        return im.u8_to_image(out, False)

    def _render_bands(self, src, params, ctx, halo):
        """Run on overlapping bands of rows across all cores. Each band gets `halo` extra rows
        above and below, so the result matches a single whole-image run.
        """
        img = src.img
        w, h = img.width(), img.height()
        out = np.empty((h, w, 4), np.uint8)

        def job(y0, y1):
            _check(ctx)
            a0, a1 = max(0, y0 - halo), min(h, y1 + halo)
            band = img.copy(0, a0, w, a1 - a0)
            if self.premult:
                res = self.fn(im.to_array(band, True), params, ctx)
            else:
                res = np.clip(self.fn(im.to_array(band, False), params, ctx), 0, 1)
            out[y0:y1] = im.float_to_u8(res[y0 - a0:y1 - a0])
        step = max(32, halo * 2, -(-h // ((os.cpu_count() or 1) * 4)))
        im.parallel(h, job, step=step)
        return im.u8_to_image(out, self.premult)

    def run(self, arr_premult, params, ctx):
        if self.premult:
            return self.fn(arr_premult, params, ctx)
        a = im.unpremultiply(arr_premult)
        out = self.fn(a, params, ctx)
        return im.premultiply(np.clip(out, 0, 1))


class Cancelled(Exception):
    """Raised inside a preview render when newer settings have arrived."""


def _check(ctx):
    c = ctx.get("cancelled")
    if c is not None and c():
        raise Cancelled()


class Source:
    """The pixels an effect works on, converted once and shared by every preview."""

    def __init__(self, img):
        self.img = img
        self._cache = {}

    def _get(self, key, make):
        if key not in self._cache:
            a = make()
            a.flags.writeable = False      # effects must not change their input
            self._cache[key] = a
        return self._cache[key]

    @property
    def premult(self):
        return self._get("p", lambda: im.to_array(self.img, True))

    @property
    def straight(self):
        return self._get("s", lambda: im.to_array(self.img, False))

    @property
    def u8_straight(self):
        return self._get("u", lambda: im.to_u8(self.img, False))


_strips = im.parallel


# params: (key, label, kind, default, min, max)  kind: int/float/bool/choice/angle/seed

# --- Adjustments ---
def auto_level(a, p, ctx):
    """Stretch each channel's darkest/lightest tones to black and white (ignoring outliers),
    then shift the midtones together so the average lands near the middle. A shared
    midtone shift keeps the overall colour cast, e.g. a sunset stays warm.
    """
    out = a.copy()
    mask = a[..., 3] > 0
    if ctx.get("mask") is not None and ctx["mask"].shape == mask.shape:
        mask &= ctx["mask"]
    if not mask.any():
        return out
    step = max(1, int(math.sqrt(mask.sum() / 400000.0)))      # stats from a sample on big pictures
    sm = mask[::step, ::step]
    sample = a[::step, ::step, :3][sm]                         # N x 3
    ramp = np.linspace(0.0, 1.0, 4096, dtype=np.float32)
    los, his = [], []
    for c in range(3):
        lo, hi = np.percentile(sample[:, c], 0.1), np.percentile(sample[:, c], 99.9)
        if hi - lo < 1e-3:
            lo, hi = 0.0, 1.0
        los.append(lo); his.append(hi)
    st = np.clip((sample - np.array(los, np.float32)) / (np.array(his, np.float32) - np.array(los, np.float32)), 0, 1)
    mean = float((st[:, 0] * 0.299 + st[:, 1] * 0.587 + st[:, 2] * 0.114).mean())
    g = math.log(0.5) / math.log(min(max(mean, 0.02), 0.98))
    g = min(max(g, 0.6), 1.7)                                  # don't overdo very dark/bright pictures
    for c in range(3):
        lut = np.clip((ramp - los[c]) / (his[c] - los[c]), 0, 1) ** g
        idx = np.clip(a[..., c] * 4095 + 0.5, 0, 4095).astype(np.int32)
        out[..., c] = lut[idx]
    return out


def black_white(a, p, ctx):
    out = a.copy()
    y = im.luminance(a)
    out[..., 0] = out[..., 1] = out[..., 2] = y
    return out


def brightness_contrast(a, p, ctx):
    b = p["brightness"] / 100.0
    c = p["contrast"] / 100.0
    k = math.tan((c + 1) * math.pi / 4) if c < 1 else 1e6
    out = a.copy()
    out[..., :3] = (a[..., :3] - 0.5) * k + 0.5 + b
    return out


def hue_saturation(a, p, ctx):
    h, s, v = im.rgb_to_hsv(a[..., :3])
    h = h + p["hue"] / 360.0
    s = np.clip(s * p["saturation"] / 100.0, 0, 1)
    rgb = im.hsv_to_rgb(h, s, v)
    l = p["lightness"] / 100.0
    if l > 0:
        rgb = rgb + (1 - rgb) * l
    elif l < 0:
        rgb = rgb * (1 + l)
    out = a.copy()
    out[..., :3] = rgb
    return out


def invert(a, p, ctx):
    out = a.copy()
    out[..., :3] = 1 - a[..., :3]
    return out


def posterize(a, p, ctx):
    out = a.copy()
    for i, k in enumerate(("red", "green", "blue")):
        n = p["red"] if p["linked"] else p[k]
        n = max(2, int(n))
        # n equal bands over 0..1, one output level per band (matches Photoshop)
        out[..., i] = np.minimum(np.floor(a[..., i] * n), n - 1) / (n - 1)
    return out


def sepia(a, p, ctx):
    y = im.luminance(a)
    out = a.copy()
    out[..., 0] = np.clip(y * 1.07 + 0.06, 0, 1)
    out[..., 1] = np.clip(y * 0.95 + 0.02, 0, 1)
    out[..., 2] = np.clip(y * 0.78, 0, 1)
    return out


def curve_lut(points):
    """Monotone cubic interpolation of control points (0..255) into a 256 LUT."""
    pts = sorted(points)
    xs = np.array([q[0] for q in pts], np.float64)
    ys = np.array([q[1] for q in pts], np.float64)
    x = np.arange(256, dtype=np.float64)
    if len(xs) == 1:
        return np.full(256, ys[0] / 255.0)
    d = np.diff(ys) / np.maximum(np.diff(xs), 1e-9)
    m = np.zeros_like(ys)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xs) - 1):
        m[i] = 0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(len(d)):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                t = 3 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    out = np.empty(256)
    idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 2)
    x0, x1 = xs[idx], xs[idx + 1]
    h = np.maximum(x1 - x0, 1e-9)
    t = np.clip((x - x0) / h, 0, 1)
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    out = h00 * ys[idx] + h10 * h * m[idx] + h01 * ys[idx + 1] + h11 * h * m[idx + 1]
    out[x < xs[0]] = ys[0]
    out[x > xs[-1]] = ys[-1]
    return np.clip(out / 255.0, 0, 1)


def apply_lut(ch, lut):
    idx = np.clip(ch * 255 + 0.5, 0, 255).astype(np.int32)
    return lut[idx].astype(np.float32)


def curves(a, p, ctx):
    out = a.copy()
    if p["mode"] == "luminosity":
        lut = curve_lut(p["points"]["lum"])
        y = im.luminance(a)
        y2 = apply_lut(y, lut)
        with np.errstate(divide="ignore", invalid="ignore"):
            k = np.where(y > 1e-4, y2 / np.maximum(y, 1e-4), 0)[..., None]
        rgb = a[..., :3] * k
        dark = (y <= 1e-4)[..., None]
        rgb = np.where(dark, y2[..., None], rgb)
        out[..., :3] = rgb
    else:
        for i, k in enumerate(("r", "g", "b")):
            out[..., i] = apply_lut(a[..., i], curve_lut(p["points"][k]))
    return out


def levels(a, p, ctx):
    lo, hi = p["in_lo"] / 255.0, p["in_hi"] / 255.0
    olo, ohi = p["out_lo"] / 255.0, p["out_hi"] / 255.0
    g = p["gamma"]
    out = a.copy()
    for i, k in enumerate(("r", "g", "b")):
        if not p[k]:
            continue
        x = np.clip((a[..., i] - lo) / max(hi - lo, 1e-4), 0, 1)
        x = x ** (1.0 / max(g, 0.01))
        out[..., i] = olo + x * (ohi - olo)
    return out


ADJUSTMENTS = [
    Effect("Auto-Level", auto_level, shortcut="Ctrl+Shift+L"),
    Effect("Black and White", black_white, shortcut="Ctrl+Shift+G", pointwise=True),
    Effect("Brightness / Contrast", brightness_contrast,
           [("brightness", "Brightness", "int", 0, -100, 100), ("contrast", "Contrast", "int", 0, -100, 100)],
           per_channel=lambda p: True),
    Effect("Curves", curves, shortcut="Ctrl+Shift+M", custom="curves", pointwise=True,
           per_channel=lambda p: p.get("mode") != "luminosity"),
    Effect("Hue / Saturation", hue_saturation,
           [("hue", "Hue", "int", 0, -180, 180), ("saturation", "Saturation", "int", 100, 0, 200),
            ("lightness", "Lightness", "int", 0, -100, 100)], shortcut="Ctrl+Shift+U", pointwise=True),
    Effect("Invert Colours", invert, shortcut="Ctrl+Shift+I", per_channel=lambda p: True),
    Effect("Levels", levels, shortcut="Ctrl+L", custom="levels", per_channel=lambda p: True),
    Effect("Posterise", posterize,
           [("red", "Red", "int", 4, 2, 64), ("green", "Green", "int", 4, 2, 64),
            ("blue", "Blue", "int", 4, 2, 64), ("linked", "Linked", "bool", True, 0, 1)],
           shortcut="Ctrl+Shift+P", per_channel=lambda p: True),
    Effect("Sepia", sepia, shortcut="Ctrl+Shift+E", pointwise=True),
]


# --- Effects (premultiplied) ---
def gaussian(a, p, ctx):
    return im.gaussian_blur(a, p["radius"])


def _shift_sum(a, offsets):
    """Average of copies of a shifted by integer offsets, with edge clamping."""
    h, w = a.shape[:2]
    m = int(max([abs(v) for o in offsets for v in o] + [0]))
    pad = np.pad(a, ((m, m), (m, m), (0, 0)), mode="edge") if m else a
    acc = np.zeros_like(a)
    for dx, dy in offsets:
        acc += pad[m + dy:m + dy + h, m + dx:m + dx + w]
    return acc / len(offsets)


def _nearest(a, xs, ys):
    h, w = a.shape[:2]
    xi = np.clip((xs + 0.5).astype(np.int32), 0, w - 1)
    yi = np.clip((ys + 0.5).astype(np.int32), 0, h - 1)
    return a.reshape(-1, a.shape[2])[(yi * w + xi).ravel()].reshape(a.shape)


def _grid(h, w):
    ys, xs = np.mgrid[0:h, 0:w]
    return xs.astype(np.float32), ys.astype(np.float32)


def motion_blur(a, p, ctx):
    ang = math.radians(p["angle"])
    dist = float(p["distance"])
    n = max(2, int(dist) + 1)
    dx, dy = math.cos(ang), -math.sin(ang)
    offs = []
    for i in range(n):
        t = (i / (n - 1) - (0.5 if p["centered"] else 0.0)) * dist
        o = (int(round(dx * t)), int(round(dy * t)))
        offs.append(o)
    return _shift_sum(a, offs)


def zoom_blur(a, p, ctx):
    h, w = a.shape[:2]
    cx = w / 2 + p["offset_x"] / 100.0 * w / 2
    cy = h / 2 + p["offset_y"] / 100.0 * h / 2
    amt = p["amount"] / 100.0 * 0.25
    n = max(2, min(32, int(amt * max(w, h) / 3) + 2))
    xs, ys = _grid(h, w)
    rx, ry = xs - cx, ys - cy
    acc = np.zeros_like(a)
    for i in range(n):
        s = 1 - amt * i / (n - 1)
        acc += _nearest(a, cx + rx * s, cy + ry * s)
    return acc / n


def radial_blur(a, p, ctx):
    h, w = a.shape[:2]
    cx, cy = w / 2 + p["offset_x"] / 100.0 * w / 2, h / 2 + p["offset_y"] / 100.0 * h / 2
    ang = math.radians(p["angle"])
    n = max(3, min(48, int(p["angle"] * 1.5) + 3))
    xs, ys = _grid(h, w)
    rx, ry = xs - cx, ys - cy
    acc = np.zeros_like(a)
    for i in range(n):
        t = (i / (n - 1) - 0.5) * ang
        c, s = math.cos(t), math.sin(t)
        acc += _nearest(a, cx + rx * c - ry * s, cy + rx * s + ry * c)
    return acc / n


def fragment(a, p, ctx):
    n = p["fragments"]
    d = p["distance"]
    rot = math.radians(p["rotation"])
    offs = [(int(round(d * math.cos(rot + i * 2 * math.pi / n))), int(round(d * math.sin(rot + i * 2 * math.pi / n))))
            for i in range(n)]
    return _shift_sum(a, offs)


def unfocus(a, p, ctx):
    return im.box_blur(im.box_blur(a, p["radius"]), max(1, p["radius"] // 2))


def add_noise(a, p, ctx):
    rng = np.random.default_rng(p["seed"])
    h, w = a.shape[:2]
    inten = p["intensity"] / 100.0 * 0.5
    sat = p["saturation"] / 100.0
    cov = p["coverage"] / 100.0
    gray = rng.normal(0, 1, (h, w, 1)).astype(np.float32)
    col = rng.normal(0, 1, (h, w, 3)).astype(np.float32)
    n = (gray * (1 - sat) + col * sat) * inten
    mask = (rng.random((h, w, 1)) < cov).astype(np.float32)
    out = a.copy()
    out[..., :3] = np.clip(a[..., :3] + n * mask * a[..., 3:4], 0, a[..., 3:4])
    return out


def median(a, p, ctx):
    r = int(p["radius"])
    pct = p["percentile"]
    h, w = a.shape[:2]
    a8 = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    pad = np.pad(a8, ((r, r), (r, r), (0, 0)), mode="edge")
    k = (2 * r + 1) ** 2
    kth = int(round(pct / 100 * (k - 1)))
    out = np.empty_like(a8)
    rows = max(1, int(60_000_000 / max(1, k * w * 4)))
    for y0 in range(0, h, rows):
        y1 = min(h, y0 + rows)
        stack = np.empty((k, y1 - y0, w, 4), np.uint8)
        i = 0
        for dy in range(2 * r + 1):
            for dx in range(2 * r + 1):
                stack[i] = pad[y0 + dy:y1 + dy, dx:dx + w]
                i += 1
        out[y0:y1] = np.partition(stack, kth, axis=0)[kth]
    res = out.astype(np.float32) / 255.0
    res[..., :3] = np.minimum(res[..., :3], res[..., 3:4])
    return res


def reduce_noise(a, p, ctx):
    r = p["radius"]
    s = p["strength"] / 100.0
    blur = im.gaussian_blur(a, r)
    diff = np.abs(a - blur).mean(axis=-1, keepdims=True)
    k = np.clip(s * (1 - diff * 6), 0, 1)
    return a * (1 - k) + blur * k


def pixelate(a, p, ctx):
    s = max(1, int(p["size"]))
    h, w = a.shape[:2]
    H, W = -(-h // s) * s, -(-w // s) * s
    pad = np.pad(a, ((0, H - h), (0, W - w), (0, 0)), mode="edge")
    blocks = pad.reshape(H // s, s, W // s, s, 4).mean(axis=(1, 3))
    return np.repeat(np.repeat(blocks, s, axis=0), s, axis=1)[:h, :w].copy()


def twist(a, p, ctx):
    h, w = a.shape[:2]
    amt = p["amount"] / 10.0
    cx, cy = w / 2, h / 2
    R = min(w, h) / 2 * p["size"] / 100.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xs + 0.5 - cx, ys + 0.5 - cy
    d = np.sqrt(dx * dx + dy * dy)
    t = np.clip(1 - d / max(R, 1), 0, 1)
    ang = amt * t * t
    c, s = np.cos(ang), np.sin(ang)
    return im.bilinear(a, cx + dx * c - dy * s - 0.5, cy + dx * s + dy * c - 0.5)


def bulge(a, p, ctx):
    h, w = a.shape[:2]
    amt = p["amount"] / 100.0
    cx = w / 2 + p["offset_x"] / 100.0 * w / 2
    cy = h / 2 + p["offset_y"] / 100.0 * h / 2
    R = min(w, h) / 2
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xs - cx, ys - cy
    d = np.sqrt(dx * dx + dy * dy) / R
    inside = d < 1
    f = np.where(inside, 1 - amt * (1 - d * d) * 0.5, 1)
    f = np.maximum(f, 0.05)
    return im.bilinear(a, cx + dx * f, cy + dy * f)


def frosted_glass(a, p, ctx):
    rng = np.random.default_rng(p["seed"])
    h, w = a.shape[:2]
    r = p["amount"]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ox = rng.uniform(-r, r, (h, w)).astype(np.float32)
    oy = rng.uniform(-r, r, (h, w)).astype(np.float32)
    out = im.bilinear(a, xs + ox, ys + oy)
    if p["smooth"]:
        out = im.box_blur(out, 1)
    return out


def tile_reflection(a, p, ctx):
    h, w = a.shape[:2]
    size = max(4, int(p["tile_size"]))
    curv = p["curvature"] / 10.0
    ang = math.radians(p["angle"])
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    c, s = math.cos(ang), math.sin(ang)
    u = xs * c + ys * s
    v = -xs * s + ys * c
    fu = (u % size) / size * 2 - 1
    fv = (v % size) / size * 2 - 1
    du = np.sin(fu * math.pi / 2) * curv * size / 4
    dv = np.sin(fv * math.pi / 2) * curv * size / 4
    sx = xs + du * c - dv * s
    sy = ys + du * s + dv * c
    return im.bilinear(a, sx, sy)


def sharpen(a, p, ctx):
    amt = p["amount"] / 10.0
    blur = im.gaussian_blur(a, 2)
    out = a + (a - blur) * amt
    out[..., 3] = a[..., 3]
    out[..., :3] = np.clip(out[..., :3], 0, out[..., 3:4])
    return out


def glow(a, p, ctx):
    blur = im.gaussian_blur(a, p["radius"])
    b = p["brightness"] / 100.0
    c = 1 + p["contrast"] / 100.0
    g = np.clip((blur - 0.5) * c + 0.5 + b, 0, 1)
    out = a.copy()
    out[..., :3] = 1 - (1 - a[..., :3]) * (1 - g[..., :3] * a[..., 3:4])
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


def soften_portrait(a, p, ctx):
    soft = p["softness"] / 10.0
    blur = im.gaussian_blur(a, 2 + soft * 6)
    out = a * (1 - soft * 0.6) + blur * (soft * 0.6)
    lit = p["lighting"] / 100.0
    out[..., :3] = np.clip(out[..., :3] + lit * 0.4 * out[..., 3:4], 0, out[..., 3:4])
    warm = p["warmth"] / 100.0
    out[..., 0] = np.minimum(out[..., 0] * (1 + warm * 0.15), out[..., 3])
    out[..., 2] = out[..., 2] * (1 - warm * 0.12)
    return out


def vignette(a, p, ctx):
    h, w = a.shape[:2]
    cx = w / 2 + p["offset_x"] / 100.0 * w / 2
    cy = h / 2 + p["offset_y"] / 100.0 * h / 2
    R = math.hypot(w, h) / 2 * p["radius"] / 100.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(R, 1)
    k = np.clip(d, 0, 1) ** 2 * (p["density"] / 100.0)
    out = a.copy()
    out[..., :3] *= (1 - k)[..., None]
    return out


def pencil_sketch(a, p, ctx):
    u = im.unpremultiply(a)
    y = im.luminance(u)
    inv = 1 - y
    blur = im.gaussian_blur(inv[..., None], p["tip"] * 2)[..., 0]
    sketch = np.clip(y / np.maximum(1 - blur, 1e-3), 0, 1)
    sketch = np.clip(sketch + p["range"] / 100.0, 0, 1)
    out = np.stack([sketch, sketch, sketch, u[..., 3]], -1)
    return im.premultiply(out)


def ink_sketch(a, p, ctx):
    u = im.unpremultiply(a)
    y = im.luminance(u)[..., None]
    gx = im.convolve3(y, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])
    gy = im.convolve3(y, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]])
    edge = np.sqrt(gx ** 2 + gy ** 2)[..., 0]
    ink = np.clip(1 - edge * p["ink"] / 25.0, 0, 1)
    col = p["coloring"] / 100.0
    gray = y[..., 0]
    base = u[..., :3] * col + gray[..., None] * (1 - col)
    base = np.clip(base * 0.6 + 0.4, 0, 1)
    out = u.copy()
    out[..., :3] = base * ink[..., None]
    return im.premultiply(out)


def oil_painting(a, p, ctx):
    u = im.unpremultiply(a)
    r = int(p["brush"])
    levels = int(p["coarseness"])
    y = im.luminance(u)
    q = np.clip((y * (levels - 1) + 0.5).astype(np.int32), 0, levels - 1)
    best = np.full(y.shape, -1.0, np.float32)
    out = u.copy()
    for lv in range(levels):
        ind = (q == lv).astype(np.float32)[..., None]
        if not ind.any():
            continue
        cnt = im.box_blur(ind, r)[..., 0]
        better = cnt > best
        if better.any():
            sums = im.box_blur(u[..., :3] * ind, r)
            col = sums / np.maximum(cnt[..., None], 1e-6)
            out[..., :3] = np.where(better[..., None], col, out[..., :3])
            best = np.where(better, cnt, best)
    return im.premultiply(np.clip(out, 0, 1))


# --- Old Photo / artistic extras ---
OLD_PHOTO_STYLES = ["Sepia", "Black and white", "Faded colour"]


def _grain(h, w, rng, scale):
    """Film grain: noise made at a coarser size for big pictures, so it doesn't look like
    single-pixel static."""
    s = max(1, int(round(scale)))
    g = rng.normal(0, 1, ((h + s - 1) // s, (w + s - 1) // s)).astype(np.float32)
    if s > 1:
        g = np.repeat(np.repeat(g, s, 0), s, 1)[:h, :w]
        g = im.box_blur(np.ascontiguousarray(g[..., None]), max(1, s // 2))[..., 0]
    return g


def old_photo(a, p, ctx):
    """Aged print: toning, lifted blacks, softer contrast, grain, scratches, dust, vignette."""
    u = im.unpremultiply(a)
    h, w = u.shape[:2]
    rng = np.random.default_rng(p["seed"])
    age = p["age"] / 100.0
    style = p["style"]
    y = im.luminance(u)
    out = u.copy()
    if style == "Black and white":
        rgb = np.stack([y, y, y], -1)
    elif style == "Faded colour":
        grey = y[..., None]
        rgb = u[..., :3] * 0.55 + grey * 0.45                      # washed-out colour
        rgb = rgb * np.array([1.06, 1.0, 0.84], np.float32) + np.array([0.04, 0.02, 0.0], np.float32)
    else:
        rgb = np.stack([y * 1.07 + 0.08, y * 0.92 + 0.04, y * 0.72 + 0.01], -1)
    # fading: blacks lift, whites dull and yellow a little with age
    lift = 0.16 * age
    rgb = rgb * (1.0 - lift - 0.06 * age) + lift
    rgb[..., 2] *= 1.0 - 0.10 * age
    scale = max(1.0, min(h, w) / 900.0)
    if p["grain"] > 0:
        g = _grain(h, w, rng, scale) * (0.10 * p["grain"] / 100.0)
        rgb += g[..., None]
    if p["scratches"] > 0:
        amt = p["scratches"] / 100.0
        n_lines = int(round(amt * 14 * (w / 1000.0 + 0.6)))
        for _ in range(n_lines):
            x0 = rng.uniform(0, w)
            ya = int(rng.uniform(0, h * 0.6)); yb = int(min(h, ya + rng.uniform(0.2, 1.0) * h))
            ys = np.arange(ya, yb)
            if len(ys) == 0:
                continue
            xs = (x0 + rng.uniform(2, 12) * scale * np.sin(ys / (rng.uniform(80, 400) * scale) + rng.uniform(0, 6))).astype(int)
            ok = (xs >= 0) & (xs < w)
            bright = rng.uniform(0.25, 0.6)
            sign = 1.0 if rng.random() < 0.75 else -0.7               # mostly light scratches
            for t in range(max(1, int(scale))):
                xx = np.clip(xs + t, 0, w - 1)
                rgb[ys[ok], xx[ok]] += sign * bright
        # dust: little specks and hairs
        n_dust = int(round(amt * 260 * (w * h) / 1e6 + amt * 30))
        dy = rng.integers(0, h, n_dust); dx = rng.integers(0, w, n_dust)
        size = np.maximum(1, (rng.random(n_dust) ** 3 * 3 * scale).astype(int))
        val = np.where(rng.random(n_dust) < 0.6, 0.5, -0.45).astype(np.float32)
        for k in range(n_dust):
            y0, x0, sz = dy[k], dx[k], size[k]
            rgb[y0:y0 + sz, x0:x0 + sz] += val[k]
    if p["vignette"] > 0:
        ys, xs = np.ogrid[0:h, 0:w]
        d = np.sqrt(((xs - w / 2) / (w / 2)) ** 2 + ((ys - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        k = np.clip((d - 0.35) / 0.65, 0, 1) ** 1.6 * (0.75 * p["vignette"] / 100.0)
        rgb *= (1.0 - k)[..., None]
    out[..., :3] = np.clip(rgb, 0, 1)
    return im.premultiply(out)


def _flat_colours(u, brush, coarseness, ctx, max_px=2_000_000):
    """Flat patches of the most common colour nearby. Large images are processed at a
    reduced size and scaled back up.
    """
    h, w = u.shape[:2]
    f = 1.0
    if h * w > max_px:
        f = math.sqrt(max_px / (h * w))
    if f < 1.0:
        small = im.from_array(im.premultiply(u), True).scaled(max(1, int(w * f)), max(1, int(h * f)),
                                                          Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        sa = im.to_array(small, True)
        res = oil_painting(sa, {"brush": max(1, int(round(brush * f))), "coarseness": coarseness}, ctx)
        big = im.from_array(np.clip(res, 0, 1), True).scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return im.unpremultiply(im.to_array(big, True)), f
    return im.unpremultiply(oil_painting(im.premultiply(u), {"brush": brush, "coarseness": coarseness}, ctx)), f


def _sobel(y):
    gx = im.convolve3(y, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])
    gy = im.convolve3(y, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]])
    return np.sqrt(gx * gx + gy * gy)[..., 0]


def watercolour(a, p, ctx):
    """Soft washes that bleed a little, darker pigment at the edges of each wash, some
    blotchiness and paper grain.
    """
    u = im.unpremultiply(a)
    h, w = u.shape[:2]
    r = int(p["brush"])
    scale = max(1.0, min(h, w) / 800.0)
    wash, _ = _flat_colours(u, max(1, int(r * scale)), 10, ctx)
    wash = np.ascontiguousarray(wash[..., :3])
    wash = im.gaussian_blur(wash, max(1.5, r * scale * 1.2))       # colours bleed into each other
    # pigment pooling: darker rims where washes meet
    y = im.luminance(np.concatenate([wash, u[..., 3:]], -1))[..., None]
    rim = np.clip(_sobel(y) * 3.0, 0, 1)
    rim = im.gaussian_blur(np.ascontiguousarray(rim[..., None]), max(1.0, scale))[..., 0]
    rgb = wash * (1.0 - rim[..., None] * (0.6 * p["edges"] / 100.0))
    rng = np.random.default_rng(7)
    # blotchy, uneven pigment (large soft noise)
    blot = _grain(h, w, rng, 24 * scale)
    blot = im.gaussian_blur(np.ascontiguousarray(blot[..., None]), 12 * scale)[..., 0]
    blot /= np.abs(blot).max() + 1e-6
    rgb = rgb * (1.0 + blot[..., None] * 0.10)
    # thin paint on white paper: lighter
    rgb = 1.0 - (1.0 - np.clip(rgb, 0, 1)) * 0.78
    if p["paper"] > 0:
        tex = _grain(h, w, rng, scale)
        tex = im.gaussian_blur(np.ascontiguousarray(tex[..., None]), 1.2 * scale)[..., 0]
        tex /= np.abs(tex).max() + 1e-6
        rgb = rgb * (1.0 + tex[..., None] * 0.22 * p["paper"] / 100.0)
    out = u.copy()
    out[..., :3] = np.clip(rgb, 0, 1)
    return im.premultiply(out)


def cartoon(a, p, ctx):
    """Cartoon: flat, smoothed colours in a few brightness steps, with bold dark outlines."""
    u = im.unpremultiply(a)
    h, w = u.shape[:2]
    scale = max(1.0, min(h, w) / 800.0)
    sm = int(p["smoothness"])
    base, _ = _flat_colours(u, max(1, int(sm * scale)), 20, ctx)
    base = np.ascontiguousarray(base[..., :3])
    base = im.gaussian_blur(base, max(1.0, sm * scale * 0.8))
    # step the brightness only, so colours keep their hue
    n = int(p["levels"])
    lum = np.maximum(im.luminance(np.concatenate([base, u[..., 3:]], -1)), 1e-4)
    lq = (np.floor(lum * n) + 0.5) / n
    rgb = np.clip(base * (lq / lum)[..., None], 0, 1)
    # outlines from the flattened picture (so fine texture doesn't turn into specks)
    g = _sobel(lum[..., None])
    thr = np.percentile(g, 100 - 3 - 12 * p["edges"] / 100.0) + 1e-4
    line = (g > thr).astype(np.float32)
    t = max(1, int(round(p["thickness"] * scale)))
    if t > 1:
        line = (im.box_blur(np.ascontiguousarray(line[..., None]), t - 1)[..., 0] > 0.2).astype(np.float32)
    line = im.gaussian_blur(np.ascontiguousarray(line[..., None]), 0.8)[..., 0]
    rgb = rgb * (1.0 - np.clip(line, 0, 1)[..., None] * 0.9)
    out = u.copy()
    out[..., :3] = np.clip(rgb, 0, 1)
    return im.premultiply(out)


HALFTONE_STYLES = ["Black dots", "Colour dots (CMYK)"]


def _dots(ink, size, angle, aa):
    """Halftone screen for one ink: ink amount 0..1 -> dot coverage 0..1 on a rotated grid."""
    h, w = ink.shape
    a = math.radians(angle)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    uu = (xs * math.cos(a) + ys * math.sin(a)) / size
    vv = (-xs * math.sin(a) + ys * math.cos(a)) / size
    fu = uu - np.floor(uu) - 0.5
    fv = vv - np.floor(vv) - 0.5
    d = np.sqrt(fu * fu + fv * fv)                                  # distance to the dot centre (cells)
    rad = np.sqrt(np.clip(ink, 0, 1) / math.pi)                     # dot area == ink amount
    return np.clip((rad - d) * size / aa + 0.5, 0, 1)


def halftone(a, p, ctx):
    """Newspaper-style halftone: the picture made of dots of different sizes."""
    u = im.unpremultiply(a)
    size = float(p["size"])
    out = u.copy()
    if p["style"] == HALFTONE_STYLES[1]:
        rgb = u[..., :3]
        k = 1.0 - rgb.max(axis=2)
        div = np.maximum(1.0 - k, 1e-4)
        c = (1.0 - rgb[..., 0] - k) / div
        m = (1.0 - rgb[..., 1] - k) / div
        yl = (1.0 - rgb[..., 2] - k) / div
        ang = p["angle"]
        C = _dots(c, size, ang + 15, 1.0); M = _dots(m, size, ang + 75, 1.0)
        Y = _dots(yl, size, ang, 1.0); K = _dots(k, size, ang + 45, 1.0)
        out[..., 0] = (1 - C) * (1 - K)
        out[..., 1] = (1 - M) * (1 - K)
        out[..., 2] = (1 - Y) * (1 - K)
    else:
        ink = 1.0 - im.luminance(u)
        dots = _dots(ink, size, p["angle"], 1.0)
        v = 1.0 - dots
        out[..., 0] = v; out[..., 1] = v; out[..., 2] = v
    return im.premultiply(np.clip(out, 0, 1))


def edge_detect(a, p, ctx):
    ang = math.radians(p["angle"])
    u = im.unpremultiply(a)
    kx = [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]
    ky = [[-1, -2, -1], [0, 0, 0], [1, 2, 1]]
    gx = im.convolve3(u[..., :3], kx)
    gy = im.convolve3(u[..., :3], ky)
    g = gx * math.cos(ang) + gy * math.sin(ang)
    out = u.copy()
    out[..., :3] = np.clip(0.5 + g * 0.5, 0, 1)
    return im.premultiply(out)


def emboss(a, p, ctx):
    ang = math.radians(p["angle"])
    u = im.unpremultiply(a)
    y = im.luminance(u)[..., None]
    gx = im.convolve3(y, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])
    gy = im.convolve3(y, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]])
    g = np.clip(0.5 + (gx * math.cos(ang) + gy * math.sin(ang)) * 0.5, 0, 1)[..., 0]
    out = u.copy()
    out[..., 0] = out[..., 1] = out[..., 2] = g
    return im.premultiply(out)


def relief(a, p, ctx):
    ang = math.radians(p["angle"])
    u = im.unpremultiply(a)
    y = im.luminance(u)[..., None]
    gx = im.convolve3(y, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])
    gy = im.convolve3(y, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]])
    g = (gx * math.cos(ang) + gy * math.sin(ang)) * 0.6
    out = u.copy()
    out[..., :3] = np.clip(u[..., :3] + g, 0, 1)
    return im.premultiply(out)


def outline_effect(a, p, ctx):
    u = im.unpremultiply(a)
    r = int(p["thickness"])
    y = im.luminance(u)[..., None]
    blur = im.box_blur(y, r)
    edge = np.abs(y - blur)[..., 0]
    k = np.clip(edge * p["intensity"] / 10.0, 0, 1)
    out = u.copy()
    out[..., :3] = u[..., :3] * (1 - k[..., None])
    return im.premultiply(out)


# sky at the top, sky at the horizon, cloud highlight, cloud shadow
CLOUD_SKIES = {
    "Sky blue": ((38, 110, 200), (140, 196, 242), (255, 255, 255), (196, 206, 222)),
    "Sunset": ((48, 52, 118), (250, 160, 90), (255, 214, 180), (150, 92, 120)),
    "Stormy": ((58, 66, 80), (122, 132, 144), (190, 194, 202), (86, 92, 104)),
    "Night": ((6, 10, 30), (30, 42, 82), (120, 130, 160), (40, 46, 70)),
}


def clouds(a, p, ctx):
    h, w = a.shape[:2]
    t = rf.fbm(h, w, max(2, p["scale"]), p["roughness"] / 100.0, np.random.default_rng(p["seed"]), ctx=ctx)
    style = p.get("style", "Sky blue")
    if style in CLOUD_SKIES:
        top, horizon, light, dark = (np.array(c, np.float32) / 255.0 for c in CLOUD_SKIES[style])
        # sky: deeper colour at the top, paler towards the horizon
        yy = (np.arange(h, dtype=np.float32) / max(h - 1, 1))[:, None, None]
        sky = top * (1 - yy) + horizon * yy
        # clouds: the brightest parts of the noise, soft-edged; thicker parts lighter
        cover = p.get("coverage", 50) / 100.0
        thr = float(np.percentile(t, 100 * (1 - cover))) if 0 < cover < 1 else (1.01 if cover <= 0 else -0.01)
        soft = 0.04 + 0.16 * p.get("softness", 50) / 100.0
        x = np.clip((t - thr + soft) / (2 * soft), 0, 1)
        amt = (x * x * (3 - 2 * x))[..., None]
        depth = np.clip((t - thr) / max(1 - thr, 1e-3), 0, 1)[..., None]
        cloud = dark * (1 - depth) + light * depth
        rgb = sky * (1 - amt) + cloud * amt
        cl = np.concatenate([rgb, np.ones((h, w, 1), np.float32)], axis=2)
    else:
        t = t[..., None]
        c0, c1 = ctx["primary"], ctx["secondary"]
        col0 = np.array([c0.redF(), c0.greenF(), c0.blueF(), c0.alphaF()], np.float32)
        col1 = np.array([c1.redF(), c1.greenF(), c1.blueF(), c1.alphaF()], np.float32)
        cl = im.premultiply(col0 * (1 - t) + col1 * t)
    mode = p["blend"]
    if mode == "Replace":
        return cl
    if mode == "Multiply":
        return a * cl + a * (1 - cl[..., 3:4]) + cl * (1 - a[..., 3:4])
    if mode == "Screen":
        return a + cl - a * cl
    return cl + a * (1 - cl[..., 3:4])  # Normal (over)


ALIGN_CHOICES = ["Top Left", "Top Centre", "Top Right", "Middle Left", "Centre", "Middle Right",
                 "Bottom Left", "Bottom Centre", "Bottom Right", "Centre Horizontally", "Centre Vertically",
                 "Left", "Right", "Top", "Bottom"]


FEATHER_DIRECTIONS = ["Outward", "Inward", "Both sides"]


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def feather_object(a, p, ctx):
    """Feather Object on a float RGBA array (straight colours) - wraps feather_object_image."""
    img = im.from_array(a, False)
    out = feather_object_image(img, p, ctx)
    return im.to_array(out, False)


def feather_object_image(img, p, ctx):
    """Soften the edge of an object on a transparent background (or of the selection).

    Outward: the object keeps its size; the pixels around it take its colour and fade out
    over `radius`. Inward: the object's own edge fades. Both: the fade straddles the edge.
    Only 32 px tiles near an edge are touched.
    """
    r = int(p["radius"])
    direction = p.get("direction", FEATHER_DIRECTIONS[0])
    direction = FEATHER_DIRECTIONS.index(direction) if direction in FEATHER_DIRECTIONS else 0
    out = img.convertToFormat(im.FMT).copy()
    w, h = out.width(), out.height()
    if r <= 0 or w == 0 or h == 0:
        return out
    bpl = out.bytesPerLine()
    arr = np.frombuffer(out.bits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
    src = arr.copy()                        # unchanged pixels, for the colour bleed
    alpha = src[..., 3]                     # premultiplied ARGB32: alpha is the 4th byte
    mask = ctx.get("mask")
    use_mask = mask is not None and mask.shape == alpha.shape
    if not use_mask and alpha.min() == 255:
        return out                          # nothing see-through and no selection: no edge
    inside = alpha >= 128
    if use_mask:
        inside &= mask
    m = int(r * 2.0) + 3                    # how far the blur reaches
    # outside the area counts as background with a selection, or as more of the same without
    mode = "constant" if use_mask else "edge"
    pin = np.pad(inside, m, mode=mode)
    # colours of the object only (premultiplied, so transparent pixels add nothing)
    obj = np.where(inside[..., None], src, 0).astype(np.uint8)
    pobj = np.pad(obj, ((m, m), (m, m), (0, 0)), mode=mode)

    # per 32x32 block of the padded mask: is it all inside / partly inside?
    B = 32
    ph, pw = pin.shape
    bh, bw = -(-ph // B), -(-pw // B)
    blk = np.zeros((bh * B, bw * B), bool)
    blk[:ph, :pw] = pin
    blk = blk.reshape(bh, B, bw, B)
    any_b = blk.any(axis=(1, 3))
    full = np.zeros((bh * B, bw * B), bool)
    full[:ph, :pw] = True
    all_b = (blk | ~full.reshape(bh, B, bw, B)).all(axis=(1, 3))
    T = 192
    tiles = [(y, x) for y in range(0, h, T) for x in range(0, w, T)]

    def window_state(y0, y1, x0, x1):
        by0, by1, bx0, bx1 = y0 // B, -(-y1 // B), x0 // B, -(-x1 // B)
        if all_b[by0:by1, bx0:bx1].all():
            return "in"
        if not any_b[by0:by1, bx0:bx1].any():
            return "out"
        return "edge"

    sig = r * 2.0                            # blur "radius" ~ 3 sigma: fades span about r px

    def job(k0, k1):
        _check(ctx)
        for (ty, tx) in tiles[k0:k1]:
            y1, x1 = min(h, ty + T), min(w, tx + T)
            wy0, wy1, wx0, wx1 = ty, y1 + 2 * m, tx, x1 + 2 * m
            state = window_state(wy0, wy1, wx0, wx1)
            tile = arr[ty:y1, tx:x1]
            if state == "in":
                continue                     # deep inside the object: unchanged
            if state == "out":
                if direction == 1 and tile[..., 3].any():
                    tile[...] = 0            # inward: stray faint pixels fade away completely
                continue
            th, tw = y1 - ty, x1 - tx
            ins = inside[ty:y1, tx:x1]
            win = np.ascontiguousarray(pin[wy0:wy1, wx0:wx1, None], dtype=np.float32)
            b = im.gaussian_blur(win, sig)[m:m + th, m:m + tw, 0]
            px = tile.astype(np.float32)
            if direction == 1:
                # inward: fade the object's own edge, clear at the edge -> solid r px in
                t = _smooth(b * 2.0 - 1.0)
                t[~ins] = 0.0
                tile[...] = (px * t[..., None] + 0.5).astype(np.uint8)
                continue
            # outward / both sides: the pixels around the object take on its colour - a blur of
            # the object's colours divided by the blurred coverage - and fade out with distance
            cwin = np.ascontiguousarray(pobj[wy0:wy1, wx0:wx1], dtype=np.float32)
            cb = im.gaussian_blur(cwin, sig)[m:m + th, m:m + tw]
            cov = np.maximum(cb[..., 3:4], 1e-3)
            # premultiplied colour / opacity = the nearby object's own (straight) colour, 0..255
            colour = cb[..., :3] * 255.0 / cov
            if direction == 0:
                a_new = _smooth(b * 2.0)          # solid at the edge -> clear about r px out
            else:
                a_new = _smooth(b)                # centred on the edge
            res = px.copy()
            outside = ~ins
            # outside the object: new soft halo (never less than what was already there)
            a_px = np.maximum(px[..., 3] / 255.0, a_new)
            o = outside
            res[o, 3] = a_px[o] * 255.0
            res[o, :3] = colour[o] * a_px[o, None]
            if direction == 2:
                # both sides: the object's own edge fades to half too
                t_in = np.minimum(1.0, a_new)
                res[ins] = px[ins] * t_in[ins, None]
            tile[...] = (np.clip(res, 0, 255) + 0.5).astype(np.uint8)
    im.parallel(len(tiles), job, step=8)
    return out


def align_object(a, p, ctx):
    """Move the object in the selection to an edge or the centre of the selection. The object
    is every non-transparent pixel, or, if the area is opaque, every pixel that differs
    from the background (the most common corner colour).
    """
    h, w = a.shape[:2]
    mask = ctx.get("mask")
    inside = np.ones((h, w), bool) if mask is None else mask
    alpha = a[..., 3]
    tol = p.get("tolerance", 10) / 100.0
    opaque = bool((alpha[inside] > 0.99).all())
    if not opaque:
        obj = (alpha > max(tol * 0.5, 1 / 255.0)) & inside
        bg = np.zeros(4, np.float32)
    else:
        corners = [a[0, 0], a[0, w - 1], a[h - 1, 0], a[h - 1, w - 1]]
        best = max(corners, key=lambda c: sum(np.abs(c - d).max() <= 1 / 255.0 for d in corners))
        bg = best.astype(np.float32)
        obj = (np.abs(a - bg).max(axis=2) > max(tol, 1 / 255.0)) & inside
    ys, xs = np.nonzero(obj)
    if len(xs) == 0:
        return a
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    ow, oh = x1 - x0, y1 - y0
    m = p["align"]
    nx, ny = x0, y0
    if m in ("Top Left", "Middle Left", "Bottom Left", "Left"):
        nx = 0
    elif m in ("Top Right", "Middle Right", "Bottom Right", "Right"):
        nx = w - ow
    elif m in ("Top Centre", "Centre", "Bottom Centre", "Centre Horizontally"):
        nx = (w - ow) // 2
    if m in ("Top Left", "Top Centre", "Top Right", "Top"):
        ny = 0
    elif m in ("Bottom Left", "Bottom Centre", "Bottom Right", "Bottom"):
        ny = h - oh
    elif m in ("Middle Left", "Centre", "Middle Right", "Centre Vertically"):
        ny = (h - oh) // 2
    out = a.copy()
    out[obj] = bg
    block = a[y0:y1, x0:x1]
    sel = obj[y0:y1, x0:x1]
    region = out[ny:ny + oh, nx:nx + ow]
    region[sel] = block[sel]
    return out


EFFECTS = [
    ("Artistic", [
        Effect("Ink Sketch", ink_sketch, [("ink", "Ink outline", "int", 50, 0, 99), ("coloring", "Colouring", "int", 50, 0, 100)], halo=lambda p: 2, premult=True),
        Effect("Oil Painting", oil_painting, [("brush", "Brush size", "int", 3, 1, 8), ("coarseness", "Coarseness", "int", 20, 3, 60)], halo=lambda p: p["brush"] + 2, premult=True),
        Effect("Cartoon", cartoon, [("smoothness", "Smoothness", "int", 4, 1, 10), ("levels", "Colour levels", "int", 6, 2, 16),
                                     ("edges", "Outline amount", "int", 60, 0, 100), ("thickness", "Outline thickness", "int", 2, 1, 6)],
               premult=True),
        Effect("Halftone", halftone, [("size", "Dot size", "int", 8, 3, 60), ("angle", "Angle", "angle", 45, -180, 180),
                                       ("style", "Style", "choice", HALFTONE_STYLES[0], HALFTONE_STYLES, None)], premult=True),
        Effect("Pencil Sketch", pencil_sketch, [("tip", "Pencil tip size", "int", 2, 1, 20), ("range", "Colour range", "int", 0, -20, 20)], halo=lambda p: p["tip"] * 2 + 4, premult=True),
        Effect("Watercolour", watercolour, [("brush", "Brush size", "int", 4, 1, 10), ("edges", "Pigment at edges", "int", 50, 0, 100),
                                             ("paper", "Paper texture", "int", 40, 0, 100)], premult=True),
    ]),
    ("Blurs", [
        Effect("Fragment", fragment, [("fragments", "Fragments", "int", 4, 2, 50), ("distance", "Distance", "int", 8, 0, 100), ("rotation", "Rotation", "angle", 45, -180, 180)], premult=True),
        Effect("Gaussian Blur", gaussian, [("radius", "Radius", "int", 2, 0, 200)], premult=True,
               image_fn=lambda img, p, c: im.blur_image(img, p["radius"])),
        Effect("Motion Blur", motion_blur, [("angle", "Angle", "angle", 25, -180, 180), ("distance", "Distance", "int", 10, 1, 200), ("centered", "Centred", "bool", True, 0, 1)], premult=True),
        Effect("Radial Blur", radial_blur, [("angle", "Angle", "int", 2, 0, 90), ("offset_x", "Offset X", "int", 0, -100, 100), ("offset_y", "Offset Y", "int", 0, -100, 100)], premult=True),
        Effect("Unfocus", unfocus, [("radius", "Radius", "int", 4, 1, 100)], halo=lambda p: p["radius"] + p["radius"] // 2 + 2, premult=True),
        Effect("Zoom Blur", zoom_blur, [("amount", "Zoom amount", "int", 10, 0, 100), ("offset_x", "Offset X", "int", 0, -100, 100), ("offset_y", "Offset Y", "int", 0, -100, 100)], premult=True),
    ]),
    ("Borders", [
        Effect("Simple Border", bd.simple_border, [
            ("width", "Width", "int", 10, 1, 300), ("style", "Line", "choice", "Solid", ["Solid", "Dashed", "Dotted"], None),
            ("colour", "Colour", "choice", "Primary colour", bd.COLOURS, None),
            ("gap", "Gap from the edge", "int", 0, 0, 300), ("radius", "Round the corners", "int", 0, 0, 500),
            ("outside_corners", "Outside rounded corners", "choice", "Transparent", ["Transparent", "Secondary colour", "White", "Black", "Leave as they are"], None),
            ("outside", "Add it outside the picture (makes the image bigger)", "bool", False, 0, 1)], premult=True, grow=bd._grow_simple),
        Effect("Double Border", bd.double_border, [
            ("outer", "Outer line width", "int", 8, 0, 200), ("space", "Space between", "int", 6, 0, 200),
            ("inner", "Inner line width", "int", 3, 0, 200),
            ("colour", "Line colour", "choice", "Primary colour", bd.COLOURS, None),
            ("space_fill", "Space between is", "choice", "Secondary colour", ["Secondary colour", "White", "Black", "Primary colour", "See-through (keep picture)"], None),
            ("outside", "Add it outside the picture (makes the image bigger)", "bool", False, 0, 1)], premult=True, grow=bd._grow_double),
        Effect("Picture Frame", bd.picture_frame, [
            ("width", "Frame width", "int", 30, 4, 400),
            ("style", "Style", "choice", "Moulded", bd.FRAME_STYLES, None),
            ("colour", "Frame colour (not Wood, Gold, Silver)", "choice", "Primary colour", bd.COLOURS, None),
            ("shading", "Shading (light and shadow)", "int", 50, 0, 100),
            ("mount", "Mount width (card round the picture)", "int", 0, 0, 400),
            ("mount_colour", "Mount colour", "choice", "White", bd.MOUNT_COLOURS, None),
            ("inner_shadow", "Shadow on the picture", "bool", True, 0, 1),
            ("outside", "Add it outside the picture (makes the image bigger)", "bool", False, 0, 1)], premult=True, grow=bd._grow_frame),
        Effect("Rounded Corners", bd.rounded_corners, [
            ("radius", "Corner radius", "int", 40, 1, 1000),
            ("fill", "Corners become", "choice", "Transparent", ["Transparent", "Secondary colour", "White", "Black", "Primary colour"], None)], premult=True),
        Effect("Soft Edge", bd.soft_edge, [
            ("width", "Fade width", "int", 40, 1, 1000), ("shape", "Shape", "choice", "Rectangle", ["Rectangle", "Oval"], None),
            ("fill", "Fade to", "choice", "Transparent", ["Transparent", "Secondary colour", "White", "Black", "Primary colour"], None)], premult=True),
        Effect("Rough Edge", bd.rough_edge, [
            ("width", "Edge depth", "int", 20, 2, 300), ("roughness", "Roughness", "int", 60, 0, 100),
            ("fill", "Torn area becomes", "choice", "Transparent", ["Transparent", "Secondary colour", "White", "Black", "Primary colour"], None),
            ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Drop Shadow", bd.drop_shadow, [
            ("offset_x", "Offset X", "int", 8, -100, 100), ("offset_y", "Offset Y", "int", 8, -100, 100),
            ("blur", "Softness", "int", 12, 0, 150), ("opacity", "Strength", "int", 60, 0, 100),
            ("colour", "Shadow colour", "choice", "Black", ["Black", "Primary colour", "Secondary colour", "White"], None),
            ("outside", "Add room around the picture for the shadow (makes it bigger)", "bool", True, 0, 1),
            ("background", "Background around the picture", "choice", "White",
             ["White", "Transparent", "Secondary colour", "Black"], None)], premult=True, grow=bd._grow_shadow),
    ]),
    ("Distort", [
        Effect("Bulge", bulge, [("amount", "Bulge", "int", 45, -200, 100), ("offset_x", "Offset X", "int", 0, -100, 100), ("offset_y", "Offset Y", "int", 0, -100, 100)], premult=True),
        Effect("Frosted Glass", frosted_glass, [("amount", "Scatter", "int", 3, 1, 20), ("smooth", "Smooth", "bool", True, 0, 1), ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Pixelate", pixelate, [("size", "Cell size", "int", 8, 1, 100)], premult=True),
        Effect("Tile Reflection", tile_reflection, [("tile_size", "Tile size", "int", 40, 4, 200), ("curvature", "Curvature", "int", 8, -100, 100), ("angle", "Angle", "angle", 30, -180, 180)], premult=True),
        Effect("Twist", twist, [("amount", "Amount", "int", 30, -200, 200), ("size", "Size", "int", 100, 1, 200)], premult=True),
    ]),
    ("Noise", [
        Effect("Add Noise", add_noise, [("intensity", "Intensity", "int", 64, 0, 100), ("saturation", "Colour saturation", "int", 100, 0, 100), ("coverage", "Coverage", "int", 100, 0, 100), ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Median", median, [("radius", "Radius", "int", 2, 1, 10), ("percentile", "Percentile", "int", 50, 0, 100)], halo=lambda p: p["radius"] + 1, premult=True),
        Effect("Reduce Noise", reduce_noise, [("radius", "Radius", "int", 4, 1, 50), ("strength", "Strength", "int", 40, 0, 100)], halo=lambda p: p["radius"] + 4, premult=True),
    ]),
    ("Object", [
        Effect("Align Object", align_object, [("align", "Alignment", "choice", "Centre", ALIGN_CHOICES, None),
                                               ("tolerance", "Background tolerance", "int", 10, 0, 100)], premult=True),
        Effect("Feather Object", feather_object, [("radius", "Feather radius", "int", 2, 1, 100),
                                                   ("direction", "Direction", "choice", FEATHER_DIRECTIONS[0],
                                                    FEATHER_DIRECTIONS, None)],
               image_fn=feather_object_image),
    ]),
    ("Photo", [
        Effect("Glow", glow, [("radius", "Radius", "int", 6, 1, 50), ("brightness", "Brightness", "int", 10, -100, 100), ("contrast", "Contrast", "int", 10, -100, 100)], halo=lambda p: p["radius"] + 4, premult=True),
        Effect("Old Photo", old_photo, [("style", "Style", "choice", OLD_PHOTO_STYLES[0], OLD_PHOTO_STYLES, None),
                                         ("age", "Age (fading)", "int", 60, 0, 100), ("grain", "Grain", "int", 35, 0, 100),
                                         ("scratches", "Scratches and dust", "int", 30, 0, 100), ("vignette", "Dark edges", "int", 50, 0, 100),
                                         ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Sharpen", sharpen, [("amount", "Amount", "int", 4, 1, 20)], halo=lambda p: 6, premult=True),
        Effect("Soften Portrait", soften_portrait, [("softness", "Softness", "int", 5, 0, 10), ("lighting", "Lighting", "int", 0, -20, 20), ("warmth", "Warmth", "int", 10, 0, 20)], halo=lambda p: 2 + p["softness"] * 0.6 + 4, premult=True),
        Effect("Vignette", vignette, [("radius", "Radius", "int", 80, 1, 200), ("density", "Density", "int", 100, 0, 100), ("offset_x", "Offset X", "int", 0, -100, 100), ("offset_y", "Offset Y", "int", 0, -100, 100)], premult=True),
    ]),
    ("Render", [
        Effect("Clouds", clouds, [("style", "Colours", "choice", "Sky blue",
                                    ["Sky blue", "Sunset", "Stormy", "Night", "Primary to secondary colour"], None),
                                   ("coverage", "Cloud cover (sky colours)", "int", 45, 0, 100),
                                   ("softness", "Cloud softness (sky colours)", "int", 50, 0, 100),
                                   ("scale", "Scale", "int", 250, 2, 1000), ("roughness", "Roughness", "int", 50, 0, 100),
                                   ("blend", "Blend mode", "choice", "Normal", ["Normal", "Replace", "Multiply", "Screen"], None),
                                   ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Difference Clouds", rf.difference_clouds, [
            ("scale", "Scale", "int", 250, 2, 1000), ("roughness", "Roughness", "int", 50, 0, 100),
            ("colours", "Colours", "choice", "Primary to secondary colour", ["Primary to secondary colour", "Black and white"], None),
            ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Fibres", rf.fibres, [
            ("variance", "Variance (thickness)", "int", 16, 1, 64), ("strength", "Strength (length)", "int", 4, 1, 64),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Multiply"], None),
            ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Julia Fractal", rf.julia, [
            ("palette", "Colours", "choice", "Rainbow", rf.PALETTES, None),
            ("c_real", "Shape (real part)", "float", -0.8, -2.0, 2.0), ("c_imag", "Shape (imaginary part)", "float", 0.156, -2.0, 2.0),
            ("zoom", "Zoom %", "int", 100, 10, 20000), ("angle", "Angle", "angle", 0, -180, 180),
            ("iterations", "Detail (iterations)", "int", 200, 20, 2000), ("colour_cycle", "Colour bands", "int", 100, 10, 1000),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Screen"], None)], premult=True),
        Effect("Lens Flare", rf.lens_flare, [
            ("x", "Position across (%)", "int", 30, 0, 100), ("y", "Position down (%)", "int", 25, 0, 100),
            ("brightness", "Brightness", "int", 100, 10, 300), ("size", "Size", "int", 100, 10, 400),
            ("lens", "Lens type", "choice", "Zoom lens", rf.LENSES, None)], premult=True),
        Effect("Lighting Effects", rf.lighting, [
            ("type", "Light type", "choice", "Spotlight", rf.LIGHT_TYPES, None),
            ("x", "Light position across (%)", "int", 30, 0, 100), ("y", "Light position down (%)", "int", 25, 0, 100),
            ("angle", "Direction", "angle", 40, -180, 180), ("size", "Size of the light", "int", 70, 5, 200),
            ("intensity", "Brightness", "int", 110, 0, 250), ("ambient", "Light in the dark areas", "int", 25, 0, 100),
            ("colour", "Light colour", "choice", "White", rf.LIGHT_COLOURS, None),
            ("texture", "Surface texture (bumps from the picture)", "int", 0, 0, 100)], premult=True),
        Effect("Mandelbrot Fractal", rf.mandelbrot, [
            ("palette", "Colours", "choice", "Fire", rf.PALETTES, None),
            ("offset_x", "Move across", "int", 0, -200, 200), ("offset_y", "Move down", "int", 0, -200, 200),
            ("zoom", "Zoom %", "int", 100, 10, 20000), ("angle", "Angle", "angle", 0, -180, 180),
            ("iterations", "Detail (iterations)", "int", 200, 20, 2000), ("colour_cycle", "Colour bands", "int", 100, 10, 1000),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Screen"], None)], premult=True),
        Effect("Plasma", rf.plasma, [
            ("scale", "Scale", "int", 200, 4, 1000), ("turbulence", "Turbulence", "int", 40, 0, 100),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Screen", "Multiply"], None),
            ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Rain and Snow", rf.rain_snow, [
            ("type", "Weather", "choice", "Rain", ["Rain", "Snow"], None),
            ("amount", "Amount", "int", 50, 1, 200), ("size", "Size", "int", 100, 20, 400),
            ("wind", "Wind (slant)", "int", 12, -60, 60), ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Starfield", rf.starfield, [
            ("background", "Background", "choice", "Black space", rf.STAR_BACKGROUNDS, None),
            ("density", "Number of stars", "int", 30, 1, 200), ("size", "Star size", "int", 100, 20, 400),
            ("brightness", "Brightness", "int", 100, 10, 200), ("sparkle", "Sparkle on bright stars", "bool", True, 0, 1),
            ("nebula", "Coloured nebula", "bool", False, 0, 1), ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
        Effect("Sunburst", rf.sunburst, [
            ("colours", "Colours", "choice", "Sunny yellow", rf.SUNBURST_COLOURS, None),
            ("x", "Centre across (%)", "int", 50, 0, 100), ("y", "Centre down (%)", "int", 50, 0, 100),
            ("rays", "Number of rays", "int", 16, 2, 120), ("ray_width", "Ray width (%)", "int", 50, 5, 95),
            ("angle", "Turn", "angle", 0, -180, 180), ("glow", "Glow in the middle", "bool", True, 0, 1),
            ("fade", "Darker towards the edges", "bool", False, 0, 1),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Screen"], None)], premult=True),
        Effect("Textures", rf.texture, [
            ("type", "Texture", "choice", "Wood", rf.TEXTURES, None),
            ("colours", "Colours", "choice", "Natural", ["Natural", "Primary and secondary colour"], None),
            ("scale", "Scale", "int", 120, 5, 1000),
            ("blend", "Blend mode", "choice", "Replace", ["Replace", "Multiply"], None),
            ("seed", "Random seed", "seed", 1, 0, 99999)], premult=True),
    ]),
    ("Stylise", [
        Effect("Edge Detect", edge_detect, [("angle", "Angle", "angle", 45, -180, 180)], halo=lambda p: 2, premult=True),
        Effect("Emboss", emboss, [("angle", "Angle", "angle", 0, -180, 180)], halo=lambda p: 2, premult=True),
        Effect("Outline", outline_effect, [("thickness", "Thickness", "int", 3, 1, 50), ("intensity", "Intensity", "int", 50, 0, 100)], halo=lambda p: p["thickness"] + 2, premult=True),
        Effect("Relief", relief, [("angle", "Angle", "angle", 45, -180, 180)], halo=lambda p: 2, premult=True),
    ]),
]
