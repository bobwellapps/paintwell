"""Tools, Colours, Layers and History windows."""
import math
import numpy as np
from PySide6.QtCore import Qt, QObject, Signal, QRectF, QPointF, QSize, QRect, QTimer, QEvent
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QImage, QPixmap, QIcon, QPalette,
                           QPainterPath, QLinearGradient, QPolygonF)
from PySide6.QtWidgets import (QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QToolButton, QButtonGroup,
                               QListWidget, QListWidgetItem, QLabel, QPushButton, QSlider, QSpinBox,
                               QLineEdit, QColorDialog, QSizePolicy, QFrame, QAbstractItemView, QMenu)

from . import icons
from .canvas import checker_pixmap


# --- Colors ---
PALETTE_COLS = 15   # swatches per row (palette and Recent row)


def default_palette():
    """90 colours in 15 columns: a row of greys, then 15 hues at five shades (pale to dark)."""
    greys = [int(round(255 * (1 - i / (PALETTE_COLS - 1)))) for i in range(PALETTE_COLS)]
    rows = [[QColor(g, g, g) for g in greys]]
    for s, v in ((0.28, 1.0), (0.6, 1.0), (1.0, 1.0), (1.0, 0.72), (1.0, 0.45)):
        rows.append([QColor.fromHsvF(h / PALETTE_COLS, s, v) for h in range(PALETTE_COLS)])
    return [c for r in rows for c in r]


def old_default_palette():
    """The earlier 12-column default, recognised so a saved copy of it is swapped for the new one."""
    cols = []
    for i in range(12):
        v = int(255 * (1 - i / 11))
        cols.append(QColor(v, v, v))
    for hue in range(0, 360, 30):
        for s, v in ((0.25, 1.0), (0.5, 1.0), (0.8, 1.0), (1.0, 1.0), (1.0, 0.75), (1.0, 0.5), (1.0, 0.3)):
            cols.append(QColor.fromHsvF(hue / 360, s, v))
    # reorder so each column is one hue family: 12 grays row first, then rows by shade
    rows = [cols[:12]]
    hues = [cols[12 + h * 7: 12 + h * 7 + 7] for h in range(12)]
    for shade in range(7):
        rows.append([hues[h][shade] for h in range(12)])
    return [c for r in rows for c in r]


PALETTE_MAX = 192  # palette grows a row at a time as colours are added (Paint.NET files hold 96)


