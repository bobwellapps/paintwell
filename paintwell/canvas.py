"""The scrollable, zoomable canvas view."""
import math
import time
from PySide6.QtCore import Qt, QPointF, QRectF, QRect, QPoint, QTimer, Signal, QEvent
from PySide6.QtGui import (QPainter, QPen, QColor, QBrush, QPixmap, QTransform, QCursor,
                           QPalette)
from PySide6.QtWidgets import QAbstractScrollArea
from .rulers import Ruler, RulerCorner, RULER, GUIDE_COLOR

SNAP_PX = 7      # snap to a guide when within this many screen pixels
GUIDE_HIT = 4    # grab a guide when within this many screen pixels

ZOOM_LEVELS = [0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.16, 0.25, 0.33, 0.5, 0.66, 1, 1.5, 2, 3, 4,
               5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64]
MARGIN = 24


def checker_pixmap(size=8, a=QColor(255, 255, 255), b=QColor(204, 204, 204)):
    pm = QPixmap(size * 2, size * 2)
    pm.fill(a)
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, b)
    p.fillRect(size, size, size, size, b)
    p.end()
    return pm


class Canvas(QAbstractScrollArea):
    zoomChanged = Signal(float)
    cursorMoved = Signal(object)   # QPointF in image coords or None

    def __init__(self, doc, app):
        super().__init__()
        self.doc = doc
        self.app = app
        self.zoom = 1.0
        self.show_grid = False
        self._pan_start = None
        self._space = False
        self._ants = 0
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.NoFrame)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self._checker = QBrush(checker_pixmap())
        self._ant_timer = QTimer(self)
        self._ant_timer.timeout.connect(self._tick_ants)
        self._ant_timer.start(120)
        self.last_mouse = None
        self.raw_ip = None
        # rulers and guides (switched on/off from the View menu)
        self.show_rulers = False
        self.show_guides = True
        self.snap_guides = True
        self._guide_drag = None          # [orientation "h"/"v", index or None, position or None]
        self._ruler_state = None
        self.hruler = Ruler(self, True)
        self.vruler = Ruler(self, False)
        self.ruler_corner = RulerCorner(self)
        for w in (self.hruler, self.vruler, self.ruler_corner):
            w.hide()
        doc.changed.connect(self._doc_changed)
        doc.sizeChanged.connect(lambda: (self.reset_slack(), self._layout()))
        doc.selectionChanged.connect(self.viewport().update)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)

    # ------------------------------------------------------------ geometry
    def _axis(self, view, size):
        """Centring shift for an axis: when the image fits, scroll value 0 means centred."""
        return (view - size) / 2 - MARGIN if size + 2 * MARGIN <= view else 0.0

    def origin(self):
        vw, vh = self.viewport().width(), self.viewport().height()
        sw, sh = self.doc.w * self.zoom, self.doc.h * self.zoom
        ox = MARGIN + self._axis(vw, sw) - self.horizontalScrollBar().value()
        oy = MARGIN + self._axis(vh, sh) - self.verticalScrollBar().value()
        return QPointF(math.floor(ox), math.floor(oy))

    def transform(self):
        o = self.origin()
        return QTransform().translate(o.x(), o.y()).scale(self.zoom, self.zoom)

    def to_image(self, vp):
        o = self.origin()
        return QPointF((vp.x() - o.x()) / self.zoom, (vp.y() - o.y()) / self.zoom)

    def to_view(self, ip):
        o = self.origin()
        return QPointF(ip.x() * self.zoom + o.x(), ip.y() * self.zoom + o.y())

    def _layout(self, want=None):
        """Set the scroll ranges. want=(x, y) scroll values that must be reachable (pointer zoom);
        the range is stretched past the usual margins to allow them."""
        vw, vh = self.viewport().width(), self.viewport().height()
        sw, sh = self.doc.w * self.zoom, self.doc.h * self.zoom
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        if not hasattr(self, "_slack"):
            self._slack = [0, 0, 0, 0]
        if want is not None:
            self._slack = [min(0, want[0]), want[0], min(0, want[1]), want[1]]
        rx = int(max(0, sw + 2 * MARGIN - vw))
        ry = int(max(0, sh + 2 * MARGIN - vh))
        hb.setRange(min(0, self._slack[0]), max(rx, self._slack[1]))
        vb.setRange(min(0, self._slack[2]), max(ry, self._slack[3]))
        hb.setPageStep(vw); vb.setPageStep(vh)
        hb.setSingleStep(24); vb.setSingleStep(24)
        self.viewport().update()

    def reset_slack(self):
        self._slack = [0, 0, 0, 0]

    # ------------------------------------------------------------ rulers
    def set_rulers(self, on):
        self.show_rulers = bool(on)
        m = RULER if self.show_rulers else 0
        self.setViewportMargins(m, m, 0, 0)
        for w in (self.hruler, self.vruler, self.ruler_corner):
            w.setVisible(self.show_rulers)
        self._place_rulers()
        self.viewport().update()

    def _place_rulers(self):
        if not self.show_rulers:
            return
        vp = self.viewport().geometry()
        self.hruler.setGeometry(vp.x(), vp.y() - RULER, vp.width(), RULER)
        self.vruler.setGeometry(vp.x() - RULER, vp.y(), RULER, vp.height())
        self.ruler_corner.setGeometry(vp.x() - RULER, vp.y() - RULER, RULER, RULER)

    def _update_rulers(self, force=False):
        if not self.show_rulers:
            return
        st = (self.origin(), self.zoom, self.doc.w, self.doc.h)
        if force or st != self._ruler_state:
            self._ruler_state = st
            self.hruler.update()
            self.vruler.update()

    # ------------------------------------------------------------ guides
    def guide_view_pos(self, orient, pos):
        o = self.origin()
        return (o.y() if orient == "h" else o.x()) + pos * self.zoom

    def guide_at(self, vp):
        """Index of the guide under view point vp, or None."""
        if not self.show_guides:
            return None
        best, bd = None, GUIDE_HIT + 0.01
        for i, (orient, pos) in enumerate(self.doc.guides):
            d = abs((vp.y() if orient == "h" else vp.x()) - self.guide_view_pos(orient, pos))
            if d < bd:
                best, bd = i, d
        return best

    def begin_guide_drag(self, orient, index, vp):
        if not self.show_guides:
            self.app.set_guides_visible(True)
        self._guide_drag = [orient, index, None]
        self.guide_drag_to(vp)

    def guide_drag_to(self, vp):
        g = self._guide_drag
        if g is None:
            return
        ip = self.to_image(vp)
        g[2] = round(ip.y() if g[0] == "h" else ip.x())
        self.last_mouse = QPointF(vp)
        self.cursorMoved.emit(ip)
        self.viewport().update()
        self._update_rulers(force=True)

    def end_guide_drag(self, vp):
        g = self._guide_drag
        self._guide_drag = None
        if g is None:
            return
        orient, index, pos = g
        inside = self.viewport().rect().contains(vp.toPoint())
        guides = self.doc.guides
        if inside and pos is not None:
            if index is None:
                guides.append([orient, pos])
            else:
                guides[index][1] = pos
        elif index is not None:
            del guides[index]          # dragged back onto a ruler (or off the canvas): remove it
        self.viewport().update()
        self._update_rulers(force=True)
        self.app.guides_changed()

    def snap(self, ip):
        """ip moved onto a nearby guide (if snapping is on)."""
        if not (self.snap_guides and self.show_guides and self.doc.guides) or ip is None:
            return ip
        thr = SNAP_PX / self.zoom
        x, y = ip.x(), ip.y()
        bx = by = thr + 1e-9
        for orient, pos in self.doc.guides:
            if orient == "v" and abs(ip.x() - pos) < bx:
                x, bx = pos, abs(ip.x() - pos)
            elif orient == "h" and abs(ip.y() - pos) < by:
                y, by = pos, abs(ip.y() - pos)
        return QPointF(x, y)

    def snap_delta(self, orient, values):
        """Smallest shift that puts one of values (image coords) onto a guide, or 0."""
        if not (self.snap_guides and self.show_guides and self.doc.guides):
            return 0.0
        thr = SNAP_PX / self.zoom
        best = None
        for o, pos in self.doc.guides:
            if o != orient:
                continue
            for v in values:
                d = pos - v
                if abs(d) <= thr and (best is None or abs(d) < abs(best)):
                    best = d
        return best or 0.0

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_rulers()
        pa = getattr(self, "_pending_anchor", None)
        if pa is not None and time.monotonic() - pa[3] < 0.5:
            # scroll bars appeared/disappeared just after a zoom: keep the spot under the pointer
            self._anchor_zoom(pa[0], pa[1], pa[2])
            self._last_zoom = (pa[0], pa[1], self.origin())
        else:
            self._layout()

    # ------------------------------------------------------------ zoom
    def set_zoom(self, z, anchor=None):
        z = max(ZOOM_LEVELS[0], min(ZOOM_LEVELS[-1], z))
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        ip = self.to_image(anchor)
        last = getattr(self, "_last_zoom", None)
        if last is not None and last[0] == anchor and last[2] == self.origin():
            ip = last[1]   # same pointer spot as the previous wheel step: reuse the exact point
        zooming_out = z < self.zoom
        self.zoom = z
        self._anchor_zoom(anchor, ip, zooming_out)
        self._last_zoom = (QPointF(anchor), ip, self.origin())
        self._pending_anchor = (QPointF(anchor), ip, zooming_out, time.monotonic())
        self.viewport().update()
        self.zoomChanged.emit(self.zoom)

    def _anchor_zoom(self, anchor, ip, zooming_out):
        z = self.zoom
        # scroll values that put image point ip back under the anchor (the mouse pointer).
        # Repeated because scroll bars appearing/disappearing changes the view size.
        for _ in range(3):
            vw, vh = self.viewport().width(), self.viewport().height()
            sw, sh = self.doc.w * z, self.doc.h * z
            want_x = int(round(MARGIN + self._axis(vw, sw) - (anchor.x() - ip.x() * z)))
            want_y = int(round(MARGIN + self._axis(vh, sh) - (anchor.y() - ip.y() * z)))
            # zooming out to where the whole picture fits: settle it back in the middle
            if zooming_out and sw + 2 * MARGIN <= vw:
                want_x = 0
            if zooming_out and sh + 2 * MARGIN <= vh:
                want_y = 0
            self._layout((want_x, want_y))
            self.horizontalScrollBar().setValue(want_x)
            self.verticalScrollBar().setValue(want_y)
            if (vw, vh) == (self.viewport().width(), self.viewport().height()):
                break

    def zoom_in(self, anchor=None):
        nxt = [z for z in ZOOM_LEVELS if z > self.zoom + 1e-6]
        if nxt:
            self.set_zoom(nxt[0], anchor)

    def zoom_out(self, anchor=None):
        prv = [z for z in ZOOM_LEVELS if z < self.zoom - 1e-6]
        if prv:
            self.set_zoom(prv[-1], anchor)

    def best_fit(self):
        self.reset_slack()
        vw = max(50, self.viewport().width() - 2 * MARGIN)
        vh = max(50, self.viewport().height() - 2 * MARGIN)
        z = min(vw / self.doc.w, vh / self.doc.h, 1.0)
        self.zoom = z
        self._layout()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.zoomChanged.emit(self.zoom)

    def zoom_to_rect(self, r):
        self.reset_slack()
        if r.width() < 1 or r.height() < 1:
            return
        vw = max(50, self.viewport().width() - 2 * MARGIN)
        vh = max(50, self.viewport().height() - 2 * MARGIN)
        z = min(vw / r.width(), vh / r.height())
        self.zoom = max(ZOOM_LEVELS[0], min(ZOOM_LEVELS[-1], z))
        self._layout()
        self.center_on(r.center())
        self.zoomChanged.emit(self.zoom)

    def center_on(self, ip):
        vp = self.to_view(ip)
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setValue(int(hb.value() + vp.x() - self.viewport().width() / 2))
        vb.setValue(int(vb.value() + vp.y() - self.viewport().height() / 2))

    def scroll_by(self, dx, dy):
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setValue(int(hb.value() - dx))
        vb.setValue(int(vb.value() - dy))

    # ------------------------------------------------------------ painting
    def _doc_changed(self, rect):
        if rect.isNull() or rect.isEmpty():
            self.viewport().update()
            return
        r = self.transform().mapRect(QRectF(rect)).toAlignedRect().adjusted(-2, -2, 2, 2)
        self.viewport().update(r)

    def _tick_ants(self):
        if self.doc.selection is not None:
            self._ants = (self._ants + 1) % 8
            # only the outline's area needs redrawing, not the whole picture
            r = self.transform().mapRect(self.doc.selection.boundingRect()).toAlignedRect()
            if r.width() * r.height() > 4 * 400 * 400:
                # big selection: repaint thin bands along its edges
                b = r.adjusted(-2, -2, 2, 2)
                for band in (QRect(b.left(), b.top(), b.width(), 5), QRect(b.left(), b.bottom() - 4, b.width(), 5),
                             QRect(b.left(), b.top(), 5, b.height()), QRect(b.right() - 4, b.top(), 5, b.height())):
                    self.viewport().update(band)
                if self._selection_is_rect():
                    return
            self.viewport().update(r.adjusted(-2, -2, 2, 2))

    def _selection_is_rect(self):
        sel = self.doc.selection
        key = id(sel)
        if getattr(self, "_rect_key", None) != key:
            self._rect_key = key
            br = sel.boundingRect()
            self._rect_sel = sel.elementCount() <= 6 and all(
                abs(sel.elementAt(i).x - br.left()) < 1e-6 or abs(sel.elementAt(i).x - br.right()) < 1e-6
                for i in range(sel.elementCount()))
        return self._rect_sel

    def update_ring(self, radius):
        """Repaint just around the brush-size ring at the mouse (old and new spots)."""
        old = getattr(self, "_last_ring", None)
        new = None
        if self.last_mouse is not None and radius >= 2:
            c = self.last_mouse
            new = QRectF(c.x() - radius - 3, c.y() - radius - 3, 2 * radius + 6, 2 * radius + 6).toAlignedRect()
        for r in (old, new):
            if r is not None:
                self.viewport().update(r)
        self._last_ring = new

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        pal = self.palette()
        bg = pal.color(QPalette.Window).darker(112) if pal.color(QPalette.Window).lightness() > 128 \
            else pal.color(QPalette.Window).darker(130)
        p.fillRect(e.rect(), bg)
        o = self.origin()
        z = self.zoom
        target = QRectF(o.x(), o.y(), self.doc.w * z, self.doc.h * z)
        # drop shadow
        for i, a in ((1, 50), (2, 30), (3, 15)):
            p.fillRect(target.adjusted(-i + 2, -i + 2, i + 2, i + 2), QColor(0, 0, 0, a))
        vis = target.intersected(QRectF(e.rect()))
        if not vis.isEmpty():
            p.save()
            p.setBrushOrigin(o)
            p.fillRect(vis, self._checker)
            src = QRectF((vis.left() - o.x()) / z, (vis.top() - o.y()) / z, vis.width() / z, vis.height() / z)
            src = src.toAlignedRect().intersected(self.doc.rect())
            dst = QRectF(src.x() * z + o.x(), src.y() * z + o.y(), src.width() * z, src.height() * z)
            if z < 1:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(dst, self.doc.composite(), QRectF(src))
            p.restore()
            if self.show_grid and z >= 6:
                p.setPen(QPen(QColor(128, 128, 128, 110), 0))
                x0 = max(0, int(src.left())); x1 = min(self.doc.w, src.right() + 2)
                y0 = max(0, int(src.top())); y1 = min(self.doc.h, src.bottom() + 2)
                for x in range(x0, int(x1) + 1):
                    vx = o.x() + x * z
                    p.drawLine(QPointF(vx, dst.top()), QPointF(vx, dst.bottom()))
                for y in range(y0, int(y1) + 1):
                    vy = o.y() + y * z
                    p.drawLine(QPointF(dst.left(), vy), QPointF(dst.right(), vy))
        # selection outline (marching ants)
        if self.doc.selection is not None and not self.app.hide_selection_outline():
            path = self.transform().map(self.doc.selection)
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255), 0))
            p.drawPath(path)
            pen = QPen(QColor(0, 0, 0), 0)
            pen.setDashPattern([4, 4])
            pen.setDashOffset(self._ants)
            p.setPen(pen)
            p.drawPath(path)
        # guides
        if self.show_guides and (self.doc.guides or self._guide_drag):
            p.setRenderHint(QPainter.Antialiasing, False)
            items = [(i, o_, pos) for i, (o_, pos) in enumerate(self.doc.guides)]
            g = self._guide_drag
            r = QRectF(self.viewport().rect())
            for i, orient, pos in items:
                if g is not None and g[1] == i:
                    continue            # drawn at its new place below
                self._draw_guide(p, orient, pos, r, False)
            if g is not None and g[2] is not None:
                self._draw_guide(p, g[0], g[2], r, True)
        tool = self.app.tool
        if tool is not None:
            p.setRenderHint(QPainter.Antialiasing)
            tool.draw_overlay(p, self)
        p.end()
        self._update_rulers()

    def _draw_guide(self, p, orient, pos, r, active):
        v = round(self.guide_view_pos(orient, pos)) + 0.5
        col = QColor(GUIDE_COLOR)
        if active:
            col = col.lighter(125)
        p.setPen(QPen(col, 1))
        if orient == "h":
            p.drawLine(QPointF(r.left(), v), QPointF(r.right(), v))
        else:
            p.drawLine(QPointF(v, r.top()), QPointF(v, r.bottom()))

    # ------------------------------------------------------------ input
    def _img_pos(self, e):
        return self.to_image(e.position())

    def _tool_pos(self, e):
        """Pointer position for the current tool: snapped to guides for tools that snap."""
        raw = self.to_image(e.position())
        self.raw_ip = raw
        tool = self.app.tool
        if tool is not None and getattr(tool, "snaps", False):
            return self.snap(raw)
        return raw

    def _tool_moves_guides(self):
        return getattr(self.app.tool, "moves_guides", False)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MiddleButton or (self._space and e.button() == Qt.LeftButton):
            self._pan_start = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton and self._tool_moves_guides():
            i = self.guide_at(e.position())
            if i is not None:
                self.begin_guide_drag(self.doc.guides[i][0], i, e.position())
                return
        if self.app.tool:
            self.app.paint_layer_for(self.app.tool, e.button())
            self.app.tool.press(self._tool_pos(e), e.button(), e.modifiers(), self)

    def mouseMoveEvent(self, e):
        ip = self._img_pos(e)
        self.last_mouse = e.position()
        self.cursorMoved.emit(ip)
        if self.show_rulers:
            self.hruler.update()
            self.vruler.update()
        if self._guide_drag is not None:
            self.guide_drag_to(e.position())
            return
        if self._pan_start is not None:
            d = e.position() - self._pan_start
            self._pan_start = e.position()
            self.scroll_by(d.x(), d.y())
            return
        if self.app.tool:
            tp = self._tool_pos(e)
            self.app.tool.move(tp, e.buttons(), e.modifiers(), self)
            i = self.guide_at(e.position()) if (self._tool_moves_guides() and not e.buttons()) else None
            if i is not None:
                self.viewport().setCursor(Qt.SplitVCursor if self.doc.guides[i][0] == "h" else Qt.SplitHCursor)
            else:
                self.viewport().setCursor(self.app.tool.cursor(tp, self))

    def mouseReleaseEvent(self, e):
        if self._guide_drag is not None and e.button() == Qt.LeftButton:
            self.end_guide_drag(e.position())
            return
        if self._pan_start is not None and (e.button() == Qt.MiddleButton or e.button() == Qt.LeftButton):
            self._pan_start = None
            self.viewport().setCursor(Qt.OpenHandCursor if self._space else self.app.tool.cursor(self._img_pos(e), self))
            return
        if self.app.tool:
            self.app.tool.release(self._tool_pos(e), e.button(), e.modifiers(), self)

    def mouseDoubleClickEvent(self, e):
        if self.app.tool and hasattr(self.app.tool, "double_click"):
            self.app.tool.double_click(self._img_pos(e), e.button(), self)
        else:
            self.mousePressEvent(e)

    def leaveEvent(self, e):
        self.cursorMoved.emit(None)
        if self._guide_drag is None:
            self.last_mouse = None
        self.viewport().update()
        if self.show_rulers:
            self.hruler.update()
            self.vruler.update()

    def wheelEvent(self, e):
        # Wheel = zoom around the cursor. Shift+wheel or Alt+wheel scrolls sideways,
        # Ctrl+wheel scrolls up/down.  (With Alt held, Qt often reports the wheel as
        # horizontal already, so take whichever direction has movement.)
        d = e.angleDelta()
        if e.modifiers() & (Qt.ShiftModifier | Qt.AltModifier):
            self.scroll_by((d.y() or d.x()) / 2, 0)
        elif e.modifiers() & Qt.ControlModifier:
            self.scroll_by(d.x() / 2, d.y() / 2)
        else:
            # touchpads send many small steps: add them up to one notch (120) per zoom level
            self._wheel_acc = getattr(self, "_wheel_acc", 0) + d.y()
            while self._wheel_acc >= 120:
                self._wheel_acc -= 120
                self.zoom_in(e.position())
            while self._wheel_acc <= -120:
                self._wheel_acc += 120
                self.zoom_out(e.position())
        e.accept()

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride:
            tool = self.app.tool
            if tool is not None and tool.wants_keys():
                key = e.key()
                if e.text() and e.text().isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
                    e.accept(); return True
                if key in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Left,
                           Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End, Qt.Key_Escape):
                    e.accept(); return True
        return super().event(e)

    def keyPressEvent(self, e):
        tool = self.app.tool
        if tool is not None and tool.key_press(e, self):
            return
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            if self.app.tool:
                self.viewport().setCursor(self.app.tool.cursor(None, self))
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._space = False
        super().focusOutEvent(e)
