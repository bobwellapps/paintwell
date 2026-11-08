"""Drawing and selection tools."""
import math
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QRect, QPoint, QLineF, QSizeF, QTimer
from PySide6.QtGui import (QPainter, QPen, QColor, QBrush, QPainterPath, QTransform, QImage,
                           QPolygonF, QLinearGradient, QRadialGradient, QConicalGradient, QFont,
                           QFontMetricsF, QCursor, QPainterPathStroker)

from .document import TextItem
from . import imaging
from .imaging import new_image

HANDLE = 5  # half-size of edit handles in view pixels


def selection_mode_for(button, mods, default):
    ctrl = bool(mods & Qt.ControlModifier)
    alt = bool(mods & Qt.AltModifier)
    if button == Qt.RightButton:
        if ctrl:
            return "xor"
        if alt:
            return "intersect"
        return "exclude"
    if ctrl:
        return "union"
    if alt:
        return "exclude"
    return default


def combine(base, shape, mode):
    if base is None or mode == "replace":
        return shape
    if mode == "union":
        return base.united(shape)
    if mode == "exclude":
        return base.subtracted(shape)
    if mode == "intersect":
        return base.intersected(shape)
    if mode == "xor":
        return base.united(shape).subtracted(base.intersected(shape))
    return shape


def draw_handle(p, vp, round_=False, active=False):
    p.setPen(QPen(QColor(30, 30, 30), 1))
    p.setBrush(QColor(80, 160, 255) if active else QColor(255, 255, 255))
    r = QRectF(vp.x() - HANDLE, vp.y() - HANDLE, HANDLE * 2, HANDLE * 2)
    if round_:
        p.drawEllipse(r)
    else:
        p.drawRect(r)


def near(canvas, ip, target, slack=HANDLE + 3):
    a = canvas.to_view(ip)
    b = canvas.to_view(target)
    return abs(a.x() - b.x()) <= slack and abs(a.y() - b.y()) <= slack


ROT_HANDLE_GAP = 16   # view pixels from each corner out to its round rotate handle
ROT_ZONE = 34         # grab anywhere this far outside a corner to rotate
_ROT_CURSORS = {}


def rotate_cursor(direction=-90.0):
    """Curved double-arrow cursor for rotating.

    direction is the screen angle (degrees, 0 = right, 90 = down) pointing from the
    centre of the box out towards the pointer; the curve bulges that way so it wraps
    around whichever corner you are on.  Cursors are made once per 15° and cached."""
    step = int(round(direction / 15.0)) % 24
    cur = _ROT_CURSORS.get(step)
    if cur is None:
        from PySide6.QtGui import QPixmap
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(16, 16)
        p.rotate(step * 15 + 90)          # the drawing below bulges upwards (-90°)
        arc = QPainterPath()
        box = QRectF(-11, -4, 22, 22)     # circle centred just below the hotspot
        arc.arcMoveTo(box, 145)
        arc.arcTo(box, 145, -110)
        for col, w in ((QColor(255, 255, 255), 4.2), (QColor(0, 0, 0), 1.7)):
            p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(arc)
            p.setBrush(col)
            for pt, ang in ((arc.pointAtPercent(0.0), arc.angleAtPercent(0.0) + 180),
                            (arc.pointAtPercent(1.0), arc.angleAtPercent(1.0))):
                t = QTransform().translate(pt.x(), pt.y()).rotate(-ang)
                grow = 1.2 if w > 2 else 0.0
                head = QPolygonF([t.map(QPointF(4.0 + grow, 0)), t.map(QPointF(-2.5 - grow, -3.6 - grow)),
                                  t.map(QPointF(-2.5 - grow, 3.6 + grow))])
                p.setPen(Qt.NoPen)
                p.drawPolygon(head)
                p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.end()
        cur = QCursor(pm, 16, 16)
        _ROT_CURSORS[step] = cur
    return cur


def _raw(canvas, pos):
    """The un-snapped pointer position the canvas recorded for this event (or pos)."""
    r = getattr(canvas, "raw_ip", None)
    return pos if r is None else r


def norm_angle(a):
    """Angle in degrees folded into the range (-180, 180]."""
    a = math.fmod(a, 360.0)
    if a <= -180:
        a += 360
    elif a > 180:
        a -= 360
    return 0.0 if abs(a) < 1e-9 else a


def snap_angle(origin, pt, step=15):
    d = pt - origin
    length = math.hypot(d.x(), d.y())
    ang = math.atan2(d.y(), d.x())
    st = math.radians(step)
    ang = round(ang / st) * st
    return QPointF(origin.x() + length * math.cos(ang), origin.y() + length * math.sin(ang))


class Tool:
    adds_paint = False        # draws new marks (on a text layer it starts a new layer instead)
    name = "Tool"
    icon = "tool"
    key = None
    hint = ""
    options = []
    history_icon = None

    def __init__(self, app):
        self.app = app

    @property
    def doc(self):
        return self.app.doc

    def opt(self, k):
        return self.app.opts[k]

    records_colour = True     # painting with this tool adds the colour to "Recent colours"

    def color(self, button):
        c = self.app.colors
        col = QColor(c.secondary if button == Qt.RightButton else c.primary)
        if self.records_colour:
            self._used_colour = col
        return col

    def other_color(self, button):
        c = self.app.colors
        return QColor(c.primary if button == Qt.RightButton else c.secondary)

    def commit(self, name=None, doc=None):
        (doc or self.doc).commit(name or self.name, self.history_icon or self.icon)
        used = getattr(self, "_used_colour", None)
        if used is not None:
            self._used_colour = None
            add = getattr(self.app.colors, "add_recent", None)
            if add is not None:
                add(used)

    # lifecycle
    def activate(self):
        pass

    def deactivate(self):
        self.finish()

    def finish(self):
        """Commit any pending, still-editable work."""

    def cancel(self):
        return False

    def is_editing(self):
        return False

    def options_changed(self):
        pass

    # input
    def press(self, pos, button, mods, canvas):
        pass

    def move(self, pos, buttons, mods, canvas):
        pass

    def release(self, pos, button, mods, canvas):
        pass

    def wants_keys(self):
        return False

    def key_press(self, e, canvas):
        return False

    def cursor(self, pos, canvas):
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        pass


# =================================================================== selection tools
class RectSelectTool(Tool):
    flyout = "marquee"        # shares one Tools button with Ellipse Select
    name = "Rectangle Select"
    snaps = True          # pointer snaps to guides
    icon = "rect_select"
    key = "S"
    hint = "Click and drag to draw a selection. Ctrl = add (union), Alt or right-drag = subtract, Shift = square."
    options = ["selection_mode"]
    shape = "rect"

    def __init__(self, app):
        super().__init__(app)
        self.dragging = False

    def make_path(self, a, b, mods):
        x0, y0 = round(a.x()), round(a.y())
        x1, y1 = round(b.x()), round(b.y())
        if mods & Qt.ShiftModifier:
            s = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + s * (1 if x1 >= x0 else -1)
            y1 = y0 + s * (1 if y1 >= y0 else -1)
        r = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
        path = QPainterPath()
        if self.shape == "rect":
            path.addRect(r)
        else:
            path.addEllipse(r)
        return path, r

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        self.base = self.doc.selection
        self.mode = selection_mode_for(button, mods, self.opt("selection_mode"))
        self.start = pos
        self.dragging = True
        self.moved = False

    def move(self, pos, buttons, mods, canvas):
        if not self.dragging:
            return
        path, r = self.make_path(self.start, pos, mods)
        if r.width() >= 1 and r.height() >= 1:
            self.moved = True
        if self.moved:
            self.doc.set_selection(combine(self.base, path, self.mode))
            self.app.selection_info_changed(r.size())

    def release(self, pos, button, mods, canvas):
        if not self.dragging:
            return
        self.dragging = False
        if not self.moved:
            if self.mode == "replace" and self.base is not None:
                self.doc.set_selection(None)
                self.commit("Deselect", )
            return
        self.commit()

    def history_icon_name(self):
        return self.icon


class EllipseSelectTool(RectSelectTool):
    flyout = "marquee"
    name = "Ellipse Select"
    icon = "ellipse_select"
    shape = "ellipse"
    hint = "Click and drag to draw an elliptical selection. Shift = circle."


class LassoSelectTool(Tool):
    name = "Lasso Select"
    icon = "lasso"
    key = "S"
    hint = "Click and drag to draw the outline of a selection area."
    options = ["selection_mode"]
    flyout = "lasso"          # shares one Tools button with the Magnetic Lasso

    def __init__(self, app):
        super().__init__(app)
        self.points = None

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        self.base = self.doc.selection
        self.mode = selection_mode_for(button, mods, self.opt("selection_mode"))
        self.points = [pos]

    def move(self, pos, buttons, mods, canvas):
        if self.points is None:
            return
        self.points.append(pos)
        if len(self.points) > 2:
            path = QPainterPath()
            path.addPolygon(QPolygonF(self.points))
            path.closeSubpath()
            self.doc.set_selection(combine(self.base, path, self.mode))
            self.app.selection_info_changed(path.boundingRect().size())

    def release(self, pos, button, mods, canvas):
        if self.points is None:
            return
        pts = self.points
        self.points = None
        if len(pts) < 3:
            if self.mode == "replace" and self.base is not None:
                self.doc.set_selection(None)
                self.commit("Deselect")
            return
        self.commit()


