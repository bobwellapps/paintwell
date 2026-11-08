"""Document model: layers, selection, compositing and snapshot-based history."""
import os
from PySide6.QtCore import QObject, Signal, Qt, QRect, QRectF, QPoint, QPointF, QSize
from PySide6.QtGui import QImage, QPainter, QPainterPath, QColor, QTransform

from .imaging import new_image, FMT

BLEND_MODES = [
    ("Normal", QPainter.CompositionMode_SourceOver),
    ("Multiply", QPainter.CompositionMode_Multiply),
    ("Additive", QPainter.CompositionMode_Plus),
    ("Colour Burn", QPainter.CompositionMode_ColorBurn),
    ("Colour Dodge", QPainter.CompositionMode_ColorDodge),
    ("Overlay", QPainter.CompositionMode_Overlay),
    ("Difference", QPainter.CompositionMode_Difference),
    ("Exclusion", QPainter.CompositionMode_Exclusion),
    ("Lighten", QPainter.CompositionMode_Lighten),
    ("Darken", QPainter.CompositionMode_Darken),
    ("Screen", QPainter.CompositionMode_Screen),
    ("Hard Light", QPainter.CompositionMode_HardLight),
    ("Soft Light", QPainter.CompositionMode_SoftLight),
]
BLEND = dict(BLEND_MODES)
MAX_HISTORY = 80


class TextItem:
    """A finished piece of text that can still be re-opened with the Text tool, as long as the
    pixels it covers haven't been painted over since."""

    def __init__(self, lines, opts, color, pos, rect, under, over):
        self.lines = list(lines)
        self.opts = dict(opts)        # font, font_size, font_style, text_align, antialias
        self.color = QColor(color)
        self.pos = pos                # QPointF: top-left of the text box
        self.rect = rect              # QRect covered by the text (with a margin), image coords
        self.under = under            # layer pixels in rect before the text was drawn
        self.over = over              # layer pixels in rect right after
        self._ok_key = None

    def still_editable(self, layer):
        img = layer.image
        if not img.rect().contains(self.rect):
            return False
        key = img.cacheKey()
        if key == self._ok_key:
            return True
        if key == getattr(self, "_bad_key", None):
            return False
        if self.rect == img.rect():
            ok = img == self.over               # a whole text layer
        else:
            ok = img.copy(self.rect) == self.over
        if ok:
            self._ok_key = key
        else:
            self._bad_key = key
        return ok


class Layer:
    def __init__(self, image, name, visible=True, opacity=255, blend="Normal", texts=None, text=None):
        self.image = image
        self.name = name
        self.visible = visible
        self.opacity = opacity
        self.blend = blend
        self.texts = list(texts) if texts else []   # TextItems still editable on this layer
        self.text = text      # TextItem when this is a text layer (its text stays editable)

    def text_item(self):
        """The layer's text, if it is a text layer that hasn't been painted on since."""
        if self.text is not None and self.text.still_editable(self):
            return self.text
        return None

    def snapshot(self):
        # QImage copies are implicitly shared; painting on one detaches it.
        return Layer(QImage(self.image), self.name, self.visible, self.opacity, self.blend, self.texts, self.text)


class State:
    def __init__(self, w, h, layers, active, selection):
        self.w, self.h = w, h
        self.layers = layers
        self.active = active
        self.selection = selection


class HistoryItem:
    def __init__(self, name, icon, state):
        self.name, self.icon, self.state = name, icon, state


