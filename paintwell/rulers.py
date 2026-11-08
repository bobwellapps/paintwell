"""Rulers along the top and left of the canvas, and the guides you drag out of them."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget, QMenu

RULER = 20            # ruler thickness in pixels
GUIDE_COLOR = QColor(0, 170, 255)
_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000]


def tick_steps(zoom, min_label_px=56):
    """(major, minor) tick spacing in image pixels for the given zoom."""
    major = next((s for s in _STEPS if s * zoom >= min_label_px), _STEPS[-1])
    minor = major
    for div in (10, 5, 2):
        if major % div == 0 and (major // div) * zoom >= 5:
            minor = major // div
            break
    return major, minor


class Ruler(QWidget):
    """horizontal=True: the ruler above the canvas (drag down from it for a horizontal guide).
    horizontal=False: the ruler on the left (drag right from it for a vertical guide)."""

    def __init__(self, canvas, horizontal):
        super().__init__(canvas)
        self.canvas = canvas
        self.horizontal = horizontal
        self.setMouseTracking(True)
        self.setCursor(Qt.SplitVCursor if horizontal else Qt.SplitHCursor)
        self.setToolTip("Drag onto the picture to add a guide. Right-click for guide options.")
        self._dragging = False

    # ------------------------------------------------------------ painting
    def paintEvent(self, e):
        c = self.canvas
        p = QPainter(self)
        pal = self.palette()
        base = pal.color(QPalette.Window)
        dark = base.lightness() < 128
        bg = base.darker(108) if not dark else base.lighter(115)
        inside = base.lighter(104) if not dark else base.lighter(135)
        fg = pal.color(QPalette.WindowText)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), bg)
        o, z = c.origin(), c.zoom
        # lighter band over the picture's extent
        if self.horizontal:
            p.fillRect(QRectF(o.x(), 0, c.doc.w * z, h), inside)
        else:
            p.fillRect(QRectF(0, o.y(), w, c.doc.h * z), inside)
        major, minor = tick_steps(z)
        length = w if self.horizontal else h
        start = (0 - (o.x() if self.horizontal else o.y())) / z
        end = (length - (o.x() if self.horizontal else o.y())) / z
        f = QFont(self.font())
        f.setPixelSize(9)
        p.setFont(f)
        tick = QColor(fg)
        tick.setAlpha(150)
        p.setPen(QPen(tick, 1))
        i0 = math.floor(start / minor)
        i1 = math.ceil(end / minor)
        for i in range(i0, i1 + 1):
            v = i * minor
            pos = round((o.x() if self.horizontal else o.y()) + v * z) + 0.5
            is_major = v % major == 0
            half = (major // 2) and v % (major // 2) == 0 and minor < major // 2
            size = RULER - 2 if is_major else (8 if half else 4)
            if self.horizontal:
                p.drawLine(QPointF(pos, h - size), QPointF(pos, h))
                if is_major:
                    p.setPen(QPen(fg))
                    p.drawText(QPointF(pos + 3, 10), str(v))
                    p.setPen(QPen(tick, 1))
            else:
                p.drawLine(QPointF(w - size, pos), QPointF(w, pos))
                if is_major:
                    p.save()
                    p.setPen(QPen(fg))
                    p.translate(10, pos + 3)
                    p.rotate(-90)
                    p.drawText(QPointF(-len(str(v)) * 6 - 2, 0), str(v))
                    p.restore()
        # edge line towards the canvas
        line = QColor(fg)
        line.setAlpha(70)
        p.setPen(QPen(line, 1))
        if self.horizontal:
            p.drawLine(0, h - 1, w, h - 1)
        else:
            p.drawLine(w - 1, 0, w - 1, h)
        # pointer position marker
        m = c.last_mouse
        if m is not None:
            p.setPen(QPen(GUIDE_COLOR if not dark else GUIDE_COLOR.lighter(130), 1))
            if self.horizontal:
                x = round(m.x()) + 0.5
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            else:
                y = round(m.y()) + 0.5
                p.drawLine(QPointF(0, y), QPointF(w, y))
        p.end()

    # ------------------------------------------------------------ dragging out a guide
    def _viewport_pos(self, e):
        return QPointF(self.canvas.viewport().mapFromGlobal(e.globalPosition().toPoint()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self.canvas.begin_guide_drag("h" if self.horizontal else "v", None, self._viewport_pos(e))

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.canvas.guide_drag_to(self._viewport_pos(e))

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.LeftButton:
            self._dragging = False
            self.canvas.end_guide_drag(self._viewport_pos(e))

    def contextMenuEvent(self, e):
        app = self.canvas.app
        m = QMenu(self)
        for name in ("new_guide", "show_guides", "snap_guides", "clear_guides", None, "rulers"):
            if name is None:
                m.addSeparator()
            elif name in app.act:
                m.addAction(app.act[name])
        m.exec(e.globalPos())


class RulerCorner(QWidget):
    """The little square where the two rulers meet."""

    def paintEvent(self, e):
        p = QPainter(self)
        base = self.palette().color(QPalette.Window)
        p.fillRect(self.rect(), base.darker(108) if base.lightness() >= 128 else base.lighter(115))
        p.end()