# ------------------------------------------------------------------ magnetic lasso
class EdgeMap:
    """Edge information for a picture, worked out a tile at a time as it is needed.

    The picture is smoothed a little first (so film grain and JPEG noise don't count as edges),
    then for each pixel we keep the edge strength (colour gradient; a full black/white step is
    about 1) and whether it sits right on the edge line (a zero crossing of the Laplacian),
    which pins the outline to the middle of soft edges instead of letting it wander."""
    TILE = 256
    MARGIN = 6
    _full_step = None

    @classmethod
    def full_step(cls):
        """Edge strength of a sharp black-to-white edge: Contrast 100 % means that much."""
        if cls._full_step is None:
            im = QImage(32, 32, QImage.Format_ARGB32_Premultiplied)
            im.fill(QColor(0, 0, 0))
            p = QPainter(im); p.fillRect(16, 0, 16, 32, QColor(255, 255, 255)); p.end()
            g, _ = EdgeMap(im)._tile(0, 0)
            cls._full_step = float(g.max())
        return cls._full_step

    def __init__(self, img):
        self.img = img
        self.w, self.h = img.width(), img.height()
        self.tiles = {}

    def _tile(self, tx, ty):
        t = self.tiles.get((tx, ty))
        if t is None:
            T, M = self.TILE, self.MARGIN
            x0, y0 = tx * T, ty * T
            r = QRect(x0 - M, y0 - M, T + 2 * M, T + 2 * M).intersected(QRect(0, 0, self.w, self.h))
            a = imaging.to_array(self.img.copy(r), premultiplied=True)     # edges against transparency too
            a = imaging.gaussian_blur(a, 3.0)                                # ~1 px sigma
            p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
            gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
            gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
            g = np.sqrt((gx * gx + gy * gy).sum(axis=-1)) / 2.0
            lum = a[..., :3].mean(axis=-1) + a[..., 3] * 0.5
            q = np.pad(lum, 1, mode="edge")
            lap = q[:-2, 1:-1] + q[2:, 1:-1] + q[1:-1, :-2] + q[1:-1, 2:] - 4 * lum
            zc = np.zeros(lum.shape, bool)
            for sl_a, sl_b in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                               ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
                la, lb = lap[sl_a], lap[sl_b]
                cross = (la * lb) < 0
                zc[sl_a] |= cross & (np.abs(la) <= np.abs(lb))
                zc[sl_b] |= cross & (np.abs(lb) < np.abs(la))
            ox, oy = x0 - r.x(), y0 - r.y()
            hh, ww = min(T, self.h - y0), min(T, self.w - x0)
            t = (g[oy:oy + hh, ox:ox + ww].astype(np.float32), zc[oy:oy + hh, ox:ox + ww])
            self.tiles[(tx, ty)] = t
        return t

    def region(self, x0, y0, x1, y1):
        """(strength, on_edge_line) arrays for x0 <= x < x1, y0 <= y < y1, clipped to the
        picture, plus the clipped origin."""
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w, x1), min(self.h, y1)
        g = np.zeros((max(0, y1 - y0), max(0, x1 - x0)), np.float32)
        z = np.zeros(g.shape, bool)
        if g.size == 0:
            return g, z, x0, y0
        T = self.TILE
        for ty in range(y0 // T, (y1 - 1) // T + 1):
            for tx in range(x0 // T, (x1 - 1) // T + 1):
                tg, tz = self._tile(tx, ty)
                ax0, ay0 = max(x0, tx * T), max(y0, ty * T)
                ax1, ay1 = min(x1, tx * T + tg.shape[1]), min(y1, ty * T + tg.shape[0])
                dst = (slice(ay0 - y0, ay1 - y0), slice(ax0 - x0, ax1 - x0))
                src = (slice(ay0 - ty * T, ay1 - ty * T), slice(ax0 - tx * T, ax1 - tx * T))
                g[dst] = tg[src]
                z[dst] = tz[src]
        return g, z, x0, y0


class LiveWire:
    """Cheapest edge-hugging path from an anchor to wherever the pointer is ("intelligent
    scissors").  Only pixels near where the pointer has actually been are allowed (the
    lasso's Width), and the search is grown only as far as it needs to be, so following the
    pointer stays quick."""
    RADIUS = 200          # search window round the anchor

    def __init__(self, edges, anchor, contrast):
        import heapq
        self.hq = heapq
        R = self.RADIUS
        ax, ay = anchor
        g, z, ox, oy = edges.region(ax - R, ay - R, ax + R + 1, ay + R + 1)
        self.ox, self.oy = ox, oy
        self.h, self.w = g.shape
        # edge strength judged against the contrast round here, so soft edges still count
        norm = max(float(np.percentile(g, 99.0)) if g.size else 0.0, 0.08)
        gn = np.clip(g / norm, 0.0, 1.0)
        # weaker than the Contrast setting: not an edge (gentle at the low end, so the default
        # still catches soft photo edges; 100 % leaves only sharp black/white edges)
        gn[g < (contrast ** 1.5) * EdgeMap.full_step() * 0.999] = 0.0
        self.gn = gn
        cost = 0.62 * (1.0 - gn) ** 2 + 0.26 * np.where(z & (gn > 0.15), 0.0, 1.0) + 0.04
        self.base_cost = cost.ravel().tolist()
        self.cost = list(self.base_cost)
        # how close each pixel is to where the pointer has been (0..1 of the Width); where there
        # is no edge, a small pull towards the pointer's track makes the outline follow the hand
        self.near = np.full(cost.shape, 2.0, np.float32)
        n = self.w * self.h
        self.allowed_buf = bytearray(n)
        self.allowed = np.frombuffer(self.allowed_buf, np.uint8).reshape(self.h, self.w)
        self.dist = [math.inf] * n
        self.prev = [-1] * n
        self.heap = []
        self.start = self.index(ax, ay)
        if self.start is not None:
            self.allowed_buf[self.start] = 1
            self.dist[self.start] = 0.0
            self.heap.append((0.0, self.start))

    def index(self, x, y):
        x -= self.ox
        y -= self.oy
        if 0 <= x < self.w and 0 <= y < self.h:
            return y * self.w + x
        return None

    def mark(self, x, y, r):
        """Allow pixels within r of (x, y); join new ones onto paths already found."""
        cx, cy = x - self.ox, y - self.oy
        x0, x1 = max(0, cx - r), min(self.w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(self.h, cy + r + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / float(max(1, r * r))
        disk = d2 <= 1.0
        nsub = self.near[y0:y1, x0:x1]
        closer = disk & (d2 < nsub)
        if closer.any():
            nsub[closer] = d2[closer]
            cost, base, w = self.cost, self.base_cost, self.w
            ys, xs = np.nonzero(closer)
            for j, dd in zip(((ys + y0) * w + xs + x0).tolist(), d2[closer].tolist()):
                cost[j] = base[j] + 0.3 * dd
        sub = self.allowed[y0:y1, x0:x1]
        new = disk & (sub == 0)
        if not new.any():
            return
        sub[new] = 1
        dist, prev, cost, w, h = self.dist, self.prev, self.cost, self.w, self.h
        push = self.hq.heappush
        for j in (np.nonzero(new)[0] + y0) * w + (np.nonzero(new)[1] + x0):
            j = int(j)
            jy, jx = divmod(j, w)
            best, bi = dist[j], -1
            for dx, dy, k in self.STEPS:
                nx, ny = jx + dx, jy + dy
                if 0 <= nx < w and 0 <= ny < h:
                    i = ny * w + nx
                    di = dist[i]
                    if di < best:
                        nd = di + k * (cost[i] + cost[j]) * 0.5
                        if nd < best:
                            best, bi = nd, i
            if bi >= 0:
                dist[j] = best
                prev[j] = bi
                push(self.heap, (best, j))

    STEPS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))

    def path_to(self, x, y):
        """Pixel path from the anchor to (x, y), or None if (x, y) can't be reached."""
        t = self.index(x, y)
        if t is None or not self.allowed_buf[t]:
            return None
        dist, prev, cost, allowed, w, h = self.dist, self.prev, self.cost, self.allowed_buf, self.w, self.h
        heap, pop, push = self.heap, self.hq.heappop, self.hq.heappush
        steps = self.STEPS
        while heap and heap[0][0] < dist[t]:
            d, i = pop(heap)
            if d > dist[i]:
                continue
            iy, ix = divmod(i, w)
            ci = cost[i]
            for dx, dy, k in steps:
                nx, ny = ix + dx, iy + dy
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if allowed[j]:
                        nd = d + k * (ci + cost[j]) * 0.5
                        if nd < dist[j]:
                            dist[j] = nd
                            prev[j] = i
                            push(heap, (nd, j))
        if dist[t] == math.inf:
            return None
        out = []
        i = t
        guard = w * h
        while i >= 0 and guard:
            iy, ix = divmod(i, w)
            out.append((ix + self.ox, iy + self.oy))
            i = prev[i]
            guard -= 1
        out.reverse()
        return out

    def strength(self, x, y):
        i = self.index(x, y)
        if i is None:
            return 0.0
        return float(self.gn.flat[i])


def smooth_points(pts, sigma=1.1, closed=False):
    """Soften a pixel-by-pixel outline (takes the staircase out) with a small Gaussian."""
    if len(pts) < 5:
        return [(x + 0.5, y + 0.5) for x, y in pts]
    a = np.asarray(pts, np.float32) + 0.5
    r = max(1, int(round(sigma * 2.5)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    if closed:
        p = np.concatenate([a[-r:], a, a[:r]])
    else:
        p = np.concatenate([np.repeat(a[:1], r, 0), a, np.repeat(a[-1:], r, 0)])
    out = np.stack([np.convolve(p[:, 0], k, "valid"), np.convolve(p[:, 1], k, "valid")], axis=1)
    if not closed:
        out[0], out[-1] = a[0], a[-1]
    return [tuple(v) for v in out.tolist()]


class MagneticLassoTool(Tool):
    """Selection outline that clings to edges as you trace round an object, like Photoshop's
    Magnetic Lasso.  Click to start, then move the pointer along the edge: the outline from the
    last anchor to the pointer follows the strongest edge nearby and updates as you move, and
    anchors are fixed behind you as you go.  Click to add an anchor yourself, Backspace removes
    the last one, double-click / Enter / clicking the start point closes it, Esc cancels."""
    name = "Magnetic Lasso"
    icon = "magnetic_lasso"
    key = "S"
    flyout = "lasso"
    hint = ("Click, then move along an edge: the outline snaps to it. Click adds an anchor, Backspace "
            "removes one, double-click or Enter closes, Esc cancels.")
    options = ["selection_mode", "lasso_width", "edge_contrast", "lasso_frequency"]
    CLOSE_PX = 8          # click this close (on screen) to the start point to close the outline

    def __init__(self, app):
        super().__init__(app)
        self.active = False
        self.fixed = []       # pixels fixed so far (start .. last anchor)
        self.anchors = []     # indices into fixed
        self.live = []        # last anchor .. pointer, still following the pointer
        self.edoc = None

    # ---------------------------------------------------------------- helpers
    def _px(self, pos):
        return (int(math.floor(pos.x())), int(math.floor(pos.y())))

    def _width(self):
        return max(1, int(self.opt("lasso_width")))

    def _contrast(self):
        return self.opt("edge_contrast") / 100.0

    def _new_wire(self, anchor):
        self.wire = LiveWire(self.edges, anchor, self._contrast())
        self.wire.mark(anchor[0], anchor[1], self._width())

    def _snap(self, p):
        """Strongest (allowed) edge within the detection width of p, or p itself."""
        wire = self.wire
        W = self._width()
        cx, cy = p[0] - wire.ox, p[1] - wire.oy
        x0, x1 = max(0, cx - W), min(wire.w, cx + W + 1)
        y0, y1 = max(0, cy - W), min(wire.h, cy + W + 1)
        if x0 >= x1 or y0 >= y1:
            return p
        g = wire.gn[y0:y1, x0:x1]
        ok = wire.allowed[y0:y1, x0:x1] > 0
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / float(W * W)
        score = np.where(ok & (d2 <= 1.0), g * (1.0 - 0.35 * d2), -1.0)
        i = int(np.argmax(score))
        if score.flat[i] < 0.12:
            return p
        y, x = divmod(i, score.shape[1])
        return (x + x0 + wire.ox, y + y0 + wire.oy)

    def _mark_line(self, a, b):
        """Allow a corridor along the straight line a -> b (keeps the search connected)."""
        W = self._width()
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / max(1.0, W / 2.0)))
        for i in range(n + 1):
            f = i / n
            self.wire.mark(int(round(a[0] + (b[0] - a[0]) * f)), int(round(a[1] + (b[1] - a[1]) * f)), W)

    def _update_live(self, target):
        snapped = self._snap(target)
        path = self.wire.path_to(*snapped)
        if path is None:
            path = self.wire.path_to(*target)
        if path is None:
            path = [self.fixed[-1], target]
        self.live = path

    def _fix_anchor(self, k):
        """Fix the live outline up to live[k] and start a new anchor there."""
        k = max(1, min(k, len(self.live) - 1))
        self.fixed.extend(self.live[1:k + 1])
        self.anchors.append(len(self.fixed) - 1)
        anchor = self.fixed[-1]
        self._new_wire(anchor)
        # carry the recent pointer trail over, so the new search has room to follow it
        W = self._width()
        for (x, y) in self.trail[-60:]:
            if abs(x - anchor[0]) < LiveWire.RADIUS - W and abs(y - anchor[1]) < LiveWire.RADIUS - W:
                self.wire.mark(x, y, W)
        if self.trail:
            self._mark_line(anchor, self.trail[-1])

    def _spacing(self):
        """Outline length between automatic anchors (higher Frequency = closer anchors)."""
        return max(12, 130 - int(self.opt("lasso_frequency")))

    def _follow(self, target):
        self.trail.append(target)
        self.wire.mark(target[0], target[1], self._width())
        self._update_live(target)
        spacing = self._spacing()
        if len(self.live) > spacing:
            # the start of the live outline has settled: fix it, keep the last part live
            self._fix_anchor(len(self.live) - 1 - int(spacing * 0.4))
            self._update_live(target)
        elif abs(target[0] - self.fixed[-1][0]) > LiveWire.RADIUS - 2 * self._width() or \
                abs(target[1] - self.fixed[-1][1]) > LiveWire.RADIUS - 2 * self._width():
            self._fix_anchor(len(self.live) - 1)
            self._update_live(target)

    def points(self, closed=False):
        return self.fixed + self.live[1:]

    # ---------------------------------------------------------------- input
    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if not self.active:
            d = self.doc
            self.edoc = d
            self.base = d.selection
            self.mode = selection_mode_for(button, mods, self.opt("selection_mode"))
            self.edges = EdgeMap(QImage(d.layer.image))
            p = self._px(pos)
            p = (min(max(p[0], 0), d.w - 1), min(max(p[1], 0), d.h - 1))
            self._new_wire(p)
            p = self._snap(p)
            self._new_wire(p)
            self.active = True
            self.fixed = [p]
            self.anchors = [0]
            self.live = [p]
            self.trail = [p]
            self.cursor_px = p
            canvas.viewport().update()
            return
        if self.edoc is not self.doc:
            self.cancel(); return
        if len(self.points()) > 2:
            s = self.fixed[0]
            v0 = canvas.to_view(QPointF(s[0] + 0.5, s[1] + 0.5))
            v = canvas.to_view(pos)
            if math.hypot(v.x() - v0.x(), v.y() - v0.y()) <= self.CLOSE_PX:
                self.close(); return
        # click: fix an anchor at the end of the live outline
        self.move(pos, Qt.NoButton, mods, canvas)
        if len(self.live) > 1:
            self._fix_anchor(len(self.live) - 1)
            self.live = [self.fixed[-1]]
        canvas.viewport().update()

    def move(self, pos, buttons, mods, canvas):
        if not self.active or self.edoc is not self.doc:
            return
        d = self.edoc
        target = self._px(pos)
        target = (min(max(target[0], 0), d.w - 1), min(max(target[1], 0), d.h - 1))
        last = self.cursor_px
        dist = math.hypot(target[0] - last[0], target[1] - last[1])
        step = max(1.5, self._width() / 2.0)
        if dist >= 1:
            n = max(1, int(math.ceil(dist / step)))
            for i in range(1, n + 1):
                f = i / n
                self._follow((int(round(last[0] + (target[0] - last[0]) * f)),
                              int(round(last[1] + (target[1] - last[1]) * f))))
            self.cursor_px = target
        canvas.viewport().update()

    def double_click(self, pos, button, canvas):
        if self.active:
            self.close()

    def key_press(self, e, canvas):
        if not self.active:
            return False
        k = e.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.close(); return True
        if k == Qt.Key_Escape:
            self.cancel(); return True
        if k in (Qt.Key_Backspace, Qt.Key_Delete):
            if len(self.anchors) <= 1:
                self.cancel()
            else:
                self.anchors.pop()
                del self.fixed[self.anchors[-1] + 1:]
                anchor = self.fixed[-1]
                self._new_wire(anchor)
                self.trail = [anchor]
                self._mark_line(anchor, self.cursor_px)
                self._update_live(self.cursor_px)
                canvas.viewport().update()
            return True
        return False

    def wants_keys(self):
        return self.active

    def is_editing(self):
        return self.active

    def finish(self):
        if self.active:
            self.close()

    def cancel(self):
        if not self.active:
            return False
        self.active = False
        self.fixed, self.anchors, self.live = [], [], []
        self.edges = self.wire = None
        self.app.canvas_update()
        return True

    def close(self):
        """Join the end back to the start (along the edge when it can) and make the selection."""
        if not self.active:
            return
        d = self.edoc
        pts = self.points()
        if len(pts) > 2:
            start, end = pts[0], pts[-1]
            if math.hypot(start[0] - end[0], start[1] - end[1]) > 1.5:
                # one more magnetic stretch from the end back to the start
                self._fix_anchor(len(self.live) - 1) if len(self.live) > 1 else None
                self._mark_line(self.fixed[-1], start)
                back = self.wire.path_to(*start)
                pts = self.fixed + (back[1:-1] if back else [])
        self.active = False
        self.fixed, self.anchors, self.live = [], [], []
        self.edges = self.wire = None
        if len(pts) < 3 or d is None:
            self.app.canvas_update()
            return
        outline = QPainterPath()
        outline.addPolygon(QPolygonF([QPointF(x, y) for x, y in smooth_points(pts, closed=True)]))
        outline.closeSubpath()
        d.set_selection(combine(self.base, outline, self.mode))
        self.app.selection_info_changed(outline.boundingRect().size())
        d.commit(self.name, self.history_icon or self.icon)
        self.app.canvas_update()

    def cursor(self, pos, canvas):
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if not self.active or self.edoc is not self.doc or not self.fixed:
            return
        pts = smooth_points(self.points())
        poly = [canvas.to_view(QPointF(x, y)) for x, y in pts]
        vp = QPainterPath()
        vp.addPolygon(QPolygonF(poly))
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 3)); p.drawPath(vp)
        p.setPen(QPen(QColor(0, 0, 0), 1)); p.drawPath(vp)
        if canvas.last_mouse is not None and poly:
            p.setPen(QPen(QColor(0, 0, 0, 90), 1, Qt.DotLine))
            p.drawLine(poly[-1], canvas.last_mouse)
        for k in self.anchors:
            if k < len(self.fixed):
                v = canvas.to_view(QPointF(self.fixed[k][0] + 0.5, self.fixed[k][1] + 0.5))
                p.setPen(QPen(QColor(0, 0, 0), 1)); p.setBrush(QColor(255, 255, 255))
                p.drawRect(QRectF(v.x() - 2.5, v.y() - 2.5, 5, 5))
        s = self.fixed[0]
        v0 = canvas.to_view(QPointF(s[0] + 0.5, s[1] + 0.5))
        if canvas.last_mouse is not None and len(self.points()) > 2 and \
                math.hypot(canvas.last_mouse.x() - v0.x(), canvas.last_mouse.y() - v0.y()) <= self.CLOSE_PX:
            p.setPen(QPen(QColor(0, 120, 215), 2)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(v0, 6, 6)               # "click here to close"


class MagicWandTool(Tool):
    """Click to select an area of similar colour.  Changing Tolerance (or Flood mode, or
    Sampling) straight after a click redoes that click live, so you can watch the selection
    grow and shrink, as in Photoshop."""
    name = "Magic Wand"
    icon = "magic_wand"
    key = "S"
    hint = ("Click to select an area of similar colour; then move Tolerance to adjust it live. "
            "Ctrl = add, Alt/right-click = subtract.")
    options = ["selection_mode", "flood_mode", "tolerance", "sampling"]
    LIVE_DELAY = 60      # ms after the last slider move before the selection is redone

    def __init__(self, app):
        super().__init__(app)
        self.last = None             # the last click, while it can still be adjusted
        self._timer = QTimer(app)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._redo)

    def _pixels(self, doc, sampling, layer_index):
        """Picture colours to compare (unpremultiplied RGBA bytes), kept from the click."""
        src = doc.composite() if sampling == "image" else doc.layers[layer_index].image
        return imaging.to_u8(src, premultiplied=False)

    def _mask(self, arr, x, y):
        similar = imaging.color_distance_mask(arr, arr[y, x], self.opt("tolerance") / 100.0)
        if self.opt("flood_mode") == "global":
            return similar
        return imaging.flood_region(similar, x, y)

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        doc = self.doc
        x, y = int(math.floor(pos.x())), int(math.floor(pos.y()))
        if not (0 <= x < doc.w and 0 <= y < doc.h):
            return
        self._timer.stop()
        mode = selection_mode_for(button, mods, self.opt("selection_mode"))
        base = doc.selection
        sampling = self.opt("sampling")
        arr = self._pixels(doc, sampling, doc.active)
        self.app.busy(True)
        try:
            path = imaging.mask_to_path(self._mask(arr, x, y))
        finally:
            self.app.busy(False)
        doc.set_selection(combine(base, path, mode))
        self.app.selection_info_changed(path.boundingRect().size())
        self.commit()
        self.last = {"doc": doc, "x": x, "y": y, "mode": mode, "base": base, "layer": doc.active,
                     "sampling": sampling, "arr": arr, "entry": doc.history[doc.hindex]}

    def _still_adjustable(self):
        L = self.last
        if L is None:
            return False
        d = L["doc"]
        # only while that click is still the newest thing that happened to the picture
        return d is self.doc and 0 <= d.hindex < len(d.history) and d.history[d.hindex] is L["entry"]

    def options_changed(self):
        if self._still_adjustable():
            self._timer.start(self.LIVE_DELAY)
        else:
            self.last = None

    def _redo(self):
        if not self._still_adjustable():
            self.last = None
            return
        L = self.last
        d = L["doc"]
        if self.opt("sampling") != L["sampling"]:
            L["sampling"] = self.opt("sampling")
            L["arr"] = self._pixels(d, L["sampling"], L["layer"])
        path = imaging.mask_to_path(self._mask(L["arr"], L["x"], L["y"]))
        d.set_selection(combine(L["base"], path, L["mode"]))
        self.app.selection_info_changed(path.boundingRect().size())
        d.replace_last(self.name, self.history_icon or self.icon)   # still one history step
        L["entry"] = d.history[d.hindex]
        self.app.canvas_update()

    def deactivate(self):
        self._timer.stop()
        self.last = None          # let go of the kept picture data
        super().deactivate()

    def cursor(self, pos, canvas):
        return Qt.PointingHandCursor


# =================================================================== move tools
class XForm:
    """Centre / rotation / scale description of a transform applied to a rect."""

    def __init__(self, rect):
        self.r0 = QRectF(rect)
        c = rect.center()
        self.cx, self.cy = c.x(), c.y()
        self.angle = 0.0
        self.sx = self.sy = 1.0

    def copy(self):
        x = XForm(self.r0)
        x.cx, x.cy, x.angle, x.sx, x.sy = self.cx, self.cy, self.angle, self.sx, self.sy
        return x

    def identity(self):
        c = self.r0.center()
        return (abs(self.cx - c.x()) < 1e-9 and abs(self.cy - c.y()) < 1e-9 and self.angle == 0
                and self.sx == 1 and self.sy == 1)

    def transform(self):
        c0 = self.r0.center()
        return (QTransform().translate(-c0.x(), -c0.y()) * QTransform().scale(self.sx, self.sy)
                * QTransform().rotate(self.angle) * QTransform().translate(self.cx, self.cy))

    def is_integer_translation(self):
        if self.angle % 360 or self.sx != 1 or self.sy != 1:
            return False
        c0 = self.r0.center()
        dx, dy = self.cx - c0.x(), self.cy - c0.y()
        return abs(dx - round(dx)) < 1e-6 and abs(dy - round(dy)) < 1e-6

    # handle i: 0 tl,1 t,2 tr,3 r,4 br,5 b,6 bl,7 l
    DIRS = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]

    def rotate_handles(self, canvas):
        """[(round rotate-handle view point, corner view point)] for the four corners.
        Each round handle sits just outside its corner, along the line from the centre."""
        pts = self.handle_points()
        c = canvas.to_view(QPointF(self.cx, self.cy))
        out = []
        for i in (0, 2, 4, 6):
            k = canvas.to_view(pts[i])
            dx, dy = k.x() - c.x(), k.y() - c.y()
            n = math.hypot(dx, dy)
            if n < 1e-6:
                dx, dy, n = self.DIRS[i][0], self.DIRS[i][1], math.hypot(*self.DIRS[i])
            out.append((QPointF(k.x() + dx / n * ROT_HANDLE_GAP, k.y() + dy / n * ROT_HANDLE_GAP), k))
        return out

    def is_crisp(self):
        """True when the transform only moves pixels by whole steps and quarter turns,
        so it can be drawn without smoothing (no blur)."""
        if abs(self.sx) != 1 or abs(self.sy) != 1:
            return False
        q = self.angle / 90.0
        if abs(q - round(q)) > 1e-9:
            return False
        tl = self.transform().mapRect(self.r0).topLeft()
        return abs(tl.x() - round(tl.x())) < 1e-6 and abs(tl.y() - round(tl.y())) < 1e-6

    def snap_to_pixels(self):
        """Nudge the centre so a quarter-turned box lands exactly on the pixel grid."""
        tl = self.transform().mapRect(self.r0).topLeft()
        self.cx -= tl.x() - round(tl.x())
        self.cy -= tl.y() - round(tl.y())

    def handle_points(self):
        T = self.transform()
        r = self.r0
        c = r.center()
        pts = []
        for dx, dy in self.DIRS:
            pts.append(T.map(QPointF(c.x() + dx * r.width() / 2, c.y() + dy * r.height() / 2)))
        return pts


