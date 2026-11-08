"""Dialogs: new image, resize, canvas size, layer properties, effect parameters, curves, levels."""
import math
import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QBrush, QIcon, QImage
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel,
                               QSpinBox, QDoubleSpinBox, QSlider, QCheckBox, QComboBox, QDialogButtonBox,
                               QPushButton, QRadioButton, QButtonGroup, QGroupBox, QLineEdit, QWidget,
                               QToolButton, QSizePolicy, QFileDialog, QStackedWidget)

from .document import BLEND_MODES


def buttons(dlg, reset=None):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    if reset is not None:
        b = bb.addButton("Reset", QDialogButtonBox.ResetRole)
        b.clicked.connect(reset)
    return bb


class SliderSpin(QWidget):
    valueChanged = Signal(object)

    def __init__(self, lo, hi, val, decimals=0):
        super().__init__()
        self.scale = 10 ** decimals
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
        if decimals:
            self.spin = QDoubleSpinBox(); self.spin.setDecimals(decimals)
            self.spin.setSingleStep(1 / self.scale)
        else:
            self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setMinimumWidth(70)
        h.addWidget(self.slider, 1)
        h.addWidget(self.spin)
        self.set_value(val)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def set_value(self, v):
        self.spin.blockSignals(True); self.slider.blockSignals(True)
        self.spin.setValue(v)
        self.slider.setValue(int(round(v * self.scale)))
        self.spin.blockSignals(False); self.slider.blockSignals(False)

    def value(self):
        return self.spin.value()

    def _from_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v / self.scale if self.scale > 1 else v)
        self.spin.blockSignals(False)
        self.valueChanged.emit(self.value())

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.valueChanged.emit(self.value())


# ======================================================================= effect dialog
class EffectDialog(QDialog):
    """Generic parameter dialog; calls preview(params) live."""

    def __init__(self, parent, title, params, preview, initial=None, icon=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        if icon:
            self.setWindowIcon(icon)
        self.spec = params
        self.preview_fn = preview
        self.widgets = {}
        lay = QVBoxLayout(self)
        form = QGridLayout()
        lay.addLayout(form)
        init = initial or {}
        row = 0
        for spec in params:
            key, label, kind, default = spec[:4]
            val = init.get(key, default)
            form.addWidget(QLabel(label), row, 0, 1, 2)
            row += 1
            if kind in ("int", "angle"):
                w = SliderSpin(spec[4], spec[5], val)
                w.valueChanged.connect(self.schedule)
            elif kind == "float":
                w = SliderSpin(spec[4], spec[5], val, 2)
                w.valueChanged.connect(self.schedule)
            elif kind == "bool":
                w = QCheckBox(label); w.setChecked(bool(val))
                form.itemAtPosition(row - 1, 0).widget().hide()
                w.toggled.connect(self.schedule)
            elif kind == "choice":
                w = QComboBox(); w.addItems(spec[4]); w.setCurrentText(val)
                w.currentTextChanged.connect(self.schedule)
            elif kind == "seed":
                w = QPushButton(f"Reseed ({val})")
                w.seed = val
                def reseed(_=False, b=w):
                    import random
                    b.seed = random.randint(0, 99999)
                    b.setText(f"Reseed ({b.seed})")
                    self.schedule()
                w.clicked.connect(reseed)
            else:
                continue
            self.widgets[key] = (kind, w)
            form.addWidget(w, row, 0, 1, 2)
            row += 1
        lay.addStretch(1)
        lay.addWidget(buttons(self, self.reset))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._run)
        self.setMinimumWidth(360)
        QTimer.singleShot(0, self._run)

    def reset(self):
        for spec in self.spec:
            key, kind, default = spec[0], spec[2], spec[3]
            if key not in self.widgets:
                continue
            w = self.widgets[key][1]
            if kind in ("int", "angle", "float"):
                w.set_value(default)
            elif kind == "bool":
                w.setChecked(bool(default))
            elif kind == "choice":
                w.setCurrentText(default)
        self.schedule()

    def values(self):
        out = {}
        for key, (kind, w) in self.widgets.items():
            if kind in ("int", "angle", "float"):
                out[key] = w.value()
            elif kind == "bool":
                out[key] = w.isChecked()
            elif kind == "choice":
                out[key] = w.currentText()
            elif kind == "seed":
                out[key] = w.seed
        return out

    def schedule(self, *_):
        self.timer.start(70)

    def _run(self):
        QApplication = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.preview_fn(self.values())
        finally:
            QApplication.restoreOverrideCursor()


