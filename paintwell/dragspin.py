"""Click-and-drag on any number box to change its value.

Press the left button inside a number box and slide left or right: the value goes
down or up as you move.  Hold Shift for fine steps.  A plain click (no sliding)
still puts the text cursor in the box so you can type a number as before.

Big ranges such as brush width (1-2000 px) change in proportion to the current value,
so small brushes move one pixel at a time and big brushes move faster.
"""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QLineEdit, QApplication

THRESHOLD = 3      # pixels of movement before a press becomes a drag
PX_PER_STEP = 1.0  # one step per pixel for small ranges


class _DragState:
    __slots__ = ("spin", "x0", "last_x", "value", "dragging")

    def __init__(self, spin, x):
        self.spin = spin
        self.x0 = x
        self.last_x = x
        self.value = float(spin.value())
        self.dragging = False


def _proportional(spin):
    """True for wide, non-negative ranges, where steps should grow with the value."""
    step = spin.singleStep() or 1
    return spin.minimum() >= 0 and (spin.maximum() - spin.minimum()) / step > 300


def _advance(spin, value, px, fine):
    step = spin.singleStep() or 1
    sign = 1 if px > 0 else -1
    if _proportional(spin):
        rate = 0.002 if fine else 0.012
        for _ in range(abs(int(px))):
            value += sign * max(step * (0.25 if fine else 1.0), abs(value) * rate)
            value = max(spin.minimum(), min(spin.maximum(), value))
    else:
        value += px * step * (0.1 if fine else 1.0) / PX_PER_STEP
        value = max(spin.minimum(), min(spin.maximum(), value))
    return value


def _alive(w):
    try:
        import shiboken6
        return w is not None and shiboken6.isValid(w)
    except Exception:
        return w is not None


class DragSpinFilter(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None

    def _finish(self):
        """End a drag.  The release itself is always passed on to Qt: if it were swallowed,
        Qt would not notice that the pointer had moved on (to the canvas, say) during the
        drag, and the number box's left-right arrows would stay on screen."""
        st, self.state = self.state, None
        if st is not None and st.dragging and _alive(st.spin):
            le = st.spin.lineEdit()
            le.deselect()
            le.clearFocus()
        return False

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Enter:
            # show the left-right arrows over number boxes, as a hint that they can be dragged
            if isinstance(obj, QLineEdit) and not obj.property("_dragspin") \
                    and isinstance(obj.parent(), QAbstractSpinBox) and hasattr(obj.parent(), "value"):
                obj.setProperty("_dragspin", True)
                obj.setCursor(Qt.SizeHorCursor)
            return False
        if t == QEvent.MouseButtonPress:
            if self.state is not None:         # a drag that never saw its release: tidy up
                self._finish()
            if ev.button() == Qt.LeftButton and isinstance(obj, QLineEdit):
                spin = obj.parent()
                if isinstance(spin, QAbstractSpinBox) and spin.isEnabled() and not spin.isReadOnly() \
                        and hasattr(spin, "value"):
                    self.state = _DragState(spin, ev.globalPosition().x())
            return False
        if t == QEvent.MouseMove:
            st = self.state
            if st is None:
                return False
            if not (ev.buttons() & Qt.LeftButton) or not _alive(st.spin):
                self._finish()
                return False
            if obj is not st.spin.lineEdit():
                return False
            x = ev.globalPosition().x()
            if not st.dragging:
                if abs(x - st.x0) < THRESHOLD:
                    return False
                st.dragging = True
                st.last_x = x
                st.spin.lineEdit().deselect()
            px = x - st.last_x
            if abs(px) >= 1:
                fine = bool(ev.modifiers() & Qt.ShiftModifier)
                st.value = _advance(st.spin, st.value, px, fine)
                st.last_x = x
                spin = st.spin
                if isinstance(spin, QDoubleSpinBox):
                    spin.setValue(st.value)
                else:
                    spin.setValue(int(round(st.value)))
            return True
        if t == QEvent.MouseButtonRelease:
            if self.state is None or ev.button() != Qt.LeftButton:
                return False
            self._finish()
            return False
        if t in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate) and self.state is not None:
            self._finish()
        return False


def install(app):
    f = DragSpinFilter(app)
    app.installEventFilter(f)
    app._drag_spin_filter = f
    return f