class MoveTool(Tool):
    name = "Move Selected Pixels"
    snaps = True
    moves_guides = True   # guides can be dragged while this tool is active
    icon = "move_pixels"
    key = "M"
    hint = ("Drag to move. Square handles resize. Drag just outside a corner (round handles) "
            "or right-drag to rotate; Shift snaps to 15°.")
    options = ["rotate"]
    pixels = True

    def __init__(self, app):
        super().__init__(app)
        self.token = None
        self.drag = None

    def _valid(self):
        d = self.doc
        return (self.token is not None and self.token == (id(d), d.hindex, d.active, len(d.history)))

    def reset(self):
        self.token = None

    def refresh_token(self):
        d = self.doc
        self.token = (id(d), d.hindex, d.active, len(d.history))

    def deactivate(self):
        self.token = None

    def lift(self):
        d = self.doc
        if self.pixels:
            path0 = d.selection_path_or_all()
        else:
            if d.selection is None:
                return False
            path0 = QPainterPath(d.selection)
        br = path0.boundingRect()
        if br.width() <= 0 or br.height() <= 0:
            return False
        self.had_selection = d.selection is not None
        self.path0 = path0
        self.xf = XForm(br)
        self.first = True
        if self.pixels:
            bri = br.toAlignedRect().intersected(d.rect())
            if bri.isEmpty():
                return False
            self.bri = bri
            fl = d.layer.image.copy(bri)
            imaging.keep_inside(fl, QPainterPath(path0).translated(-bri.x(), -bri.y()))
            self.floating = fl
            # a plain pixel-aligned rectangle has no soft edge; anything else (circle, lasso,
            # rotated outline) does, and needs the edge-aware blend when it is moved
            self.soft_edge = not (path0.elementCount() <= 5 and br == QRectF(br.toAlignedRect())
                                  and path0 == self._rect_path(br))
            base = d.layer.image.copy()
            if self.soft_edge:
                # cut the hole with the same soft edge as the lifted pixels so they fit together
                hole = base.copy(bri)
                imaging.cut_out(hole, QPainterPath(path0).translated(-bri.x(), -bri.y()))
                p = QPainter(base)
                p.setCompositionMode(QPainter.CompositionMode_Source)
                p.drawImage(bri.topLeft(), hole)
                p.end()
            else:
                p = QPainter(base)
                p.setCompositionMode(QPainter.CompositionMode_Clear)
                p.fillPath(path0, QColor(0, 0, 0, 255))
                p.end()
            self.base = base
        return True

    @staticmethod
    def _rect_path(r):
        q = QPainterPath()
        q.addRect(r)
        return q

    def lift_from_image(self, img, pos):
        """Used by paste: make img the floating pixels at pos on the active layer."""
        d = self.doc
        r = QRectF(pos.x(), pos.y(), img.width(), img.height())
        self.path0 = QPainterPath()
        self.path0.addRect(r)
        self.had_selection = True
        self.xf = XForm(r)
        self.bri = r.toAlignedRect()
        self.floating = QImage(img).convertToFormat(imaging.FMT)
        self.soft_edge = False
        self.base = d.layer.image.copy()
        self.first = False
        d.set_selection(QPainterPath(self.path0))
        self.apply()
        self.token = (id(d), d.hindex, d.active, len(d.history))

    def apply(self):
        d = self.doc
        T = self.xf.transform()
        if self.had_selection:
            d.selection = T.map(self.path0)
            d.selectionChanged.emit()
        if self.pixels:
            img = self.base.copy()
            if self.soft_edge:
                # draw the transformed pixels on their own, then blend so the soft edge of the
                # hole and the soft edge of the moved pixels close up without a light ring
                R = T.mapRect(QRectF(self.bri)).toAlignedRect().adjusted(-1, -1, 1, 1).intersected(d.rect())
                if not R.isEmpty():
                    tmp = new_image(R.width(), R.height())
                    p = QPainter(tmp)
                    if not self.xf.is_crisp():
                        p.setRenderHint(QPainter.SmoothPixmapTransform)
                        p.setRenderHint(QPainter.Antialiasing)
                    p.setTransform(T * QTransform.fromTranslate(-R.x(), -R.y()))
                    p.drawImage(QPointF(self.bri.topLeft()), self.floating)
                    p.end()
                    imaging.disjoint_over(img, tmp, R.x(), R.y())
            else:
                p = QPainter(img)
                if not self.xf.is_crisp():
                    p.setRenderHint(QPainter.SmoothPixmapTransform)
                    p.setRenderHint(QPainter.Antialiasing)
                p.setTransform(T)
                p.drawImage(QPointF(self.bri.topLeft()), self.floating)
                p.end()
            d.layer.image = img
            d.invalidate()
        self.app.selection_info_changed(T.mapRect(self.path0.boundingRect()).size())
        self._show_angle(self.xf.angle)

    def _show_angle(self, ang):
        w = getattr(self.app, "opt_widgets", {}).get("rotate")
        if w is not None:
            w._set(norm_angle(ang))

    def current_angle(self):
        return norm_angle(self.xf.angle) if self._valid() else 0.0

    def set_angle(self, ang, name="Rotate Selection"):
        """Rotate the selected pixels (or outline) to an exact angle, e.g. from the options bar.
        Repeated changes from the bar are merged into one history entry."""
        d = self.doc
        if d is None:
            return
        if not self._valid():
            if not self.lift():
                self.token = None
                self._show_angle(0)
                return
            self.angle_entry = None
        xf = self.xf
        xf.angle = norm_angle(ang)
        q = xf.angle / 90.0
        if abs(q - round(q)) < 1e-9 and abs(xf.sx) == 1 and abs(xf.sy) == 1:
            xf.snap_to_pixels()
        self.apply()
        self.first = False
        merge = getattr(self, "angle_entry", None)
        if merge is not None and merge == (id(d), d.hindex, len(d.history)) and d.history[d.hindex].name == name:
            d.replace_last(name, self.history_icon or self.icon)
        else:
            self.commit(name)
        self.angle_entry = (id(d), d.hindex, len(d.history))
        self.refresh_token()

    def rotate_by(self, delta):
        base = self.xf.angle if self._valid() else 0.0
        self.angle_entry = None   # each button press is its own step in History
        self.set_angle(base + delta, "Rotate Selection 90° Clockwise" if delta > 0
                       else "Rotate Selection 90° Counter-clockwise")

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if not self._valid():
            if not self.lift():
                self.token = None
                return
            self.refresh_token()   # the lifted pixels are live from now on (handles follow the drag)
        self.start = pos
        self.start_raw = _raw(canvas, pos)
        self.xf0 = self.xf.copy()
        self.angle_entry = None
        self.drag = "move"
        if button == Qt.RightButton:
            self.drag = "rotate"
        elif self._on_rotate_handle(pos, canvas):
            self.drag = "rotate"
        else:
            for i, hp in enumerate(self.xf.handle_points()):
                if near(canvas, pos, hp):
                    self.drag = ("handle", i)
                    break

    def move(self, pos, buttons, mods, canvas):
        if self.drag is None:
            return
        xf, x0 = self.xf, self.xf0
        if self.drag == "move":
            # move by the raw pointer, then let the box's edges / centre snap to guides
            raw = _raw(canvas, pos)
            st = getattr(self, "start_raw", self.start)
            dx, dy = raw.x() - st.x(), raw.y() - st.y()
            if self.pixels and x0.is_integer_translation():
                dx, dy = round(dx), round(dy)
            xf.cx, xf.cy = x0.cx + dx, x0.cy + dy
            if hasattr(canvas, "snap_delta"):
                b = xf.transform().mapRect(self.path0.boundingRect())
                xf.cx += canvas.snap_delta("v", (b.left(), b.center().x(), b.right()))
                xf.cy += canvas.snap_delta("h", (b.top(), b.center().y(), b.bottom()))
        elif self.drag == "rotate":
            raw = _raw(canvas, pos)
            st = getattr(self, "start_raw", self.start)
            a0 = math.atan2(st.y() - x0.cy, st.x() - x0.cx)
            a1 = math.atan2(raw.y() - x0.cy, raw.x() - x0.cx)
            ang = x0.angle + math.degrees(a1 - a0)
            if mods & Qt.ShiftModifier:
                ang = round(ang / 15) * 15
            xf.angle = ang
            xf.cx, xf.cy = x0.cx, x0.cy
            q = ang / 90.0
            if abs(q - round(q)) < 1e-9 and abs(xf.sx) == 1 and abs(xf.sy) == 1:
                xf.snap_to_pixels()   # quarter turns stay pin-sharp
        else:
            i = self.drag[1]
            hx, hy = XForm.DIRS[i]
            anchor = x0.handle_points()[(i + 4) % 8]
            a = math.radians(x0.angle)
            ca, sa = math.cos(a), math.sin(a)
            vx, vy = pos.x() - anchor.x(), pos.y() - anchor.y()
            lx = vx * ca + vy * sa
            ly = -vx * sa + vy * ca
            w0, h0 = x0.r0.width(), x0.r0.height()
            nw = max(1.0, lx * hx) if hx else w0 * x0.sx
            nh = max(1.0, ly * hy) if hy else h0 * x0.sy
            if (mods & Qt.ShiftModifier) and hx and hy:
                s = max(nw / (w0 * x0.sx), nh / (h0 * x0.sy))
                nw, nh = w0 * x0.sx * s, h0 * x0.sy * s
            xf.sx, xf.sy = nw / w0, nh / h0
            ox, oy = hx * nw / 2, hy * nh / 2
            xf.cx = anchor.x() + ox * ca - oy * sa
            xf.cy = anchor.y() + ox * sa + oy * ca
            xf.angle = x0.angle
        self.apply()

    def release(self, pos, button, mods, canvas):
        if self.drag is None:
            return
        self.drag = None
        canvas.viewport().update()   # redraw the handles un-highlighted
        d = self.doc
        if self.xf.identity() and self.first:
            d.revert()
            self.token = None
            return
        self.first = False
        self.commit("Move Selected Pixels" if self.pixels else "Move Selection",
                    )
        self.token = (id(d), d.hindex, d.active, len(d.history))

    def key_press(self, e, canvas):
        k = e.key()
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if k in moves and not (e.modifiers() & Qt.ControlModifier):
            if not self._valid() and not self.lift():
                return True
            step = 10 if e.modifiers() & Qt.ShiftModifier else 1
            dx, dy = moves[k]
            self.xf.cx += dx * step
            self.xf.cy += dy * step
            self.apply()
            self.first = False
            self.commit("Move Selected Pixels" if self.pixels else "Move Selection")
            d = self.doc
            self.token = (id(d), d.hindex, d.active, len(d.history))
            return True
        return False

    def _box(self):
        """The transform box to show handles for (the live one, or the selection's bounds)."""
        d = self.doc
        if self._valid():
            return self.xf
        if self.pixels or d.selection is not None:
            r = d.selection.boundingRect() if d.selection is not None else QRectF(d.rect())
            if r.width() > 0 and r.height() > 0:
                return XForm(r)
        return None

    def _on_rotate_handle(self, pos, canvas):
        """True over a round corner handle, or anywhere just outside a corner of the box
        (but not on a square resize handle and not inside the box)."""
        box = self._box()
        if box is None:
            return False
        if any(near(canvas, pos, hp) for hp in box.handle_points()):
            return False   # square handles resize
        v = canvas.to_view(pos)
        for h, k in box.rotate_handles(canvas):
            if abs(v.x() - h.x()) <= HANDLE + 3 and abs(v.y() - h.y()) <= HANDLE + 3:
                return True
        corners = QPolygonF([canvas.to_view(q) for q in box.handle_points()[0::2]])
        if corners.containsPoint(v, Qt.OddEvenFill):
            return False
        return any(math.hypot(v.x() - k.x(), v.y() - k.y()) <= ROT_ZONE for _, k in box.rotate_handles(canvas))

    def _rot_direction(self, pos, canvas):
        """Screen angle from the box centre out to the pointer (for the rotate cursor)."""
        box = self.xf if self._valid() else self._box()
        if box is None or pos is None:
            return -90.0
        c = canvas.to_view(QPointF(box.cx, box.cy))
        v = canvas.to_view(pos)
        if abs(v.x() - c.x()) < 1e-6 and abs(v.y() - c.y()) < 1e-6:
            return -90.0
        return math.degrees(math.atan2(v.y() - c.y(), v.x() - c.x()))

    def cursor(self, pos, canvas):
        if self.drag == "rotate":
            return rotate_cursor(self._rot_direction(pos, canvas))
        if pos is not None and self._on_rotate_handle(pos, canvas):
            return rotate_cursor(self._rot_direction(pos, canvas))
        if pos is not None and self._valid():
            for i, hp in enumerate(self.xf.handle_points()):
                if near(canvas, pos, hp):
                    return [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor][i % 4]
        return Qt.SizeAllCursor

    def draw_overlay(self, p, canvas):
        box = self._box()
        if not self._valid():
            self._show_angle(0)
        if box is None:
            return
        vpts = [canvas.to_view(q) for q in box.handle_points()]
        p.setPen(QPen(QColor(80, 160, 255, 160), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF([vpts[0], vpts[2], vpts[4], vpts[6]]))
        for v in vpts:
            draw_handle(p, v)
        for rh, _ in box.rotate_handles(canvas):
            draw_handle(p, rh, round_=True, active=self.drag == "rotate")


class MoveSelectionTool(MoveTool):
    name = "Move Selection"
    icon = "move_selection"
    hint = ("Drag to move the outline. Square handles resize. Drag just outside a corner (round handles) "
            "or right-drag to rotate; Shift snaps to 15°.")
    pixels = False


# =================================================================== view tools
class ZoomTool(Tool):
    name = "Zoom"
    icon = "zoom"
    key = "Z"
    hint = "Click to zoom in, right-click to zoom out, drag a rectangle to zoom to it."

    def __init__(self, app):
        super().__init__(app)
        self.start = None
        self.cur = None

    def press(self, pos, button, mods, canvas):
        self.start, self.cur, self.button = pos, pos, button

    def move(self, pos, buttons, mods, canvas):
        if self.start is not None:
            self.cur = pos
            canvas.viewport().update()

    def release(self, pos, button, mods, canvas):
        if self.start is None:
            return
        a, b = canvas.to_view(self.start), canvas.to_view(pos)
        r = QRectF(self.start, pos).normalized()
        self.start = None
        if abs(a.x() - b.x()) > 4 and abs(a.y() - b.y()) > 4 and button == Qt.LeftButton:
            canvas.zoom_to_rect(r)
        elif button == Qt.LeftButton:
            canvas.zoom_in(b)
        elif button == Qt.RightButton:
            canvas.zoom_out(b)
        canvas.viewport().update()

    def draw_overlay(self, p, canvas):
        if self.start is not None:
            r = QRectF(canvas.to_view(self.start), canvas.to_view(self.cur)).normalized()
            p.setPen(QPen(QColor(40, 120, 215), 1, Qt.DashLine))
            p.setBrush(QColor(40, 120, 215, 40))
            p.drawRect(r)

    def cursor(self, pos, canvas):
        from . import icons
        return QCursor(icons.pixmap("zoom", 24), 9, 9)


class PanTool(Tool):
    name = "Pan"
    moves_guides = True
    icon = "pan"
    key = "H"
    hint = "Drag to move the view. Tip: hold Space or use the middle mouse button with any tool."

    def __init__(self, app):
        super().__init__(app)
        self.last = None

    def press(self, pos, button, mods, canvas):
        self.last = QCursor.pos()

    def move(self, pos, buttons, mods, canvas):
        if self.last is not None and buttons:
            cur = QCursor.pos()
            canvas.scroll_by(cur.x() - self.last.x(), cur.y() - self.last.y())
            self.last = cur

    def release(self, pos, button, mods, canvas):
        self.last = None

    def cursor(self, pos, canvas):
        return Qt.ClosedHandCursor if self.last is not None else Qt.OpenHandCursor


# =================================================================== fill tools
class BucketTool(Tool):
    adds_paint = True
    name = "Paint Bucket"
    icon = "bucket"
    key = "F"
    hint = "Click to fill an area with the primary colour (right-click: secondary)."
    options = ["flood_mode", "tolerance", "sampling"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        doc = self.doc
        x, y = int(math.floor(pos.x())), int(math.floor(pos.y()))
        if not (0 <= x < doc.w and 0 <= y < doc.h):
            return
        if doc.selection is not None and not doc.selection.contains(QPointF(x + 0.5, y + 0.5)):
            return
        src = doc.composite() if self.opt("sampling") == "image" else doc.layer.image
        arr = imaging.to_u8(src, premultiplied=False)
        similar = imaging.color_distance_mask(arr, arr[y, x], self.opt("tolerance") / 100.0)
        if self.opt("flood_mode") == "global":
            mask = similar
        else:
            mask = imaging.flood_region(similar, x, y)
        cov = mask.astype(np.float32)
        if doc.selection is not None:
            cov *= imaging.path_mask(doc.selection, doc.w, doc.h)
        c = self.color(button)
        ca = c.alphaF()
        cp = np.array([c.redF() * ca, c.greenF() * ca, c.blueF() * ca, ca], np.float32)
        dst = imaging.to_array(doc.layer.image, premultiplied=True)
        painted = cp + dst * (1 - ca)
        k = cov[..., None]
        out = dst * (1 - k) + painted * k
        doc.layer.image = imaging.from_array(out)
        doc.invalidate()
        self.commit()


class GradientTool(Tool):
    adds_paint = True
    name = "Gradient"
    snaps = True
    icon = "gradient"
    key = "G"
    hint = "Drag to draw a gradient from primary to secondary colour. Drag the handles to adjust; press Enter to finish."
    options = ["gradient_type", "gradient_mode"]

    def __init__(self, app):
        super().__init__(app)
        self.editing = False
        self.drag = None

    def is_editing(self):
        return self.editing

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if self.editing and self.edoc is self.doc:
            if near(canvas, pos, self.p0):
                self.drag = 0; return
            if near(canvas, pos, self.p1):
                self.drag = 1; return
            self.finish()
        self.edoc = self.doc
        self.elayer = self.doc.active
        self.base = QImage(self.doc.layer.image)
        self.c0, self.c1 = self.color(button), self.other_color(button)
        self.p0 = QPointF(pos)
        self.p1 = QPointF(pos)
        self.editing = True
        self.drag = 1

    def move(self, pos, buttons, mods, canvas):
        if self.drag is None:
            return
        if mods & Qt.ShiftModifier:
            pos = snap_angle(self.p1 if self.drag == 0 else self.p0, pos)
        if self.drag == 0:
            self.p0 = QPointF(pos)
        else:
            self.p1 = QPointF(pos)
        self.render()

    def release(self, pos, button, mods, canvas):
        if self.drag is None:
            return
        self.drag = None
        if QLineF(self.p0, self.p1).length() < 0.5:
            self.cancel()

    def options_changed(self):
        if self.editing:
            self.render()

    def render(self):
        d = self.edoc
        L = d.layers[self.elayer]
        img = QImage(self.base).copy()
        kind = self.opt("gradient_type")
        alpha_mode = self.opt("gradient_mode") == "transparency"
        p0, p1 = self.p0, self.p1
        c0, c1 = QColor(self.c0), QColor(self.c1)
        if alpha_mode:
            c0, c1 = QColor(0, 0, 0, 255), QColor(0, 0, 0, 0)
        brush = None
        gimg = None
        if kind == "linear":
            g = QLinearGradient(p0, p1)
            g.setColorAt(0, c0); g.setColorAt(1, c1)
            brush = QBrush(g)
        elif kind == "radial":
            g = QRadialGradient(p0, max(0.5, QLineF(p0, p1).length()))
            g.setColorAt(0, c0); g.setColorAt(1, c1)
            brush = QBrush(g)
        elif kind == "conical":
            ang = -math.degrees(math.atan2(p1.y() - p0.y(), p1.x() - p0.x()))
            g = QConicalGradient(p0, ang)
            g.setColorAt(0, c0); g.setColorAt(1, c1)
            brush = QBrush(g)
        else:
            gimg = self.numpy_gradient(kind, d.w, d.h, p0, p1, c0, c1)
        p = QPainter(img)
        d.clip(p)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn if alpha_mode
                             else QPainter.CompositionMode_SourceOver)
        if brush is not None:
            p.fillRect(QRectF(d.rect()), brush)
        else:
            p.drawImage(0, 0, gimg)
        p.end()
        L.image = img
        d.invalidate()

    @staticmethod
    def numpy_gradient(kind, w, h, p0, p1, c0, c1):
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        xs += 0.5; ys += 0.5
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        rx, ry = xs - p0.x(), ys - p0.y()
        along = (rx * ux + ry * uy) / L
        if kind == "reflected":
            t = np.abs(along)
        else:  # diamond
            across = (-rx * uy + ry * ux) / L
            t = np.abs(along) + np.abs(across)
        t = np.clip(t, 0, 1)[..., None]
        a = np.array([c0.redF(), c0.greenF(), c0.blueF(), c0.alphaF()], np.float32)
        b = np.array([c1.redF(), c1.greenF(), c1.blueF(), c1.alphaF()], np.float32)
        a = np.concatenate([a[:3] * a[3], a[3:]])
        b = np.concatenate([b[:3] * b[3], b[3:]])
        return imaging.from_array(a * (1 - t) + b * t)

    def finish(self):
        if self.editing:
            self.editing = False
            self.drag = None
            self.commit(doc=self.edoc)
            self.app.canvas_update()

    def cancel(self):
        if self.editing:
            self.editing = False
            self.drag = None
            self.edoc.revert()
            return True
        return False

    def key_press(self, e, canvas):
        if self.editing and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(); return True
        if self.editing and e.key() == Qt.Key_Escape:
            self.cancel(); return True
        return False

    def cursor(self, pos, canvas):
        if self.editing and pos is not None and (near(canvas, pos, self.p0) or near(canvas, pos, self.p1)):
            return Qt.SizeAllCursor
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if self.editing and self.edoc is self.doc:
            a, b = canvas.to_view(self.p0), canvas.to_view(self.p1)
            p.setPen(QPen(QColor(255, 255, 255), 3)); p.drawLine(a, b)
            p.setPen(QPen(QColor(0, 0, 0), 1)); p.drawLine(a, b)
            draw_handle(p, a, True, self.drag == 0)
            draw_handle(p, b, True, self.drag == 1)


# =================================================================== brush-like tools
# smoothstep fall-off from the hard core to the brush edge
_SOFT_STOPS = [(0.0, 1.0), (0.25, 0.844), (0.5, 0.5), (0.75, 0.156), (1.0, 0.0)]


def stroke_line(p, a, b, w, hardness, color, antialias):
    """Paint one stroke segment from a to b, w pixels wide, onto painter p.

    Hardness 100 % is a plain round pen.  Below that the stroke is a row of round dabs
    whose edges fade out; they are combined with Lighten so overlapping dabs don't
    build up, which gives an even soft edge along the whole stroke."""
    if hardness >= 100:
        p.setRenderHint(QPainter.Antialiasing, antialias)
        p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if a == b:
            p.drawPoint(a)
        else:
            p.drawLine(a, b)
        return
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setCompositionMode(QPainter.CompositionMode_Lighten)
    r = max(0.5, w / 2)
    core = max(0.0, min(0.999, hardness / 100.0))
    clear = QColor(color); clear.setAlpha(0)
    dist = math.hypot(b.x() - a.x(), b.y() - a.y())
    n = max(1, int(math.ceil(dist / max(0.5, w * 0.08))))
    for i in range(n + 1):
        t = i / n
        c = QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
        g = QRadialGradient(c, r)
        for f, k in _SOFT_STOPS:
            col = QColor(color)
            col.setAlphaF(color.alphaF() * k)
            g.setColorAt(core + (1.0 - core) * f, col if k > 0 else clear)
        if core > 0:
            g.setColorAt(0.0, color)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r, r)
        if dist == 0:
            break