def read_palette(path):
    """Read a Paint.NET palette (.txt, one AARRGGBB or RRGGBB per line, ';' comments) or a
    GIMP palette (.gpl).
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    cols = []
    lines = text.splitlines()
    if lines and lines[0].strip().upper().startswith("GIMP PALETTE"):
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln or ln.startswith("#") or ":" in ln.split()[0]:
                continue
            parts = ln.split()
            if len(parts) >= 3:
                try:
                    r, g, b = (int(v) for v in parts[:3])
                    cols.append(QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))
                except ValueError:
                    continue
    else:
        for ln in lines:
            ln = ln.split(";")[0].strip().lstrip("#")
            if len(ln) == 8:
                cols.append(QColor.fromRgba(int(ln, 16)))
            elif len(ln) == 6:
                cols.append(QColor(int(ln[0:2], 16), int(ln[2:4], 16), int(ln[4:6], 16)))
    return cols[:PALETTE_MAX]


def write_palette(path, cols):
    """Save as a GIMP palette (.gpl) or, otherwise, a Paint.NET palette (.txt)."""
    if path.lower().endswith(".gpl"):
        out = ["GIMP Palette", "Name: Paintwell palette", "Columns: 12", "#"]
        out += [f"{c.red():3d} {c.green():3d} {c.blue():3d}\t{c.name()}" for c in cols]
    else:
        out = ["; Paint.NET Palette File", "; Saved by Paintwell", f"; {len(cols)} colours"]
        out += [f"{c.rgba():08X}" for c in cols]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


class ColorState(QObject):
    changed = Signal()
    recentChanged = Signal()
    RECENT_MAX = 15

    def __init__(self):
        super().__init__()
        self.primary = QColor(0, 0, 0)
        self.secondary = QColor(255, 255, 255)
        self.recent = []            # most recently used first

    def add_recent(self, c):
        """Remember a colour that was just painted with (newest first, no repeats)."""
        c = QColor(c)
        key = c.rgba()
        self.recent = [c] + [r for r in self.recent if r.rgba() != key]
        del self.recent[self.RECENT_MAX:]
        self.recentChanged.emit()

    def set_primary(self, c):
        self.primary = QColor(c); self.changed.emit()

    def set_secondary(self, c):
        self.secondary = QColor(c); self.changed.emit()

    def swap(self):
        self.primary, self.secondary = self.secondary, self.primary
        self.changed.emit()

    def reset(self):
        self.primary, self.secondary = QColor(0, 0, 0), QColor(255, 255, 255)
        self.changed.emit()


class SwatchWidget(QWidget):
    """Overlapping primary/secondary squares with swap and reset hot-spots."""
    targetChanged = Signal(str)
    editRequested = Signal(str)

    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.target = "primary"
        self.setFixedSize(64, 64)
        self.checker = QBrush(checker_pixmap(4))
        colors.changed.connect(self.update)
        self.setToolTip("Click to choose primary or secondary colour (double-click opens a picker).\n"
                        "Top-right arrows swap them (X), bottom-left resets to black and white.")

    def rects(self):
        return QRect(2, 2, 38, 38), QRect(22, 22, 38, 38), QRect(44, 2, 18, 18), QRect(2, 44, 18, 18)

    def paintEvent(self, e):
        p = QPainter(self)
        prim, sec, swap, reset = self.rects()
        for r, c, t in ((sec, self.colors.secondary, "secondary"), (prim, self.colors.primary, "primary")):
            p.fillRect(r, self.checker)
            p.fillRect(r, c)
            sel = self.target == t
            p.setPen(QPen(self.palette().highlight().color() if sel else self.palette().text().color(), 2 if sel else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
        p.drawPixmap(swap, icons.pixmap("swap", 18))
        p.setPen(QPen(self.palette().text().color(), 1))
        p.fillRect(QRect(8, 50, 10, 10), Qt.white); p.drawRect(QRect(8, 50, 10, 10))
        p.fillRect(QRect(3, 45, 10, 10), Qt.black); p.drawRect(QRect(3, 45, 10, 10))

    def _which(self, pos):
        prim, sec, swap, reset = self.rects()
        if swap.contains(pos):
            return "swap"
        if reset.contains(pos):
            return "reset"
        if prim.contains(pos):
            return "primary"
        if sec.contains(pos):
            return "secondary"
        return None

    def mousePressEvent(self, e):
        w = self._which(e.position().toPoint())
        if w == "swap":
            self.colors.swap()
        elif w == "reset":
            self.colors.reset()
        elif w:
            self.target = w
            self.targetChanged.emit(w)
            self.update()

    def mouseDoubleClickEvent(self, e):
        w = self._which(e.position().toPoint())
        if w in ("primary", "secondary"):
            self.editRequested.emit(w)


class PaletteWidget(QWidget):
    """Swatch grid sized at paint time. Shift+click replaces a swatch with the current colour,
    Ctrl+click removes it (if editable). `slots` reserves empty cells, used for Recent.
    """
    picked = Signal(QColor, bool)      # color, is_secondary
    replaceRequested = Signal(int)     # Shift+click on swatch i
    removeRequested = Signal(int)      # Ctrl+click on swatch i
    ASPECT = 1.0        # square swatches; the grid fills the panel width
                        # without getting too tall

    def __init__(self, colors_list, slots=0, editable=False):
        super().__init__()
        self.colors = colors_list
        self.slots = slots
        self.editable = editable
        # the height follows the width (square cells); set in resizeEvent, so the layout
        # simply sees a fixed height
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._fixed_h = None
        self.checker = QBrush(checker_pixmap(3))
        tip = "Left-click: primary colour. Right-click: secondary colour."
        if editable:
            tip += "\nShift+click: replace this swatch with the current colour.\nCtrl+click: remove this swatch."
        self.setToolTip(tip)

    def set_colors(self, colors_list):
        self.colors = list(colors_list)
        self._fit_height()
        self.updateGeometry()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_height()

    def _fit_height(self):
        h = self.heightForWidth(self.width())
        if h != self._fixed_h:
            self._fixed_h = h
            self.setFixedHeight(h)

    def heightForWidth(self, w):
        return int(self.rows() * max(9.0, w / self.cols() * self.ASPECT)) + 1

    def cols(self):
        return PALETTE_COLS

    def rows(self):
        return max(1, math.ceil(max(len(self.colors), self.slots) / self.cols()))

    def sizeHint(self):
        return QSize(PALETTE_COLS * 16, self.rows() * 16 + 1)

    def minimumSizeHint(self):
        return QSize(PALETTE_COLS * 9, self.rows() * 9 + 1)

    def cell(self):
        """(width, height) of one swatch."""
        cw = max(4.0, self.width() / self.cols())
        return cw, max(3.0, (self.height() - 1) / self.rows())

    def x0(self):
        return 0

    def paintEvent(self, e):
        p = QPainter(self)
        n = self.cols()
        cw, ch = self.cell()
        pen = QPen(self.palette().window().color(), 1)
        empty = QPen(self.palette().mid().color(), 1, Qt.DotLine)
        x0 = self.x0()
        for i in range(max(len(self.colors), self.slots)):
            # snap the cell edges to whole pixels so every gap line is equally crisp
            cx, cy = i % n, i // n
            left, top = round(x0 + cx * cw), round(cy * ch)
            r = QRectF(left, top, round(x0 + (cx + 1) * cw) - left, round((cy + 1) * ch) - top)
            if i < len(self.colors):
                col = self.colors[i]
                if col.alpha() < 255:
                    p.fillRect(r, self.checker)
                p.fillRect(r, col)
                p.setPen(pen)
                p.drawRect(r)
            else:
                p.setPen(empty)
                p.drawRect(r.adjusted(1.5, 1.5, -1.5, -1.5))

    def _index(self, pos):
        n = self.cols()
        cw, ch = self.cell()
        x, y = int((pos.x() - self.x0()) // cw), int(pos.y() // ch)
        i = y * n + x
        return i if 0 <= x < n and 0 <= i < len(self.colors) else None

    def mousePressEvent(self, e):
        i = self._index(e.position())
        if i is None:
            return
        mods = e.modifiers()
        if self.editable and mods & Qt.ShiftModifier and e.button() == Qt.LeftButton:
            self.replaceRequested.emit(i)
        elif self.editable and mods & Qt.ControlModifier and e.button() == Qt.LeftButton:
            self.removeRequested.emit(i)
        else:
            self.picked.emit(self.colors[i], e.button() == Qt.RightButton)


class ColorWheel(QWidget):
    changed = Signal(float, float)  # hue 0..1, sat 0..1

    def __init__(self):
        super().__init__()
        self.setMinimumSize(84, 84)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.h, self.s, self.v = 0.0, 0.0, 1.0
        self._img = None

    def sizeHint(self):
        return QSize(200, 200)

    def side(self):
        return max(20, min(self.width(), self.height(), 600))

    def origin(self):
        return QPointF((self.width() - self.side()) / 2, (self.height() - self.side()) / 2)

    def _render(self):
        n = self.side()
        ys, xs = np.mgrid[0:n, 0:n].astype(np.float32)
        c = (n - 1) / 2
        dx, dy = xs - c, ys - c
        r = np.sqrt(dx * dx + dy * dy) / (c - 2)
        hue = (np.degrees(np.arctan2(-dy, dx)) % 360) / 360.0
        from .imaging import hsv_to_rgb, from_array
        rgb = hsv_to_rgb(hue, np.clip(r, 0, 1), np.ones_like(r))
        alpha = np.clip((1 - r) * (c - 2) + 0.5, 0, 1)
        arr = np.concatenate([rgb * alpha[..., None], alpha[..., None]], -1)
        self._img = from_array(arr.astype(np.float32))

    def set_hsv(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def paintEvent(self, e):
        if self._img is None or self._img.width() != self.side():
            self._render()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        o = self.origin()
        p.translate(o)
        n = self.side()
        p.drawImage(0, 0, self._img)
        c = (n - 1) / 2
        a = self.h * 2 * math.pi
        pt = QPointF(c + math.cos(a) * self.s * (c - 2), c - math.sin(a) * self.s * (c - 2))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(Qt.black, 2)); p.drawEllipse(pt, 6, 6)
        p.setPen(QPen(Qt.white, 1.5)); p.drawEllipse(pt, 6, 6)

    def _pick(self, pos):
        pos = pos - self.origin()
        c = (self.side() - 1) / 2
        dx, dy = pos.x() - c, pos.y() - c
        s = min(1.0, math.hypot(dx, dy) / (c - 2))
        h = (math.degrees(math.atan2(-dy, dx)) % 360) / 360
        self.h, self.s = h, s
        self.update()
        self.changed.emit(h, s)

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons():
            self._pick(e.position())


class BrightnessBar(QWidget):
    """Vertical brightness bar beside the wheel: full colour at the top, black at the bottom."""
    valueChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.maximum = 100
        self._value = 100
        self.top = QColor(255, 255, 255)
        self.side = 200
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Brightness of the colour (drag up and down)")

    def value(self):
        return self._value

    def setValue(self, v):
        v = max(0, min(self.maximum, int(v)))
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def set_top(self, c):
        self.top = QColor(c)
        self.update()

    def bar(self):
        h = self.height()
        y0 = max(0, (h - self.side) / 2) + 4
        return QRectF(5, y0, self.width() - 10, min(h, self.side) - 8)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.bar()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, self.top)
        g.setColorAt(1, QColor(0, 0, 0))
        p.setPen(QPen(self.palette().mid().color(), 1))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 3, 3)
        y = r.bottom() - r.height() * self._value / self.maximum
        p.setPen(QPen(self.palette().text().color(), 1))
        p.setBrush(self.palette().base())
        p.drawPolygon(QPolygonF([QPointF(r.left() - 0.5, y), QPointF(0, y - 4.5), QPointF(0, y + 4.5)]))
        p.drawPolygon(QPolygonF([QPointF(r.right() + 0.5, y), QPointF(self.width(), y - 4.5), QPointF(self.width(), y + 4.5)]))

    def _set_from(self, pos):
        r = self.bar()
        self.setValue(round((r.bottom() - pos.y()) / max(1, r.height()) * self.maximum))

    def mousePressEvent(self, e):
        self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons():
            self._set_from(e.position())

    def wheelEvent(self, e):
        self.setValue(self._value + (2 if e.angleDelta().y() > 0 else -2))
        e.accept()


class WheelArea(QWidget):
    """The colour wheel with the brightness bar beside it."""

    # wheel diameter as a share of the panel width, so it grows and shrinks with the palette
    SCALE = 0.62
    MIN_SIDE = 84

    def __init__(self, wheel, bar):
        super().__init__()
        self.wheel, self.bar = wheel, bar
        wheel.setParent(self)
        bar.setParent(self)
        # height follows the width (like the palette), so the layout sees a fixed height
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._fixed_h = None
        self.setFixedHeight(self.MIN_SIDE)

    def heightForWidth(self, w):
        return int(max(self.MIN_SIDE, round(w * self.SCALE)))

    def sizeHint(self):
        return QSize(240, self.height())

    def minimumSizeHint(self):
        return QSize(140, self.height())

    def resizeEvent(self, e):
        h = self.heightForWidth(self.width())
        if h != self._fixed_h:
            self._fixed_h = h
            self.setFixedHeight(h)
        bw = 22
        side = int(max(60, min(self.width() - bw - 8, self.height(), 600)))
        x = int((self.width() - (side + 8 + bw)) / 2)
        self.wheel.setGeometry(x, int((self.height() - side) / 2), side, side)
        self.bar.side = side
        self.bar.setGeometry(x + side + 8, 0, bw, self.height())
        super().resizeEvent(e)


class GradientSlider(QWidget):
    """Horizontal slider drawn as a colour ramp (over a checkerboard) with a triangle handle."""
    valueChanged = Signal(int)

    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum
        self._value = maximum
        self.c0, self.c1 = QColor(0, 0, 0), QColor(255, 255, 255)
        self.setMinimumWidth(100)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.checker = QBrush(checker_pixmap(4))

    def value(self):
        return self._value

    def setValue(self, v):
        v = max(0, min(self.maximum, int(v)))
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def set_colors(self, c0, c1):
        self.c0, self.c1 = QColor(c0), QColor(c1)
        self.update()

    def bar(self):
        return QRectF(6, 2, self.width() - 12, 12)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.bar()
        p.fillRect(r, self.checker)
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        g.setColorAt(0, self.c0)
        g.setColorAt(1, self.c1)
        p.fillRect(r, QBrush(g))
        p.setPen(QPen(self.palette().mid().color(), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        x = r.left() + r.width() * self._value / self.maximum
        tri = QPolygonF([QPointF(x, r.bottom() + 1), QPointF(x - 5, self.height() - 1), QPointF(x + 5, self.height() - 1)])
        p.setPen(QPen(self.palette().text().color(), 1))
        p.setBrush(self.palette().base())
        p.drawPolygon(tri)

    def _set_from(self, pos):
        r = self.bar()
        self.setValue(round((pos.x() - r.left()) / max(1, r.width()) * self.maximum))

    def mousePressEvent(self, e):
        self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons():
            self._set_from(e.position())

    def wheelEvent(self, e):
        step = max(1, self.maximum // 50)
        self.setValue(self._value + (step if e.angleDelta().y() > 0 else -step))
        e.accept()


class FitScrollArea(QScrollArea):
    """Scroll area that gives its widget the full visible height when it fits, and only
    scrolls when even the widget's minimum height doesn't.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)

    def _fit(self):
        w = self.widget()
        if w is None:
            return
        # decide from the full width whether a scrollbar is needed, and set it explicitly:
        # letting Qt toggle it would change the width, which changes the height the palette
        # needs, which can toggle the scrollbar back again, forever
        fw = self.frameWidth() * 2
        full_w = self.width() - fw
        height = self.height() - fw

        def need(width):
            return w.compact_height(width) if hasattr(w, "compact_height") else w.minimumSizeHint().height()
        if need(full_w) <= height:
            policy, width = Qt.ScrollBarAlwaysOff, full_w
        else:
            policy, width = Qt.ScrollBarAlwaysOn, full_w - self.verticalScrollBar().sizeHint().width()
        if self.verticalScrollBarPolicy() != policy:
            self.setVerticalScrollBarPolicy(policy)
        size = QSize(width, max(need(width), height))
        if w.size() != size:
            w.resize(size)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def eventFilter(self, obj, e):
        if obj is self.widget() and e.type() == QEvent.LayoutRequest:
            QTimer.singleShot(0, self._fit)
        return super().eventFilter(obj, e)

    def setWidget(self, w):
        super().setWidget(w)
        w.installEventFilter(self)
        QTimer.singleShot(0, self._fit)


