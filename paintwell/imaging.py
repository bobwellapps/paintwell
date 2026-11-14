"""QImage <-> numpy helpers and pixel algorithms (flood fill, blurs, remaps)."""
import numpy as np
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QImage, QPainter, QPainterPath, QColor

FMT = QImage.Format_ARGB32_Premultiplied


def _path_mask(size, path):
    from PySide6.QtGui import QPainter, QColor
    mask = QImage(size, QImage.Format_ARGB32_Premultiplied)
    mask.fill(0)
    p = QPainter(mask)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillPath(path, QColor(0, 0, 0, 255))
    p.end()
    return mask


def cut_out(img, path):
    """Clear the pixels inside path (in place), with the same soft edge as keep_inside so the
    two halves fit back together.
    """
    from PySide6.QtGui import QPainter
    mask = _path_mask(img.size(), path)
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
    p.drawImage(0, 0, mask)
    p.end()
    return img


def keep_inside(img, path):
    """Clear every pixel outside path (in place), with a soft edge.

    DestinationIn with the shape itself would leave pixels outside it untouched, so a
    full-size mask is drawn instead.
    """
    from PySide6.QtGui import QPainter
    mask = _path_mask(img.size(), path)
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()
    return img


def disjoint_over(dst, src, x, y):
    """Draw src onto dst at (x, y), treating their soft edges as complementary.

    When a selection is moved, both the hole and the lifted pixels have antialiased edges.
    Normal 'over' blending leaves a faint ring there; adding the two coverages closes it,
    while opaque pixels blend as usual. Both images must be FMT (premultiplied ARGB32).
    """
    r = QRect(x, y, src.width(), src.height()).intersected(dst.rect())
    if r.isEmpty():
        return
    sx, sy = r.x() - x, r.y() - y
    bpl_d, bpl_s = dst.bytesPerLine(), src.bytesPerLine()
    d = np.frombuffer(dst.bits(), np.uint8, count=bpl_d * dst.height()).reshape(dst.height(), bpl_d)
    s_ = np.frombuffer(src.constBits(), np.uint8, count=bpl_s * src.height()).reshape(src.height(), bpl_s)
    D = d[r.y():r.y() + r.height(), r.x() * 4:(r.x() + r.width()) * 4].reshape(r.height(), r.width(), 4)
    S = s_[sy:sy + r.height(), sx * 4:(sx + r.width()) * 4].reshape(r.height(), r.width(), 4)
    a_s = S[..., 3:4].astype(np.float32)
    a_d = D[..., 3:4].astype(np.float32)
    k = np.minimum(1.0, (255.0 - a_s) / np.maximum(a_d, 1.0))
    out = S.astype(np.float32) + D.astype(np.float32) * k + 0.5
    np.clip(out, 0, 255, out=out)
    D[...] = out.astype(np.uint8)


def new_image(w, h, color=None):
    img = QImage(max(1, w), max(1, h), FMT)
    img.fill(color if color is not None else Qt.transparent)
    return img


_RGBA = {True: QImage.Format_RGBA8888_Premultiplied, False: QImage.Format_RGBA8888}


def to_u8(img, premultiplied=True):
    """HxWx4 uint8 RGBA copy. Qt does any (un)premultiplying in optimised C++."""
    img = img.convertToFormat(_RGBA[premultiplied])
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()


def to_array(img, premultiplied=True):
    """Copy of the image as float32 HxWx4 array in RGBA order, 0..1."""
    img = img.convertToFormat(_RGBA[premultiplied])
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    a = buf.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    out = np.empty((h, w, 4), np.float32)
    np.multiply(a, np.float32(1.0 / 255.0), out=out)
    return out