class StrokeTool(Tool):
    options = ["brush_width", "hardness", "antialias"]

    def __init__(self, app):
        super().__init__(app)
        self.drawing = False

    def width(self):
        return max(1, self.opt("brush_width"))

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        d = self.doc
        self.base = QImage(d.layer.image)
        self.buf = new_image(d.w, d.h)
        self.paint_color = self.color(button)
        self.last = pos
        self.drawing = True
        self.stroke_doc = d
        self.segment(pos, pos)

    def move(self, pos, buttons, mods, canvas):
        if self.drawing:
            self.segment(self.last, pos)
            self.last = pos
        canvas.update_ring(self.width() * canvas.zoom / 2)

    def release(self, pos, button, mods, canvas):
        if self.drawing:
            self.drawing = False
            self.buf = None
            self.commit(doc=self.stroke_doc)

    def segment(self, a, b):
        w = self.width()
        p = QPainter(self.buf)
        c = QColor(self.paint_color); c.setAlpha(255)
        stroke_line(p, a, b, w, self.opt("hardness"), c, self.opt("antialias"))
        p.end()
        r = QRectF(a, b).normalized().adjusted(-w - 2, -w - 2, w + 2, w + 2).toAlignedRect()
        r = r.intersected(self.stroke_doc.rect())
        if r.isEmpty():
            return
        self.render(r)
        self.stroke_doc.invalidate(r)

    def render(self, r):
        L = self.stroke_doc.layer
        p = QPainter(L.image)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(r.topLeft(), self.base, r)
        self.stroke_doc.clip(p)
        self.composite_buffer(p, r)
        p.end()

    def composite_buffer(self, p, r):
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(self.paint_color.alphaF())
        p.drawImage(r.topLeft(), self.buf, r)

    def draw_overlay(self, p, canvas):
        if canvas.last_mouse is None:
            return
        rad = self.width() * canvas.zoom / 2
        if rad < 2:
            return
        c = canvas.last_mouse
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1)); p.drawEllipse(c, rad + 1, rad + 1)
        p.setPen(QPen(QColor(0, 0, 0, 200), 1)); p.drawEllipse(c, rad, rad)

    def key_press(self, e, canvas):
        if e.key() in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            w = self.opt("brush_width")
            step = max(1, int(w * 0.15))
            self.app.set_option("brush_width", max(1, min(2000, w + (step if e.key() == Qt.Key_BracketRight else -step))))
            canvas.viewport().update()
            return True
        return False


class BrushTool(StrokeTool):
    adds_paint = True
    flyout = "brush"          # shares one Tools button with the Pencil
    name = "Paintbrush"
    icon = "brush"
    key = "B"
    hint = "Left-click to draw with the primary colour, right-click for the secondary colour. [ ] change size."


class EraserTool(StrokeTool):
    records_colour = False
    name = "Eraser"
    icon = "eraser"
    key = "E"
    flyout = "eraser"         # shares one Tools button with the Magic Eraser
    hint = "Click and drag to erase to transparency. [ ] change size."

    def composite_buffer(self, p, r):
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        p.drawImage(r.topLeft(), self.buf, r)