class ColorsPanel(QWidget):
    def __init__(self, colors, settings):
        super().__init__()
        self.colors = colors
        self.settings = settings
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.swatch = SwatchWidget(colors)
        top.addWidget(self.swatch, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.more = QPushButton("More »")
        self.more.setCheckable(True)
        self.more.setToolTip("Show the exact red / green / blue, hue / saturation / brightness values")
        col.addWidget(self.more)
        self.pal_btn = QPushButton("Palette")
        self.pal_btn.setToolTip("Add the current colour to the palette, or load / save a palette file")
        pm = QMenu(self.pal_btn)
        pm.addAction("Add Current Colour", self._pal_add)
        pm.addSeparator()
        pm.addAction("Load Palette...", self._pal_load)
        pm.addAction("Save Palette As...", self._pal_save)
        pm.addSeparator()
        pm.addAction("Reset to Default Palette", self._pal_reset)
        self.pal_btn.setMenu(pm)
        col.addWidget(self.pal_btn)
        col.addStretch(1)
        top.addLayout(col)
        top.addStretch(1)
        lay.addLayout(top)
        self.palette_w = PaletteWidget(self._load_palette(), editable=True)
        lay.addWidget(self.palette_w)
        rl = QLabel("Recent colours")
        rl.setAlignment(Qt.AlignCenter)
        rl.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(rl)
        self.recent_w = PaletteWidget([], slots=ColorState.RECENT_MAX)
        self.recent_w.setToolTip("Colours you painted with most recently.\n"
                                 "Left-click: primary colour. Right-click: secondary colour.")
        lay.addWidget(self.recent_w)
        # colour wheel with the brightness bar beside it: takes whatever height is left
        self.wheel = ColorWheel()
        self.v_bar = BrightnessBar()
        self.wheel_area = WheelArea(self.wheel, self.v_bar)
        lay.addWidget(self.wheel_area)
        self.spins = {}

        def slider_row(grid, row, key, label, maximum, tip, gradient=False):
            grid.addWidget(QLabel(label), row, 0)
            sl = GradientSlider(maximum) if gradient else QSlider(Qt.Horizontal)
            if not gradient:
                sl.setRange(0, maximum)
                sl.setMinimumWidth(80)
            sp = QSpinBox(); sp.setRange(0, maximum)
            sl.valueChanged.connect(sp.setValue)
            sp.valueChanged.connect(sl.setValue)
            sp.valueChanged.connect(lambda v, k=key: self._spin(k))
            sl.setToolTip(tip); sp.setToolTip(tip)
            grid.addWidget(sl, row, 1); grid.addWidget(sp, row, 2)
            self.spins[key] = sp
            return sl

        # transparency and hex, always shown
        basic = QGridLayout()
        basic.setVerticalSpacing(4)
        self.a_slider = slider_row(basic, 0, "A", "Transparency", 255,
                                   "Transparency (alpha): 255 is fully solid, 0 is fully transparent", True)
        basic.addWidget(QLabel("Hex"), 1, 0)
        self.hex = QLineEdit(); self.hex.setMaxLength(9)
        self.hex.setToolTip("RRGGBB, or AARRGGBB to include transparency")
        self.hex.editingFinished.connect(self._hex)
        basic.addWidget(self.hex, 1, 1, 1, 2)
        basic.setColumnStretch(1, 1)
        lay.addLayout(basic)
        # exact RGB / HSV values: behind the More button
        self.ext = QFrame()
        grid = QGridLayout(self.ext)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setVerticalSpacing(2)
        for i, (k, mx, tip) in enumerate([("R", 255, "Red"), ("G", 255, "Green"), ("B", 255, "Blue"),
                                           ("H", 359, "Hue"), ("S", 100, "Saturation"), ("V", 100, "Brightness")]):
            slider_row(grid, i, k, k, mx, tip)
        grid.setColumnStretch(1, 1)
        lay.addWidget(self.ext)
        lay.addStretch(1)       # any spare height goes below everything
        # the brightness bar drives the V value
        self.v_bar.valueChanged.connect(self.spins["V"].setValue)
        self.spins["V"].valueChanged.connect(self.v_bar.setValue)
        self.ext.setVisible(False)
        self.more.toggled.connect(self._toggle_more)
        self.more.setChecked(settings.value("colors_more", "false") in ("true", True))
        self.wheel.changed.connect(self._wheel)
        self.palette_w.picked.connect(self._palette)
        self.palette_w.replaceRequested.connect(self._pal_replace)
        self.palette_w.removeRequested.connect(self._pal_remove)
        self.recent_w.picked.connect(self._palette)
        self.swatch.targetChanged.connect(lambda t: self._sync())
        self.swatch.editRequested.connect(self._dialog)
        colors.changed.connect(self._sync)
        colors.recentChanged.connect(self._recent_changed)
        self._load_recent()
        self._sync()

    # fit the space the window gives the panel: the colour wheel takes up any slack, so the
    # panel only needs its compact size (otherwise the scroll area makes it taller than needed)
    def compact_height(self, width):
        """Height the panel needs at this width with the colour wheel at its smallest."""
        lay = self.layout()
        m = lay.contentsMargins()
        inner = width - m.left() - m.right()
        full = lay.sizeHint().height()
        for pw in (self.palette_w, self.recent_w, self.wheel_area):     # their heights at this width
            full += pw.heightForWidth(inner) - pw.height()
        return full

    # --- Recent colours ---
    def _load_recent(self):
        raw = self.settings.value("recent_colours", "") or ""
        cols = []
        for t in str(raw).split(","):
            t = t.strip()
            if len(t) == 8:
                try:
                    cols.append(QColor.fromRgba(int(t, 16)))
                except ValueError:
                    pass
        self.colors.recent = cols[:ColorState.RECENT_MAX]
        self.recent_w.set_colors(self.colors.recent)

    def _recent_changed(self):
        self.recent_w.set_colors(self.colors.recent)
        self.settings.setValue("recent_colours", ",".join(f"{c.rgba():08X}" for c in self.colors.recent))

    # --- Palette editing / files ---
    def _load_palette(self):
        raw = self.settings.value("palette_colours", "") or ""
        cols = []
        for t in str(raw).split(","):
            t = t.strip()
            if len(t) == 8:
                try:
                    cols.append(QColor.fromRgba(int(t, 16)))
                except ValueError:
                    pass
        if not cols or [c.rgba() for c in cols] == [c.rgba() for c in old_default_palette()]:
            return default_palette()
        return cols

    def _store_palette(self):
        self.settings.setValue("palette_colours", ",".join(f"{c.rgba():08X}" for c in self.palette_w.colors))

    def _set_palette(self, cols):
        self.palette_w.set_colors(cols[:PALETTE_MAX])
        self._store_palette()

    def _pal_add(self):
        cols = list(self.palette_w.colors)
        if len(cols) >= PALETTE_MAX:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Palette", "The palette is full. Shift+click a swatch to replace it "
                                                     "with the current colour, or Ctrl+click one to remove it.")
            return
        cols.append(QColor(self.current()))
        self._set_palette(cols)

    def _pal_replace(self, i):
        cols = list(self.palette_w.colors)
        cols[i] = QColor(self.current())
        self._set_palette(cols)

    def _pal_remove(self, i):
        cols = list(self.palette_w.colors)
        if len(cols) > 1:
            del cols[i]
            self._set_palette(cols)

    def _pal_reset(self):
        self._set_palette(default_palette())
        self.settings.remove("palette_colours")

    def _pal_load(self):
        from . import filedialogs
        start = self.settings.value("palette_dir", "") or ""
        path = filedialogs.open_file(self, "Load Palette", start,
                                     "Palettes (*.txt *.gpl);;Paint.NET palette (*.txt);;GIMP palette (*.gpl);;All files (*)")
        if not path:
            return
        import os
        self.settings.setValue("palette_dir", os.path.dirname(path))
        try:
            cols = read_palette(path)
        except Exception as ex:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load Palette", f"Couldn't read that palette:\n{ex}")
            return
        if not cols:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Load Palette", "That file doesn't contain any colours.")
            return
        self._set_palette(cols)

    def _pal_save(self):
        from . import filedialogs
        import os
        start = self.settings.value("palette_dir", "") or os.path.expanduser("~")
        path, chosen = filedialogs.save_file(self, "Save Palette As", os.path.join(start, "palette.txt"),
                                             "Paint.NET palette (*.txt);;GIMP palette (*.gpl)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".gpl" if "gpl" in (chosen or "") else ".txt"
        self.settings.setValue("palette_dir", os.path.dirname(path))
        try:
            write_palette(path, self.palette_w.colors)
        except OSError as ex:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save Palette", f"Couldn't save the palette:\n{ex}")

    def _toggle_more(self, on):
        self.settings.setValue("colors_more", on)
        self.ext.setVisible(on)
        self.more.setText("« Less" if on else "More »")

    def current(self):
        return self.colors.primary if self.swatch.target == "primary" else self.colors.secondary

    def set_current(self, c):
        if self.swatch.target == "primary":
            self.colors.set_primary(c)
        else:
            self.colors.set_secondary(c)

    def _dialog(self, which):
        self.swatch.target = which
        c0 = self.colors.primary if which == "primary" else self.colors.secondary
        c = QColorDialog.getColor(c0, self, f"{which.title()} Colour", QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.set_current(c)

    def _palette(self, c, secondary):
        if secondary:
            self.colors.set_secondary(c)
        else:
            self.colors.set_primary(c)

    def _wheel(self, h, s):
        c = self.current()
        v = c.valueF() if c.valueF() > 0.05 else 1.0
        n = QColor.fromHsvF(h, s, max(v, 0.0), c.alphaF())
        self.set_current(n)

    def _spin(self, k):
        if self._updating:
            return
        sp = self.spins
        if k in "RGB":
            c = QColor(sp["R"].value(), sp["G"].value(), sp["B"].value(), sp["A"].value())
        elif k == "A":
            c = QColor(self.current())
            c.setAlpha(sp["A"].value())
        else:
            c = QColor.fromHsvF(sp["H"].value() / 359.0 if k == "H" else self.wheel.h,
                                sp["S"].value() / 100.0 if k == "S" else self.wheel.s,
                                sp["V"].value() / 100.0, sp["A"].value() / 255.0)
        self.set_current(c)

    def _hex(self):
        t = self.hex.text().strip().lstrip("#")
        try:
            if len(t) == 6:
                c = QColor(int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16), self.current().alpha())
            elif len(t) == 8:
                c = QColor(int(t[2:4], 16), int(t[4:6], 16), int(t[6:8], 16), int(t[0:2], 16))
            else:
                raise ValueError
        except ValueError:
            self._sync(); return
        self.set_current(c)

    def _sync(self):
        self._updating = True
        c = self.current()
        h = c.hsvHueF()
        h = self.wheel.h if h < 0 else h
        sat = c.hsvSaturationF() if c.valueF() > 0 else self.wheel.s
        self.wheel.set_hsv(h, sat, c.valueF())
        vals = {"R": c.red(), "G": c.green(), "B": c.blue(), "H": max(0, c.hsvHue()) if c.hsvHue() >= 0 else int(self.wheel.h * 359),
                "S": round(c.hsvSaturationF() * 100), "V": round(c.valueF() * 100), "A": c.alpha()}
        for k, v in vals.items():
            self.spins[k].setValue(v)
        self.hex.setText(c.name(QColor.HexRgb).upper().lstrip("#") if c.alpha() == 255 else c.name(QColor.HexArgb).upper().lstrip("#"))
        full = QColor.fromHsvF(max(0.0, h), sat, 1.0)
        self.v_bar.set_top(full)
        solid = QColor(c); solid.setAlpha(255)
        clear = QColor(c); clear.setAlpha(0)
        self.a_slider.set_colors(clear, solid)
        self._updating = False
        self.swatch.update()


# --- Tools ---
class FlyoutButton(QToolButton):
    """Button for a group of tools. Shows the last one used; hold or right-click for the rest."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = max(4.0, self.width() * 0.11)
        x, y = self.width() - 3.0, self.height() - 3.0
        col = self.palette().color(QPalette.WindowText)
        col.setAlpha(170)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(x, y - s), QPointF(x, y), QPointF(x - s, y)]))
        p.end()


class ToolsPanel(QWidget):
    """Tool buttons in rows of five, placed by hand rather than with a layout so resizing never
    feeds back into the dock's size.
    """
    COLS = 5
    MAX_BUTTON = 72

    @classmethod
    def max_width(cls):
        """Width at which the buttons are as big as they get; wider would only add empty space."""
        return cls.COLS * cls.MAX_BUTTON + 8

    def __init__(self, tools, on_select):
        super().__init__()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self.order = []
        self._min_h = None
        self.tool_by_name = {t.name: t for t in tools}
        flyouts = {}
        for t in tools:
            fly = getattr(t, "flyout", None)
            if fly and fly in flyouts:          # another member of a group: add it to that button's menu
                b = flyouts[fly]
                self._add_member(b, t, on_select)
                self.buttons[t.name] = b
                continue
            b = FlyoutButton(self) if fly else QToolButton(self)
            b.setCheckable(True)
            b.setAutoRaise(True)
            if fly:
                flyouts[fly] = b
                b.members = []
                m = QMenu(b)
                b.setMenu(m)
                b.setPopupMode(QToolButton.DelayedPopup)      # press and hold opens the menu
                b.setContextMenuPolicy(Qt.CustomContextMenu)  # so does right-click
                b.customContextMenuRequested.connect(lambda pos, b=b: b.menu().exec(b.mapToGlobal(pos)))
                b.clicked.connect(lambda _=False, b=b: on_select(b.current))
                self._add_member(b, t, on_select)
                self._show_member(b, t)
            else:
                b.setIcon(icons.icon(t.icon))
                b.setToolTip(self._tip(t))
                b.clicked.connect(lambda _=False, t=t: on_select(t.name))
            self.group.addButton(b)
            self.buttons[t.name] = b
            self.order.append(b)
        for b in flyouts.values():       # tooltips can list the whole group now
            self._show_member(b, self.tool_by_name[b.current])
        self.rows = math.ceil(len(self.order) / self.COLS)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def sizeHint(self):
        return QSize(self.COLS * 40 + 8, self.rows * 40 + 8)

    def minimumSizeHint(self):
        return QSize(self.COLS * 26 + 8, self.rows * 26 + 8)

    def button_size(self, width):
        return int(max(26, min(self.MAX_BUTTON, (width - 8) // self.COLS)))

    def resizeEvent(self, e):
        size = self.button_size(self.width())
        # shrink to fit if the dock is shorter than the buttons want
        size = int(max(26, min(size, (self.height() - 8) // self.rows)))
        icon = int(size * 0.66)
        x0 = (self.width() - size * self.COLS) // 2
        for i, b in enumerate(self.order):
            b.setGeometry(x0 + (i % self.COLS) * size, 4 + (i // self.COLS) * size, size, size)
            b.setIconSize(QSize(icon, icon))
        # ask (later, outside this event) for enough height to show full-size buttons
        want = self.rows * self.button_size(self.width()) + 8
        if want != self._min_h:
            self._min_h = want
            QTimer.singleShot(0, self._apply_min_height)
        super().resizeEvent(e)

    def _apply_min_height(self):
        if self._min_h is not None and self.minimumHeight() != self._min_h:
            self.setMinimumHeight(min(self._min_h, self.rows * 72 + 8))

    @staticmethod
    def _tip(t):
        return f"{t.name}" + (f" ({t.key})" if t.key else "") + f"\n{t.hint}"

    def _add_member(self, b, t, on_select):
        b.members.append(t.name)
        a = b.menu().addAction(icons.icon(t.icon), t.name + (f"\t{t.key}" if t.key else ""))
        a.triggered.connect(lambda _=False, n=t.name: on_select(n))

    def _show_member(self, b, t):
        b.current = t.name
        b.setIcon(icons.icon(t.icon))
        others = ", ".join(n for n in b.members if n != t.name)
        b.setToolTip(self._tip(t) + (f"\nHold down or right-click for: {others}" if others else ""))

    def set_current(self, name):
        if name in self.buttons:
            b = self.buttons[name]
            if getattr(b, "members", None) and name in b.members:
                self._show_member(b, self.tool_by_name[name])
            b.setChecked(True)


# --- Layers ---
def thumb(img, size=40, text_badge=False):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    s = img.size().scaled(size - 2, size - 2, Qt.KeepAspectRatio)
    r = QRect((size - s.width()) // 2, (size - s.height()) // 2, max(1, s.width()), max(1, s.height()))
    p.fillRect(r, QBrush(checker_pixmap(3)))
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(r, img)
    p.setPen(QPen(QColor(120, 120, 120), 1))
    p.drawRect(r.adjusted(0, 0, -1, -1))
    if text_badge:
        # "T" badge: a text layer (its text can still be edited)
        b = QRectF(size - 17, size - 17, 16, 16)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(40, 90, 170), 1)); p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(b, 3, 3)
        f = p.font(); f.setPixelSize(12); f.setBold(True); p.setFont(f)
        p.setPen(QColor(40, 90, 170)); p.drawText(b, Qt.AlignCenter, "T")
    p.end()
    return pm


def small_button(icon_name, tip, cb):
    b = QToolButton()
    b.setIcon(icons.icon(icon_name))
    b.setIconSize(QSize(20, 20))
    b.setAutoRaise(True)
    b.setToolTip(tip)
    b.clicked.connect(cb)
    return b


class LayersPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.doc = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.list = QListWidget()
        self.list.setIconSize(QSize(40, 40))
        self.list.setSpacing(1)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.setSpacing(0)
        for icon, tip, name in (("layer_add", "Add New Layer", "add_layer"), ("layer_delete", "Delete Layer", "delete_layer"),
                                ("layer_dup", "Duplicate Layer", "duplicate_layer"), ("layer_merge", "Merge Layer Down", "merge_down"),
                                ("layer_up", "Move Layer Up", "layer_up"), ("layer_down", "Move Layer Down", "layer_down"),
                                ("layer_props", "Layer Properties", "layer_properties")):
            row.addWidget(small_button(icon, tip, lambda _=False, n=name: app.action(n)))
        row.addStretch(1)
        lay.addLayout(row)
        self.list.currentRowChanged.connect(self._row)
        self.list.itemChanged.connect(self._item_changed)
        self.list.itemDoubleClicked.connect(lambda it: app.action("layer_properties"))
        self.list.customContextMenuRequested.connect(self._menu)
        self._building = False

    def _menu(self, pos):
        m = QMenu(self)
        for label, name in (("Add New Layer", "add_layer"), ("Duplicate Layer", "duplicate_layer"),
                            ("Delete Layer", "delete_layer"), ("Merge Layer Down", "merge_down"),
                            (None, None), ("Layer Properties...", "layer_properties")):
            if label is None:
                m.addSeparator()
            else:
                m.addAction(label, lambda n=name: self.app.action(n))
        m.exec(self.list.mapToGlobal(pos))

    def set_doc(self, doc):
        if self.doc is not None:
            try:
                self.doc.layersChanged.disconnect(self.rebuild)
                self.doc.historyChanged.disconnect(self.rebuild)
            except (RuntimeError, TypeError):
                pass
        self.doc = doc
        if doc is not None:
            doc.layersChanged.connect(self.rebuild)
            doc.historyChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        self._building = True
        self.list.clear()
        d = self.doc
        if d is not None:
            n = len(d.layers)
            for i in range(n - 1, -1, -1):
                L = d.layers[i]
                is_text = L.text_item() is not None
                it = QListWidgetItem(QIcon(thumb(L.image, text_badge=is_text)), L.name)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if L.visible else Qt.Unchecked)
                tip = f"{L.name}\nBlend: {L.blend}, opacity {round(L.opacity / 2.55)}%"
                if is_text:
                    tip += "\nText layer: click the text with the Text tool to edit it"
                it.setToolTip(tip)
                self.list.addItem(it)
            self.list.setCurrentRow(n - 1 - d.active)
        self._building = False

    def _row(self, row):
        if self._building or self.doc is None or row < 0:
            return
        self.app.commit_tool()
        self.doc.set_active(len(self.doc.layers) - 1 - row)

    def _item_changed(self, it):
        if self._building or self.doc is None:
            return
        i = len(self.doc.layers) - 1 - self.list.row(it)
        vis = it.checkState() == Qt.Checked
        L = self.doc.layers[i]
        if L.visible != vis:
            self.app.commit_tool()
            L.visible = vis
            self.doc.invalidate()
            self.doc.commit("Layer Shown" if vis else "Layer Hidden", "layer_props")


# --- History ---
class HistoryPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.doc = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.list = QListWidget()
        self.list.setIconSize(QSize(18, 18))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(small_button("undo", "Undo (Ctrl+Z)", lambda: app.action("undo")))
        row.addWidget(small_button("redo", "Redo (Ctrl+Y)", lambda: app.action("redo")))
        row.addStretch(1)
        lay.addLayout(row)
        self.list.itemClicked.connect(self._clicked)
        self._building = False

    def set_doc(self, doc):
        if self.doc is not None:
            try:
                self.doc.historyChanged.disconnect(self.rebuild)
            except (RuntimeError, TypeError):
                pass
        self.doc = doc
        if doc is not None:
            doc.historyChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        self._building = True
        self.list.clear()
        d = self.doc
        if d is not None:
            dim = self.palette().color(QPalette.Disabled, QPalette.Text)
            for i, h in enumerate(d.history):
                it = QListWidgetItem(icons.icon(h.icon), h.name)
                if i > d.hindex:
                    it.setForeground(dim)
                self.list.addItem(it)
            self.list.setCurrentRow(d.hindex)
            self.list.scrollToItem(self.list.currentItem())
        self._building = False

    def _clicked(self, it):
        if self._building or self.doc is None:
            return
        self.app.commit_tool(cancel=True)
        self.doc.goto(self.list.row(it))


# --- Navigator ---
class NavView(QWidget):
    """Thumbnail of the whole picture with a red box showing the part in view.
    Click or drag to move the view; the mouse wheel zooms."""

    def __init__(self, nav):
        super().__init__()
        self.nav = nav
        self.thumb = None
        self.drag_off = None
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setToolTip("Drag the red box (or click anywhere) to move round the picture; mouse wheel zooms")

    def image_rect(self):
        """Where the thumbnail sits in the widget, and the scale from picture to widget."""
        c = self.nav.canvas
        if c is None:
            return None, 1.0
        d = c.doc
        W, H = self.width() - 8, self.height() - 8
        s = min(W / max(1, d.w), H / max(1, d.h))
        w, h = d.w * s, d.h * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), s

    def view_rect(self):
        """The part of the picture in view, in widget coordinates."""
        c = self.nav.canvas
        r, s = self.image_rect()
        if r is None:
            return None
        vp = c.viewport()
        a = c.to_image(QPointF(0, 0))
        b = c.to_image(QPointF(vp.width(), vp.height()))
        img = QRectF(0, 0, c.doc.w, c.doc.h)
        vis = QRectF(a, b).intersected(img)
        if vis.isEmpty():
            return None
        return QRectF(r.x() + vis.x() * s, r.y() + vis.y() * s, vis.width() * s, vis.height() * s)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(QPalette.Base).darker(104))
        r, s = self.image_rect()
        if r is None:
            p.end()
            return
        p.fillRect(r, QBrush(checker_pixmap(4)))
        if self.thumb is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(r, self.thumb)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        v = self.view_rect()
        if v is not None and not (v.width() >= r.width() - 1 and v.height() >= r.height() - 1):
            # dim what is out of view, then the red box
            out = QPainterPath(); out.addRect(r)
            box = QPainterPath(); box.addRect(v)
            p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 70)); p.drawPath(out.subtracted(box))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 200), 3)); p.drawRect(v)
            p.setPen(QPen(QColor(220, 40, 40), 1.6)); p.drawRect(v)
        p.end()

    def _to_image(self, pos):
        r, s = self.image_rect()
        return QPointF((pos.x() - r.x()) / s, (pos.y() - r.y()) / s)

    def mousePressEvent(self, e):
        c = self.nav.canvas
        if c is None or e.button() != Qt.LeftButton:
            return
        v = self.view_rect()
        pos = e.position()
        if v is not None and v.contains(pos):
            self.drag_off = pos - v.center()            # grab the box where you clicked it
        else:
            self.drag_off = QPointF(0, 0)               # jump the box's centre to the click
        self._move_to(pos)

    def mouseMoveEvent(self, e):
        if self.drag_off is not None and (e.buttons() & Qt.LeftButton):
            self._move_to(e.position())
        else:
            v = self.view_rect()
            self.setCursor(Qt.SizeAllCursor if v is not None and v.contains(e.position()) else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.drag_off = None

    def _move_to(self, pos):
        c = self.nav.canvas
        c.center_on(self._to_image(pos - self.drag_off))
        self.update()

    def wheelEvent(self, e):
        c = self.nav.canvas
        if c is None:
            return
        if e.angleDelta().y() > 0:
            c.zoom_in()
        elif e.angleDelta().y() < 0:
            c.zoom_out()
        e.accept()


class NavigatorPanel(QWidget):
    """Navigator window: picture thumbnail with the view box, plus zoom controls."""
    THUMB = 480       # longest side of the thumbnail kept in memory

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.canvas = None
        self.doc = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.view = NavView(self)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setSpacing(2)

        def btn(text, tip, fn):
            b = QToolButton(); b.setText(text); b.setAutoRaise(True); b.setToolTip(tip)
            b.clicked.connect(fn)
            return b
        row.addWidget(btn("−", "Zoom out", lambda: self.canvas and self.canvas.zoom_out()))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setToolTip("Zoom")
        self.slider.valueChanged.connect(self._slider)
        row.addWidget(self.slider, 1)
        row.addWidget(btn("+", "Zoom in", lambda: self.canvas and self.canvas.zoom_in()))
        self.pct = QLabel("100%")
        self.pct.setMinimumWidth(44)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.pct)
        row.addWidget(btn("Fit", "Fit the whole picture in the window", lambda: self.canvas and self.canvas.best_fit()))
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh_thumb)
        self._dirty = True

    # --- Following the canvas ---
    def set_canvas(self, canvas):
        if canvas is self.canvas:
            return
        if self.canvas is not None:
            for sig, fn in self._conns:
                try:
                    sig.disconnect(fn)
                except (RuntimeError, TypeError):
                    pass
            self.canvas.viewport().removeEventFilter(self)
        self.canvas = canvas
        self.doc = canvas.doc if canvas is not None else None
        self._conns = []
        if canvas is not None:
            d = canvas.doc
            self._conns = [(canvas.zoomChanged, self._zoom_changed),
                           (canvas.horizontalScrollBar().valueChanged, self._view_moved),
                           (canvas.verticalScrollBar().valueChanged, self._view_moved),
                           (d.changed, self._doc_changed),
                           (d.sizeChanged, self._size_changed),
                           (d.layersChanged, self._size_changed)]
            for sig, fn in self._conns:
                sig.connect(fn)
            canvas.viewport().installEventFilter(self)
            self._zoom_changed(canvas.zoom)
        self.view.thumb = None
        self._dirty = True
        self._schedule(0)
        self.view.update()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize:
            self.view.update()
        return False

    def _view_moved(self, *_):
        self.view.update()

    def _zoom_changed(self, z):
        self.pct.setText(f"{round(z * 100):d}%" if z >= 0.1 else f"{z * 100:.1f}%")
        from .canvas import ZOOM_LEVELS
        lo, hi = math.log(ZOOM_LEVELS[0]), math.log(ZOOM_LEVELS[-1])
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((math.log(max(z, 1e-6)) - lo) / (hi - lo) * 1000)))
        self.slider.blockSignals(False)
        self.view.update()

    def _slider(self, v):
        if self.canvas is None:
            return
        from .canvas import ZOOM_LEVELS
        lo, hi = math.log(ZOOM_LEVELS[0]), math.log(ZOOM_LEVELS[-1])
        self.canvas.set_zoom(math.exp(lo + (hi - lo) * v / 1000.0))

    def _doc_changed(self, *_):
        self._dirty = True
        self._schedule(250)

    def _size_changed(self, *_):
        self._dirty = True
        self._schedule(50)

    def _schedule(self, ms):
        if not self._timer.isActive():
            self._timer.start(ms)

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self._schedule(0)

    def _refresh_thumb(self):
        # only work on it while it can be seen
        if not self.isVisible() or self.doc is None:
            return
        d = self.doc
        img = d.composite()
        s = min(1.0, self.THUMB / max(d.w, d.h))
        if s < 1.0:
            self.view.thumb = img.scaled(max(1, round(d.w * s)), max(1, round(d.h * s)),
                                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self.view.thumb = QImage(img)
        self._dirty = False
        self.view.update()