# ======================================================================= curves
class CurveWidget(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(280, 280)
        self.points = {k: [(0, 0), (255, 255)] for k in ("lum", "r", "g", "b")}
        self.mode = "luminosity"
        self.active = {"r": True, "g": True, "b": True}
        self.drag = None
        self.hist = None

    def channels(self):
        if self.mode == "luminosity":
            return ["lum"]
        return [k for k in ("r", "g", "b") if self.active[k]]

    def _map(self, x, y):
        m = 10
        W, H = self.width() - 2 * m, self.height() - 2 * m
        return QPointF(m + x / 255 * W, m + (255 - y) / 255 * H)

    def _unmap(self, pt):
        m = 10
        W, H = self.width() - 2 * m, self.height() - 2 * m
        x = (pt.x() - m) / W * 255
        y = 255 - (pt.y() - m) / H * 255
        return max(0, min(255, round(x))), max(0, min(255, round(y)))

    def paintEvent(self, e):
        from .effects import curve_lut
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self._map(0, 255), self._map(255, 0))
        p.fillRect(r, self.palette().base())
        if self.hist is not None:
            h = self.hist / max(self.hist.max(), 1)
            p.setPen(Qt.NoPen); p.setBrush(QColor(128, 128, 128, 70))
            path = QPainterPath(self._map(0, 0))
            for i, v in enumerate(h):
                path.lineTo(self._map(i, v * 255))
            path.lineTo(self._map(255, 0)); p.drawPath(path)
        p.setPen(QPen(QColor(128, 128, 128, 90), 1))
        for i in range(1, 4):
            v = i * 255 / 4
            p.drawLine(self._map(v, 0), self._map(v, 255)); p.drawLine(self._map(0, v), self._map(255, v))
        p.drawLine(self._map(0, 0), self._map(255, 255))
        cols = {"lum": self.palette().text().color(), "r": QColor(220, 40, 40), "g": QColor(40, 170, 40), "b": QColor(40, 90, 230)}
        chans = ["lum"] if self.mode == "luminosity" else ["r", "g", "b"]
        for k in chans:
            lut = curve_lut(self.points[k])
            p.setPen(QPen(cols[k], 2 if k in self.channels() else 1))
            path = QPainterPath(self._map(0, lut[0] * 255))
            for i in range(1, 256):
                path.lineTo(self._map(i, lut[i] * 255))
            p.setBrush(Qt.NoBrush); p.drawPath(path)
            if k in self.channels():
                p.setBrush(QColor(255, 255, 255))
                for (x, y) in self.points[k]:
                    p.drawEllipse(self._map(x, y), 4, 4)
        p.setPen(self.palette().text().color()); p.setBrush(Qt.NoBrush); p.drawRect(r)

    def mousePressEvent(self, e):
        x, y = self._unmap(e.position())
        chans = self.channels()
        if not chans:
            return
        pts = self.points[chans[0]]
        hit = None
        for i, (px, py) in enumerate(pts):
            q = self._map(px, py)
            if abs(q.x() - e.position().x()) < 7 and abs(q.y() - e.position().y()) < 7:
                hit = i
        if e.button() == Qt.RightButton:
            if hit is not None and len(pts) > 2:
                for k in chans:
                    if hit < len(self.points[k]):
                        del self.points[k][hit]
                self.changed.emit(); self.update()
            return
        if hit is None:
            if any(px == x for px, _ in pts):
                return
            for k in chans:
                if not any(px == x for px, _ in self.points[k]):
                    self.points[k].append((x, y))
                    self.points[k].sort()
            hit = pts.index((x, y))
            self.changed.emit()
        self.drag = hit
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag is None:
            return
        x, y = self._unmap(e.position())
        for k in self.channels():
            pts = self.points[k]
            i = min(self.drag, len(pts) - 1)
            lo = pts[i - 1][0] + 1 if i > 0 else 0
            hi = pts[i + 1][0] - 1 if i < len(pts) - 1 else 255
            pts[i] = (max(lo, min(hi, x)), y)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None