class MagicEraserTool(Tool):
    """Click a colour to erase it to transparency, with the pixels around it that are similar,
    like Photoshop's Magic Eraser (e.g. to remove a plain background in one click)."""
    records_colour = False
    name = "Magic Eraser"
    icon = "magic_eraser"
    key = "E"
    flyout = "eraser"
    hint = ("Click a colour to erase it and similar colours around it to transparency. "
            "Flood mode Global erases that colour everywhere.")
    options = ["flood_mode", "erase_tolerance", "sampling", "antialias"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        doc = self.doc
        x, y = int(math.floor(pos.x())), int(math.floor(pos.y()))
        if not (0 <= x < doc.w and 0 <= y < doc.h):
            return
        if doc.selection is not None and not doc.selection.contains(QPointF(x + 0.5, y + 0.5)):
            return
        self.app.busy(True)
        try:
            src = doc.composite() if self.opt("sampling") == "image" else doc.layer.image
            arr = imaging.to_u8(src, premultiplied=False)
            similar = imaging.color_distance_mask(arr, arr[y, x], self.opt("erase_tolerance") / 100.0)
            region = similar if self.opt("flood_mode") == "global" else imaging.flood_region(similar, x, y)
            if not region.any():
                return
            m = region.astype(np.float32)
            if self.opt("antialias"):
                # soften the cut: pixels just outside the region are partly erased
                m = np.maximum(m, imaging.box_blur(m, 1))
            # erase by drawing the mask with Destination Out (Qt does the per-pixel work)
            m8 = np.ascontiguousarray(np.rint(m * 255.0).astype(np.uint8))
            h, w = m8.shape
            mask = QImage(m8.data, w, h, w, QImage.Format_Alpha8).copy()
            L = doc.layer
            p = QPainter(L.image)
            if doc.selection is not None:
                p.setRenderHint(QPainter.Antialiasing)
                p.setClipPath(doc.selection)
            p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            p.drawImage(0, 0, mask)
            p.end()
            doc.invalidate(doc.rect())
        finally:
            self.app.busy(False)
        self.commit()

    def cursor(self, pos, canvas):
        return Qt.PointingHandCursor


class CloneStampTool(StrokeTool):
    records_colour = False
    name = "Clone Stamp"
    icon = "clone"
    key = "L"
    hint = "Ctrl+click to set the source point, then drag to paint copied pixels."

    def __init__(self, app):
        super().__init__(app)
        self.source = None
        self.offset = None

    def press(self, pos, button, mods, canvas):
        if button == Qt.LeftButton and mods & Qt.ControlModifier:
            self.source = QPointF(pos)
            self.offset = None
            canvas.viewport().update()
            return
        if self.source is None:
            self.app.status_message("Ctrl+click to set the clone source first.")
            return
        if self.offset is None:
            self.offset = pos - self.source
        super().press(pos, button, mods, canvas)

    def move(self, pos, buttons, mods, canvas):
        super().move(pos, buttons, mods, canvas)
        canvas.viewport().update()      # the source marker follows the mouse too

    def composite_buffer(self, p, r):
        off = self.offset
        tmp = new_image(r.width(), r.height())
        tp = QPainter(tmp)
        tp.drawImage(QPointF(off.x() - r.x(), off.y() - r.y()), self.base)
        tp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        tp.drawImage(QPoint(0, 0), self.buf, r)
        tp.end()
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.drawImage(r.topLeft(), tmp)

    def draw_overlay(self, p, canvas):
        super().draw_overlay(p, canvas)
        src = None
        if self.offset is not None and canvas.last_mouse is not None:
            src = canvas.to_image(canvas.last_mouse) - self.offset
        elif self.source is not None:
            src = self.source
        if src is not None:
            v = canvas.to_view(src)
            p.setPen(QPen(QColor(255, 255, 255), 3))
            p.drawLine(v + QPointF(-7, 0), v + QPointF(7, 0)); p.drawLine(v + QPointF(0, -7), v + QPointF(0, 7))
            p.setPen(QPen(QColor(220, 40, 40), 1))
            p.drawLine(v + QPointF(-7, 0), v + QPointF(7, 0)); p.drawLine(v + QPointF(0, -7), v + QPointF(0, 7))


class RecolorTool(StrokeTool):
    name = "Recolour"
    icon = "recolor"
    key = "R"
    hint = "Drag to replace the secondary colour with the primary colour (right-drag does the reverse)."
    options = ["brush_width", "hardness", "tolerance", "antialias"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        self.button = button
        super().press(pos, button, mods, canvas)

    def segment(self, a, b):
        d = self.stroke_doc
        w = self.width()
        r = QRectF(a, b).normalized().adjusted(-w - 2, -w - 2, w + 2, w + 2).toAlignedRect().intersected(d.rect())
        if r.isEmpty():
            return
        m = QImage(r.width(), r.height(), QImage.Format_ARGB32_Premultiplied)
        m.fill(0)
        p = QPainter(m)
        p.translate(-r.x(), -r.y())
        if d.selection is not None:
            p.setClipPath(d.selection)
        stroke_line(p, a, b, w, self.opt("hardness"), QColor(0, 0, 0), self.opt("antialias"))
        p.end()
        m = m.convertToFormat(QImage.Format_Alpha8)
        buf = np.frombuffer(m.constBits(), np.uint8, count=m.bytesPerLine() * r.height())
        cov = buf.reshape(r.height(), m.bytesPerLine())[:, :r.width()].astype(np.float32) / 255.0
        L = d.layer
        region = L.image.copy(r)
        px = imaging.to_array(region, premultiplied=False)
        new = self.color(self.button)
        old = self.other_color(self.button)
        oc = np.array([old.redF(), old.greenF(), old.blueF()], np.float32)
        dist = np.sqrt(((px[..., :3] - oc) ** 2).sum(-1) / 3.0)
        tol = self.opt("tolerance") / 100.0
        sim = np.clip(1 - dist / max(tol, 1e-3), 0, 1) if tol > 0 else (dist < 1e-3).astype(np.float32)
        k = (cov * sim)[..., None]
        nc = np.array([new.redF(), new.greenF(), new.blueF()], np.float32)
        px[..., :3] = px[..., :3] * (1 - k) + nc * k
        out = imaging.from_array(px, premultiplied=False)
        p = QPainter(L.image)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(r.topLeft(), out)
        p.end()
        d.invalidate(r)


class BlurTool(StrokeTool):
    """Softens the picture where you paint, like Photoshop's Blur tool.  Within one stroke the
    blur doesn't build up (going back over a spot does nothing more); paint another stroke over
    it to blur it further."""
    records_colour = False
    name = "Blur"
    icon = "blur"
    key = "R"
    hint = "Drag over the picture to soften it; go over it again with another stroke to blur more. [ ] change size."
    options = ["brush_width", "hardness", "blur_strength"]
    flyout = "soften"         # shares one Tools button with Sharpen and Smudge

    def blur_radius(self):
        """Blur reach in pixels: grows with the strength and (up to a point) with the brush size."""
        reach = max(6.0, min(60.0, self.width() * 0.6))
        return max(1.0, self.opt("blur_strength") / 100.0 * reach)

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        self.radius = self.blur_radius()
        self.margin = int(math.ceil(self.radius)) + 2
        self.tiles = {}
        super().press(pos, button, mods, canvas)

    def segment(self, a, b):
        # the brush only marks where to blur; full strength coverage, softened by the hardness
        self.paint_color = QColor(0, 0, 0)
        super().segment(a, b)

    TILE = 128

    def process(self, src):
        """The effect applied to a piece of the original picture (src includes a margin)."""
        return imaging.blur_image(src, self.radius)

    def processed(self, r):
        """Region r of the processed picture.  The picture doesn't change during a stroke, so it
        is worked out in tiles once and reused as the brush passes over the same spot again."""
        d = self.stroke_doc
        T, m = self.TILE, self.margin
        out = QImage(r.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(0)
        p = QPainter(out)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        for ty in range(r.top() // T, r.bottom() // T + 1):
            for tx in range(r.left() // T, r.right() // T + 1):
                tile = self.tiles.get((tx, ty))
                trect = QRect(tx * T, ty * T, T, T).intersected(d.rect())
                if tile is None:
                    big = trect.adjusted(-m, -m, m, m).intersected(d.rect())
                    res = self.process(self.base.copy(big))
                    tile = res.copy(QRect(trect.x() - big.x(), trect.y() - big.y(), trect.width(), trect.height()))
                    self.tiles[(tx, ty)] = tile
                p.drawImage(trect.topLeft() - r.topLeft(), tile)
        p.end()
        return out

    def composite_buffer(self, p, r):
        tmp = self.processed(r)
        tp = QPainter(tmp)
        tp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        tp.drawImage(QPoint(0, 0), self.buf, r)
        tp.end()
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(1.0)
        p.drawImage(r.topLeft(), tmp)


class SharpenTool(BlurTool):
    """Crisps up detail where you paint (unsharp mask), like Photoshop's Sharpen tool.  As with
    Blur, one stroke doesn't build up; paint another stroke to sharpen further."""
    name = "Sharpen"
    icon = "sharpen"
    hint = "Drag over the picture to bring out detail; another stroke sharpens more. [ ] change size."
    options = ["brush_width", "hardness", "sharpen_strength"]
    RADIUS = 3.0      # blur used for the unsharp mask (about 1 pixel sigma): sharpens fine detail

    def blur_radius(self):
        return self.RADIUS

    def process(self, src):
        a = imaging.to_array(src, premultiplied=True)
        b = imaging.to_array(imaging.blur_image(src, self.radius), premultiplied=True)
        amount = np.float32(self.opt("sharpen_strength") / 100.0 * 1.6)
        out = a + (a - b) * amount
        out[..., 3] = a[..., 3]                                   # transparency unchanged
        np.clip(out, 0.0, None, out=out)
        np.minimum(out[..., :3], out[..., 3:4], out=out[..., :3])  # stay valid premultiplied
        return imaging.from_array(out, True)


class SmudgeTool(StrokeTool):
    """Picks up the colour under the brush and drags it along the stroke, like Photoshop's
    Smudge tool.  Strength sets how far the colour carries before it fades out; Finger
    painting starts each stroke with the primary colour instead."""
    records_colour = False
    name = "Smudge"
    icon = "smudge"
    key = "R"
    flyout = "soften"
    hint = "Drag to push colours around, like smearing wet paint with a finger. [ ] change size."
    options = ["brush_width", "hardness", "smudge_strength", "finger_paint"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        d = self.doc
        self.stroke_doc = d
        w = self.width()
        self.size = int(math.ceil(w)) + 2
        n = self.size
        # brush mask: 1 in the hard core, smooth fall-off to 0 at the edge
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        c = (n - 1) / 2.0
        dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / max(0.5, w / 2.0)
        core = min(0.999, self.opt("hardness") / 100.0)
        t = np.clip((dist - core) / max(1e-3, 1.0 - core), 0.0, 1.0)
        mask = 1.0 - t * t * (3 - 2 * t)
        if self.opt("hardness") >= 100:
            mask = np.clip(w / 2.0 + 0.5 - dist * (w / 2.0), 0.0, 1.0)   # just antialiased
        self.mask = mask.astype(np.float32)
        self.strength = self.opt("smudge_strength") / 100.0
        self.spacing = max(1.0, w * 0.05)      # close dabs: a smooth smear without stripes
        self.carried = None
        if self.opt("finger_paint"):
            col = self.color(button)
            px = np.array([col.redF(), col.greenF(), col.blueF(), 1.0], np.float32) * col.alphaF()
            self.carried = np.broadcast_to(px, (n, n, 4)).copy()
        self.drawing = True
        self.last = QPointF(pos)
        self._dab(pos, pickup_only=self.carried is None)

    def move(self, pos, buttons, mods, canvas):
        if self.drawing:
            a, b = self.last, QPointF(pos)
            dist = math.hypot(b.x() - a.x(), b.y() - a.y())
            if dist >= self.spacing:
                steps = int(dist // self.spacing)
                for i in range(1, steps + 1):
                    f = i * self.spacing / dist
                    self._dab(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
                f = steps * self.spacing / dist
                self.last = QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f)
        canvas.update_ring(self.width() * canvas.zoom / 2)

    def release(self, pos, button, mods, canvas):
        if self.drawing:
            self.drawing = False
            self.carried = None
            self.commit(doc=self.stroke_doc)

    def _dab(self, pt, pickup_only=False):
        d = self.stroke_doc
        n = self.size
        x0 = int(round(pt.x() - n / 2.0))
        y0 = int(round(pt.y() - n / 2.0))
        r = QRect(x0, y0, n, n)
        vr = r.intersected(d.rect())
        if vr.isEmpty():
            return
        sy = slice(vr.y() - y0, vr.y() - y0 + vr.height())
        sx = slice(vr.x() - x0, vr.x() - x0 + vr.width())
        L = d.layer
        P = imaging.to_array(L.image.copy(vr), premultiplied=True)
        if self.carried is None:
            self.carried = np.zeros((n, n, 4), np.float32)
        if pickup_only:
            self.carried[sy, sx] = P
            return
        m = self.mask[sy, sx] * np.float32(self.strength)
        if d.selection is not None:
            sel = QImage(vr.width(), vr.height(), QImage.Format_Alpha8)
            sel.fill(0)
            sp = QPainter(sel)
            sp.setRenderHint(QPainter.Antialiasing)
            sp.translate(-vr.x(), -vr.y())
            sp.fillPath(d.selection, QColor(0, 0, 0, 255))
            sp.end()
            buf = np.frombuffer(sel.constBits(), np.uint8, count=sel.bytesPerLine() * vr.height())
            m = m * (buf.reshape(vr.height(), sel.bytesPerLine())[:, :vr.width()].astype(np.float32) / 255.0)
        C = self.carried[sy, sx]
        res = P + (C - P) * m[..., None]
        self.carried[sy, sx] = res
        out = imaging.from_array(res, True)
        p = QPainter(L.image)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(vr.topLeft(), out)
        p.end()
        d.invalidate(vr)


class PencilTool(Tool):
    adds_paint = True
    flyout = "brush"
    name = "Pencil"
    icon = "pencil"
    key = "P"
    hint = "Click and drag to draw one-pixel-wide freehand lines."

    def __init__(self, app):
        super().__init__(app)
        self.drawing = False

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        self.c = self.color(button)
        self.last = QPointF(math.floor(pos.x()) + 0.5, math.floor(pos.y()) + 0.5)
        self.drawing = True
        self.sdoc = self.doc
        self.line(self.last, self.last)

    def line(self, a, b):
        d = self.sdoc
        p = QPainter(d.layer.image)
        d.clip(p)
        p.setPen(QPen(self.c, 1))
        if a == b:
            p.drawPoint(a)
        else:
            p.drawLine(a, b)
        p.end()
        d.invalidate(QRectF(a, b).normalized().adjusted(-2, -2, 2, 2).toAlignedRect())

    def move(self, pos, buttons, mods, canvas):
        if self.drawing:
            pt = QPointF(math.floor(pos.x()) + 0.5, math.floor(pos.y()) + 0.5)
            self.line(self.last, pt)
            self.last = pt

    def release(self, pos, button, mods, canvas):
        if self.drawing:
            self.drawing = False
            self.commit(doc=self.sdoc)


class ColorPickerTool(Tool):
    records_colour = False
    name = "Colour Picker"
    icon = "picker"
    key = "K"
    hint = "Click to set the primary colour, right-click to set the secondary colour."
    options = ["picker_size", "sampling", "picker_after"]

    def __init__(self, app):
        super().__init__(app)
        self.down = None

    def pick(self, pos, button):
        d = self.doc
        x, y = int(math.floor(pos.x())), int(math.floor(pos.y()))
        if not (0 <= x < d.w and 0 <= y < d.h):
            return
        n = self.opt("picker_size")
        r = QRect(x - n // 2, y - n // 2, n, n).intersected(d.rect())
        src = d.composite() if self.opt("sampling") == "image" else d.layer.image
        arr = imaging.to_array(src.copy(r), premultiplied=True)
        m = arr.reshape(-1, 4).mean(axis=0)
        a = m[3]
        if a > 0:
            rgb = m[:3] / a
        else:
            rgb = m[:3]
        c = QColor.fromRgbF(float(min(1, rgb[0])), float(min(1, rgb[1])), float(min(1, rgb[2])), float(a))
        if button == Qt.RightButton:
            self.app.colors.set_secondary(c)
        else:
            self.app.colors.set_primary(c)

    def press(self, pos, button, mods, canvas):
        if button in (Qt.LeftButton, Qt.RightButton):
            self.down = button
            self.pick(pos, button)

    def move(self, pos, buttons, mods, canvas):
        if self.down is not None and buttons:
            self.pick(pos, self.down)

    def release(self, pos, button, mods, canvas):
        if self.down is None:
            return
        self.down = None
        after = self.opt("picker_after")
        if after == "previous":
            self.app.select_previous_tool()
        elif after == "pencil":
            self.app.select_tool("Pencil")


# =================================================================== editable vector tools
class EditableTool(Tool):
    """Base for tools whose result stays editable until finished."""
    snaps = True   # line, shape and text points snap to guides

    def __init__(self, app):
        super().__init__(app)
        self.editing = False
        self.drag = None

    def is_editing(self):
        return self.editing

    def begin(self):
        d = self.doc
        self.edoc = d
        self.elayer = d.active
        self.base = QImage(d.layer.image)
        self.editing = True

    def render(self):
        d = self.edoc
        img = QImage(self.base).copy()
        p = QPainter(img)
        d.clip(p)
        p.setRenderHint(QPainter.Antialiasing, self.opt("antialias"))
        p.setRenderHint(QPainter.TextAntialiasing, self.opt("antialias"))
        self.paint(p)
        p.end()
        d.layers[self.elayer].image = img
        d.invalidate()

    def paint(self, p):
        pass

    def finish(self):
        if self.editing:
            self.editing = False
            self.drag = None
            if self.has_content():
                self.commit(doc=self.edoc)
            else:
                self.edoc.revert()
            self.app.canvas_update()

    def has_content(self):
        return True

    def cancel(self):
        if self.editing:
            self.editing = False
            self.drag = None
            self.edoc.revert()
            self.app.canvas_update()
            return True
        return False

    def options_changed(self):
        if self.editing:
            self.render()

    def key_press(self, e, canvas):
        if self.editing and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(); return True
        if self.editing and e.key() == Qt.Key_Escape:
            self.cancel(); return True
        return False

    def mine(self):
        return self.editing and self.edoc is self.doc


def pen_for(app, color, width):
    pen = QPen(color, width)
    style = app.opts["dash"]
    pen.setStyle({"solid": Qt.SolidLine, "dash": Qt.DashLine, "dot": Qt.DotLine,
                  "dashdot": Qt.DashDotLine}.get(style, Qt.SolidLine))
    cap = app.opts.get("line_cap", "flat")
    pen.setCapStyle(Qt.RoundCap if cap == "round" else Qt.FlatCap)
    pen.setJoinStyle(Qt.MiterJoin if app.opts["shape_type"] in ("rect",) else Qt.RoundJoin)
    return pen


class LineTool(EditableTool):
    adds_paint = True
    flyout = "shapes"         # shares one Tools button with Shapes (as in Photoshop)
    name = "Line / Curve"
    icon = "line"
    key = "O"
    hint = "Drag to draw a line. Drag the middle handles to bend it into a curve; Enter to finish. Shift snaps angles."
    options = ["brush_width", "antialias", "dash", "line_cap", "arrows", "arrow_size"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if self.mine():
            for i, q in enumerate(self.pts):
                if near(canvas, pos, q):
                    self.drag = i
                    return
            self.finish()
        self.begin()
        self.c = self.color(button)
        self.pts = [QPointF(pos)] * 4
        self.bent = False
        self.drag = 3

    def move(self, pos, buttons, mods, canvas):
        if self.drag is None:
            return
        i = self.drag
        if mods & Qt.ShiftModifier and i in (0, 3):
            pos = snap_angle(self.pts[3 - i], pos)
        pts = list(self.pts)
        pts[i] = QPointF(pos)
        if i in (1, 2):
            self.bent = True
        if not self.bent:
            a, b = pts[0], pts[3]
            pts[1] = a + (b - a) / 3
            pts[2] = a + (b - a) * 2 / 3
        self.pts = pts
        self.render()

    def release(self, pos, button, mods, canvas):
        if self.drag is None:
            return
        self.drag = None
        if QLineF(self.pts[0], self.pts[3]).length() < 0.5 and not self.bent:
            self.cancel()

    ARROW_SCALE = {"small": (3.0, 9.0), "medium": (4.5, 13.0), "large": (6.5, 18.0)}

    def paint(self, p):
        w = max(1, self.opt("brush_width"))
        a, c1, c2, b = self.pts
        path = QPainterPath(a)
        path.cubicTo(c1, c2, b)
        arrows = self.opt("arrows")
        start_arrow = arrows in ("start", "both")
        end_arrow = arrows in ("end", "both")
        k, lo = self.ARROW_SCALE.get(self.opt("arrow_size"), self.ARROW_SCALE["medium"])
        head = max(lo, w * k)                         # arrowhead length
        L = path.length()
        if (start_arrow or end_arrow) and L > 0:
            head = min(head, L * (0.45 if (start_arrow and end_arrow) else 0.9))
        pen = pen_for(self.app, self.c, w)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if (start_arrow or end_arrow) and L > 1:
            # stop the line just inside each arrowhead so its end doesn't poke out past the tip
            s0 = head * 0.8 if start_arrow else 0.0
            s1 = L - (head * 0.8 if end_arrow else 0.0)
            if s1 > s0:
                n = max(8, min(400, int((s1 - s0) / 2)))
                pts = [path.pointAtPercent(path.percentAtLength(s0 + (s1 - s0) * i / n)) for i in range(n + 1)]
                trimmed = QPainterPath(pts[0])
                for q in pts[1:]:
                    trimmed.lineTo(q)
                p.drawPath(trimmed)
        else:
            p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(self.c)
        if L > 0:
            if end_arrow:
                self._arrow(p, b, path.pointAtPercent(path.percentAtLength(max(0.0, L - head))), head)
            if start_arrow:
                self._arrow(p, a, path.pointAtPercent(path.percentAtLength(min(L, head))), head)

    @staticmethod
    def _arrow(p, tip, frm, size):
        """Filled arrowhead with its point at tip, aimed along frm -> tip, `size` long."""
        ang = math.atan2(tip.y() - frm.y(), tip.x() - frm.x())
        half = math.radians(24)                       # half the angle at the point
        side = size / math.cos(half)
        l, r = ang + math.pi - half, ang + math.pi + half
        back = QPointF(tip.x() - size * 0.82 * math.cos(ang), tip.y() - size * 0.82 * math.sin(ang))
        p.drawPolygon(QPolygonF([tip,
                                 QPointF(tip.x() + side * math.cos(l), tip.y() + side * math.sin(l)),
                                 back,      # slightly swept-back base looks sharper
                                 QPointF(tip.x() + side * math.cos(r), tip.y() + side * math.sin(r))]))

    def cursor(self, pos, canvas):
        if self.mine() and pos is not None and any(near(canvas, pos, q) for q in self.pts):
            return Qt.SizeAllCursor
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if self.mine():
            v = [canvas.to_view(q) for q in self.pts]
            p.setPen(QPen(QColor(80, 160, 255, 150), 1, Qt.DashLine))
            p.drawLine(v[0], v[1]); p.drawLine(v[3], v[2])
            for i, q in enumerate(v):
                draw_handle(p, q, True, self.drag == i)


SHAPES = [("rect", "Rectangle"), ("rounded", "Rounded Rectangle"), ("ellipse", "Ellipse"),
          ("triangle", "Triangle"), ("right_triangle", "Right Triangle"), ("diamond", "Diamond"),
          ("pentagon", "Pentagon"), ("hexagon", "Hexagon"), ("star", "Star"), ("arrow", "Arrow"),
          ("heart", "Heart"), ("cross", "Cross")]


def shape_path(kind, r, radius=10):
    path = QPainterPath()
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    cx, cy = r.center().x(), r.center().y()
    if kind == "rect":
        path.addRect(r)
    elif kind == "rounded":
        rad = min(radius, w / 2, h / 2)
        path.addRoundedRect(r, rad, rad)
    elif kind == "ellipse":
        path.addEllipse(r)
    elif kind == "triangle":
        path.addPolygon(QPolygonF([QPointF(cx, y), QPointF(x + w, y + h), QPointF(x, y + h)]))
    elif kind == "right_triangle":
        path.addPolygon(QPolygonF([QPointF(x, y), QPointF(x + w, y + h), QPointF(x, y + h)]))
    elif kind == "diamond":
        path.addPolygon(QPolygonF([QPointF(cx, y), QPointF(x + w, cy), QPointF(cx, y + h), QPointF(x, cy)]))
    elif kind in ("pentagon", "hexagon", "star"):
        n = {"pentagon": 5, "hexagon": 6, "star": 10}[kind]
        pts = []
        for i in range(n):
            a = -math.pi / 2 + i * 2 * math.pi / n
            k = 0.45 if (kind == "star" and i % 2) else 1.0
            pts.append(QPointF(cx + k * w / 2 * math.cos(a), cy + k * h / 2 * math.sin(a)))
        path.addPolygon(QPolygonF(pts))
    elif kind == "arrow":
        path.addPolygon(QPolygonF([QPointF(x, y + h * 0.3), QPointF(x + w * 0.6, y + h * 0.3),
                                   QPointF(x + w * 0.6, y), QPointF(x + w, cy), QPointF(x + w * 0.6, y + h),
                                   QPointF(x + w * 0.6, y + h * 0.7), QPointF(x, y + h * 0.7)]))
    elif kind == "heart":
        path.moveTo(cx, y + h * 0.28)
        path.cubicTo(cx, y, x, y, x, y + h * 0.32)
        path.cubicTo(x, y + h * 0.62, cx, y + h * 0.78, cx, y + h)
        path.cubicTo(cx, y + h * 0.78, x + w, y + h * 0.62, x + w, y + h * 0.32)
        path.cubicTo(x + w, y, cx, y, cx, y + h * 0.28)
    elif kind == "cross":
        t = 0.3
        path.addPolygon(QPolygonF([QPointF(x + w * t, y), QPointF(x + w * (1 - t), y), QPointF(x + w * (1 - t), y + h * t),
                                   QPointF(x + w, y + h * t), QPointF(x + w, y + h * (1 - t)), QPointF(x + w * (1 - t), y + h * (1 - t)),
                                   QPointF(x + w * (1 - t), y + h), QPointF(x + w * t, y + h), QPointF(x + w * t, y + h * (1 - t)),
                                   QPointF(x, y + h * (1 - t)), QPointF(x, y + h * t), QPointF(x + w * t, y + h * t)]))
    path.closeSubpath()
    return path


class ShapeTool(EditableTool):
    adds_paint = True
    flyout = "shapes"
    name = "Shapes"
    icon = "shapes"
    key = "O"
    hint = "Drag to draw a shape (Shift = equal sides). Drag the handles to adjust or inside to move; Enter to finish."
    options = ["shape_type", "draw_mode", "brush_width", "antialias", "dash", "radius"]

    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if self.mine():
            hp = XForm(self.rect).handle_points()
            for i, q in enumerate(hp):
                if near(canvas, pos, q):
                    self.drag = ("handle", i, QRectF(self.rect))
                    return
            if self.rect.contains(pos):
                self.drag = ("move", pos, QRectF(self.rect))
                return
            self.finish()
        self.begin()
        self.c_line, self.c_fill = self.color(button), self.other_color(button)
        self.start = QPointF(pos)
        self.rect = QRectF(pos, pos)
        self.drag = ("new",)

    def move(self, pos, buttons, mods, canvas):
        if self.drag is None:
            return
        kind = self.drag[0]
        if kind == "new":
            end = QPointF(pos)
            if mods & Qt.ShiftModifier:
                dx, dy = end.x() - self.start.x(), end.y() - self.start.y()
                s = max(abs(dx), abs(dy))
                end = QPointF(self.start.x() + math.copysign(s, dx or 1), self.start.y() + math.copysign(s, dy or 1))
            self.rect = QRectF(self.start, end).normalized()
        elif kind == "move":
            d = pos - self.drag[1]
            self.rect = self.drag[2].translated(d)
        else:
            i, r0 = self.drag[1], self.drag[2]
            hx, hy = XForm.DIRS[i]
            l, t, rr, b = r0.left(), r0.top(), r0.right(), r0.bottom()
            if hx < 0: l = pos.x()
            if hx > 0: rr = pos.x()
            if hy < 0: t = pos.y()
            if hy > 0: b = pos.y()
            self.rect = QRectF(QPointF(l, t), QPointF(rr, b)).normalized()
        self.render()

    def release(self, pos, button, mods, canvas):
        if self.drag is None:
            return
        self.drag = None
        if self.rect.width() < 1 and self.rect.height() < 1:
            self.cancel()

    def paint(self, p):
        w = max(1, self.opt("brush_width"))
        mode = self.opt("draw_mode")
        path = shape_path(self.opt("shape_type"), self.rect, self.opt("radius"))
        if mode == "fill":
            p.fillPath(path, self.c_line)
            return
        if mode == "both":
            p.fillPath(path, self.c_fill)
        p.setPen(pen_for(self.app, self.c_line, w))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def cursor(self, pos, canvas):
        if self.mine() and pos is not None:
            for i, q in enumerate(XForm(self.rect).handle_points()):
                if near(canvas, pos, q):
                    return [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor][i % 4]
            if self.rect.contains(pos):
                return Qt.SizeAllCursor
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if self.mine():
            r = QRectF(canvas.to_view(self.rect.topLeft()), canvas.to_view(self.rect.bottomRight()))
            p.setPen(QPen(QColor(80, 160, 255, 150), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            for q in XForm(self.rect).handle_points():
                draw_handle(p, canvas.to_view(q))


class _ItemRender:
    """Just enough of a TextTool to draw a finished TextItem from its saved settings."""

    def __init__(self, app, item):
        self.app = app
        self.lines = list(item.lines)
        self.pos = QPointF(item.pos)
        self.c = QColor(item.color)
        self._o = dict(item.opts)

    def opt(self, k):
        if k in self._o:
            return self._o[k]
        if k in TextTool.TEXT_DEFAULTS:
            return TextTool.TEXT_DEFAULTS[k]
        return self.app.opts[k]


class TextTool(EditableTool):
    """Text: click to place, type, drag to move, drag the round handle above the box to
    rotate.  Orientation can be horizontal, vertical (letters stacked), along an arc or on a
    wave; letter and line spacing, an outline (secondary colour) and a drop shadow are under
    "Spacing & Effects".  Finished text can be clicked again to edit, move or rotate it."""
    name = "Text"
    icon = "text"
    key = "T"
    hint = ("Click to place text, then type. Drag the box to move it; drag just outside a corner (round "
            "handles) to rotate it, Shift for 15° steps. Esc or click away to finish; click finished text to edit it.")
    TEXT_OPTS = ("font", "font_size", "font_style", "text_align", "antialias", "text_dir", "text_angle",
                 "letter_spacing", "line_spacing", "text_bend", "text_outline", "text_outline_color", "text_shadow")
    TEXT_DEFAULTS = {"text_dir": "horizontal", "text_angle": 0.0, "letter_spacing": 0, "line_spacing": 100,
                     "text_bend": 50, "text_outline": 0, "text_outline_color": "#000000", "text_shadow": False}
    options = ["font", "font_size", "font_style", "text_align", "text_dir", "text_angle", "text_more", "antialias"]
    HANDLE_GAP = 24       # screen pixels between the box and the rotate handle

    def __init__(self, app):
        super().__init__(app)
        self.blink = True
        self.timer = QTimer(app)
        self.timer.timeout.connect(self._blink)
        self.timer.start(530)

    def _blink(self):
        if self.editing:
            self.blink = not self.blink
            self.app.canvas_update()

    def wants_keys(self):
        return self.mine()

    # ---------------------------------------------------------------- font and layout
    def font(self):
        f = QFont(self.opt("font"))
        f.setPixelSize(max(1, int(self.opt("font_size"))))
        st = self.opt("font_style")
        f.setBold("bold" in st)
        f.setItalic("italic" in st)
        f.setUnderline("underline" in st)
        f.setStrikeOut("strike" in st)
        if not self.opt("antialias"):
            f.setStyleStrategy(QFont.NoAntialias)
        ls = self.tracking()
        if ls:
            f.setLetterSpacing(QFont.AbsoluteSpacing, ls)
        return f

    def tracking(self):
        return self.opt("font_size") * float(self.opt("letter_spacing")) / 100.0

    def layout(self):
        """Where everything goes, in the text's own (unrotated) coordinates:
        (runs, carets, rect).  runs: [(text, QPointF baseline start, angle)] to draw;
        carets: per line, a list of (top, bottom) QPointF pairs, one per caret position."""
        f = self.font()
        fm = QFontMetricsF(f)
        mode = self.opt("text_dir")
        lines = self.lines
        ls = fm.lineSpacing() * max(0.3, float(self.opt("line_spacing")) / 100.0)
        al = self.opt("text_align")
        runs, carets = [], []
        if mode == "vertical":
            # letters stacked top to bottom; lines are columns, right to left (as in Photoshop)
            cw = max([fm.horizontalAdvance(ch) for l in lines for ch in l] + [fm.averageCharWidth()])
            pitch = max(cw, fm.height() * 0.8) * max(0.3, float(self.opt("line_spacing")) / 100.0)
            step = fm.height() * 0.92 + self.tracking()
            longest = max([len(l) for l in lines] + [1])
            n = len(lines)
            for i, line in enumerate(lines):
                cx = (n - 1 - i) * pitch + cw / 2
                off = 0.0
                if al == "center":
                    off = (longest - len(line)) * step / 2
                elif al == "right":
                    off = (longest - len(line)) * step
                cl = []
                for j, ch in enumerate(line):
                    top = off + j * step
                    runs.append((ch, QPointF(cx - fm.horizontalAdvance(ch) / 2, top + fm.ascent()), 0.0))
                for j in range(len(line) + 1):
                    y = off + j * step
                    cl.append((QPointF(cx - cw / 2, y), QPointF(cx + cw / 2, y)))
                carets.append(cl)
            rect = QRectF(0, 0, max(1.0, (n - 1) * pitch + cw), max(step, longest * step))
            return runs, carets, rect, fm
        widths = [fm.horizontalAdvance(l) for l in lines]
        W = max(widths + [fm.averageCharWidth()])

        def line_x(i):
            if al == "center":
                return (W - widths[i]) / 2
            if al == "right":
                return W - widths[i]
            return 0.0
        if mode == "horizontal":
            for i, line in enumerate(lines):
                x0, base = line_x(i), fm.ascent() + i * ls
                runs.append((line, QPointF(x0, base), 0.0))
                carets.append([(QPointF(x0 + fm.horizontalAdvance(line[:c]), base - fm.ascent()),
                                QPointF(x0 + fm.horizontalAdvance(line[:c]), base + fm.descent()))
                               for c in range(len(line) + 1)])
            rect = QRectF(0, 0, W, max(fm.height(), ls * (len(lines) - 1) + fm.height()))
            return runs, carets, rect, fm
        # arc and wave: each letter placed and turned on its own
        bend = max(-100.0, min(100.0, float(self.opt("text_bend")))) / 100.0
        size = float(self.opt("font_size"))
        for i, line in enumerate(lines):
            x0, base = line_x(i), fm.ascent() + i * ls
            xs = [x0 + fm.horizontalAdvance(line[:c]) for c in range(len(line) + 1)]

            def place(x, at_baseline_y):
                """(point on the curve, angle in degrees, outward normal) for horizontal
                position x on this line."""
                if mode == "arc" and abs(bend) > 1e-3:
                    theta = bend * math.pi                          # 100 % bend: a half circle
                    R = max(W, 1.0) / theta                         # negative: curves the other way
                    R_line = R - (at_baseline_y - fm.ascent())      # later lines nest inside
                    cx, cy = W / 2, fm.ascent() + R
                    phi = (x - W / 2) / R
                    px = cx + R_line * math.sin(phi)
                    py = cy - R_line * math.cos(phi)
                    nrm = (math.sin(phi), -math.cos(phi))           # the letters' "up"
                    return QPointF(px, py), math.degrees(phi), nrm
                if mode == "wave":
                    amp = bend * size * 0.35
                    period = size * 4.0
                    k = 2 * math.pi / period
                    y = at_baseline_y + amp * math.sin(k * x)
                    slope = amp * k * math.cos(k * x)
                    ang = math.atan(slope)
                    return QPointF(x, y), math.degrees(ang), (math.sin(ang), -math.cos(ang))
                return QPointF(x, at_baseline_y), 0.0, (0.0, -1.0)
            for c, ch in enumerate(line):
                adv = xs[c + 1] - xs[c]
                mid, ang, _ = place(xs[c] + adv / 2, base)
                a = math.radians(ang)
                start = QPointF(mid.x() - math.cos(a) * adv / 2, mid.y() - math.sin(a) * adv / 2)
                runs.append((ch, start, ang))
            cl = []
            for c in range(len(line) + 1):
                pt, ang, nrm = place(xs[c], base)
                cl.append((QPointF(pt.x() + nrm[0] * fm.ascent(), pt.y() + nrm[1] * fm.ascent()),
                           QPointF(pt.x() - nrm[0] * fm.descent(), pt.y() - nrm[1] * fm.descent())))
            carets.append(cl)
        path = self._runs_path(runs, f)
        rect = path.boundingRect()
        for cl in carets:
            for a_, b_ in cl:
                rect = rect.united(QRectF(a_, b_).normalized().adjusted(-1, -1, 1, 1))
        return runs, carets, rect, fm

    @staticmethod
    def _runs_path(runs, f):
        path = QPainterPath()
        for text, start, ang in runs:
            if not text:
                continue
            if ang == 0.0:
                path.addText(start, f, text)
            else:
                g = QPainterPath()
                g.addText(QPointF(0, 0), f, text)
                path.addPath(QTransform().translate(start.x(), start.y()).rotate(ang).map(g))
        return path

    def transform(self, rect=None):
        """Text coordinates -> picture coordinates (move, then turn round the box centre)."""
        if rect is None:
            rect = self.layout()[2]
        c = rect.center()
        t = QTransform().translate(self.pos.x(), self.pos.y())
        t.translate(c.x(), c.y())
        t.rotate(float(self.opt("text_angle")))
        t.translate(-c.x(), -c.y())
        return t

    def box_polygon(self, pad=2.0):
        runs, carets, rect, fm = self.layout()
        return self.transform(rect).map(QPolygonF(rect.adjusted(-pad, -pad, pad, pad)))

    def box(self):
        return self.box_polygon(0).boundingRect()

    def local(self, pos):
        """Picture point -> text coordinates."""
        inv, ok = self.transform().inverted()
        return inv.map(QPointF(pos))

    def inside(self, pos, pad=4.0):
        rect = self.layout()[2]
        return rect.adjusted(-pad, -pad, pad, pad).contains(self.local(pos))

    def corners(self):
        """The text box's four corners and its centre, in picture coordinates."""
        runs, carets, rect, fm = self.layout()
        t = self.transform(rect)
        r = rect.adjusted(-2, -2, 2, 2)
        return [t.map(q) for q in (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())], t.map(rect.center())

    def rotate_handles(self, canvas):
        """[(round handle, corner)] in view coordinates: a round handle just outside each
        corner, as on the selection tools."""
        pts, c = self.corners()
        vc = canvas.to_view(c)
        out = []
        for q in pts:
            k = canvas.to_view(q)
            dx, dy = k.x() - vc.x(), k.y() - vc.y()
            n = math.hypot(dx, dy) or 1.0
            out.append((QPointF(k.x() + dx / n * ROT_HANDLE_GAP, k.y() + dy / n * ROT_HANDLE_GAP), k))
        return out

    def on_handle(self, pos, canvas):
        """Over a round corner handle, or anywhere just outside a corner (not inside the box)."""
        v = canvas.to_view(pos)
        hs = self.rotate_handles(canvas)
        for h, k in hs:
            if abs(v.x() - h.x()) <= HANDLE + 3 and abs(v.y() - h.y()) <= HANDLE + 3:
                return True
        if QPolygonF([k for _, k in hs]).containsPoint(v, Qt.OddEvenFill):
            return False
        return any(math.hypot(v.x() - k.x(), v.y() - k.y()) <= ROT_ZONE for _, k in hs)

    def rot_direction(self, pos, canvas):
        """Screen angle from the box centre to the pointer (which way the rotate cursor faces)."""
        _, c = self.corners()
        vc, v = canvas.to_view(c), canvas.to_view(pos)
        if abs(v.x() - vc.x()) < 1e-6 and abs(v.y() - vc.y()) < 1e-6:
            return -90.0
        return math.degrees(math.atan2(v.y() - vc.y(), v.x() - vc.x()))

    def has_content(self):
        return any(l for l in self.lines)

    # ---------------------------------------------------------------- re-editable text
    def _covered_rect(self, doc):
        poly = self.box_polygon(0)
        pad = max(4, int(self.opt("font_size") * 0.35)) + int(self.opt("text_outline")) + 2
        if self.opt("text_shadow"):
            pad += int(self.opt("font_size") * 0.2) + 4
        return poly.boundingRect().adjusted(-pad, -pad, pad, pad).toAlignedRect().intersected(QRect(0, 0, doc.w, doc.h))

    def finish(self):
        if not self.editing or self.edoc is None:
            return super().finish()
        d = self.edoc
        idx = self.elayer
        if not self.has_content():
            self.editing = False
            self.drag = None
            if getattr(self, "reopened", False):
                # reopened text deleted completely: the text layer goes too
                d.set_active(idx)
                d.delete_layer()
                self.reopened = False
                d.commit("Delete Text", "text")
            else:
                d.revert()                       # nothing typed: no new layer either
            self.app.canvas_update()
            return
        L = d.layers[idx]
        opts = {k: self.opt(k) for k in self.TEXT_OPTS}
        # a separate copy of the pixels (the same image object would change along with any
        # painting on the layer, and painting would then go unnoticed)
        L.text = TextItem(self.lines, opts, self.c, QPointF(self.pos), d.rect(), None, QImage(L.image))
        label = " ".join(l for l in self.lines if l).strip()
        L.name = (label[:28] + "…") if len(label) > 29 else (label or "Text")
        d.layersChanged.emit()
        self.reopened = False
        self.editing = False
        self.drag = None
        self.commit(name="Text", doc=d)
        self.app.canvas_update()

    def cancel(self):
        self.reopened = False
        return super().cancel()

    def text_layer_at(self, pos):
        """(layer index, text) of the topmost visible text layer whose text is under pos."""
        d = self.doc
        if d is None:
            return None
        for i in range(len(d.layers) - 1, -1, -1):
            L = d.layers[i]
            if not L.visible:
                continue
            item = L.text_item()
            if item is None:
                continue
            if self.item_polygon(item, 4).containsPoint(QPointF(pos), Qt.OddEvenFill):
                return i, item
        return None

    def item_polygon(self, item, pad=2.0):
        r = _ItemRender(self.app, item)
        runs, carets, rect, fm = r.layout()
        return r.transform(rect).map(QPolygonF(rect.adjusted(-pad, -pad, pad, pad)))

    def reopen_layer(self, idx, item, pos):
        """Open a text layer's text for editing again."""
        d = self.doc
        d.set_active(idx)
        L = d.layer
        L.image = new_image(d.w, d.h)
        for k in self.TEXT_OPTS:            # editing is off here, so this doesn't re-render
            if k in item.opts:
                self.app.set_option(k, item.opts[k])
            elif k in self.TEXT_DEFAULTS:
                self.app.set_option(k, self.TEXT_DEFAULTS[k])
        self.begin()
        self.reopened = True
        self.c = QColor(item.color)
        self.pos = QPointF(item.pos)
        self.lines = list(item.lines)
        self.row = len(self.lines) - 1
        self.col = len(self.lines[-1])
        self.render()
        # a drag straight away moves it; a plain click puts the caret where you clicked
        self.drag = ("move", pos, QPointF(self.pos))
        self.moved = False

    # ---------------------------------------------------------------- mouse
    def press(self, pos, button, mods, canvas):
        if button not in (Qt.LeftButton, Qt.RightButton):
            return
        if self.mine():
            if button == Qt.LeftButton and self.on_handle(pos, canvas):
                c = self.transform().map(self.layout()[2].center())
                self.drag = ("rotate", c, float(self.opt("text_angle")),
                             math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x())))
                self.moved = True
                return
            if self.inside(pos):
                self.drag = ("move", pos, QPointF(self.pos))
                self.moved = False
                return
            self.finish()
        if button == Qt.LeftButton:
            hit = self.text_layer_at(pos)
            if hit is not None:
                self.reopen_layer(hit[0], hit[1], pos)
                return
        # new text goes on its own text layer, above the current one
        d = self.doc
        d.add_layer(name="Text")
        self.begin()
        self.reopened = False
        self.c = self.color(button)
        self.lines = [""]
        self.row = self.col = 0
        # put the first line's middle where you clicked
        self.pos = QPointF(0, 0)
        runs, carets, rect, fm = self.layout()
        anchor = carets[0][0][0] * 0.5 + carets[0][0][1] * 0.5 if carets and carets[0] else QPointF(0, 0)
        here = self.transform(rect).map(anchor)
        self.pos = QPointF(round(pos.x() - here.x()), round(pos.y() - here.y()))
        self.render()

    def move(self, pos, buttons, mods, canvas):
        if not self.drag:
            return
        if self.drag[0] == "move":
            d = pos - self.drag[1]
            if abs(d.x()) + abs(d.y()) > 1:
                self.moved = True
            self.pos = QPointF(round(self.drag[2].x() + d.x()), round(self.drag[2].y() + d.y()))
            self.render()
        elif self.drag[0] == "rotate":
            _, c, a0, start = self.drag
            ang = a0 + math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x())) - start
            if mods & Qt.ShiftModifier:
                ang = round(ang / 15.0) * 15.0
            ang = (ang + 180.0) % 360.0 - 180.0
            # keep the box turning round its own centre
            self.app.set_option("text_angle", round(ang, 1))
            canvas.viewport().update()

    def release(self, pos, button, mods, canvas):
        if self.drag and self.drag[0] == "move" and not self.moved:
            self.row, self.col = self.caret_at(pos)
        self.drag = None
        self.app.canvas_update()

    def caret_at(self, pos):
        """Nearest caret position (line, column) to a picture point."""
        runs, carets, rect, fm = self.layout()
        p = self.transform(rect).inverted()[0].map(QPointF(pos))
        best, where = 1e18, (0, 0)
        for i, cl in enumerate(carets):
            for c, (a, b) in enumerate(cl):
                m = (a + b) * 0.5
                dd = (m.x() - p.x()) ** 2 + (m.y() - p.y()) ** 2
                if dd < best:
                    best, where = dd, (i, c)
        return where

    # ---------------------------------------------------------------- drawing
    def paint(self, p):
        f = self.font()
        runs, carets, rect, fm = self.layout()
        path = self.transform(rect).map(self._runs_path(runs, f))
        if path.isEmpty():
            return
        if self.opt("text_shadow"):
            size = float(self.opt("font_size"))
            off = max(1.0, size * 0.06)
            blur = max(0.7, size * 0.035)
            sh = path.translated(off, off)
            if self.opt("text_outline"):
                st = QPainterPathStroker(); st.setWidth(self.opt("text_outline") * 2); st.setJoinStyle(Qt.RoundJoin)
                sh = sh.united(st.createStroke(sh))
            br = sh.boundingRect().adjusted(-blur * 3 - 2, -blur * 3 - 2, blur * 3 + 2, blur * 3 + 2).toAlignedRect()
            if br.width() > 0 and br.height() > 0 and br.width() * br.height() < 60_000_000:
                tmp = QImage(br.size(), QImage.Format_ARGB32_Premultiplied)
                tmp.fill(0)
                tp = QPainter(tmp)
                tp.setRenderHint(QPainter.Antialiasing)
                tp.translate(-br.x(), -br.y())
                tp.fillPath(sh, QColor(0, 0, 0, 175))
                tp.end()
                p.drawImage(br.topLeft(), imaging.blur_image(tmp, blur * 3))
        if self.opt("text_outline"):
            pen = QPen(QColor(self.opt("text_outline_color")), float(self.opt("text_outline")) * 2.0)
            pen.setJoinStyle(Qt.RoundJoin)
            p.strokePath(path, pen)
        p.fillPath(path, self.c)

    # ---------------------------------------------------------------- keys
    def key_press(self, e, canvas):
        if not self.mine():
            return False
        k = e.key()
        line = self.lines[self.row]
        if k == Qt.Key_Escape:
            self.finish(); return True
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.lines[self.row] = line[:self.col]
            self.lines.insert(self.row + 1, line[self.col:])
            self.row += 1; self.col = 0
        elif k == Qt.Key_Backspace:
            if self.col > 0:
                self.lines[self.row] = line[:self.col - 1] + line[self.col:]
                self.col -= 1
            elif self.row > 0:
                prev = self.lines[self.row - 1]
                self.col = len(prev)
                self.lines[self.row - 1] = prev + line
                del self.lines[self.row]
                self.row -= 1
        elif k == Qt.Key_Delete:
            if self.col < len(line):
                self.lines[self.row] = line[:self.col] + line[self.col + 1:]
            elif self.row < len(self.lines) - 1:
                self.lines[self.row] = line + self.lines.pop(self.row + 1)
        elif k == Qt.Key_Left:
            if self.col > 0: self.col -= 1
            elif self.row > 0: self.row -= 1; self.col = len(self.lines[self.row])
        elif k == Qt.Key_Right:
            if self.col < len(line): self.col += 1
            elif self.row < len(self.lines) - 1: self.row += 1; self.col = 0
        elif k == Qt.Key_Up:
            if self.row > 0: self.row -= 1; self.col = min(self.col, len(self.lines[self.row]))
        elif k == Qt.Key_Down:
            if self.row < len(self.lines) - 1: self.row += 1; self.col = min(self.col, len(self.lines[self.row]))
        elif k == Qt.Key_Home:
            self.col = 0
        elif k == Qt.Key_End:
            self.col = len(line)
        elif e.text() and e.text().isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.lines[self.row] = line[:self.col] + e.text() + line[self.col:]
            self.col += len(e.text())
        else:
            return False
        self.blink = True
        self.render()
        return True

    def cursor(self, pos, canvas):
        if self.mine() and self.drag and self.drag[0] == "rotate" and pos is not None:
            return rotate_cursor(self.rot_direction(pos, canvas))
        if self.mine() and pos is not None:
            if self.on_handle(pos, canvas):
                return rotate_cursor(self.rot_direction(pos, canvas))
            if self.inside(pos):
                return Qt.SizeAllCursor
        if pos is not None and not self.mine() and self.text_layer_at(pos) is not None:
            return Qt.PointingHandCursor
        return Qt.IBeamCursor

    def draw_overlay(self, p, canvas):
        if not self.mine():
            # faint outline round every text that can be clicked to edit
            d = self.doc
            if d is not None:
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(60, 140, 240, 170), 1, Qt.DashLine))
                for L in d.layers:
                    item = L.text_item() if L.visible else None
                    if item is not None:
                        poly = self.item_polygon(item, 2)
                        p.drawPolygon(QPolygonF([canvas.to_view(q) for q in poly]))
            return
        runs, carets, rect, fm = self.layout()
        t = self.transform(rect)
        poly = t.map(QPolygonF(rect.adjusted(-2, -2, 2, 2)))
        vpoly = QPolygonF([canvas.to_view(q) for q in poly])
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(80, 160, 255, 170), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(vpoly)
        # round rotate handles just outside the corners (as on the selection tools)
        rotating = bool(self.drag and self.drag[0] == "rotate")
        for rh, _ in self.rotate_handles(canvas):
            draw_handle(p, rh, round_=True, active=rotating)
        if self.blink and carets and self.row < len(carets) and self.col < len(carets[self.row]):
            a, b = carets[self.row][self.col]
            va, vb = canvas.to_view(t.map(a)), canvas.to_view(t.map(b))
            p.setPen(QPen(QColor(0, 0, 0), 2)); p.drawLine(va, vb)
            p.setPen(QPen(QColor(255, 255, 255), 1)); p.drawLine(va, vb)


# =================================================================== crop / remove / red eye
class CropTool(Tool):
    """Photoshop-style Crop: a crop box (starting as the whole picture) with handles; drag
    inside it to move it, drag the handles to resize it, drag outside it to draw a new one.
    The area that will be cut off is shaded.  Enter or double-click crops, Esc cancels.
    The box may reach outside the picture: the extra area becomes transparent."""
    name = "Crop"
    icon = "crop"
    key = "C"
    hint = ("Drag the handles or draw a box, then press Enter (or double-click) to crop. Esc cancels. "
            "Shift keeps the shape.")
    options = ["crop_ratio", "crop_actions"]
    RATIOS = {"free": None, "original": "original", "1:1": 1.0, "4:3": 4 / 3, "3:2": 3 / 2,
              "16:9": 16 / 9, "5:4": 5 / 4, "2:1": 2.0}

    def __init__(self, app):
        super().__init__(app)
        self.rect = None          # QRectF in picture coordinates
        self.edoc = None
        self.drag = None

    # ---------------------------------------------------------------- state
    def activate(self):
        d = self.doc
        if d is None:
            return
        self.edoc = d
        if d.selection is not None and not d.selection.isEmpty():
            self.rect = QRectF(d.selection.boundingRect().toAlignedRect())
        else:
            self.rect = QRectF(d.rect())
        self.app.canvas_update()

    def deactivate(self):
        self.cancel()

    def finish(self):
        # other commands (save, undo...) shouldn't crop by surprise: just drop the box
        self.cancel()

    def cancel(self):
        had = self.rect is not None
        self.rect = None
        self.drag = None
        self.app.canvas_update()
        return had

    def is_editing(self):
        return self.rect is not None and self.edoc is self.doc and self.rect != QRectF(self.doc.rect())

    def _ensure(self):
        if self.edoc is not self.doc or self.rect is None:
            self.activate()

    def ratio(self):
        r = self.RATIOS.get(self.opt("crop_ratio"))
        if r == "original":
            d = self.doc
            return d.w / max(1, d.h)
        return r

    # ---------------------------------------------------------------- geometry helpers
    def _handles(self, canvas):
        r = self.rect
        pts = [r.topLeft(), QPointF(r.center().x(), r.top()), r.topRight(), QPointF(r.right(), r.center().y()),
               r.bottomRight(), QPointF(r.center().x(), r.bottom()), r.bottomLeft(), QPointF(r.left(), r.center().y())]
        return [canvas.to_view(p) for p in pts]

    def _hit(self, pos, canvas):
        if self.rect is None:
            return None
        v = canvas.to_view(pos)
        for i, h in enumerate(self._handles(canvas)):
            if abs(v.x() - h.x()) <= 7 and abs(v.y() - h.y()) <= 7:
                return i
        if self.rect.contains(pos):
            return "move"
        return None

    def _apply_ratio(self, r, anchor_corner, keep_shape):
        """Bend r to the chosen aspect ratio (or keep its shape with Shift), keeping the
        corner opposite the one being dragged in place."""
        ratio = self.ratio()
        if keep_shape and ratio is None and self.start_rect is not None and self.start_rect.height() > 0:
            ratio = self.start_rect.width() / self.start_rect.height()
        if ratio is None:
            return r
        w, h = abs(r.width()), abs(r.height())
        if w < 1 and h < 1:
            return r
        if self.portrait:
            ratio = 1.0 / ratio
        if w / max(h, 1e-6) > ratio:
            h = w / ratio
        else:
            w = h * ratio
        ax, ay = anchor_corner
        sx = 1 if r.right() >= ax else -1
        sy = 1 if r.bottom() >= ay else -1
        return QRectF(QPointF(ax, ay), QPointF(ax + sx * w, ay + sy * h)).normalized()

    # ---------------------------------------------------------------- input
    def press(self, pos, button, mods, canvas):
        if button != Qt.LeftButton:
            return
        self._ensure()
        hit = self._hit(pos, canvas)
        self.start = QPointF(pos)
        self.start_rect = QRectF(self.rect) if self.rect is not None else None
        r = self.rect
        if hit == "move":
            self.drag = "move"
        elif isinstance(hit, int):
            self.drag = hit
            self.portrait = r.height() > r.width()
        else:
            self.drag = "new"
            self.portrait = False
            self.rect = QRectF(pos, pos)
        canvas.viewport().update()

    def move(self, pos, buttons, mods, canvas):
        if self.drag is None or not (buttons & Qt.LeftButton):
            canvas.viewport().update()
            return
        shift = bool(mods & Qt.ShiftModifier)
        s0 = self.start_rect
        if self.drag == "move":
            d = pos - self.start
            self.rect = s0.translated(round(d.x()), round(d.y()))
        elif self.drag == "new":
            self.portrait = abs(pos.y() - self.start.y()) > abs(pos.x() - self.start.x())
            r = QRectF(self.start, pos).normalized()
            self.rect = self._apply_ratio(r, (self.start.x(), self.start.y()), shift)
        else:
            i = self.drag
            l, t, rr, b = s0.left(), s0.top(), s0.right(), s0.bottom()
            dx, dy = XForm.DIRS[i]
            if dx < 0: l = pos.x()
            if dx > 0: rr = pos.x()
            if dy < 0: t = pos.y()
            if dy > 0: b = pos.y()
            r = QRectF(QPointF(l, t), QPointF(rr, b))
            if (self.ratio() is not None or shift) and dx != 0 and dy != 0:
                ax = s0.right() if dx < 0 else s0.left()
                ay = s0.bottom() if dy < 0 else s0.top()
                r = self._apply_ratio(r, (ax, ay), shift)
            elif self.ratio() is not None:
                # side handle with a fixed ratio: grow the other side round the centre
                ratio = self.ratio() if not self.portrait else 1.0 / self.ratio()
                r = r.normalized()
                c = r.center()
                if dx != 0:
                    h = r.width() / ratio
                    r = QRectF(r.left(), c.y() - h / 2, r.width(), h)
                else:
                    w = r.height() * ratio
                    r = QRectF(c.x() - w / 2, r.top(), w, r.height())
            self.rect = r.normalized()
        self.app.selection_info_changed(self.rect.size())
        canvas.viewport().update()

    def release(self, pos, button, mods, canvas):
        if self.drag is None:
            return
        self.drag = None
        r = self.rect
        if r is None or r.width() < 1 or r.height() < 1:
            self.rect = self.start_rect if self.start_rect is not None else QRectF(self.doc.rect())
        else:
            self.rect = QRectF(round(r.left()), round(r.top()), max(1, round(r.width())), max(1, round(r.height())))
        canvas.viewport().update()

    def double_click(self, pos, button, canvas):
        if self.rect is not None and self.rect.contains(pos):
            self.apply()

    def key_press(self, e, canvas):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.rect is not None:
            self.apply(); return True
        if e.key() == Qt.Key_Escape and self.rect is not None:
            self.cancel(); self.activate(); return True
        return False

    def apply(self):
        d = self.doc
        if d is None or self.rect is None:
            return
        r = self.rect.toAlignedRect()
        if r.width() < 1 or r.height() < 1 or r == d.rect():
            return
        x, y, w, h = r.x(), r.y(), r.width(), r.height()

        def fn(img):
            out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            out.fill(0)
            p = QPainter(out)
            p.drawImage(-x, -y, img)
            p.end()
            return out
        d.transform_all(fn, w, h, QTransform().translate(-x, -y))
        d.selection = None
        d.selectionChanged.emit()
        d.commit("Crop", "crop")
        self.rect = None
        if self.app.canvas is not None:
            self.app.canvas.best_fit()
        self.activate()

    # ---------------------------------------------------------------- display
    def cursor(self, pos, canvas):
        if pos is None or self.rect is None:
            return Qt.CrossCursor
        hit = self._hit(pos, canvas)
        if hit == "move":
            return Qt.SizeAllCursor
        if isinstance(hit, int):
            return [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor] [hit % 4]
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if self.edoc is not self.doc:
            self.rect = None
            self.edoc = self.doc
            if self.doc is not None:
                self.activate()
        if self.rect is None:
            return
        vr = QRectF(canvas.to_view(self.rect.topLeft()), canvas.to_view(self.rect.bottomRight())).normalized()
        full = QRectF(canvas.viewport().rect())
        shade = QPainterPath(); shade.addRect(full)
        hole = QPainterPath(); hole.addRect(vr)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 120))
        p.drawPath(shade.subtracted(hole))
        # rule of thirds
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for k in (1, 2):
            x = vr.left() + vr.width() * k / 3
            y = vr.top() + vr.height() * k / 3
            p.drawLine(QPointF(x, vr.top()), QPointF(x, vr.bottom()))
            p.drawLine(QPointF(vr.left(), y), QPointF(vr.right(), y))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0), 3)); p.drawRect(vr)
        p.setPen(QPen(QColor(255, 255, 255), 1)); p.drawRect(vr)
        for h in self._handles(canvas):
            r = QRectF(h.x() - 4, h.y() - 4, 8, 8)
            p.setPen(QPen(QColor(0, 0, 0), 1)); p.setBrush(QColor(255, 255, 255)); p.drawRect(r)