class Document(QObject):
    changed = Signal(QRect)          # pixels changed (image coords); null rect = everything
    layersChanged = Signal()
    selectionChanged = Signal()
    sizeChanged = Signal()
    historyChanged = Signal()
    titleChanged = Signal()

    _untitled = 0

    def __init__(self, w=800, h=600, background=QColor(255, 255, 255), layers=None, path=None):
        super().__init__()
        self.w, self.h = w, h
        if layers is None:
            img = new_image(w, h, background if background is not None else None)
            layers = [Layer(img, "Background")]
        self.layers = layers
        self.active = len(layers) - 1
        self.selection = None            # None = no selection; QPainterPath otherwise
        self.guides = []                 # [["h" or "v", position in image pixels], ...] (not in history)
        self.path = path
        if path is None:
            Document._untitled += 1
            self.untitled_no = Document._untitled
        self._comp = None
        self.history = []
        self.hindex = -1
        self.saved_index = 0
        self.commit("Open Image" if path else "New Image", "image")
        self.saved_index = self.hindex

    # ------------------------------------------------------------ naming
    @property
    def title(self):
        if getattr(self, "display_name", None) and not self.path:
            return self.display_name
        if self.path:
            return os.path.basename(self.path)
        return "Untitled" if self.untitled_no == 1 else f"Untitled {self.untitled_no}"

    @property
    def modified(self):
        return self.hindex != self.saved_index

    def mark_saved(self):
        self.saved_index = self.hindex
        self.titleChanged.emit()

    # ------------------------------------------------------------ layers
    @property
    def layer(self):
        return self.layers[self.active]

    def rect(self):
        return QRect(0, 0, self.w, self.h)

    def set_active(self, i):
        i = max(0, min(len(self.layers) - 1, i))
        if i != self.active:
            self.active = i
            self.layersChanged.emit()

    # ------------------------------------------------------------ compositing
    def invalidate(self, rect=None):
        """Recompose the given region and notify views."""
        if rect is None or rect.isNull():
            rect = self.rect()
            self._comp = None
        rect = rect.intersected(self.rect())
        if self._comp is not None and not rect.isEmpty():
            self._compose(rect)
        self.changed.emit(rect)

    def composite(self):
        if self._comp is None or self._comp.width() != self.w or self._comp.height() != self.h:
            self._comp = new_image(self.w, self.h)
            self._compose(self.rect())
        return self._comp

    def _compose(self, rect):
        p = QPainter(self._comp)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(rect, Qt.transparent)
        for L in self.layers:
            if not L.visible or L.opacity == 0:
                continue
            p.setCompositionMode(BLEND.get(L.blend, QPainter.CompositionMode_SourceOver))
            p.setOpacity(L.opacity / 255.0)
            p.drawImage(rect.topLeft(), L.image, rect)
        p.end()

    def flattened(self):
        return QImage(self.composite()).copy()

    # ------------------------------------------------------------ selection
    def has_selection(self):
        return self.selection is not None

    def set_selection(self, path):
        self.selection = path
        self.selectionChanged.emit()

    def selection_bounds(self):
        if self.selection is None:
            return self.rect()
        return self.selection.boundingRect().toAlignedRect().intersected(self.rect())

    def clip(self, painter):
        if self.selection is not None:
            painter.setClipPath(self.selection)

    def selection_path_or_all(self):
        if self.selection is not None:
            return QPainterPath(self.selection)
        p = QPainterPath()
        p.addRect(QRectF(self.rect()))
        return p

    # ------------------------------------------------------------ history
    def _capture(self):
        return State(self.w, self.h, [L.snapshot() for L in self.layers], self.active,
                     QPainterPath(self.selection) if self.selection is not None else None)

    def commit(self, name, icon="image"):
        del self.history[self.hindex + 1:]
        if self.saved_index > self.hindex:
            self.saved_index = -10
        self.history.append(HistoryItem(name, icon, self._capture()))
        self.hindex += 1
        if len(self.history) > MAX_HISTORY:
            drop = len(self.history) - MAX_HISTORY
            del self.history[:drop]
            self.hindex -= drop
            self.saved_index -= drop
        self.historyChanged.emit()
        self.titleChanged.emit()

    def replace_last(self, name, icon="image"):
        """Update the newest history entry in place (used to merge a run of small changes)."""
        if self.saved_index == self.hindex:
            self.saved_index = -10
        self.history[self.hindex] = HistoryItem(name, icon, self._capture())
        self.historyChanged.emit()
        self.titleChanged.emit()

    def _apply_state(self, st):
        size_changed = (st.w, st.h) != (self.w, self.h)
        self.w, self.h = st.w, st.h
        self.layers = [L.snapshot() for L in st.layers]
        self.active = min(st.active, len(self.layers) - 1)
        self.selection = QPainterPath(st.selection) if st.selection is not None else None
        self._comp = None
        if size_changed:
            self.sizeChanged.emit()
        self.layersChanged.emit()
        self.selectionChanged.emit()
        self.invalidate()

    def revert(self):
        """Throw away uncommitted live changes (used to cancel previews)."""
        self._apply_state(self.history[self.hindex].state)

    def goto(self, index):
        index = max(0, min(len(self.history) - 1, index))
        self.hindex = index
        self._apply_state(self.history[index].state)
        self.historyChanged.emit()
        self.titleChanged.emit()

    def can_undo(self):
        return self.hindex > 0

    def can_redo(self):
        return self.hindex < len(self.history) - 1

    def undo(self):
        if self.can_undo():
            self.goto(self.hindex - 1)

    def redo(self):
        if self.can_redo():
            self.goto(self.hindex + 1)

    def committed_layer_image(self, index=None):
        """Layer pixels as of the last history entry (for live-preview tools)."""
        st = self.history[self.hindex].state
        i = self.active if index is None else index
        if i < len(st.layers):
            return QImage(st.layers[i].image)
        return QImage(self.layers[i].image)

    # ------------------------------------------------------------ structural ops
    def add_layer(self, image=None, name=None, index=None):
        if image is None:
            image = new_image(self.w, self.h)
        if name is None:
            name = f"Layer {len(self.layers) + 1}"
        if index is None:
            index = self.active + 1
        self.layers.insert(index, Layer(image, name))
        self.active = index
        self.layersChanged.emit()
        self.invalidate()

    def delete_layer(self):
        if len(self.layers) <= 1:
            return False
        del self.layers[self.active]
        self.active = max(0, self.active - 1)
        self.layersChanged.emit()
        self.invalidate()
        return True

    def duplicate_layer(self):
        L = self.layer
        c = Layer(L.image.copy(), L.name + " copy", L.visible, L.opacity, L.blend, L.texts)
        if L.text_item() is not None:
            t = L.text
            c.text = TextItem(t.lines, t.opts, t.color, QPointF(t.pos), QRect(t.rect), None, QImage(c.image))
        self.layers.insert(self.active + 1, c)
        self.active += 1
        self.layersChanged.emit()
        self.invalidate()

    def merge_down(self):
        if self.active == 0:
            return False
        top = self.layers[self.active]
        bot = self.layers[self.active - 1]
        img = bot.image.copy()
        if top.visible:
            p = QPainter(img)
            p.setCompositionMode(BLEND.get(top.blend))
            p.setOpacity(top.opacity / 255.0)
            p.drawImage(0, 0, top.image)
            p.end()
        bot.image = img
        del self.layers[self.active]
        self.active -= 1
        self.layersChanged.emit()
        self.invalidate()
        return True

    def move_layer(self, delta):
        j = self.active + delta
        if not (0 <= j < len(self.layers)):
            return False
        self.layers[self.active], self.layers[j] = self.layers[j], self.layers[self.active]
        self.active = j
        self.layersChanged.emit()
        self.invalidate()
        return True

    def flatten(self):
        img = self.flattened()
        self.layers = [Layer(img, "Background")]
        self.active = 0
        self.layersChanged.emit()
        self.invalidate()

    def transform_all(self, fn, new_w=None, new_h=None, selection_transform=None):
        """Apply fn(QImage)->QImage to every layer (resize, rotate, crop...)."""
        for L in self.layers:
            L.image = fn(L.image)
        if new_w is not None:
            self.w, self.h = new_w, new_h
        if selection_transform is not None and self.selection is not None:
            self.selection = selection_transform.map(self.selection)
        elif new_w is not None:
            self.selection = None
        self._comp = None
        self.sizeChanged.emit()
        self.layersChanged.emit()
        self.selectionChanged.emit()
        self.invalidate()