class CurvesDialog(QDialog):
    def __init__(self, parent, preview, hist=None, initial=None):
        super().__init__(parent)
        self.setWindowTitle("Curves")
        self.preview_fn = preview
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Transfer map:"))
        self.mode = QComboBox(); self.mode.addItems(["Luminosity", "RGB"])
        top.addWidget(self.mode); top.addStretch(1)
        lay.addLayout(top)
        self.curve = CurveWidget()
        self.curve.hist = hist
        if initial:
            self.curve.points = {k: list(v) for k, v in initial["points"].items()}
            self.mode.setCurrentText("RGB" if initial["mode"] == "rgb" else "Luminosity")
            self.curve.mode = initial["mode"]
        lay.addWidget(self.curve, 1)
        row = QHBoxLayout()
        self.checks = {}
        for k, name in (("r", "Red"), ("g", "Green"), ("b", "Blue")):
            c = QCheckBox(name); c.setChecked(True)
            c.toggled.connect(lambda v, k=k: self._toggle(k, v))
            self.checks[k] = c; row.addWidget(c)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Click to add a point, drag to move, right-click to remove."))
        lay.addWidget(buttons(self, self.reset))
        self.mode.currentTextChanged.connect(self._mode)
        self.curve.changed.connect(self.schedule)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self._run)
        self._mode(self.mode.currentText())

    def _toggle(self, k, v):
        self.curve.active[k] = v
        self.curve.update()

    def _mode(self, t):
        self.curve.mode = "rgb" if t == "RGB" else "luminosity"
        for c in self.checks.values():
            c.setVisible(t == "RGB")
        self.curve.update()
        self.schedule()

    def reset(self):
        self.curve.points = {k: [(0, 0), (255, 255)] for k in ("lum", "r", "g", "b")}
        self.curve.update()
        self.schedule()

    def values(self):
        return {"mode": self.curve.mode, "points": {k: list(v) for k, v in self.curve.points.items()}}

    def schedule(self):
        self.timer.start(60)

    def _run(self):
        self.preview_fn(self.values())


class Histogram(QWidget):
    def __init__(self, hist):
        super().__init__()
        self.hist = hist
        self.setMinimumSize(256, 90)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().base())
        if self.hist is None:
            return
        cols = [QColor(220, 40, 40, 120), QColor(40, 170, 40, 120), QColor(40, 90, 230, 120)]
        mx = max(1, max(h.max() for h in self.hist))
        W, H = self.width(), self.height()
        for h, c in zip(self.hist, cols):
            path = QPainterPath(QPointF(0, H))
            for i, v in enumerate(h):
                path.lineTo(QPointF(i / 255 * W, H - v / mx * H))
            path.lineTo(QPointF(W, H))
            p.setPen(Qt.NoPen); p.setBrush(c); p.drawPath(path)