class RemoveTool(StrokeTool):
    """Photoshop-style Remove tool: paint over something you don't want (a blemish, a
    passer-by, a wire) and when you let go it is filled in from its surroundings."""
    records_colour = False
    name = "Remove"
    icon = "remove"
    key = "J"
    flyout = "heal"
    hint = "Paint over what you want to remove; when you let go it is filled in from the surroundings. [ ] change size."
    options = ["brush_width"]

    def press(self, pos, button, mods, canvas):
        if button != Qt.LeftButton:
            return
        self.stroke_doc = self.doc
        self.pts = [QPointF(pos)]
        self.drawing = True
        canvas.viewport().update()

    def move(self, pos, buttons, mods, canvas):
        if self.drawing:
            self.pts.append(QPointF(pos))
            canvas.viewport().update()
        else:
            canvas.update_ring(self.width() * canvas.zoom / 2)

    def _stroke_path(self):
        line = QPainterPath(self.pts[0])
        for q in self.pts[1:]:
            line.lineTo(q)
        if len(self.pts) == 1:
            line.lineTo(self.pts[0] + QPointF(0.01, 0))
        st = QPainterPathStroker()
        st.setWidth(self.width())
        st.setCapStyle(Qt.RoundCap)
        st.setJoinStyle(Qt.RoundJoin)
        return st.createStroke(line).simplified()

    def release(self, pos, button, mods, canvas):
        if not self.drawing:
            return
        self.drawing = False
        d = self.stroke_doc
        if d is not self.doc:
            return
        path = self._stroke_path()
        if d.selection is not None:
            path = path.intersected(d.selection)
        self.pts = []
        canvas.viewport().update()
        if path.isEmpty():
            return
        # filled a piece at a time from nearby picture only, so a long stroke across
        # different things fills each part from what is next to it
        piece = int(max(48, min(256, self.width() * 4)))
        self.app.fill_from_surroundings(path, "Remove", "remove", grow=3, piece=piece)

    def draw_overlay(self, p, canvas):
        super().draw_overlay(p, canvas)
        if self.drawing and self.pts:
            vpath = QPainterPath(canvas.to_view(self.pts[0]))
            for q in self.pts[1:]:
                vpath.lineTo(canvas.to_view(q))
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setBrush(Qt.NoBrush)
            w = max(1.0, self.width() * canvas.zoom)
            p.setPen(QPen(QColor(255, 40, 90, 110), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if len(self.pts) == 1:
                p.drawPoint(canvas.to_view(self.pts[0]))
            else:
                p.drawPath(vpath)


class RedEyeTool(Tool):
    """Red Eye: put the circle over a red pupil and click.  Only the red patch of the pupil
    inside the circle is corrected (made dark and neutral); the catchlight, the iris, the
    eyelids and the skin are left alone, even if the circle is a bit bigger than the pupil.
    How red counts as "red eye" is worked out for each click, by comparing the inside of the
    circle with the skin just round it, so it copes with pink skin and pinkish pupils."""
    records_colour = False
    name = "Red Eye"
    icon = "red_eye"
    key = "J"
    flyout = "heal"
    hint = "Put the circle over the red pupil and click. [ ] change the size."
    options = ["redeye_size", "darken_amount"]
    RAMP_LO, RAMP_W = 0.20, 0.25     # redness where the correction starts, and how fast it builds
    GROW = 2                         # rim round the pupil that may be partly corrected
    CORE_W = 1.0                     # correction strength inside the pupil

    def size(self):
        return max(2, int(self.opt("redeye_size")))

    def press(self, pos, button, mods, canvas):
        if button != Qt.LeftButton:
            return
        d = self.doc
        if not self.fix(d, pos.x(), pos.y(), self.size() / 2.0):
            self.app.status_message("No red eye found in the circle. Put the circle over the red pupil "
                                    "(make it a little bigger than the pupil with ] if needed).")

    def move(self, pos, buttons, mods, canvas):
        canvas.update_ring(self.size() * canvas.zoom / 2 + 2)

    @staticmethod
    def find(a, cx, cy, R):
        """Correction weights (0..1) for the red pupil in the circle, or None.
        a: H x W x 4 float array (not premultiplied) of the area round the circle, with the
        circle centre at (cx, cy) in its coordinates."""
        rgb = a[..., :3]
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        red = (r - np.maximum(g, b)) / np.maximum(r, 1e-3)
        red = np.where((r > 0.15) & (a[..., 3] > 0.5), red, 0.0)
        h, w = red.shape
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2)
        inside = dist <= R
        ring = (dist >= R * 1.15 + 0.5) & (dist <= R * 1.8 + 1.5)
        if inside.sum() < 3:
            return None
        hi = float(np.percentile(red[inside], 90))
        base = float(np.percentile(red[ring], 50)) if ring.sum() >= 6 else 0.15
        if hi < 0.25 or hi - base < 0.08:
            return None
        lo = max(0.10, base + 0.35 * (hi - base))
        top = lo + max(0.05, 0.45 * (hi - lo))
        weight = np.clip((red - lo) / (top - lo), 0.0, 1.0)
        # stay in the circle (soft edge)
        weight *= np.clip((R + 0.5 - dist) / max(1.0, R * 0.2), 0.0, 1.0)
        # only the red patch that is the pupil: the one with the most red near the centre
        core = weight > 0.5
        if not core.any():
            return None
        best, keep = -1.0, None
        left = core.copy()
        while left.any():
            ys, xs = np.nonzero(left)
            comp = imaging.flood_region(left, int(xs[0]), int(ys[0]))
            left &= ~comp
            score = float((weight * comp / (1.0 + dist / max(R, 1.0))).sum())
            if score > best:
                best, keep = score, comp
        # second look, now we know where the pupil is: compare with what is right round the
        # pupil itself (not round the circle), so the result doesn't depend on the circle size
        near = imaging.box_blur(keep.astype(np.float32), 1) > 1e-6
        around = (imaging.box_blur(keep.astype(np.float32), 4) > 1e-6) & ~near
        if around.sum() >= 6:
            base2 = float(np.percentile(red[around], 50))
            hi2 = float(np.percentile(red[keep], 75))
            lo = max(0.10, base2 + 0.4 * (hi2 - base2))
            top = lo + max(0.05, 0.45 * (hi2 - lo))
        weight = np.clip((red - lo) / (top - lo), 0.0, 1.0)
        # the pupil patch plus a pixel of soft edge; holes inside it (the pinkish centre) are
        # included in proportion to their redness, so the catchlight stays bright
        weight = weight * near
        weight = np.maximum(weight, keep.astype(np.float32) * np.clip(red / max(lo, 1e-3), 0, 1))
        # the whole pupil, holes (the catchlight) included: its red cast is taken out everywhere
        border = np.zeros_like(keep)
        border[0, :] = border[-1, :] = True
        border[:, 0] = border[:, -1] = True
        free = ~keep
        outside = np.zeros_like(keep)
        for yy0, xx0 in zip(*np.nonzero(border & free)):
            if not outside[yy0, xx0]:
                outside |= imaging.flood_region(free & ~outside, int(xx0), int(yy0))
        pupil = keep | (free & ~outside)
        pupil_soft = np.maximum(pupil.astype(np.float32), weight)
        return weight, pupil_soft

    def fix(self, d, x, y, R):
        m = int(math.ceil(R * 1.8)) + 3
        box = QRect(int(math.floor(x)) - m, int(math.floor(y)) - m, 2 * m + 1, 2 * m + 1).intersected(d.rect())
        if box.width() < 3 or box.height() < 3:
            return False
        L = d.layer
        a = imaging.to_array(L.image.copy(box), premultiplied=False)
        found = self.find(a, x - box.x(), y - box.y(), R)
        if found is None:
            return False
        weight, pupil = found
        if weight.max() <= 0.05:
            return False
        rgb = a[..., :3]
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        dk = self.opt("darken_amount") / 100.0
        # red from the other two channels, the remaining cast taken out, then darkened
        # Photoshop's correction (measured from its result on a real photo): red becomes the
        # pixel's brightness, then all three channels are darkened by the same amount.  That
        # gives a natural dark pupil that keeps its shading, with no pink or green cast.
        k = 1.0 - 0.6 * dk                       # 50 % darken -> x 0.70
        Y = 0.299 * r + 0.587 * g + 0.114 * b
        target = np.empty_like(rgb)
        target[..., 0] = Y * k
        target[..., 1] = g * k
        target[..., 2] = b * k
        # how strongly each pixel changes goes smoothly with how red it is (only slightly red
        # pixels at the pupil's edge change a little), inside the pupil found above
        red = (r - np.maximum(g, b)) / np.maximum(r, 1e-3)
        core = pupil >= 0.5
        # the pupil and a ring of GROW pixels round it (its slightly red rim)
        reach = imaging.box_blur(core.astype(np.float32), self.GROW) > 1e-6
        w = np.clip((red - self.RAMP_LO) / self.RAMP_W, 0.0, 1.0) * reach
        # inside the pupil itself (its light centre included) the correction is full
        w = np.where(core, np.maximum(w, self.CORE_W), w)
        out = a.copy()
        out[..., :3] = rgb + (target - rgb) * w[..., None]
        p = QPainter(L.image)
        if d.selection is not None:
            p.setClipPath(d.selection)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(box.topLeft(), imaging.from_array(out, premultiplied=False))
        p.end()
        d.invalidate(box)
        self.commit()
        return True

    def key_press(self, e, canvas):
        if e.key() in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            v = self.size()
            step = max(1, int(v * 0.15))
            self.app.set_option("redeye_size", max(2, min(500, v + (step if e.key() == Qt.Key_BracketRight else -step))))
            canvas.viewport().update()
            return True
        return False

    def cursor(self, pos, canvas):
        return Qt.CrossCursor

    def draw_overlay(self, p, canvas):
        if canvas.last_mouse is None:
            return
        rad = self.size() * canvas.zoom / 2
        c = canvas.last_mouse
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 220), 1)); p.drawEllipse(c, rad + 1, rad + 1)
        p.setPen(QPen(QColor(0, 0, 0, 220), 1)); p.drawEllipse(c, rad, rad)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.drawLine(c + QPointF(-3, 0), c + QPointF(3, 0)); p.drawLine(c + QPointF(0, -3), c + QPointF(0, 3))