def float_to_u8(arr):
    """float 0..1 -> uint8 with rounding and clipping, without big temporaries."""
    out = np.empty(arr.shape, np.uint8)
    step = max(1, (1 << 22) // max(1, arr[0].size))       # ~4M values per chunk
    for y in range(0, arr.shape[0], step):
        t = arr[y:y + step] * np.float32(255.0)
        t += np.float32(0.5)
        np.clip(t, 0, 255, out=t)
        out[y:y + step] = t
    return out


def u8_to_image(u8, premultiplied=True):
    h, w = u8.shape[:2]
    u8 = np.ascontiguousarray(u8)
    # convertToFormat makes a new image, so it doesn't keep pointing at numpy's memory
    return QImage(u8.data, w, h, w * 4, _RGBA[premultiplied]).convertToFormat(FMT)


def from_array(arr, premultiplied=True):
    """float 0..1 (or uint8) RGBA array -> QImage (premultiplied format)."""
    u8 = arr if arr.dtype == np.uint8 else float_to_u8(arr)
    return u8_to_image(u8, premultiplied)


def premultiply(a):
    out = a.copy()
    out[..., :3] *= out[..., 3:4]
    return out


def unpremultiply(a):
    out = a.copy()
    al = out[..., 3:4]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[..., :3] = np.where(al > 0, out[..., :3] / np.maximum(al, 1e-6), 0)
    return np.clip(out, 0, 1)


def path_mask(path, w, h):
    """Boolean coverage (float 0..1) mask of a QPainterPath."""
    img = QImage(w, h, QImage.Format_Alpha8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillPath(path, QColor(0, 0, 0, 255))
    p.end()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, :w].astype(np.float32) / 255.0


# --- Flood fill ---
def color_distance_mask(rgba_u8, color, tolerance):
    """rgba_u8: H x W x 4 uint8, not premultiplied; tolerance 0..1.
    Integer maths on bands of rows in parallel, to avoid a full float copy.
    """
    h, w = rgba_u8.shape[:2]
    c = [int(v) for v in color]
    limit = (tolerance + 1e-6) ** 2 * 4 * 255 * 255
    out = np.empty((h, w), bool)

    def job(y0, y1):
        band = rgba_u8[y0:y1]
        acc = None
        for ch in range(4):
            d = band[..., ch].astype(np.int32)
            d -= c[ch]
            d *= d
            acc = d if acc is None else acc.__iadd__(d)
        np.less_equal(acc, limit, out=out[y0:y1])
    parallel(h, job)
    return out


def flood_region(similar, x, y):
    """Contiguous region of True cells containing (x,y), scanline fill."""
    h, w = similar.shape
    out = np.zeros_like(similar)
    if not (0 <= x < w and 0 <= y < h) or not similar[y, x]:
        return out
    stack = [(x, y)]
    while stack:
        sx, sy = stack.pop()
        if out[sy, sx] or not similar[sy, sx]:
            continue
        row = similar[sy]
        # extend left / right using vectorised search
        left_part = row[:sx + 1][::-1]
        stop = np.argmin(left_part) if not left_part.all() else len(left_part)
        x0 = sx - stop + 1
        right_part = row[sx:]
        stop = np.argmin(right_part) if not right_part.all() else len(right_part)
        x1 = sx + stop - 1
        out[sy, x0:x1 + 1] = True
        for ny in (sy - 1, sy + 1):
            if 0 <= ny < h:
                seg = similar[ny, x0:x1 + 1] & ~out[ny, x0:x1 + 1]
                if seg.any():
                    # start of each run
                    d = np.diff(np.concatenate(([0], seg.view(np.int8))))
                    for s in np.nonzero(d == 1)[0]:
                        stack.append((x0 + int(s), ny))
    return out


def mask_to_path(mask):
    """Convert a boolean mask into an outline QPainterPath."""
    from PySide6.QtGui import QRegion, QBitmap
    h, w = mask.shape
    g = np.ascontiguousarray(np.where(mask, 0, 255).astype(np.uint8))
    img = QImage(g.data, w, h, w, QImage.Format_Grayscale8).copy()
    region = QRegion(QBitmap.fromImage(img, Qt.ThresholdDither | Qt.MonoOnly))
    path = QPainterPath()
    path.addRegion(region)
    if region.rectCount() < 20000:
        path = path.simplified()
    return path


# --- Multi-core helper ---
_POOL = None


def parallel(n_rows, job, min_rows=64, step=None):
    """Run job(start, end) over chunks of range(n_rows) on all cores. numpy releases the GIL
    inside its loops, so the threads do run in parallel.
    """
    global _POOL
    import os
    import threading
    n = max(1, min(os.cpu_count() or 1, 16))
    # already inside a worker (e.g. a blur inside a banded effect): just do it here, waiting on
    # the pool from one of its own threads would deadlock
    if n == 1 or n_rows < min_rows or threading.current_thread().name.startswith("paintwell"):
        job(0, n_rows)
        return
    if _POOL is None:
        from concurrent.futures import ThreadPoolExecutor
        _POOL = ThreadPoolExecutor(n, thread_name_prefix="paintwell")
    if step is None:
        step = max(16, -(-n_rows // (n * 2)))
    for f in [_POOL.submit(job, y, min(n_rows, y + step)) for y in range(0, n_rows, step)]:
        f.result()


# --- Filters ---
def _box_blur_axis1(a, r, axis):
    if r <= 0:
        return a
    n = a.shape[axis]
    if r <= 3:
        # small boxes: just add up the 2r+1 shifted copies (fewer passes than running sums)
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        p = np.pad(a, pad, mode="edge")
        sl = [slice(None)] * a.ndim

        def view(k):
            sl[axis] = slice(k, k + n)
            return p[tuple(sl)]
        out = np.add(view(0), view(1))
        for k in range(2, 2 * r + 1):
            out += view(k)
        out *= np.float32(1.0 / (2 * r + 1))
        return out
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float32)
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(2 * r + 1, 2 * r + 1 + n)
    lo[axis] = slice(0, n)
    out = np.subtract(c[tuple(hi)], c[tuple(lo)])      # views, no extra copies
    out *= np.float32(1.0 / (2 * r + 1))
    return out


def box_blur_axis(a, r, axis):
    """Box blur along one axis; big images are split along the other axis across CPU cores."""
    if r <= 0:
        return a
    if a.size < 1 << 18:
        return _box_blur_axis1(a, r, axis)
    out = np.empty(a.shape, np.float32)
    other = 1 - axis
    n = a.shape[other]

    def job(i0, i1):
        sl = [slice(None)] * a.ndim
        sl[other] = slice(i0, i1)
        sl = tuple(sl)
        out[sl] = _box_blur_axis1(a[sl], r, axis)
    parallel(n, job)
    return out


def box_blur(a, r):
    return box_blur_axis(box_blur_axis(a, r, 0), r, 1)


def _gauss_boxes(radius):
    sigma = max(radius / 3.0, 0.5)
    n = 3
    wideal = np.sqrt(12 * sigma * sigma / n + 1)
    wl = int(np.floor(wideal))
    if wl % 2 == 0:
        wl -= 1
    wu = wl + 2
    m = round((12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4))
    return [max(0, ((wl if i < m else wu) - 1) // 2) for i in range(n)]


def gaussian_blur(a, radius):
    """Gaussian approximated by three box blurs (radius ~ 3 sigma). Each band of rows/columns
    gets all three passes while it's still in cache.
    """
    if radius <= 0:
        return a.copy()
    rs = [r for r in _gauss_boxes(radius) if r > 0]
    if not rs:
        return a.copy()
    if a.size < 1 << 18:
        out = a
        for r in rs:
            out = box_blur(out, r)
        return out
    h, w = a.shape[:2]
    mid = np.empty(a.shape, np.float32)
    out = np.empty(a.shape, np.float32)

    def rows(y0, y1):
        t = a[y0:y1]
        for r in rs:
            t = _box_blur_axis1(t, r, 1)
        mid[y0:y1] = t

    def cols(x0, x1):
        t = mid[:, x0:x1]
        for r in rs:
            t = _box_blur_axis1(t, r, 0)
        out[:, x0:x1] = t
    band = max(8, (1 << 20) // max(1, a[0].size * 4))        # ~1 MB of rows per job
    parallel(h, rows, step=band)
    band = max(8, (1 << 20) // max(1, a[:, 0].size * 4))
    parallel(w, cols, step=band)
    return out


def bilinear(a, xs, ys):
    """Sample array a (HxWxC) at float coords with edge clamping."""
    h, w = a.shape[:2]
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (xs - x0)[..., None]
    fy = (ys - y0)[..., None]
    top = a[y0, x0] * (1 - fx) + a[y0, x1] * fx
    bot = a[y1, x0] * (1 - fx) + a[y1, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def convolve3(a, k):
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = a.shape[:2]
    out = np.zeros_like(a)
    for dy in range(3):
        for dx in range(3):
            if k[dy][dx]:
                out += k[dy][dx] * p[dy:dy + h, dx:dx + w]
    return out


def luminance(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb[..., :3], axis=-1)
    mn = np.min(rgb[..., :3], axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = ((g - b)[rm] / d[rm]) % 6
    h[gm] = (b - r)[gm] / d[gm] + 2
    h[bm] = (r - g)[bm] / d[bm] + 4
    h /= 6.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0)
    return h, s, mx


def hsv_to_rgb(h, s, v):
    h6 = (h % 1.0) * 6
    i = np.floor(h6).astype(np.int32) % 6
    f = h6 - np.floor(h6)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def blur_image(img, radius):
    """Gaussian blur of a QImage. Large radii are done on a scaled-down copy, since a wide
    blur has no fine detail to lose.
    """
    if radius <= 0:
        return QImage(img)
    w, h = img.width(), img.height()
    f = max(1, int(radius // 5))
    if f >= 2 and w >= 8 * f and h >= 8 * f:
        sw, sh = max(1, round(w / f)), max(1, round(h / f))
        small = img.convertToFormat(FMT).scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        out = from_array(gaussian_blur(to_array(small, True), radius / f), True)
        return out.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return from_array(gaussian_blur(to_array(img, True), radius), True)