class LevelsDialog(QDialog):
    def __init__(self, parent, preview, hist=None, auto_values=None):
        super().__init__(parent)
        self.setWindowTitle("Levels")
        self.preview_fn = preview
        self.auto_values = auto_values
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Input histogram"))
        lay.addWidget(Histogram(hist))
        g = QGridLayout()
        self.in_lo = QSpinBox(); self.in_lo.setRange(0, 254)
        self.in_hi = QSpinBox(); self.in_hi.setRange(1, 255); self.in_hi.setValue(255)
        self.gamma = QDoubleSpinBox(); self.gamma.setRange(0.1, 10); self.gamma.setSingleStep(0.05); self.gamma.setValue(1.0)
        self.out_lo = QSpinBox(); self.out_lo.setRange(0, 255)
        self.out_hi = QSpinBox(); self.out_hi.setRange(0, 255); self.out_hi.setValue(255)
        g.addWidget(QLabel("Input black"), 0, 0); g.addWidget(self.in_lo, 0, 1)
        g.addWidget(QLabel("Input white"), 0, 2); g.addWidget(self.in_hi, 0, 3)
        g.addWidget(QLabel("Gamma"), 1, 0); g.addWidget(self.gamma, 1, 1)
        g.addWidget(QLabel("Output black"), 2, 0); g.addWidget(self.out_lo, 2, 1)
        g.addWidget(QLabel("Output white"), 2, 2); g.addWidget(self.out_hi, 2, 3)
        lay.addLayout(g)
        row = QHBoxLayout()
        self.checks = {}
        for k, name in (("r", "Red"), ("g", "Green"), ("b", "Blue")):
            c = QCheckBox(name); c.setChecked(True); c.toggled.connect(self.schedule)
            self.checks[k] = c; row.addWidget(c)
        auto = QPushButton("Auto"); auto.clicked.connect(self.auto)
        row.addStretch(1); row.addWidget(auto)
        lay.addLayout(row)
        lay.addWidget(buttons(self, self.reset))
        for w in (self.in_lo, self.in_hi, self.out_lo, self.out_hi):
            w.valueChanged.connect(self.schedule)
        self.gamma.valueChanged.connect(self.schedule)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self._run)

    def auto(self):
        if self.auto_values:
            lo, hi = self.auto_values
            self.in_lo.setValue(lo); self.in_hi.setValue(hi)

    def reset(self):
        self.in_lo.setValue(0); self.in_hi.setValue(255); self.gamma.setValue(1.0)
        self.out_lo.setValue(0); self.out_hi.setValue(255)

    def values(self):
        v = {"in_lo": self.in_lo.value(), "in_hi": max(self.in_hi.value(), self.in_lo.value() + 1),
             "gamma": self.gamma.value(), "out_lo": self.out_lo.value(), "out_hi": self.out_hi.value()}
        for k, c in self.checks.items():
            v[k] = c.isChecked()
        return v

    def schedule(self, *_):
        self.timer.start(60)

    def _run(self):
        self.preview_fn(self.values())


# ======================================================================= image dialogs
class NewImageDialog(QDialog):
    def __init__(self, parent, w, h):
        super().__init__(parent)
        self.setWindowTitle("New Image")
        lay = QVBoxLayout(self)
        g = QGroupBox("Pixel size")
        f = QFormLayout(g)
        self.keep = QCheckBox("Maintain aspect ratio")
        self.w = QSpinBox(); self.w.setRange(1, 32000); self.w.setValue(w); self.w.setSuffix(" px")
        self.h = QSpinBox(); self.h.setRange(1, 32000); self.h.setValue(h); self.h.setSuffix(" px")
        f.addRow(self.keep)
        f.addRow("Width:", self.w)
        f.addRow("Height:", self.h)
        lay.addWidget(g)
        g2 = QGroupBox("Background")
        v = QVBoxLayout(g2)
        self.bg = QButtonGroup(self)
        for i, t in enumerate(("White", "Secondary colour", "Transparent")):
            r = QRadioButton(t); v.addWidget(r); self.bg.addButton(r, i)
        self.bg.button(0).setChecked(True)
        lay.addWidget(g2)
        self.info = QLabel()
        lay.addWidget(self.info)
        lay.addWidget(buttons(self))
        self.ratio = w / h
        self.w.valueChanged.connect(self._w)
        self.h.valueChanged.connect(self._h)
        self._info()

    def _w(self, v):
        if self.keep.isChecked():
            self.h.blockSignals(True); self.h.setValue(max(1, round(v / self.ratio))); self.h.blockSignals(False)
        else:
            self.ratio = v / self.h.value()
        self._info()

    def _h(self, v):
        if self.keep.isChecked():
            self.w.blockSignals(True); self.w.setValue(max(1, round(v * self.ratio))); self.w.blockSignals(False)
        else:
            self.ratio = self.w.value() / v
        self._info()

    def _info(self):
        mb = self.w.value() * self.h.value() * 4 / (1024 * 1024)
        self.info.setText(f"New size: {mb:.1f} MB")