# =================================================================== dodge / burn / sponge
def _range_weight(x, tone_range):
    """How much a brightness x (0..1) belongs to the chosen tonal range."""
    if tone_range == "shadows":
        return (1.0 - x) ** 2
    if tone_range == "highlights":
        return x ** 2
    return 4.0 * x * (1.0 - x)                 # midtones


def tone_curve(x, tone_range, exposure, lighten):
    """Dodge (lighten=True) or burn curve for brightness values x (0..1), like Photoshop's:
    the chosen range moves most, the other tones much less, black and white stay put
    (except highlights/shadows, which push towards white/black as in Photoshop)."""
    e = float(exposure)
    if tone_range == "midtones":
        # strongest exactly on the middle greys, nothing at black or white
        d = e * 0.8 * x * (1.0 - x) * (1.0 + 0.6 * e)
        return x + d if lighten else x - d
    if tone_range == "highlights":
        if lighten:
            return x + e * x * x * (1.0 - x) * 2.0 + e * 0.5 * x ** 4 * (1.0 - x)
        return x * (1.0 - e * 0.6 * x * x)
    # shadows
    if lighten:
        return x + e * 0.6 * (1.0 - x) ** 2 * x + e * 0.12 * (1.0 - x) ** 3
    return x - e * x * (1.0 - x) ** 2 * 1.5


class ToneBrushTool(BlurTool):
    """Common base: a brush that applies an adjustment where it paints.  One stroke doesn't
    build up on itself; paint another stroke over it to strengthen the effect."""
    records_colour = False
    key = "D"
    flyout = "tone"

    def blur_radius(self):
        return 0.0               # works pixel by pixel: no surroundings needed

    def adjust(self, rgb):
        return rgb

    def process(self, src):
        a = imaging.to_array(src, premultiplied=False)
        out = a.copy()
        out[..., :3] = np.clip(self.adjust(a[..., :3]), 0.0, 1.0)
        return imaging.from_array(out, premultiplied=False)


class DodgeTool(ToneBrushTool):
    """Lightens where you paint (Photoshop's Dodge)."""
    name = "Dodge"
    icon = "dodge"
    lighten = True
    hint = "Paint to lighten. Range picks shadows, midtones or highlights; Exposure sets the strength. [ ] change size."
    options = ["brush_width", "hardness", "tone_range", "exposure", "protect_tones"]

    def adjust(self, rgb):
        rng, e = self.opt("tone_range"), self.opt("exposure") / 100.0
        if self.opt("protect_tones"):
            # change the brightness only, keeping each colour's hue and saturation
            L = rgb.max(axis=-1, keepdims=True)
            L2 = tone_curve(L, rng, e, self.lighten)
            out = rgb * (L2 / np.maximum(L, 1e-4))
            out = np.where(L < 1e-4, L2, out)
            return out
        return tone_curve(rgb, rng, e, self.lighten)


class BurnTool(DodgeTool):
    """Darkens where you paint (Photoshop's Burn)."""
    name = "Burn"
    icon = "burn"
    lighten = False
    hint = "Paint to darken. Range picks shadows, midtones or highlights; Exposure sets the strength. [ ] change size."


class SpongeTool(ToneBrushTool):
    """Makes colours richer or greyer where you paint (Photoshop's Sponge)."""
    name = "Sponge"
    icon = "sponge"
    hint = "Paint to saturate or desaturate colours. Flow sets the strength. [ ] change size."
    options = ["brush_width", "hardness", "sponge_mode", "sponge_flow", "vibrance"]

    def adjust(self, rgb):
        f = self.opt("sponge_flow") / 100.0
        lum = (rgb * np.array([0.299, 0.587, 0.114], np.float32)).sum(-1, keepdims=True)
        chroma = rgb - lum
        if self.opt("sponge_mode") == "desaturate":
            return lum + chroma * (1.0 - f)
        amount = f * 1.2
        if self.opt("vibrance"):
            # vibrance: already-strong colours (and near-clipping ones) get less boost
            sat = (rgb.max(-1, keepdims=True) - rgb.min(-1, keepdims=True))
            amount = amount * np.clip(1.0 - sat, 0.15, 1.0)
        out = lum + chroma * (1.0 + amount)
        if self.opt("vibrance"):
            # pull back any channel that would clip, keeping the hue
            hi = out.max(-1, keepdims=True)
            lo = out.min(-1, keepdims=True)
            k = np.minimum(np.where(hi > 1.0, (1.0 - lum) / np.maximum(hi - lum, 1e-4), 1.0),
                           np.where(lo < 0.0, lum / np.maximum(lum - lo, 1e-4), 1.0))
            out = lum + (out - lum) * np.clip(k, 0.0, 1.0)
        return out


TOOL_CLASSES = [MoveTool, MoveSelectionTool, RectSelectTool, EllipseSelectTool, LassoSelectTool, MagneticLassoTool,
                MagicWandTool, CropTool, ZoomTool, PanTool, BucketTool, GradientTool, BrushTool, PencilTool,
                EraserTool, MagicEraserTool, ColorPickerTool, CloneStampTool, RemoveTool, RedEyeTool,
                RecolorTool, BlurTool, SharpenTool, SmudgeTool, DodgeTool, BurnTool, SpongeTool,
                TextTool, LineTool, ShapeTool]


# _ItemRender borrows TextTool's drawing code
for _name in ("font", "tracking", "layout", "_runs_path", "transform", "paint"):
    setattr(_ItemRender, _name, TextTool.__dict__[_name])