class ResizeDialog(QDialog):
    def __init__(self, parent, w, h, title="Resize Image"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.w0, self.h0 = w, h
        lay = QVBoxLayout(self)
        f = self.form = QFormLayout()
        self.resample = QComboBox(); self.resample.addItems(["Smooth (best quality)", "Nearest neighbour"])
        f.addRow("Resampling:", self.resample)
        lay.addLayout(f)
        self.by_pct = QRadioButton("By percentage:")
        self.by_abs = QRadioButton("By absolute size")
        self.by_pct.setChecked(True)
        self.pct = QSpinBox(); self.pct.setRange(1, 3200); self.pct.setValue(100); self.pct.setSuffix(" %")
        row = QHBoxLayout(); row.addWidget(self.by_pct); row.addWidget(self.pct); row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(self.by_abs)
        g = QFormLayout()
        self.keep = QCheckBox("Maintain aspect ratio"); self.keep.setChecked(True)
        self.w = QSpinBox(); self.w.setRange(1, 32000); self.w.setValue(w); self.w.setSuffix(" px")
        self.h = QSpinBox(); self.h.setRange(1, 32000); self.h.setValue(h); self.h.setSuffix(" px")
        g.addRow(self.keep); g.addRow("Width:", self.w); g.addRow("Height:", self.h)
        lay.addLayout(g)
        self.info = QLabel(); lay.addWidget(self.info)
        lay.addWidget(buttons(self))
        self.pct.valueChanged.connect(self._pct)
        self.w.valueChanged.connect(self._w)
        self.h.valueChanged.connect(self._h)
        self.by_pct.toggled.connect(self._mode)
        self._mode(); self._info()

    def _mode(self, *_):
        p = self.by_pct.isChecked()
        self.pct.setEnabled(p)
        for x in (self.w, self.h, self.keep):
            x.setEnabled(not p)

    def _set(self, w, h):
        for sb, v in ((self.w, w), (self.h, h)):
            sb.blockSignals(True); sb.setValue(max(1, int(round(v)))); sb.blockSignals(False)
        self._info()

    def _pct(self, v):
        self._set(self.w0 * v / 100, self.h0 * v / 100)

    def _w(self, v):
        if self.keep.isChecked():
            self._set(v, v * self.h0 / self.w0)
        self._info()

    def _h(self, v):
        if self.keep.isChecked():
            self._set(v * self.w0 / self.h0, v)
        self._info()

    def _info(self):
        self.info.setText(f"Original: {self.w0} × {self.h0}    New: {self.w.value()} × {self.h.value()}")

    def result_size(self):
        return self.w.value(), self.h.value()

    def smooth(self):
        return self.resample.currentIndex() == 0


class CanvasSizeDialog(ResizeDialog):
    def __init__(self, parent, w, h):
        super().__init__(parent, w, h, "Canvas Size")
        self.form.setRowVisible(0, False)
        lay = self.layout()
        box = QGroupBox("Anchor")
        grid = QGridLayout(box)
        self.anchor = QButtonGroup(self)
        arrows = ["↖", "↑", "↗", "←", "•", "→", "↙", "↓", "↘"]
        for i in range(9):
            b = QToolButton(); b.setCheckable(True); b.setText(arrows[i]); b.setFixedSize(30, 30)
            self.anchor.addButton(b, i); grid.addWidget(b, i // 3, i % 3)
        self.anchor.button(4).setChecked(True)
        lay.insertWidget(lay.count() - 1, box)

    def anchor_offset(self, nw, nh):
        i = self.anchor.checkedId()
        ax, ay = i % 3, i // 3
        dx = [0, (nw - self.w0) // 2, nw - self.w0][ax]
        dy = [0, (nh - self.h0) // 2, nh - self.h0][ay]
        return dx, dy


class LayerPropertiesDialog(QDialog):
    def __init__(self, parent, layer, on_change):
        super().__init__(parent)
        self.setWindowTitle("Layer Properties")
        self.on_change = on_change
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.name = QLineEdit(layer.name)
        self.visible = QCheckBox("Visible"); self.visible.setChecked(layer.visible)
        self.blend = QComboBox(); self.blend.addItems([n for n, _ in BLEND_MODES]); self.blend.setCurrentText(layer.blend)
        self.opacity = SliderSpin(0, 255, layer.opacity)
        f.addRow("Name:", self.name)
        f.addRow("", self.visible)
        f.addRow("Blend mode:", self.blend)
        f.addRow("Opacity:", self.opacity)
        lay.addLayout(f)
        lay.addWidget(buttons(self))
        self.visible.toggled.connect(self._changed)
        self.blend.currentTextChanged.connect(self._changed)
        self.opacity.valueChanged.connect(self._changed)
        self.name.textChanged.connect(self._changed)
        self.setMinimumWidth(380)

    def values(self):
        return dict(name=self.name.text(), visible=self.visible.isChecked(),
                    blend=self.blend.currentText(), opacity=int(self.opacity.value()))

    def _changed(self, *_):
        self.on_change(self.values())


def split_rects(w, h, mode, a, b, keep_edges=True):
    """Pieces as (row, col, x, y, width, height).
    mode 'grid': a columns x b rows, sizes shared out evenly.
    mode 'size': pieces a px wide and b px high; leftover strips kept if keep_edges."""
    if mode == "grid":
        cols, rows = max(1, min(a, w)), max(1, min(b, h))
        xs = [round(i * w / cols) for i in range(cols + 1)]
        ys = [round(j * h / rows) for j in range(rows + 1)]
    else:
        tw, th = max(1, a), max(1, b)
        xs = list(range(0, w, tw)) + [w]
        ys = list(range(0, h, th)) + [h]
        if not keep_edges:
            if w % tw:
                xs = xs[:-1]
            if h % th:
                ys = ys[:-1]
            if len(xs) < 2 or len(ys) < 2:
                return []
    out = []
    for r in range(len(ys) - 1):
        for c in range(len(xs) - 1):
            out.append((r, c, xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]))
    return out


class SplitPreview(QWidget):
    def __init__(self, img):
        super().__init__()
        self.img = img
        self.rects = []
        self.setMinimumSize(260, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_rects(self, rects):
        self.rects = rects
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        iw, ih = self.img.width(), self.img.height()
        sc = min((self.width() - 8) / iw, (self.height() - 8) / ih)
        dw, dh = iw * sc, ih * sc
        ox, oy = (self.width() - dw) / 2, (self.height() - dh) / 2
        tile = 8
        for y in range(int(oy), int(oy + dh), tile):
            for x in range(int(ox), int(ox + dw), tile):
                if ((x - int(ox)) // tile + (y - int(oy)) // tile) % 2:
                    p.fillRect(x, y, min(tile, int(ox + dw) - x), min(tile, int(oy + dh) - y), QColor(204, 204, 204))
                else:
                    p.fillRect(x, y, min(tile, int(ox + dw) - x), min(tile, int(oy + dh) - y), QColor(255, 255, 255))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(ox, oy, dw, dh), self.img)
        if not self.rects:
            return
        p.setBrush(Qt.NoBrush)
        for pen in (QPen(QColor(255, 255, 255), 3), QPen(QColor(220, 30, 30), 1.4)):
            p.setPen(pen)
            for (_, _, x, y, w, h) in self.rects:
                p.drawRect(QRectF(ox + x * sc, oy + y * sc, w * sc, h * sc))
        if len(self.rects) <= 400:
            p.setPen(QColor(220, 30, 30))
            f = p.font(); f.setPointSizeF(max(6.5, f.pointSizeF() * 0.8)); p.setFont(f)
            for i, (_, _, x, y, w, h) in enumerate(self.rects):
                r = QRectF(ox + x * sc, oy + y * sc, w * sc, h * sc)
                if r.width() > 18 and r.height() > 12:
                    p.drawText(r, Qt.AlignCenter, str(i + 1))


class SplitImageDialog(QDialog):
    FORMATS = [("PNG", "png"), ("JPEG", "jpg"), ("WebP", "webp"), ("BMP", "bmp"), ("TIFF", "tiff")]

    def __init__(self, parent, img, name, has_selection, settings):
        super().__init__(parent)
        self.setWindowTitle("Split Image")
        self.img = img
        self.settings = settings
        W, H = img.width(), img.height()
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        lay.addLayout(left, 0)

        g = QGroupBox("Split into")
        f = QGridLayout(g)
        self.mode = QButtonGroup(self)
        self.r_grid = QRadioButton("Rows and columns")
        self.r_size = QRadioButton("Pieces of a set size")
        self.mode.addButton(self.r_grid, 0); self.mode.addButton(self.r_size, 1)
        self.cols = QSpinBox(); self.cols.setRange(1, min(500, W)); self.cols.setValue(min(int(settings.value("split_cols", 2)), W))
        self.rows = QSpinBox(); self.rows.setRange(1, min(500, H)); self.rows.setValue(min(int(settings.value("split_rows", 2)), H))
        self.tw = QSpinBox(); self.tw.setRange(1, W); self.tw.setSuffix(" px"); self.tw.setValue(max(1, W // 2))
        self.th = QSpinBox(); self.th.setRange(1, H); self.th.setSuffix(" px"); self.th.setValue(max(1, H // 2))
        self.keep = QCheckBox("Keep smaller pieces left over at the edges")
        self.keep.setChecked(True)
        f.addWidget(self.r_grid, 0, 0, 1, 3)
        f.addWidget(QLabel("Columns (across):"), 1, 1); f.addWidget(self.cols, 1, 2)
        f.addWidget(QLabel("Rows (down):"), 2, 1); f.addWidget(self.rows, 2, 2)
        f.addWidget(self.r_size, 3, 0, 1, 3)
        f.addWidget(QLabel("Piece width:"), 4, 1); f.addWidget(self.tw, 4, 2)
        f.addWidget(QLabel("Piece height:"), 5, 1); f.addWidget(self.th, 5, 2)
        f.addWidget(self.keep, 6, 1, 1, 2)
        f.setColumnMinimumWidth(0, 14)
        left.addWidget(g)
        (self.r_size if settings.value("split_mode", "grid") == "size" else self.r_grid).setChecked(True)

        g2 = QGroupBox("Then")
        v = QGridLayout(g2)
        self.out = QButtonGroup(self)
        self.o_open = QRadioButton("Open each piece as a new image")
        self.o_save = QRadioButton("Save the pieces to a folder")
        self.out.addButton(self.o_open, 0); self.out.addButton(self.o_save, 1)
        v.addWidget(self.o_open, 0, 0, 1, 3)
        v.addWidget(self.o_save, 1, 0, 1, 3)
        self.folder = QLineEdit(settings.value("split_folder", "") or "")
        self.browse = QPushButton("Browse...")
        self.browse.clicked.connect(self._browse)
        self.prefix = QLineEdit(name)
        self.fmt = QComboBox()
        for label, ext in self.FORMATS:
            self.fmt.addItem(label, ext)
        i = self.fmt.findData(settings.value("split_format", "png"))
        self.fmt.setCurrentIndex(max(0, i))
        v.addWidget(QLabel("Folder:"), 2, 1); v.addWidget(self.folder, 2, 2); v.addWidget(self.browse, 2, 3)
        v.addWidget(QLabel("File name:"), 3, 1); v.addWidget(self.prefix, 3, 2, 1, 2)
        v.addWidget(QLabel("File type:"), 4, 1); v.addWidget(self.fmt, 4, 2, 1, 2)
        self.example = QLabel(); self.example.setStyleSheet("color: gray")
        v.addWidget(self.example, 5, 1, 1, 3)
        v.setColumnMinimumWidth(0, 14)
        left.addWidget(g2)
        (self.o_save if settings.value("split_output", "open") == "save" else self.o_open).setChecked(True)

        if has_selection:
            note = QLabel("Only the selected area will be split.")
            note.setStyleSheet("color: gray")
            left.addWidget(note)
        left.addStretch(1)
        self.bb = buttons(self)
        left.addWidget(self.bb)

        right = QVBoxLayout()
        self.preview = SplitPreview(img)
        right.addWidget(self.preview, 1)
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignCenter)
        right.addWidget(self.info)
        lay.addLayout(right, 1)
        self.resize(760, 440)

        for wdg in (self.cols, self.rows, self.tw, self.th):
            wdg.valueChanged.connect(self._update)
        for wdg in (self.r_grid, self.r_size, self.keep, self.o_open, self.o_save):
            wdg.toggled.connect(self._update)
        self.folder.textChanged.connect(self._update)
        self.prefix.textChanged.connect(self._update)
        self.fmt.currentIndexChanged.connect(self._update)
        self._update()

    def _browse(self):
        from .filedialogs import choose_folder
        d = choose_folder(self, "Choose a Folder for the Pieces", self.folder.text())
        if d:
            self.folder.setText(d)

    def rects(self):
        if self.r_grid.isChecked():
            return split_rects(self.img.width(), self.img.height(), "grid", self.cols.value(), self.rows.value())
        return split_rects(self.img.width(), self.img.height(), "size", self.tw.value(), self.th.value(),
                           self.keep.isChecked())

    def file_name(self, r, c, rows, cols):
        nr, nc = len(str(rows)), len(str(cols))
        base = self.prefix.text().strip() or "piece"
        return f"{base}_r{r + 1:0{nr}d}_c{c + 1:0{nc}d}.{self.fmt.currentData()}"

    def _update(self, *_):
        grid = self.r_grid.isChecked()
        for wdg in (self.cols, self.rows):
            wdg.setEnabled(grid)
        for wdg in (self.tw, self.th, self.keep):
            wdg.setEnabled(not grid)
        save = self.o_save.isChecked()
        for wdg in (self.folder, self.browse, self.prefix, self.fmt):
            wdg.setEnabled(save)
        rs = self.rects()
        self.preview.set_rects(rs)
        if rs:
            rows = rs[-1][0] + 1
            cols = rs[-1][1] + 1
            sizes = {(w, h) for (_, _, _, _, w, h) in rs}
            if len(sizes) == 1:
                w, h = next(iter(sizes))
                size = f"each {w} × {h} px"
            else:
                size = f"{min(s[0] for s in sizes)}–{max(s[0] for s in sizes)} × {min(s[1] for s in sizes)}–{max(s[1] for s in sizes)} px"
            self.info.setText(f"{len(rs)} pieces ({cols} across × {rows} down), {size}")
            self.example.setText("e.g. " + self.file_name(0, 0, rows, cols))
        else:
            self.info.setText("No pieces: the piece size is bigger than the image")
            self.example.setText("")
        ok = bool(rs) and (not save or bool(self.folder.text().strip()))
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok)
        self.bb.button(QDialogButtonBox.Ok).setText("Split")

    def accept(self):
        s = self.settings
        s.setValue("split_mode", "grid" if self.r_grid.isChecked() else "size")
        s.setValue("split_cols", self.cols.value()); s.setValue("split_rows", self.rows.value())
        s.setValue("split_output", "save" if self.o_save.isChecked() else "open")
        s.setValue("split_folder", self.folder.text().strip())
        s.setValue("split_format", self.fmt.currentData())
        super().accept()
