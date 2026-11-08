"""Main application window."""
import os
import math
import sys
import numpy as np
from PySide6.QtCore import Qt, QObject, Signal, QSize, QTimer, QSettings, QRect, QRectF, QPointF, QPoint, QByteArray, QUrl, QEventLoop
from PySide6.QtGui import (QAction, QActionGroup, QKeySequence, QIcon, QPainter, QColor, QImage, QPalette,
                           QPainterPath, QTransform, QPixmap, QFont, QDesktopServices, QGuiApplication)
from PySide6.QtWidgets import (QMainWindow, QWidget, QDockWidget, QToolBar, QTabBar, QStackedWidget, QLabel,
                               QStatusBar, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QToolButton, QMenu, QFileDialog,
                               QMessageBox, QInputDialog, QApplication, QVBoxLayout, QHBoxLayout, QFontComboBox,
                               QSizePolicy, QStyleFactory, QSlider, QFrame, QScrollArea, QWidgetAction, QGridLayout, QColorDialog)

from . import icons, icons_menu, imaging, fileio
from .document import Document, Layer
from .canvas import Canvas, ZOOM_LEVELS
from .tools import TOOL_CLASSES, SHAPES, MoveTool
from .panels import NavigatorPanel, ColorState, ColorsPanel, FitScrollArea, ToolsPanel, LayersPanel, HistoryPanel, thumb
from .effects import Source, ADJUSTMENTS, EFFECTS
from . import filedialogs
from .dialogs import (SplitImageDialog, NewImageDialog, ResizeDialog, CanvasSizeDialog, LayerPropertiesDialog, EffectDialog,
                      CurvesDialog, LevelsDialog, SliderSpin)

APP_NAME = "Paintwell"
LAYOUT_VERSION = 7  # bump to discard saved panel layouts after layout changes
VERSION = "1.2"

DEFAULT_OPTS = {
    "brush_width": 6, "hardness": 100, "blur_strength": 50, "sharpen_strength": 50,
    "smudge_strength": 60, "finger_paint": False, "lasso_width": 10, "erase_tolerance": 15, "crop_ratio": "free", "pupil_size": 50, "redeye_size": 24, "tone_range": "midtones",
    "exposure": 50, "protect_tones": True, "text_dir": "horizontal", "text_angle": 0.0, "letter_spacing": 0,
    "line_spacing": 100, "text_bend": 50, "text_outline": 0, "text_outline_color": "#000000", "text_shadow": False, "line_cap": "flat", "sponge_mode": "desaturate", "sponge_flow": 50, "vibrance": True,
    "darken_amount": 50, "edge_contrast": 10, "lasso_frequency": 57, "antialias": True, "selection_mode": "replace", "flood_mode": "contiguous",
    "tolerance": 50, "sampling": "layer", "gradient_type": "linear", "gradient_mode": "color",
    "picker_size": 1, "picker_after": "none", "font": "Sans Serif", "font_size": 24, "font_style": "",
    "text_align": "left", "shape_type": "rect", "draw_mode": "outline", "dash": "solid", "arrows": "none", "arrow_size": "medium",
    "radius": 16,
}


class EmptyPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        ic = QLabel(); ic.setPixmap(icons.pixmap("app", 96)); ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        t = QLabel(f"<h2>{APP_NAME}</h2><p>Create a new image with <b>Ctrl+N</b>, open one with <b>Ctrl+O</b>,<br>"
                   "paste with <b>Ctrl+Alt+V</b>, or drop image files here.</p>")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        lay.addStretch(2)


class PreviewWorker(QObject):
    """Runs render(params) on one background thread. New requests replace any waiting one, so a
    quickly dragged slider only ever costs one render at a time. Results come back on the GUI thread."""
    ready = Signal(object, object)
    failed = Signal(object, object)
    busy = Signal(bool)
    _finished = Signal(object, object, object)

    def __init__(self, render, parent=None):
        super().__init__(parent)
        from concurrent.futures import ThreadPoolExecutor
        self.render = render
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="preview")
        self.pending = None
        self.future = None
        self.stop = None
        self._finished.connect(self._on_finished)

    def request(self, params):
        self.pending = (params,)
        if self.future is None:
            self._start()
        else:
            self.stop.set()          # tell the running render to give up at its next band

    def _start(self):
        import threading
        from .effects import Cancelled
        (p,) = self.pending
        self.pending = None
        self.busy.emit(True)
        stop = self.stop = threading.Event()

        def job():
            try:
                self._finished.emit(p, self.render(p, stop.is_set), None)
            except Cancelled:
                self._finished.emit(p, None, "cancelled")
            except Exception as ex:      # reported on the GUI thread
                self._finished.emit(p, None, ex)
        self.future = self.pool.submit(job)

    def _on_finished(self, p, img, ex):
        self.future = None
        if ex == "cancelled":
            if self.pending is not None:
                self._start()
            else:
                self.busy.emit(False)
            return
        if ex is not None:
            self.pending = None
            self.busy.emit(False)
            self.failed.emit(p, ex)
            return
        if self.pending is None:
            self.busy.emit(False)
            self.ready.emit(p, img)
        else:
            self._start()        # settings changed meanwhile: skip this result, do the newest

    def wait(self):
        """Stop whatever is running (used before applying for real or cancelling)."""
        self.pending = None
        if self.future is not None:
            self.stop.set()
            try:
                self.future.result()
            except Exception:
                pass
            QApplication.processEvents()   # deliver its queued result
        self.busy.emit(False)


class PercentSlider(QWidget):
    """A 0-100 % slider with a number box beside it (used for brush hardness)."""

    def __init__(self, value, on_change, tip, width=120):
        super().__init__()
        self.on_change = on_change
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(width)
        self.slider.setToolTip(tip)
        self.spin = QSpinBox()
        self.spin.setRange(0, 100)
        self.spin.setSuffix(" %")
        self.spin.setToolTip(tip)
        self.spin.setFixedWidth(self.spin.sizeHint().width() + 8)
        h.addWidget(self.slider)
        h.addWidget(self.spin)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._set(value)
        self.slider.valueChanged.connect(self._changed)
        self.spin.valueChanged.connect(self._changed)

    def _set(self, v):
        for x in (self.slider, self.spin):
            x.blockSignals(True)
            x.setValue(int(v))
            x.blockSignals(False)

    def _changed(self, v):
        self._set(v)
        self.on_change(int(v))


class BrushWidthSlider(QWidget):
    """Brush width: a logarithmic slider (1-400 px, fine steps at small sizes) plus a number box (up to 2000)."""
    LO, HI, STEPS = 1, 400, 1000

    def __init__(self, value, on_change):
        super().__init__()
        self.on_change = on_change
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.STEPS)
        self.slider.setFixedWidth(170)
        self.slider.setToolTip("Brush width  ( [ and ] change it too )")
        self.spin = QSpinBox()
        self.spin.setRange(1, 2000)
        self.spin.setSuffix(" px")
        self.spin.setToolTip("Brush width in pixels")
        self.spin.setFixedWidth(self.spin.sizeHint().width() + 8)
        h.addWidget(self.slider)
        h.addWidget(self.spin)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._set(value)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def _pos(self, v):
        v = max(self.LO, min(self.HI, v))
        return round(math.log(v / self.LO) / math.log(self.HI / self.LO) * self.STEPS)

    def _val(self, pos):
        return max(1, round(self.LO * (self.HI / self.LO) ** (pos / self.STEPS)))

    def _set(self, v):
        for x in (self.slider, self.spin):
            x.blockSignals(True)
        self.spin.setValue(int(v))
        self.slider.setValue(self._pos(v))
        for x in (self.slider, self.spin):
            x.blockSignals(False)

    def _from_slider(self, pos):
        v = self._val(pos)
        self.spin.blockSignals(True); self.spin.setValue(v); self.spin.blockSignals(False)
        self.on_change(v)

    def _from_spin(self, v):
        self.slider.blockSignals(True); self.slider.setValue(self._pos(v)); self.slider.blockSignals(False)
        self.on_change(v)


class MainWindow(QMainWindow):
    def __init__(self, files=()):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.setWindowIcon(icons.icon("app"))
        self.setAcceptDrops(True)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        self.colors = ColorState()
        self.opts = dict(DEFAULT_OPTS)
        self._load_opts()
        self.tools = [cls(self) for cls in TOOL_CLASSES]
        self.tool_by_name = {t.name: t for t in self.tools}
        self.tool = None
        self.prev_tool = None
        self.docs = []      # list of (doc, canvas)
        self.current = -1
        self.last_effect = None
        self.last_params = {}
        self.opt_widgets = {}

        self.stack = QStackedWidget()
        self.empty = EmptyPage()
        self.stack.addWidget(self.empty)
        self.setCentralWidget(self.stack)

        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self._build_status()
        self.colors.changed.connect(self._save_colors)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._update_tab_thumbs)

        self.select_tool(self.settings.value("tool", "Paintbrush"))
        self._restore_window()
        self._update_actions()
        for f in files:
            self.open_path(f)

    # =============================================================== properties
    @property
    def doc(self):
        return self.docs[self.current][0] if 0 <= self.current < len(self.docs) else None

    @property
    def canvas(self):
        return self.docs[self.current][1] if 0 <= self.current < len(self.docs) else None

    # =============================================================== settings
    def _load_opts(self):
        s = self.settings
        for k, v in DEFAULT_OPTS.items():
            val = s.value("opt/" + k, None)
            if val is None:
                continue
            if isinstance(v, bool):
                val = val in (True, "true", "1", 1)
            elif isinstance(v, float):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    val = v
            elif isinstance(v, int):
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    val = v
            self.opts[k] = val
        p, q = s.value("color/primary"), s.value("color/secondary")
        if p:
            self.colors.primary = QColor(p)
        if q:
            self.colors.secondary = QColor(q)

    def _save_colors(self):
        self.settings.setValue("color/primary", self.colors.primary.name(QColor.HexArgb))
        self.settings.setValue("color/secondary", self.colors.secondary.name(QColor.HexArgb))

    def _restore_window(self):
        g = self.settings.value("geometry")
        if g:
            self.restoreGeometry(g)
        else:
            self.resize(1280, 820)
        st = self.settings.value("windowState")
        if not (st and self.restoreState(st, LAYOUT_VERSION)):
            # first run (or layout changed): size the docks once the window is on screen
            QTimer.singleShot(0, self._initial_dock_sizes)
        self.act["pixel_grid"].setChecked(self.settings.value("pixel_grid", "false") in ("true", True))
        self.act["rulers"].setChecked(self.settings.value("rulers", "false") in ("true", True))
        self.act["show_guides"].setChecked(self.settings.value("show_guides", "true") in ("true", True))
        self.act["snap_guides"].setChecked(self.settings.value("snap_guides", "true") in ("true", True))

    # =============================================================== actions
    def _a(self, name, text, shortcut=None, icon=None, checkable=False, slot=None, tip=None):
        a = QAction(text, self)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if icon:
            a.setIcon(icons.icon(icon))
        a.setCheckable(checkable)
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(slot or (lambda checked=False, n=name: self.action(n)))
        self.act[name] = a
        self.addAction(a)
        return a

    def action(self, name):
        fn = getattr(self, "do_" + name, None)
        if fn is not None:
            fn()

    def _build_actions(self):
        self.act = {}
        A = self._a
        A("new", "&New...", "Ctrl+N", "new")
        A("open", "&Open...", "Ctrl+O", "open")
        A("save", "&Save", "Ctrl+S", "save")
        A("save_as", "Save &As...", "Ctrl+Shift+S")
        A("print", "&Print...", "Ctrl+P", "print")
        A("scan", "S&can...", "", "scan")
        A("save_all", "Save A&ll", "Ctrl+Alt+S")
        A("close", "&Close", "Ctrl+W")
        A("quit", "E&xit", "Ctrl+Q")
        A("undo", "&Undo", "Ctrl+Z", "undo")
        A("redo", "&Redo", ["Ctrl+Y", "Ctrl+Shift+Z"], "redo")
        A("cut", "Cu&t", "Ctrl+X", "cut")
        A("copy", "&Copy", "Ctrl+C", "copy")
        A("copy_merged", "Copy Mer&ged", "Ctrl+Shift+C")
        A("paste", "&Paste", "Ctrl+V", "paste")
        A("paste_layer", "Paste Into New &Layer", "Ctrl+Shift+V")
        A("paste_image", "Paste Into New &Image", "Ctrl+Alt+V")
        A("select_all", "Select &All", "Ctrl+A", "select_all")
        A("deselect", "&Deselect All", "Ctrl+D", "deselect")
        A("erase_selection", "&Erase Selection", "Delete")
        A("fill_selection", "&Fill Selection", "Backspace")
        A("content_aware_fill", "Content-A&ware Fill", "Shift+Backspace", "magic_wand",
          tip="Replace the selected area with texture made up from the picture around it")
        A("invert_selection", "&Invert Selection", "Ctrl+I")
        A("zoom_in", "Zoom &In", ["Ctrl++", "Ctrl+="])
        A("zoom_out", "Zoom &Out", "Ctrl+-")
        A("best_fit", "Zoom to &Window", "Ctrl+B")
        A("zoom_selection", "Zoom to &Selection", "Ctrl+Shift+B")
        A("actual_size", "&Actual Size", ["Ctrl+0", "Ctrl+Shift+A"])
        A("pixel_grid", "Pixel &Grid", "Ctrl+'", "grid", checkable=True,
          slot=lambda on: self._toggle_grid(on))
        A("rulers", "&Rulers", "Ctrl+Alt+R", checkable=True, slot=lambda on: self._toggle_rulers(on))
        A("show_guides", "Show G&uides", "Ctrl+;", checkable=True, slot=lambda on: self.set_guides_visible(on))
        A("snap_guides", "S&nap to Guides", "Ctrl+Shift+;", checkable=True, slot=lambda on: self._toggle_snap(on))
        A("new_guide", "New Gu&ide...")
        A("clear_guides", "Clea&r Guides")
        A("crop", "Crop to &Selection", "Ctrl+Shift+X", "crop")
        A("resize", "&Resize...", "Ctrl+R", "resize")
        A("canvas_size", "Canvas &Size...", "Ctrl+Shift+R", "canvas_size")
        A("split_image", "S&plit Image...", None, "split_image",
          tip="Cut the image (or the selection) into a grid of separate pieces")
        A("flip_h", "Flip &Horizontal", None, "flip_h")
        A("flip_v", "Flip &Vertical", None, "flip_v")
        A("rot_cw", "Rotate 90° &Clockwise", "Ctrl+H", "rot_cw")
        A("rot_ccw", "Rotate 90° Co&unter-clockwise", "Ctrl+G", "rot_ccw")
        A("rot_180", "Rotate &180°", "Ctrl+J", "rot_180")
        A("flatten", "&Flatten", "Ctrl+Shift+F", "flatten")
        A("add_layer", "&Add New Layer", "Ctrl+Shift+N", "layer_add")
        A("delete_layer", "&Delete Layer", "Ctrl+Shift+Del", "layer_delete")
        A("duplicate_layer", "D&uplicate Layer", "Ctrl+Shift+D", "layer_dup")
        A("merge_down", "&Merge Layer Down", "Ctrl+M", "layer_merge")
        A("import_layer", "&Import From File...", None)
        A("layer_flip_h", "Flip Layer &Horizontal", None, "flip_h")
        A("layer_flip_v", "Flip Layer &Vertical", None, "flip_v")
        A("layer_rotate_zoom", "&Rotate / Zoom...", None)
        A("layer_up", "Move Layer U&p", "Alt+PgUp", "layer_up")
        A("layer_down", "Move Layer Do&wn", "Alt+PgDown", "layer_down")
        A("layer_properties", "Layer &Properties...", "F4", "layer_props")
        A("repeat_effect", "Repeat Last Effect", "Ctrl+F")
        A("swap_colors", "Swap Colours", "X")
        A("brush_smaller", "Decrease Brush Size", "[")
        A("brush_bigger", "Increase Brush Size", "]")
        A("about", f"&About {APP_NAME}", None, "info")
        A("shortcuts", "&Keyboard Shortcuts", "F1", "keyboard")
        # menu entries that were added without an icon: give every one its own
        for n, ic in (("save_as", "save_as"), ("save_all", "save_all"), ("close", "close"), ("quit", "quit"),
                      ("copy_merged", "copy_merged"), ("paste_layer", "paste_layer"), ("paste_image", "paste_image"),
                      ("erase_selection", "erase_selection"), ("fill_selection", "fill_selection"),
                      ("invert_selection", "invert_selection"), ("zoom_in", "zoom_in"), ("zoom_out", "zoom_out"),
                      ("best_fit", "zoom_window"), ("zoom_selection", "zoom_selection"), ("actual_size", "actual_size"),
                      ("rulers", "rulers"), ("show_guides", "show_guides"), ("snap_guides", "snap_guides"),
                      ("new_guide", "new_guide"), ("clear_guides", "clear_guides"), ("import_layer", "import_layer"),
                      ("layer_rotate_zoom", "layer_rotate_zoom"), ("repeat_effect", "repeat_effect")):
            self.act[n].setIcon(icons.icon(ic))
        # tool letter shortcuts (cycle tools sharing a letter)
        keys = []
        for t in self.tools:
            if t.key and t.key not in keys:
                keys.append(t.key)
        for k in keys:
            a = QAction(self)
            a.setShortcut(QKeySequence(k))
            a.triggered.connect(lambda _=False, k=k: self._tool_key(k))
            self.addAction(a)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for n in ("new", "open"):
            m.addAction(self.act[n])
        self.recent_menu = m.addMenu(icons.icon("recent"), "Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addAction(self.act["scan"])
        m.addSeparator()
        for n in ("save", "save_as", "save_all"):
            m.addAction(self.act[n])
        m.addSeparator()
        m.addAction(self.act["print"])
        m.addSeparator()
        m.addAction(self.act["close"])
        m.addAction(self.act["quit"])

        m = mb.addMenu("&Edit")
        for n in ("undo", "redo", None, "cut", "copy", "copy_merged", "paste", "paste_layer", "paste_image", None,
                  "select_all", "deselect", None, "erase_selection", "fill_selection", "content_aware_fill", "invert_selection"):
            m.addSeparator() if n is None else m.addAction(self.act[n])

        m = mb.addMenu("&View")
        for n in ("zoom_in", "zoom_out", "best_fit", "zoom_selection", "actual_size", None, "pixel_grid",
                  "rulers", None, "show_guides", "snap_guides", "new_guide", "clear_guides"):
            m.addSeparator() if n is None else m.addAction(self.act[n])
        m.addSeparator()
        th = m.addMenu(icons.icon("theme"), "&Theme")
        g = QActionGroup(self)
        cur = self.settings.value("theme", "system")
        for key, label in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            a = th.addAction(icons.icon("theme_" + key), label)
            a.setCheckable(True)
            a.setChecked(cur == key)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            g.addAction(a)

        m = mb.addMenu("&Image")
        for n in ("crop", "resize", "canvas_size", "split_image", None, "flip_h", "flip_v", None, "rot_cw", "rot_ccw", "rot_180",
                  None, "flatten"):
            m.addSeparator() if n is None else m.addAction(self.act[n])

        m = mb.addMenu("&Layers")
        for n in ("add_layer", "delete_layer", "duplicate_layer", "merge_down", "import_layer", None,
                  "layer_flip_h", "layer_flip_v", "layer_rotate_zoom", None, "layer_up", "layer_down", None,
                  "layer_properties"):
            m.addSeparator() if n is None else m.addAction(self.act[n])

        m = mb.addMenu("&Adjustments")
        for eff in ADJUSTMENTS:
            a = m.addAction(icons.icon("adj_" + icons_menu.slug(eff.name)), eff.name + ("..." if eff.needs_dialog else ""))
            if eff.shortcut:
                a.setShortcut(QKeySequence(eff.shortcut))
            a.triggered.connect(lambda _=False, e=eff: self.run_effect(e, "adjust"))
            self.addAction(a)
            self.act["adj:" + eff.name] = a

        m = mb.addMenu("Effe&cts")
        m.addAction(self.act["repeat_effect"])
        m.addSeparator()
        for cat, effs in EFFECTS:
            sm = m.addMenu(icons.icon("cat_" + icons_menu.slug(cat)), cat)
            for eff in effs:
                a = sm.addAction(icons.icon("fx_" + icons_menu.slug(eff.name)), eff.name + ("..." if eff.needs_dialog else ""))
                a.triggered.connect(lambda _=False, e=eff: self.run_effect(e, "effects"))
                self.act["fx:" + eff.name] = a

        self.window_menu = mb.addMenu("&Window")
        m = mb.addMenu("&Help")
        m.addAction(self.act["shortcuts"])
        m.addAction(self.act["about"])

    def _build_toolbars(self):
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        for n in ("new", "open", "save", None, "cut", "copy", "paste", None, "crop", "deselect", None,
                  "undo", "redo", None, "pixel_grid"):
            tb.addSeparator() if n is None else tb.addAction(self.act[n])
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setExpanding(False)
        self.tabs.setIconSize(QSize(36, 36))
        self.tabs.setElideMode(Qt.ElideMiddle)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setMaximumWidth(700)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_document)
        tb.addWidget(self.tabs)
        self.addToolBar(tb)
        self.main_toolbar = tb
        self.addToolBarBreak()
        self.opt_bar = QToolBar("Tool Options")
        self.opt_bar.setObjectName("options_toolbar")
        self.opt_bar.setMovable(False)
        self.opt_bar.setIconSize(QSize(20, 20))
        self.addToolBar(self.opt_bar)

    def _dock(self, title, widget, area, name, key, icon):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        a = d.toggleViewAction()
        a.setShortcut(QKeySequence(key))
        a.setIcon(icons.icon(icon))
        self.window_menu.addAction(a)
        return d, a

    def _build_docks(self):
        self.tools_panel = ToolsPanel(self.tools, self.select_tool)
        self.colors_panel = ColorsPanel(self.colors, self.settings)
        self.layers_panel = LayersPanel(self)
        self.history_panel = HistoryPanel(self)
        self.nav_panel = NavigatorPanel(self)
        self.tools_dock, a1 = self._dock("Tools", self.tools_panel, Qt.LeftDockWidgetArea, "tools_dock", "F5", "tools")
        self.history_dock, a2 = self._dock("History", self.history_panel, Qt.RightDockWidgetArea, "history_dock", "F6", "history")
        self.layers_dock, a3 = self._dock("Layers", self.layers_panel, Qt.RightDockWidgetArea, "layers_dock", "F7", "layers")
        colors_scroll = FitScrollArea()
        colors_scroll.setWidget(self.colors_panel)
        colors_scroll.setFrameShape(QFrame.NoFrame)
        colors_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        colors_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.colors_scroll = colors_scroll
        # the left panels stop widening once the tool buttons are at their biggest
        self.tools_panel.setMaximumWidth(ToolsPanel.max_width())
        colors_scroll.setMaximumWidth(ToolsPanel.max_width())
        self.colors_dock, a4 = self._dock("Colours", colors_scroll, Qt.LeftDockWidgetArea, "colors_dock", "F8", "colors")
        self.nav_dock, a5 = self._dock("Navigator", self.nav_panel, Qt.RightDockWidgetArea, "nav_dock", "F9", "navigator")
        self.splitDockWidget(self.tools_dock, self.colors_dock, Qt.Vertical)
        self.splitDockWidget(self.nav_dock, self.history_dock, Qt.Vertical)
        self.splitDockWidget(self.history_dock, self.layers_dock, Qt.Vertical)
        self.resizeDocks([self.tools_dock, self.history_dock], [290, 230], Qt.Horizontal)
        self.resizeDocks([self.tools_dock, self.colors_dock], [150, 900], Qt.Vertical)
        self.window_menu.addSeparator()
        self.window_menu.addAction(icons.icon("reset_layout"), "Reset Window Layout", self._reset_layout)
        # window toggles on the main toolbar, like the original's top-right buttons
        tb = self.main_toolbar
        tb.addSeparator()
        for a in (a1, a2, a3, a4, a5):
            tb.addAction(a)

    def left_dock_min_width(self):
        """Narrowest left-panel width at which the Colours panel still fits (below this it stops
        shrinking and gets cut off)."""
        cp = self.colors_panel
        extra = self.colors_dock.width() - cp.width() if cp.width() > 0 else 0
        if extra <= 0 or extra > 80:
            extra = self.colors_scroll.verticalScrollBar().sizeHint().width() + 2
        return cp.minimumSizeHint().width() + extra

    def _initial_dock_sizes(self):
        self.resizeDocks([self.tools_dock, self.history_dock], [self.left_dock_min_width(), 230], Qt.Horizontal)
        self.resizeDocks([self.tools_dock, self.colors_dock], [130, 2000], Qt.Vertical)
        self.resizeDocks([self.nav_dock, self.history_dock, self.layers_dock], [190, 230, 340], Qt.Vertical)

    def _reset_layout(self):
        for d, area in ((self.tools_dock, Qt.LeftDockWidgetArea), (self.colors_dock, Qt.LeftDockWidgetArea),
                        (self.nav_dock, Qt.RightDockWidgetArea),
                        (self.history_dock, Qt.RightDockWidgetArea), (self.layers_dock, Qt.RightDockWidgetArea)):
            d.setFloating(False)
            self.addDockWidget(area, d)
            d.show()
        self.splitDockWidget(self.tools_dock, self.colors_dock, Qt.Vertical)
        self.splitDockWidget(self.nav_dock, self.history_dock, Qt.Vertical)
        self.splitDockWidget(self.history_dock, self.layers_dock, Qt.Vertical)
        QTimer.singleShot(0, self._initial_dock_sizes)

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.hint = QLabel()
        self.hint.setMinimumWidth(100)
        sb.addWidget(self.hint, 1)
        self.size_label = QLabel()
        self.sel_label = QLabel()
        self.pos_label = QLabel()
        for w, tip in ((self.size_label, "Image size"), (self.sel_label, "Selection size"), (self.pos_label, "Cursor position")):
            w.setToolTip(tip)
            w.setMinimumWidth(100)
            sb.addPermanentWidget(w)
        zo = QToolButton(); zo.setText("−"); zo.setAutoRaise(True); zo.clicked.connect(lambda: self.action("zoom_out"))
        zi = QToolButton(); zi.setText("+"); zi.setAutoRaise(True); zi.clicked.connect(lambda: self.action("zoom_in"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setInsertPolicy(QComboBox.NoInsert)
        self.zoom_combo.addItem("Window")
        for z in reversed(ZOOM_LEVELS):
            self.zoom_combo.addItem(f"{round(z * 100)}%")
        self.zoom_combo.setMinimumWidth(90)
        self.zoom_combo.activated.connect(self._zoom_combo)
        self.zoom_combo.lineEdit().returnPressed.connect(self._zoom_combo)
        sb.addPermanentWidget(zo)
        sb.addPermanentWidget(self.zoom_combo)
        sb.addPermanentWidget(zi)

    # =============================================================== theme
    def set_theme(self, key):
        self.settings.setValue("theme", key)
        apply_theme(QApplication.instance(), key)
        for i in range(len(self.docs)):
            self.docs[i][1].viewport().update()
        self.history_panel.rebuild()
        self.status_message("Theme changed. Icons will match the new theme next time Paintwell starts.")

    # =============================================================== tools
    def select_tool(self, name):
        t = self.tool_by_name.get(name) or self.tool_by_name["Paintbrush"]
        if t is self.tool:
            return
        if self.tool is not None:
            self.tool.deactivate()
            self.prev_tool = self.tool
        self.tool = t
        t.activate()
        self.tools_panel.set_current(t.name)
        self.settings.setValue("tool", t.name)
        self._build_options()
        self.hint.setText(t.hint)
        if self.canvas:
            self.canvas.viewport().setCursor(t.cursor(None, self.canvas))
            self.canvas.viewport().update()

    def select_previous_tool(self):
        if self.prev_tool is not None:
            self.select_tool(self.prev_tool.name)

    def _tool_key(self, k):
        group = [t for t in self.tools if t.key == k]
        if self.tool in group:
            nxt = group[(group.index(self.tool) + 1) % len(group)]
        else:
            nxt = group[0]
        self.select_tool(nxt.name)

    def commit_tool(self, cancel=False):
        if self.tool is not None and self.tool.is_editing():
            if cancel:
                self.tool.cancel()
            else:
                self.tool.finish()

    def canvas_update(self):
        if self.canvas:
            self.canvas.viewport().update()

    def hide_selection_outline(self):
        return False

    def busy(self, on):
        if on:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def status_message(self, text):
        self.statusBar().showMessage(text, 4000)

    def selection_info_changed(self, size=None):
        d = self.doc
        if d is None or d.selection is None:
            self.sel_label.setText("")
            return
        if size is None:
            size = d.selection.boundingRect().size()
        self.sel_label.setText(f"⬚ {round(size.width())} × {round(size.height())}")

    # =============================================================== option bar
    def set_option(self, key, value):
        self.opts[key] = value
        self.settings.setValue("opt/" + key, value)
        w = self.opt_widgets.get(key)
        if w is not None:
            setter = getattr(w, "_set", None)
            if setter:
                setter(value)
        if self.tool:
            self.tool.options_changed()

    def _combo(self, key, items):
        cb = QComboBox()
        for val, label in items:
            cb.addItem(label, val)
        idx = cb.findData(self.opts[key])
        cb.setCurrentIndex(max(0, idx))
        cb.currentIndexChanged.connect(lambda i: self.set_option(key, cb.itemData(i)))

        def _set(v, cb=cb):
            cb.blockSignals(True); cb.setCurrentIndex(max(0, cb.findData(v))); cb.blockSignals(False)
        cb._set = _set
        return cb

    def _build_options(self):
        bar = self.opt_bar
        bar.clear()
        self.opt_widgets = {}
        t = self.tool
        tb = QToolButton()
        tb.setIcon(icons.icon(t.icon))
        tb.setToolTip(t.name)
        tb.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(tb)
        for tool in self.tools:
            a = menu.addAction(icons.icon(tool.icon), tool.name)
            a.triggered.connect(lambda _=False, n=tool.name: self.select_tool(n))
        tb.setMenu(menu)
        bar.addWidget(QLabel(" Tool: "))
        bar.addWidget(tb)
        bar.addSeparator()

        def label(text):
            l = QLabel(text)
            l.setContentsMargins(6, 0, 3, 0)
            bar.addWidget(l)

        for key in t.options:
            w = None
            if key == "brush_width":
                label("Brush width:")
                w = BrushWidthSlider(self.opts[key], lambda v: self.set_option("brush_width", v))
                bar.addWidget(w)
                self.opt_widgets[key] = w
                continue
            elif key == "hardness":
                label("Hardness:")
                w = PercentSlider(self.opts[key], lambda v: self.set_option("hardness", v),
                                  "Brush hardness: 100 % gives a hard edge, lower values a soft, feathered edge")
                bar.addWidget(w)
                self.opt_widgets[key] = w
                continue
            elif key in ("blur_strength", "sharpen_strength", "smudge_strength"):
                label("Strength:")
                tip = {"blur_strength": "How much each stroke blurs: higher values soften more",
                       "sharpen_strength": "How much each stroke sharpens: higher values bring out more detail",
                       "smudge_strength": "How far the colour is dragged: higher values carry it further "
                                          "(100 % never fades)"}[key]
                w = PercentSlider(self.opts[key], lambda v, key=key: self.set_option(key, v), tip)
                bar.addWidget(w)
                self.opt_widgets[key] = w
                continue
            elif key == "lasso_width":
                label("Width:")
                w = QSpinBox(); w.setRange(1, 40); w.setValue(self.opts[key]); w.setSuffix(" px")
                w.setToolTip("How far from the pointer the outline looks for an edge")
                w.valueChanged.connect(lambda v: self.set_option("lasso_width", v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setValue(v), w.blockSignals(False))
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "edge_contrast":
                label("Contrast:")
                w = PercentSlider(self.opts[key], lambda v: self.set_option("edge_contrast", v),
                                  "Edge contrast: only edges stronger than this attract the outline. "
                                  "Raise it for crisp edges, lower it for soft ones", width=90)
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "lasso_frequency":
                label("Frequency:")
                w = QSpinBox(); w.setRange(0, 100); w.setValue(self.opts[key])
                w.setToolTip("How often anchor points are dropped: higher values add them more often")
                w.valueChanged.connect(lambda v: self.set_option("lasso_frequency", v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setValue(v), w.blockSignals(False))
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "crop_ratio":
                label("Ratio:")
                w = self._combo(key, [("free", "Free"), ("original", "Original"), ("1:1", "1 : 1 (square)"),
                                      ("4:3", "4 : 3"), ("3:2", "3 : 2"), ("16:9", "16 : 9"), ("5:4", "5 : 4"),
                                      ("2:1", "2 : 1")])
                w.setToolTip("Shape of the crop box (drag tall for portrait)")
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "crop_actions":
                tool = t
                ok = QToolButton(); ok.setText("✓ Crop"); ok.setAutoRaise(True)
                ok.setToolTip("Crop to the box (Enter)")
                ok.clicked.connect(lambda _=False, tool=tool: tool.apply())
                no = QToolButton(); no.setText("✕ Cancel"); no.setAutoRaise(True)
                no.setToolTip("Reset the box (Esc)")
                no.clicked.connect(lambda _=False, tool=tool: (tool.cancel(), tool.activate()))
                bar.addSeparator(); bar.addWidget(ok); bar.addWidget(no)
                continue
            elif key == "tone_range":
                label("Range:")
                w = self._combo(key, [("shadows", "Shadows"), ("midtones", "Midtones"), ("highlights", "Highlights")])
                w.setToolTip("Which tones are changed most")
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key in ("exposure", "sponge_flow"):
                label("Exposure:" if key == "exposure" else "Flow:")
                tip = ("Strength of each stroke" if key == "exposure"
                       else "Strength of each stroke: how much colour is added or taken out")
                w = PercentSlider(self.opts[key], lambda v, key=key: self.set_option(key, v), tip, width=100)
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "sponge_mode":
                label("Mode:")
                w = self._combo(key, [("desaturate", "Desaturate"), ("saturate", "Saturate")])
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key in ("protect_tones", "vibrance"):
                w = QCheckBox("Protect Tones" if key == "protect_tones" else "Vibrance")
                w.setChecked(self.opts[key])
                w.setToolTip("Change brightness only, keeping colours from shifting or clipping"
                             if key == "protect_tones" else
                             "Boost dull colours more than strong ones, and stop colours clipping")
                w.toggled.connect(lambda v, key=key: self.set_option(key, v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setChecked(v), w.blockSignals(False))
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "redeye_size":
                label("Size:")
                w = BrushWidthSlider(self.opts[key], lambda v: self.set_option("redeye_size", v))
                w.setToolTip("Size of the circle: make it a little bigger than the red pupil ([ and ] change it)")
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key in ("pupil_size", "darken_amount"):
                label("Pupil size:" if key == "pupil_size" else "Darken amount:")
                tip = ("How far round the red to correct: raise it if a red ring is left"
                       if key == "pupil_size" else "How dark the corrected pupil becomes")
                w = PercentSlider(self.opts[key], lambda v, key=key: self.set_option(key, v), tip, width=90)
                bar.addWidget(w); self.opt_widgets[key] = w
                continue
            elif key == "finger_paint":
                w = QCheckBox("Finger painting"); w.setChecked(self.opts[key])
                w.setToolTip("Start each stroke with the primary colour (right-drag: secondary), "
                             "smeared into the picture")
                w.toggled.connect(lambda v: self.set_option("finger_paint", v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setChecked(v), w.blockSignals(False))
            elif key == "rotate":
                tool = t
                b = QToolButton(); b.setIcon(icons.icon("rot_ccw")); b.setAutoRaise(True)
                b.setToolTip("Rotate the selection 90° counter-clockwise")
                b.clicked.connect(lambda _=False, tool=tool: (tool.rotate_by(-90), self.canvas and self.canvas.viewport().update()))
                bar.addWidget(b)
                b = QToolButton(); b.setIcon(icons.icon("rot_cw")); b.setAutoRaise(True)
                b.setToolTip("Rotate the selection 90° clockwise")
                b.clicked.connect(lambda _=False, tool=tool: (tool.rotate_by(90), self.canvas and self.canvas.viewport().update()))
                bar.addWidget(b)
                label("Angle:")
                w = QDoubleSpinBox(); w.setRange(-180.0, 180.0); w.setDecimals(1); w.setSingleStep(1.0)
                w.setSuffix("°"); w.setWrapping(True); w.setKeyboardTracking(False)
                w.setToolTip("Rotation angle of the selection (type a value, use the arrows, or click and slide)")
                w.setValue(tool.current_angle())
                w.valueChanged.connect(lambda v, tool=tool: (tool.set_angle(v), self.canvas and self.canvas.viewport().update()))
                w._set = lambda v, w=w: (None if abs(w.value() - v) < 0.05 else
                                         (w.blockSignals(True), w.setValue(v), w.blockSignals(False)))
            elif key == "antialias":
                w = QCheckBox("Antialiasing"); w.setChecked(self.opts[key])
                w.toggled.connect(lambda v: self.set_option("antialias", v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setChecked(v), w.blockSignals(False))
            elif key == "selection_mode":
                label("Selection mode:")
                w = self._combo(key, [("replace", "Replace"), ("union", "Union (Ctrl)"), ("exclude", "Exclude (Alt / Right)"),
                                      ("intersect", "Intersect"), ("xor", "Xor")])
            elif key == "flood_mode":
                label("Flood mode:")
                w = self._combo(key, [("contiguous", "Contiguous"), ("global", "Global")])
            elif key in ("tolerance", "erase_tolerance"):
                label("Tolerance:")
                w = QSlider(Qt.Horizontal); w.setRange(0, 100); w.setValue(self.opts[key]); w.setFixedWidth(120)
                val = QLabel(f"{self.opts[key]}%"); val.setMinimumWidth(36)
                w.valueChanged.connect(lambda v, val=val, key=key: (val.setText(f"{v}%"), self.set_option(key, v)))
                w._set = lambda v, w=w: w.setValue(v)
                bar.addWidget(w)
                bar.addWidget(val)
                self.opt_widgets[key] = w
                continue
            elif key == "sampling":
                label("Sampling:")
                w = self._combo(key, [("layer", "Layer"), ("image", "Image")])
            elif key == "gradient_type":
                label("Gradient:")
                w = self._combo(key, [("linear", "Linear"), ("reflected", "Linear (Reflected)"), ("diamond", "Linear (Diamond)"),
                                      ("radial", "Radial"), ("conical", "Conical")])
            elif key == "gradient_mode":
                label("Mode:")
                w = self._combo(key, [("color", "Colour mode"), ("transparency", "Transparency mode")])
            elif key == "picker_size":
                label("Sampling size:")
                w = self._combo(key, [(1, "Single pixel"), (3, "3 × 3 region"), (5, "5 × 5 region"), (7, "7 × 7 region"),
                                      (9, "9 × 9 region"), (11, "11 × 11 region"), (21, "21 × 21 region"), (51, "51 × 51 region")])
            elif key == "picker_after":
                label("After click:")
                w = self._combo(key, [("none", "Do not switch tool"), ("previous", "Switch to previous tool"),
                                      ("pencil", "Switch to Pencil tool")])
            elif key == "font":
                label("Font:")
                w = QFontComboBox(); w.setCurrentFont(QFont(self.opts[key])); w.setMaximumWidth(220)
                w.currentFontChanged.connect(lambda f: self.set_option("font", f.family()))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setCurrentFont(QFont(v)), w.blockSignals(False))
            elif key == "font_size":
                w = QSpinBox(); w.setRange(1, 2000); w.setValue(self.opts[key]); w.setSuffix(" px")
                w.valueChanged.connect(lambda v: self.set_option("font_size", v))
                w._set = lambda v, w=w: (w.blockSignals(True), w.setValue(int(v)), w.blockSignals(False))
            elif key == "font_style":
                style_buttons = {}
                for flag, text in (("bold", "B"), ("italic", "I"), ("underline", "U"), ("strike", "S")):
                    b = QToolButton(); b.setText(text); b.setCheckable(True); b.setAutoRaise(True)
                    f = b.font()
                    f.setBold(flag == "bold"); f.setItalic(flag == "italic"); f.setUnderline(flag == "underline"); f.setStrikeOut(flag == "strike")
                    b.setFont(f)
                    b.setChecked(flag in self.opts["font_style"])
                    b.toggled.connect(lambda on, flag=flag: self._toggle_style(flag, on))
                    bar.addWidget(b)
                    style_buttons[flag] = b

                class _Styles:          # lets set_option update the four buttons
                    def __init__(self, buttons):
                        self.buttons = buttons

                    def _set(self, v):
                        for flag, b in self.buttons.items():
                            b.blockSignals(True); b.setChecked(flag in str(v)); b.blockSignals(False)
                self.opt_widgets[key] = _Styles(style_buttons)
                continue
            elif key == "text_align":
                w = self._combo(key, [("left", "Left"), ("center", "Centre"), ("right", "Right")])
                w.setToolTip("Line alignment (for vertical text: top, middle, bottom)")
            elif key == "text_dir":
                label("Orientation:")
                w = self._combo(key, [("horizontal", "Horizontal"), ("vertical", "Vertical"),
                                      ("arc", "Arc"), ("wave", "Wave")])
                w.setToolTip("Horizontal, vertical (letters stacked), along an arc, or on a wave "
                             "(Bend under Spacing & Effects sets how much)")
            elif key == "text_angle":
                label("Angle:")
                w = QDoubleSpinBox(); w.setRange(-180.0, 180.0); w.setDecimals(1); w.setSingleStep(1.0)
                w.setSuffix("°"); w.setWrapping(True); w.setKeyboardTracking(False)
                w.setValue(float(self.opts[key]))
                w.setToolTip("Rotation of the text (or drag the round handle above the text box)")
                w.valueChanged.connect(lambda v: self.set_option("text_angle", float(v)))
                w._set = lambda v, w=w: (None if abs(w.value() - float(v)) < 0.05 else
                                         (w.blockSignals(True), w.setValue(float(v)), w.blockSignals(False)))
            elif key == "text_more":
                b = QToolButton(); b.setText("Spacing && Effects"); b.setAutoRaise(True)
                b.setPopupMode(QToolButton.InstantPopup)
                b.setToolTip("Letter and line spacing, bend (for Arc and Wave), outline and drop shadow")
                m = QMenu(b)
                panel = QWidget(); g = QGridLayout(panel); g.setContentsMargins(10, 8, 10, 8); g.setVerticalSpacing(6)

                def spin_row(row, k, text, lo, hi, suffix, tip):
                    g.addWidget(QLabel(text), row, 0)
                    sp = QSpinBox(); sp.setRange(lo, hi); sp.setSuffix(suffix); sp.setValue(int(self.opts[k]))
                    sp.setToolTip(tip)
                    sp.valueChanged.connect(lambda v, k=k: self.set_option(k, v))
                    sp._set = lambda v, sp=sp: (sp.blockSignals(True), sp.setValue(int(v)), sp.blockSignals(False))
                    g.addWidget(sp, row, 1)
                    self.opt_widgets[k] = sp
                def note(row, text):
                    n = QLabel(text)
                    n.setStyleSheet("color: palette(mid); font-size: 11px;")
                    g.addWidget(n, row, 1, 1, 2)
                spin_row(0, "letter_spacing", "Letter spacing:", -50, 300, " %", "Extra space between letters (% of the size)")
                spin_row(1, "line_spacing", "Line spacing:", 30, 400, " %", "Space between lines (100 % = normal)")
                note(2, "shows with two or more lines")
                spin_row(3, "text_bend", "Bend:", -100, 100, " %", "How much Arc or Wave text bends (negative bends the other way)")
                note(4, "for Arc and Wave text")
                spin_row(5, "text_outline", "Outline:", 0, 200, " px", "Outline round the letters (0 = none)")
                oc = QToolButton(); oc.setToolTip("Outline colour")
                oc.setFixedSize(26, 22)

                def show_oc(v, oc=oc):
                    pm = QPixmap(18, 14); pm.fill(QColor(v)); oc.setIcon(QIcon(pm)); oc.setIconSize(pm.size())
                show_oc(self.opts["text_outline_color"])

                def pick_oc(_=False, oc=oc):
                    col = QColorDialog.getColor(QColor(self.opts["text_outline_color"]), self, "Outline colour")
                    if col.isValid():
                        self.set_option("text_outline_color", col.name())
                        if self.opts.get("text_outline", 0) == 0:
                            self.set_option("text_outline", 2)       # picking a colour: show an outline
                oc.clicked.connect(pick_oc)

                class _OC:
                    def __init__(self, fn):
                        self._set = fn
                self.opt_widgets["text_outline_color"] = _OC(show_oc)
                g.addWidget(oc, 5, 2)
                cb = QCheckBox("Drop shadow"); cb.setChecked(bool(self.opts["text_shadow"]))
                cb.toggled.connect(lambda v: self.set_option("text_shadow", v))
                cb._set = lambda v, cb=cb: (cb.blockSignals(True), cb.setChecked(bool(v)), cb.blockSignals(False))
                g.addWidget(cb, 6, 0, 1, 2)
                self.opt_widgets["text_shadow"] = cb
                # changing Bend on straight text: switch to Arc so the change shows
                bend_spin = self.opt_widgets["text_bend"]
                bend_spin.valueChanged.connect(
                    lambda v: self.set_option("text_dir", "arc")
                    if self.opts.get("text_dir") in ("horizontal", "vertical") else None)
                rb = QToolButton(); rb.setText("↺ Reset"); rb.setAutoRaise(True)
                rb.setToolTip("Put the text settings back to normal: horizontal, no angle, normal spacing, "
                              "no outline or shadow, plain style, left aligned (font and size are kept)")
                rb.clicked.connect(self.reset_text_options)
                wa = QWidgetAction(m); wa.setDefaultWidget(panel); m.addAction(wa)
                b.setMenu(m)
                bar.addWidget(b)
                bar.addWidget(rb)
                continue
            elif key == "shape_type":
                label("Shape:")
                w = self._combo(key, SHAPES)
            elif key == "draw_mode":
                label("Draw:")
                w = self._combo(key, [("outline", "Outline"), ("fill", "Filled"), ("both", "Filled with outline")])
            elif key == "dash":
                label("Style:")
                w = self._combo(key, [("solid", "Solid"), ("dash", "Dashed"), ("dot", "Dotted"), ("dashdot", "Dash-dot")])
            elif key == "line_cap":
                label("Ends:")
                w = self._combo(key, [("flat", "Flat"), ("round", "Round")])
                w.setToolTip("Line ends: Flat stops exactly at the end points, Round is rounded off")
            elif key == "arrows":
                label("Arrows:")
                w = self._combo(key, [("none", "None"), ("end", "At end"), ("start", "At start"), ("both", "Both ends")])
            elif key == "arrow_size":
                label("Arrow size:")
                w = self._combo(key, [("small", "Small"), ("medium", "Medium"), ("large", "Large")])
            elif key == "radius":
                label("Corner radius:")
                w = QSpinBox(); w.setRange(0, 1000); w.setValue(self.opts[key]); w.setSuffix(" px")
                w.valueChanged.connect(lambda v: self.set_option("radius", v))
            if w is not None:
                bar.addWidget(w)
                self.opt_widgets[key] = w

    TEXT_RESET = {"font_style": "", "text_align": "left", "text_dir": "horizontal", "text_angle": 0.0,
                  "letter_spacing": 0, "line_spacing": 100, "text_bend": 50, "text_outline": 0,
                  "text_outline_color": "#000000", "text_shadow": False}

    def paint_layer_for(self, tool, button):
        """Drawing with a brush, line, shape, fill... while a text layer is selected: start a new
        layer above it for the drawing, so the text stays editable (as in Krita)."""
        d = self.doc
        if d is None or not getattr(tool, "adds_paint", False):
            return
        if button not in (Qt.LeftButton, Qt.RightButton) or tool.is_editing():
            return
        if d.layer.text_item() is None:
            return
        d.add_layer()
        self.status_message("Drawing on a new layer, so the text stays editable.")

    def reset_text_options(self):
        """Text settings back to normal (the font and size stay as they are)."""
        for k, v in self.TEXT_RESET.items():
            self.set_option(k, v)

    def _toggle_style(self, flag, on):
        st = set(s for s in self.opts["font_style"].split(",") if s)
        (st.add if on else st.discard)(flag)
        self.set_option("font_style", ",".join(sorted(st)))

    # =============================================================== documents
    def add_document(self, doc):
        canvas = Canvas(doc, self)
        canvas.show_grid = self.act["pixel_grid"].isChecked()
        canvas.show_guides = self.act["show_guides"].isChecked()
        canvas.snap_guides = self.act["snap_guides"].isChecked()
        canvas.set_rulers(self.act["rulers"].isChecked())
        self.docs.append((doc, canvas))
        self.stack.addWidget(canvas)
        doc.titleChanged.connect(lambda d=doc: self._doc_title_changed(d))
        doc.historyChanged.connect(lambda d=doc: self._doc_history_changed(d))
        doc.sizeChanged.connect(self._update_status)
        doc.selectionChanged.connect(lambda: self.selection_info_changed())
        canvas.zoomChanged.connect(self._zoom_changed)
        canvas.cursorMoved.connect(self._cursor_moved)
        self.tabs.blockSignals(True)
        i = self.tabs.addTab(QIcon(thumb(doc.composite(), 36)), doc.title)
        self.tabs.blockSignals(False)
        self.switch_to(i)
        QTimer.singleShot(0, canvas.best_fit)

    def switch_to(self, i):
        if i == self.current and self.stack.currentWidget() is self.canvas:
            return
        self.commit_tool()
        for t in self.tools:
            if isinstance(t, MoveTool):
                t.reset()
        self.current = i
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(i)
        self.tabs.blockSignals(False)
        if self.doc is None:
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.canvas)
            self.canvas.setFocus()
        self.layers_panel.set_doc(self.doc)
        self.history_panel.set_doc(self.doc)
        self.nav_panel.set_canvas(self.canvas if self.doc is not None else None)
        self._update_status()
        self._update_actions()
        self._update_title()
        if self.canvas:
            self._zoom_changed(self.canvas.zoom)

    def _tab_changed(self, i):
        if i >= 0:
            self.switch_to(i)

    def _doc_title_changed(self, doc):
        for i, (d, c) in enumerate(self.docs):
            if d is doc:
                self.tabs.setTabText(i, d.title + (" *" if d.modified else ""))
                self.tabs.setTabToolTip(i, d.path or d.title)
        self._update_title()

    def _doc_history_changed(self, doc):
        if doc is self.doc:
            self._update_actions()
        self._thumb_timer.start(400)

    def _update_tab_thumbs(self):
        for i, (d, c) in enumerate(self.docs):
            self.tabs.setTabIcon(i, QIcon(thumb(d.composite(), 36)))

    def _update_title(self):
        d = self.doc
        if d is None:
            self.setWindowTitle(APP_NAME)
        else:
            self.setWindowTitle(f"{d.title}{' *' if d.modified else ''} - {APP_NAME}")

    def _update_status(self):
        d = self.doc
        self.size_label.setText(f"▣ {d.w} × {d.h}" if d else "")
        self.selection_info_changed()
        if self.canvas:
            self.canvas._layout()

    def _update_actions(self):
        d = self.doc
        has = d is not None
        for name, a in self.act.items():
            if name in ("new", "open", "scan", "quit", "about", "shortcuts", "paste_image", "swap_colors",
                        "brush_smaller", "brush_bigger", "pixel_grid", "rulers", "show_guides", "snap_guides"):
                continue
            a.setEnabled(has)
        if has:
            self.act["undo"].setEnabled(True)
            self.act["redo"].setEnabled(d.can_redo())
            self.act["merge_down"].setEnabled(d.active > 0)
            self.act["delete_layer"].setEnabled(len(d.layers) > 1)
            self.act["repeat_effect"].setEnabled(self.last_effect is not None)
            self.act["repeat_effect"].setText(f"Repeat {self.last_effect[0].name}" if self.last_effect else "Repeat Last Effect")
            self.act["clear_guides"].setEnabled(bool(d.guides))

    def _zoom_changed(self, z):
        self.zoom_combo.setEditText(f"{round(z * 100)}%")

    def _zoom_combo(self, *_):
        if not self.canvas:
            return
        t = self.zoom_combo.currentText().strip().rstrip("%")
        if t.lower() == "window":
            self.canvas.best_fit(); return
        try:
            self.canvas.set_zoom(float(t) / 100)
        except ValueError:
            self._zoom_changed(self.canvas.zoom)
        self.canvas.setFocus()

    def _cursor_moved(self, ip):
        if ip is None:
            self.pos_label.setText("")
        else:
            self.pos_label.setText(f"⌖ {int(np.floor(ip.x()))}, {int(np.floor(ip.y()))}")

    # =============================================================== file
    def do_new(self):
        cb = QApplication.clipboard().image()
        w, h = (cb.width(), cb.height()) if not cb.isNull() else (int(self.settings.value("new_w", 800)), int(self.settings.value("new_h", 600)))
        dlg = NewImageDialog(self, w, h)
        if dlg.exec():
            w, h = dlg.w.value(), dlg.h.value()
            self.settings.setValue("new_w", w); self.settings.setValue("new_h", h)
            bg = [QColor(255, 255, 255), QColor(self.colors.secondary), None][dlg.bg.checkedId()]
            self.add_document(Document(w, h, background=bg))

    def do_print(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        try:
            from . import printing
            printing.print_document(self, d, self.settings)
        except ImportError:
            QMessageBox.warning(self, "Print", "Printing support is missing from this copy of Paintwell.")

    def do_scan(self):
        from . import scanning
        img = scanning.scan(self, self.settings)
        if img is None:
            return
        n = getattr(self, "_scan_count", 0) + 1
        self._scan_count = n
        doc = Document(img.width(), img.height(), layers=[Layer(img, "Background")])
        doc.display_name = f"Scan {n}"
        self.add_document(doc)
        self.status_message(f"Scanned {img.width()} × {img.height()} pixels.")

    def do_open(self):
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        files = filedialogs.open_files(self, "Open", start, fileio.open_filter())
        for f in files:
            self.open_path(f)

    def open_path(self, path):
        path = os.path.abspath(path)
        for i, (d, c) in enumerate(self.docs):
            if d.path and os.path.abspath(d.path) == path:
                self.switch_to(i)
                return
        self.busy(True)
        try:
            doc = fileio.load(path)
        except Exception as ex:
            self.busy(False)
            QMessageBox.warning(self, "Open", f"Could not open {os.path.basename(path)}:\n{ex}")
            return
        self.busy(False)
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._add_recent(path)
        self.add_document(doc)

    def _add_recent(self, path):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [path] + [r for r in rec if r != path]
        self.settings.setValue("recent", rec[:10])

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            a = self.recent_menu.addAction(os.path.basename(r))
            a.setToolTip(r)
            a.triggered.connect(lambda _=False, r=r: self.open_path(r))
        if not rec:
            self.recent_menu.addAction("(empty)").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear list", lambda: self.settings.setValue("recent", []))

    def save_doc(self, doc, path=None):
        self.commit_tool()
        if path is None:
            path = doc.path
        if path is None or os.path.splitext(path)[1].lower().lstrip(".") not in (set(fileio.write_formats()) | {"ora", "jpg"}):
            return self.save_doc_as(doc)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext != "ora" and len(doc.layers) > 1:
            r = QMessageBox.question(self, "Flatten",
                                     f"The {ext.upper()} format does not support layers, so the image will be flattened "
                                     "in the saved file (your layers stay intact here).\n\n"
                                     "Tip: save as OpenRaster (.ora) to keep layers.",
                                     QMessageBox.Ok | QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return False
        quality = -1
        if ext in ("jpg", "jpeg", "webp"):
            q, ok = QInputDialog.getInt(self, "Save Configuration", "Quality:", int(self.settings.value("quality", 95)), 1, 100)
            if not ok:
                return False
            quality = q
            self.settings.setValue("quality", q)
        self.busy(True)
        try:
            fileio.save(doc, path, quality)
        except Exception as ex:
            self.busy(False)
            QMessageBox.warning(self, "Save", f"Could not save {os.path.basename(path)}:\n{ex}")
            return False
        self.busy(False)
        if doc.path != path:
            doc.path = path
        self._add_recent(path)
        doc.mark_saved()
        self.status_message(f"Saved {path}")
        return True

    def save_doc_as(self, doc):
        start = doc.path or os.path.join(self.settings.value("last_dir", os.path.expanduser("~")), doc.title + ".png")
        if doc.path is None and len(doc.layers) > 1:
            start = os.path.splitext(start)[0] + ".ora"
        filt = fileio.save_filter()
        sel = next((f for f in filt.split(";;") if f"*.{os.path.splitext(start)[1].lstrip('.').lower()})" in f), None)
        path, chosen = filedialogs.save_file(self, "Save As", start, filt, sel or "")
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            ext = chosen.split("*.")[-1].rstrip(")") if "*." in chosen else "png"
            path += "." + ext
        self.settings.setValue("last_dir", os.path.dirname(path))
        return self.save_doc(doc, path)

    def do_save(self):
        if self.doc:
            self.save_doc(self.doc)

    def do_save_as(self):
        if self.doc:
            self.save_doc_as(self.doc)

    def do_save_all(self):
        for d, c in list(self.docs):
            if d.modified or d.path is None:
                if not self.save_doc(d):
                    break

    def maybe_save(self, i):
        d = self.docs[i][0]
        if not d.modified:
            return True
        self.switch_to(i)
        r = QMessageBox.question(self, "Unsaved Changes", f"Save changes to \"{d.title}\" before closing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_doc(d)
        return True

    def close_document(self, i=None):
        if i is None or isinstance(i, bool):
            i = self.current
        if not (0 <= i < len(self.docs)):
            return False
        self.commit_tool()
        if not self.maybe_save(i):
            return False
        d, c = self.docs.pop(i)
        self.stack.removeWidget(c)
        c.deleteLater()
        self.tabs.blockSignals(True)
        self.tabs.removeTab(i)
        self.tabs.blockSignals(False)
        self.current = -1
        self.switch_to(min(i, len(self.docs) - 1))
        return True

    def do_close(self):
        self.close_document()

    def do_quit(self):
        self.close()

    def closeEvent(self, e):
        self.commit_tool()
        for i in range(len(self.docs)):
            if self.docs[i][0].modified and not self.maybe_save(i):
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState(LAYOUT_VERSION))
        e.accept()

    def dragEnterEvent(self, e):
        from .filedrop import can_accept
        if can_accept(e.mimeData()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        from .filedrop import can_accept
        if can_accept(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        from .filedrop import dropped_paths
        paths, blocked = dropped_paths(e.mimeData())
        e.acceptProposedAction()
        for p in paths:
            self.open_path(p)
        if blocked:
            names = "\n".join(os.path.basename(p) or p for p in blocked[:8])
            hint = ""
            if os.environ.get("SNAP"):
                hint = ("\n\nAs a snap, Paintwell can open files in your home folder. For USB drives and "
                        "other disks, allow it once with:\n    sudo snap connect paintwell:removable-media")
            QMessageBox.warning(self, APP_NAME, "Paintwell isn't allowed to read:\n" + names + hint)

    # =============================================================== edit
    def do_undo(self):
        if self.tool and self.tool.is_editing():
            self.tool.cancel()
            return
        if self.doc:
            self.doc.undo()

    def do_redo(self):
        self.commit_tool()
        if self.doc:
            self.doc.redo()

    def _selected_pixels(self, merged=False):
        d = self.doc
        r = d.selection_bounds()
        if r.isEmpty():
            return None, r
        src = d.composite() if merged else d.layer.image
        img = src.copy(r)
        if d.selection is not None:
            imaging.keep_inside(img, d.selection.translated(-r.x(), -r.y()))
        return img, r

    def do_copy(self, merged=False):
        if not self.doc:
            return
        self.commit_tool()
        img, r = self._selected_pixels(merged)
        if img is not None:
            QApplication.clipboard().setImage(img.convertToFormat(QImage.Format_ARGB32))

    def do_copy_merged(self):
        self.do_copy(True)

    def do_cut(self):
        if not self.doc:
            return
        self.do_copy()
        self._erase(commit_name="Cut")

    def _clip_image(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            QMessageBox.information(self, "Paste", "The clipboard does not contain an image.")
            return None
        return img.convertToFormat(imaging.FMT)

    def do_paste(self):
        d = self.doc
        if d is None:
            return self.do_paste_image()
        self.commit_tool()
        img = self._clip_image()
        if img is None:
            return
        if img.width() > d.w or img.height() > d.h:
            r = QMessageBox.question(self, "Image Larger than Canvas",
                                     "The image being pasted is larger than the canvas size. Expand the canvas?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                nw, nh = max(d.w, img.width()), max(d.h, img.height())
                self._resize_canvas(nw, nh, 0, 0)
                d.commit("Expand Canvas", "canvas_size")
        # paste at top-left of visible area
        c = self.canvas
        tl = c.to_image(QPointF(0, 0))
        pos = QPointF(max(0, min(int(tl.x()), d.w - 1)), max(0, min(int(tl.y()), d.h - 1)))
        if img.width() >= d.w:
            pos.setX(0)
        if img.height() >= d.h:
            pos.setY(0)
        self.select_tool("Move Selected Pixels")
        mt = self.tool
        mt.lift_from_image(img, pos)
        d.commit("Paste", "paste")
        mt.refresh_token()

    def do_paste_layer(self):
        if not self.doc:
            return
        self.commit_tool()
        if QApplication.clipboard().image().isNull():
            return self._clip_image()
        self.doc.add_layer()
        self.doc.commit("Add New Layer", "layer_add")
        self.do_paste()

    def do_paste_image(self):
        img = self._clip_image()
        if img is None:
            return
        doc = Document(img.width(), img.height(), layers=[Layer(img, "Background")])
        self.add_document(doc)

    def do_select_all(self):
        d = self.doc
        if d:
            self.commit_tool()
            p = QPainterPath(); p.addRect(QRectF(d.rect()))
            d.set_selection(p)
            d.commit("Select All", "select_all")

    def do_deselect(self):
        d = self.doc
        if d and d.selection is not None:
            self.commit_tool()
            d.set_selection(None)
            d.commit("Deselect", "deselect")

    def _erase(self, commit_name="Erase Selection"):
        d = self.doc
        if d is None or d.selection is None:
            if commit_name == "Cut" and d is not None:
                # cut without selection removes the whole layer contents
                d.layer.image = imaging.new_image(d.w, d.h)
                d.invalidate(); d.commit("Cut", "cut")
            return
        p = QPainter(d.layer.image)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillPath(d.selection, QColor(0, 0, 0, 255))
        p.end()
        d.invalidate(d.selection_bounds())
        d.commit(commit_name, "cut" if commit_name == "Cut" else "deselect")

    def do_erase_selection(self):
        self.commit_tool()
        self._erase()

    def do_fill_selection(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        p = QPainter(d.layer.image)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillPath(d.selection_path_or_all(), self.colors.primary)
        p.end()
        d.invalidate(d.selection_bounds())
        d.commit("Fill Selection", "bucket")
        self.colors.add_recent(self.colors.primary)

    def do_content_aware_fill(self):
        """Fill the selection with texture rebuilt from the surrounding picture (see inpaint.py)."""
        d = self.doc
        if not d:
            return
        if d.selection is None or d.selection.isEmpty():
            QMessageBox.information(self, "Content-Aware Fill",
                                    "Select the area you want to remove first (for example with the Lasso or "
                                    "Rectangle Select tool), then choose Content-Aware Fill.")
            return
        self.commit_tool()
        self.fill_from_surroundings(d.selection, "Content-Aware Fill", "magic_wand")

    def fill_from_surroundings(self, sel, title, icon, grow=2, piece=None):
        """Fill the area inside path sel on the current layer with texture rebuilt from the
        surrounding picture (Content-Aware Fill, also used by the Remove tool).

        piece: if given, the area is filled a piece (piece x piece pixels) at a time, each from
        its own nearby surroundings only.  Used for Remove strokes, so a long stroke crossing
        different things (sky, then the sun...) fills each part from what is next to it."""
        d = self.doc
        import threading
        from PySide6.QtWidgets import QProgressDialog
        from . import inpaint
        sb = sel.boundingRect().toAlignedRect().intersected(d.rect())
        if sb.isEmpty():
            return
        # work on a box around the selection: the hole plus surroundings to copy from
        margin = piece if piece else max(48, min(600, max(sb.width(), sb.height())))
        r = sb.adjusted(-margin, -margin, margin, margin).intersected(d.rect())
        region = d.layer.image.copy(r).convertToFormat(imaging.FMT)
        # the hole: every pixel the selection touches, grown a little so soft edges and
        # halos around the removed thing go too
        mimg = QImage(r.size(), QImage.Format_Grayscale8)
        mimg.fill(0)
        mp = QPainter(mimg)
        mp.setRenderHint(QPainter.Antialiasing)
        mp.translate(-r.x(), -r.y())
        mp.fillPath(sel, QColor(255, 255, 255))
        mp.end()
        bm = mimg.bytesPerLine()
        hole = np.frombuffer(mimg.constBits(), np.uint8, count=bm * r.height()).reshape(r.height(), bm)[:, :r.width()] > 0
        hole = inpaint._dilate(hole, grow)
        arr = imaging.to_array(region, False)
        # the jobs: (box to keep, box to work in) in region coordinates
        H, W = hole.shape
        if piece:
            jobs = []
            for y0 in range(0, H, piece):
                for x0 in range(0, W, piece):
                    y1, x1 = min(H, y0 + piece), min(W, x0 + piece)
                    if hole[y0:y1, x0:x1].any():
                        jobs.append(((x0, y0, x1, y1), (max(0, x0 - piece), max(0, y0 - piece),
                                                         min(W, x1 + piece), min(H, y1 + piece))))
        else:
            jobs = [((0, 0, W, H), (0, 0, W, H))]
        state = {"done": False, "ok": False, "error": None, "progress": 0.0, "cancel": False}

        def work():
            try:
                left = hole.copy()
                for n, ((kx0, ky0, kx1, ky1), (wx0, wy0, wx1, wy1)) in enumerate(jobs):
                    sub = arr[wy0:wy1, wx0:wx1]
                    sub_hole = left[wy0:wy1, wx0:wx1]       # everything not yet filled is unknown
                    if not sub_hole.any():
                        continue
                    prog = lambda f, n=n: state.__setitem__("progress", (n + f) / len(jobs))
                    cancel = lambda: state["cancel"]
                    if piece and inpaint.hole_depth(sub_hole, 13) <= 12:
                        # thin (a wire, a scratch, a blemish): carry the surroundings in smoothly
                        res = inpaint.fill_thin(sub, sub_hole, progress=prog, cancelled=cancel)
                    else:
                        res = inpaint.fill(sub, sub_hole, progress=prog, cancelled=cancel)
                    keep = np.zeros_like(sub_hole)
                    keep[ky0 - wy0:ky1 - wy0, kx0 - wx0:kx1 - wx0] = True
                    keep &= sub_hole
                    sub[keep] = res[keep]
                    left[wy0:wy1, wx0:wx1][keep] = False
                state["ok"] = True
            except inpaint.Cancelled:
                pass
            except Exception as ex:          # e.g. not enough picture to sample from
                state["error"] = str(ex) or ex.__class__.__name__
            state["done"] = True

        dlg = QProgressDialog("Filling in from the surroundings...", "Cancel", 0, 100, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.setValue(0)
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        t = threading.Thread(target=work, name="content-aware-fill", daemon=True)
        t.start()
        while not state["done"]:
            QApplication.processEvents(QEventLoop.AllEvents, 50)
            if not state["cancel"]:
                dlg.setValue(int(state["progress"] * 100))
            t.join(0.03)
        dlg.reset()
        dlg.close()
        if state["error"]:
            QMessageBox.warning(self, title, state["error"])
            return
        if not state["ok"]:
            return                            # cancelled
        # copy just the filled pixels back into the layer
        res = imaging.from_array(np.clip(arr, 0, 1), False).convertToFormat(imaging.FMT)
        bpl = region.bytesPerLine()
        dst = np.frombuffer(region.bits(), np.uint8, count=bpl * r.height()).reshape(r.height(), bpl)[:, :r.width() * 4].reshape(r.height(), r.width(), 4)
        rb = res.bytesPerLine()
        src = np.frombuffer(res.constBits(), np.uint8, count=rb * r.height()).reshape(r.height(), rb)[:, :r.width() * 4].reshape(r.height(), r.width(), 4)
        dst[hole] = src[hole]
        p = QPainter(d.layer.image)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(r.topLeft(), region)
        p.end()
        d.invalidate(r)
        d.commit(title, icon)

    def do_invert_selection(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        full = QPainterPath(); full.addRect(QRectF(d.rect()))
        if d.selection is None:
            d.set_selection(QPainterPath())
        else:
            d.set_selection(full.subtracted(d.selection))
        d.commit("Invert Selection", "select_all")

    # =============================================================== view
    def do_zoom_in(self):
        if self.canvas:
            self.canvas.zoom_in()

    def do_zoom_out(self):
        if self.canvas:
            self.canvas.zoom_out()

    def do_best_fit(self):
        if self.canvas:
            self.canvas.best_fit()

    def do_actual_size(self):
        if self.canvas:
            self.canvas.set_zoom(1.0)

    def do_zoom_selection(self):
        if self.canvas and self.doc:
            self.canvas.zoom_to_rect(QRectF(self.doc.selection_bounds()))

    # ------------------------------------------------------------ rulers and guides
    def _toggle_rulers(self, on):
        self.settings.setValue("rulers", on)
        for d, c in self.docs:
            c.set_rulers(on)

    def set_guides_visible(self, on):
        self.settings.setValue("show_guides", on)
        a = self.act["show_guides"]
        if a.isChecked() != bool(on):
            a.blockSignals(True); a.setChecked(bool(on)); a.blockSignals(False)
        for d, c in self.docs:
            c.show_guides = bool(on)
            c.viewport().update()

    def _toggle_snap(self, on):
        self.settings.setValue("snap_guides", on)
        for d, c in self.docs:
            c.snap_guides = bool(on)

    def guides_changed(self):
        n = len(self.doc.guides) if self.doc else 0
        self.act["clear_guides"].setEnabled(n > 0)

    def do_clear_guides(self):
        if self.doc and self.canvas:
            self.doc.guides.clear()
            self.canvas.viewport().update()
            self.guides_changed()

    def do_new_guide(self):
        d, c = self.doc, self.canvas
        if not d or not c:
            return
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("New Guide")
        form = QFormLayout(dlg)
        orient = QComboBox()
        orient.addItem("Horizontal (across)", "h")
        orient.addItem("Vertical (up and down)", "v")
        pos = QSpinBox()
        pos.setRange(-100000, 100000)
        pos.setSuffix(" px")

        def default_pos():
            pos.setValue(d.h // 2 if orient.currentData() == "h" else d.w // 2)
        orient.currentIndexChanged.connect(lambda _i: default_pos())
        default_pos()
        hint = QLabel()
        hint.setStyleSheet("color: gray")

        def show_hint():
            size = d.h if orient.currentData() == "h" else d.w
            hint.setText(f"from the {'top' if orient.currentData() == 'h' else 'left'} edge (picture is {size} px)")
        orient.currentIndexChanged.connect(lambda _i: show_hint())
        show_hint()
        form.addRow("Orientation:", orient)
        form.addRow("Position:", pos)
        form.addRow("", hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            d.guides.append([orient.currentData(), pos.value()])
            if not c.show_guides:
                self.set_guides_visible(True)
            c.viewport().update()
            self.guides_changed()

    def _toggle_grid(self, on):
        self.settings.setValue("pixel_grid", on)
        for d, c in self.docs:
            c.show_grid = on
            c.viewport().update()

    def do_swap_colors(self):
        self.colors.swap()

    def do_brush_smaller(self):
        w = self.opts["brush_width"]
        self.set_option("brush_width", max(1, w - max(1, int(w * 0.15))))
        self.canvas_update()

    def do_brush_bigger(self):
        w = self.opts["brush_width"]
        self.set_option("brush_width", min(2000, w + max(1, int(w * 0.15))))
        self.canvas_update()

    # =============================================================== image
    def do_crop(self):
        d = self.doc
        if not d or d.selection is None:
            return
        self.commit_tool()
        r = d.selection_bounds()
        if r.isEmpty():
            return
        sel = d.selection.translated(-r.x(), -r.y())

        def fn(img):
            return imaging.keep_inside(img.copy(r), sel)
        d.transform_all(fn, r.width(), r.height(), QTransform().translate(-r.x(), -r.y()))
        d.selection = None
        d.selectionChanged.emit()
        d.commit("Crop to Selection", "crop")
        self.canvas.best_fit()

    def do_resize(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        dlg = ResizeDialog(self, d.w, d.h)
        if not dlg.exec():
            return
        w, h = dlg.result_size()
        if (w, h) == (d.w, d.h):
            return
        mode = Qt.SmoothTransformation if dlg.smooth() else Qt.FastTransformation
        sx, sy = w / d.w, h / d.h
        self.busy(True)
        d.transform_all(lambda img: img.scaled(w, h, Qt.IgnoreAspectRatio, mode), w, h, QTransform().scale(sx, sy))
        self.busy(False)
        d.commit("Resize Image", "resize")
        self.canvas.best_fit()

    def _resize_canvas(self, nw, nh, dx, dy):
        d = self.doc
        bg = QColor(self.colors.secondary)
        first = d.layers[0]

        def fn(img):
            out = imaging.new_image(nw, nh)
            p = QPainter(out)
            if img is first.image and first.name == "Background":
                p.fillRect(out.rect(), bg)
                p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(dx, dy, img)
            p.end()
            return out
        d.transform_all(fn, nw, nh, QTransform().translate(dx, dy))

    def do_split_image(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        r = d.selection_bounds()
        if r.isEmpty():
            return
        src = d.flattened().copy(r)
        if d.selection is not None:
            # leave pixels outside an oval/freehand selection transparent
            mask = QImage(src.size(), src.format()); mask.fill(Qt.transparent)
            p = QPainter(mask); p.translate(-r.x(), -r.y()); p.setClipPath(d.selection)
            p.drawImage(r.topLeft(), src); p.end()
            src = mask
        stem = os.path.splitext(d.title)[0].rstrip(" *") or "image"
        dlg = SplitImageDialog(self, src, stem, d.selection is not None, self.settings)
        if not dlg.exec():
            return
        rects = dlg.rects()
        if not rects:
            return
        rows, cols = rects[-1][0] + 1, rects[-1][1] + 1
        if dlg.o_open.isChecked():
            if len(rects) > 40 and QMessageBox.question(
                    self, "Split Image", f"This will open {len(rects)} new images. Carry on?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.busy(True)
            try:
                for (row, col, x, y, w, h) in rects:
                    piece = src.copy(x, y, w, h).convertToFormat(src.format())
                    nd = Document(w, h, layers=[Layer(piece, "Background")])
                    nd.display_name = f"{stem} ({row + 1},{col + 1})"
                    nd.saved_index = -10   # new, unsaved
                    nd.titleChanged.emit()
                    self.add_document(nd)
            finally:
                self.busy(False)
            self.status_message(f"Split into {len(rects)} new images")
            return
        folder = dlg.folder.text().strip()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Split Image", f"Couldn't use the folder:\n{e}")
            return
        names = [dlg.file_name(row, col, rows, cols) for (row, col, *_r) in rects]
        clash = [n for n in names if os.path.exists(os.path.join(folder, n))]
        if clash and QMessageBox.question(
                self, "Split Image", f"{len(clash)} file(s) with these names already exist in that folder "
                f"(e.g. {clash[0]}).\nReplace them?", QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        self.busy(True)
        failed = []
        try:
            for (row, col, x, y, w, h), n in zip(rects, names):
                try:
                    fileio.save_image(src.copy(x, y, w, h), os.path.join(folder, n))
                except Exception as e:
                    failed.append(f"{n}: {e}")
        finally:
            self.busy(False)
        if failed:
            QMessageBox.warning(self, "Split Image", f"{len(failed)} piece(s) couldn't be saved:\n" + "\n".join(failed[:10]))
        else:
            self.status_message(f"Saved {len(rects)} pieces to {folder}")

    def do_canvas_size(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        dlg = CanvasSizeDialog(self, d.w, d.h)
        if not dlg.exec():
            return
        nw, nh = dlg.result_size()
        if (nw, nh) == (d.w, d.h):
            return
        dx, dy = dlg.anchor_offset(nw, nh)
        self._resize_canvas(nw, nh, dx, dy)
        d.commit("Canvas Size", "canvas_size")
        self.canvas.best_fit()

    def _flip_rotate(self, kind, layer_only=False):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        w, h = d.w, d.h
        T = {"flip_h": QTransform(-1, 0, 0, 1, w, 0), "flip_v": QTransform(1, 0, 0, -1, 0, h),
             "rot_cw": QTransform(0, 1, -1, 0, h, 0), "rot_ccw": QTransform(0, -1, 1, 0, 0, w),
             "rot_180": QTransform(-1, 0, 0, -1, w, h)}[kind]
        names = {"flip_h": "Flip Horizontal", "flip_v": "Flip Vertical", "rot_cw": "Rotate 90° Clockwise",
                 "rot_ccw": "Rotate 90° Counter-clockwise", "rot_180": "Rotate 180°"}

        def fn(img):
            return img.transformed(T).convertToFormat(imaging.FMT)
        if layer_only:
            d.layer.image = fn(d.layer.image)
            d.invalidate()
            d.layersChanged.emit()
            d.commit(names[kind].replace("Flip", "Flip Layer"), kind)
            return
        if kind in ("rot_cw", "rot_ccw"):
            d.transform_all(fn, h, w, T)
        else:
            d.transform_all(fn, w, h, T)
        d.commit(names[kind], kind)
        if kind in ("rot_cw", "rot_ccw"):
            self.canvas.best_fit()

    def do_flip_h(self):
        self._flip_rotate("flip_h")

    def do_flip_v(self):
        self._flip_rotate("flip_v")

    def do_rot_cw(self):
        self._flip_rotate("rot_cw")

    def do_rot_ccw(self):
        self._flip_rotate("rot_ccw")

    def do_rot_180(self):
        self._flip_rotate("rot_180")

    def do_flatten(self):
        d = self.doc
        if d and len(d.layers) > 1:
            self.commit_tool()
            d.flatten()
            d.commit("Flatten", "flatten")

    # =============================================================== layers
    def do_add_layer(self):
        d = self.doc
        if d:
            self.commit_tool()
            d.add_layer()
            d.commit("Add New Layer", "layer_add")

    def do_delete_layer(self):
        d = self.doc
        if d:
            self.commit_tool()
            if d.delete_layer():
                d.commit("Delete Layer", "layer_delete")

    def do_duplicate_layer(self):
        d = self.doc
        if d:
            self.commit_tool()
            d.duplicate_layer()
            d.commit("Duplicate Layer", "layer_dup")

    def do_merge_down(self):
        d = self.doc
        if d:
            self.commit_tool()
            if d.merge_down():
                d.commit("Merge Layer Down", "layer_merge")

    def do_layer_up(self):
        d = self.doc
        if d:
            self.commit_tool()
            if d.move_layer(1):
                d.commit("Move Layer Up", "layer_up")

    def do_layer_down(self):
        d = self.doc
        if d:
            self.commit_tool()
            if d.move_layer(-1):
                d.commit("Move Layer Down", "layer_down")

    def do_layer_flip_h(self):
        self._flip_rotate("flip_h", True)

    def do_layer_flip_v(self):
        self._flip_rotate("flip_v", True)

    def do_import_layer(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        path = filedialogs.open_file(self, "Import From File", self.settings.value("last_dir", os.path.expanduser("~")),
                                     fileio.open_filter())
        if not path:
            return
        try:
            src = fileio.load(path).flattened()
        except Exception as ex:
            QMessageBox.warning(self, "Import", str(ex))
            return
        img = imaging.new_image(d.w, d.h)
        p = QPainter(img); p.drawImage(0, 0, src); p.end()
        d.add_layer(img, os.path.basename(path))
        d.commit("Import From File", "layer_add")

    def do_layer_properties(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        L = d.layer

        def change(v):
            L.name, L.visible, L.blend, L.opacity = v["name"], v["visible"], v["blend"], v["opacity"]
            d.invalidate()
        dlg = LayerPropertiesDialog(self, L, change)
        if dlg.exec():
            change(dlg.values())
            d.layersChanged.emit()
            d.commit("Layer Properties", "layer_props")
        else:
            d.revert()

    def do_layer_rotate_zoom(self):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        base = QImage(d.layer.image)
        sel = d.selection

        def preview(v):
            out = imaging.new_image(d.w, d.h)
            p = QPainter(out)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.setRenderHint(QPainter.Antialiasing)
            p.translate(d.w / 2 + v["pan_x"], d.h / 2 + v["pan_y"])
            p.rotate(v["angle"])
            p.scale(v["zoom"], v["zoom"])
            p.translate(-d.w / 2, -d.h / 2)
            p.drawImage(0, 0, base)
            p.end()
            if sel is not None:
                final = base.copy()
                q = QPainter(final)
                q.setClipPath(sel)
                q.setCompositionMode(QPainter.CompositionMode_Source)
                q.drawImage(0, 0, out)
                q.end()
                out = final
            d.layer.image = out
            d.invalidate()
        params = [("angle", "Angle", "float", 0.0, -180, 180), ("pan_x", "Pan X", "int", 0, -d.w, d.w),
                  ("pan_y", "Pan Y", "int", 0, -d.h, d.h), ("zoom", "Zoom", "float", 1.0, 0.05, 8)]
        dlg = EffectDialog(self, "Layer Rotate / Zoom", params, preview)
        if dlg.exec():
            preview(dlg.values())
            d.commit("Rotate / Zoom Layer", "rot_cw")
        else:
            d.revert()

    # =============================================================== adjustments & effects
    def _histogram(self, arr):
        a = arr[..., 3] > 0
        if not a.any():
            return None
        return [np.histogram(arr[..., c][a], bins=256, range=(0, 1))[0] for c in range(3)]

    def run_effect(self, eff, icon="effects", params=None):
        d = self.doc
        if not d:
            return
        self.commit_tool()
        if eff.grow is not None and (d.selection is None or eff.name == "Drop Shadow"):
            return self._run_growing_effect(eff, icon, params)
        base = QImage(d.layer.image)
        r = d.selection_bounds()
        if r.isEmpty():
            return
        src = Source(base.copy(r))
        ctx = {"primary": QColor(self.colors.primary), "secondary": QColor(self.colors.secondary), "mask": None}
        sel = d.selection
        if sel is not None:
            mimg = QImage(r.size(), QImage.Format_Grayscale8)
            mimg.fill(0)
            mp = QPainter(mimg)
            mp.translate(-r.x(), -r.y())
            mp.fillPath(sel, QColor(255, 255, 255))
            mp.end()
            ptr = mimg.constBits()
            ctx["mask"] = (np.frombuffer(ptr, np.uint8).reshape(r.height(), mimg.bytesPerLine())[:, :r.width()] > 127).copy()
        state = {"last": None}

        def apply(p, img):
            final = base.copy()
            q = QPainter(final)
            if sel is not None:
                q.setClipPath(sel)
            q.setCompositionMode(QPainter.CompositionMode_Source)
            q.drawImage(r.topLeft(), img)
            q.end()
            d.layer.image = final
            d.invalidate(r)
            state["last"] = repr(p)

        def failed(ex):
            if state.get("error"):
                return
            state["error"] = str(ex) or ex.__class__.__name__
            d.revert()
            QMessageBox.warning(self, eff.name, f"{eff.name} couldn't be applied:\n\n{state['error']}")

        # previews are worked out on a background thread so the window stays responsive while
        # you drag a slider; only the newest settings are ever drawn
        worker = PreviewWorker(lambda p, cancelled: eff.render(src, p, dict(ctx, cancelled=cancelled)), self)
        worker.ready.connect(lambda p, img: (None if state.get("error") or state.get("closed") else apply(p, img)))
        worker.failed.connect(lambda p, ex: None if state.get("closed") else failed(ex))
        worker.busy.connect(lambda on: self.status_message("Working out the preview..." if on else ""))

        def preview(p):
            if not state.get("error"):
                worker.request(p)

        def done(p):
            worker.wait()
            if state.get("error"):
                d.revert()
                return
            if state["last"] != repr(p):
                try:
                    apply(p, eff.render(src, p, ctx))
                except Exception as ex:
                    failed(ex)
                    d.revert()
                    return
            state["closed"] = True
            d.commit(eff.name, icon)
            self.last_effect = (eff, icon, p)
            self.last_params[eff.name] = p
            self._update_actions()

        if params is not None:
            self.busy(True)
            try:
                done(params)
            finally:
                self.busy(False)
            return
        if eff.custom == "curves":
            u = src.straight
            hist = None
            if (u[..., 3] > 0).any():
                hist = np.histogram(imaging.luminance(u)[u[..., 3] > 0], bins=256, range=(0, 1))[0]
            dlg = CurvesDialog(self, preview, hist, self.last_params.get(eff.name))
        elif eff.custom == "levels":
            u = src.straight
            hist = self._histogram(u)
            auto = None
            if hist is not None:
                y = imaging.luminance(u)[u[..., 3] > 0]
                auto = (int(np.percentile(y, 0.5) * 255), max(1, int(np.percentile(y, 99.5) * 255)))
            dlg = LevelsDialog(self, preview, hist, auto)
        elif eff.needs_dialog:
            dlg = EffectDialog(self, eff.name, eff.params, preview, self.last_params.get(eff.name),
                               icons.icon(icon))
        else:
            self.busy(True)
            try:
                done({})
            finally:
                self.busy(False)
            return
        if dlg.exec():
            self.busy(True)
            try:
                done(dlg.values())
            finally:
                self.busy(False)
        else:
            state["closed"] = True
            worker.wait()
            d.revert()
        self.status_message("")

    def _run_growing_effect(self, eff, icon, params):
        """Effects that may add room outside the picture (borders, frames, drop shadow).
        Each preview starts again from the last saved state, grows the canvas if asked, then
        paints on the whole active layer."""
        d = self.doc
        ctx = {"primary": QColor(self.colors.primary), "secondary": QColor(self.colors.secondary)}
        state = {"last": None, "size": (d.w, d.h)}
        # what's behind the picture when it has to make room inside the layer
        bottom = d.layers[0]
        ctx["background"] = QColor(self.colors.secondary) if (d.layer is bottom and bottom.name == "Background") else None
        on_selection = d.selection is not None
        if on_selection:
            # the selected part is the object that casts the shadow (the canvas doesn't grow)
            mimg = QImage(d.w, d.h, QImage.Format_Grayscale8)
            mimg.fill(0)
            mp = QPainter(mimg)
            mp.setRenderHint(QPainter.Antialiasing)
            mp.fillPath(d.selection, QColor(255, 255, 255))
            mp.end()
            ctx["object_mask"] = (np.frombuffer(mimg.constBits(), np.uint8)
                                  .reshape(d.h, mimg.bytesPerLine())[:, :d.w].astype(np.float32) / 255.0)

        def preview(p):
            if state.get("error"):
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                d.revert()
                l, t, r, b = (0, 0, 0, 0) if on_selection else (int(v) for v in eff.grow(p))
                if l or t or r or b:
                    self._resize_canvas(d.w + l + r, d.h + t + b, l, t)
                ctx["grown"] = (l, t, r, b)
                d.layer.image = eff.render(Source(QImage(d.layer.image)), p, ctx)
                d.invalidate()
                if (d.w, d.h) != state["size"]:
                    state["size"] = (d.w, d.h)
                    if self.canvas:
                        self.canvas.best_fit()
                state["last"] = repr(p)
            except Exception as ex:
                state["error"] = str(ex) or ex.__class__.__name__
                d.revert()
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, eff.name, f"{eff.name} couldn't be applied:\n\n{state['error']}")
                return
            QApplication.restoreOverrideCursor()

        def done(p):
            if state["last"] != repr(p):
                preview(p)
            if state.get("error"):
                d.revert()
                return
            d.commit(eff.name, icon)
            self.last_effect = (eff, icon, p)
            self.last_params[eff.name] = p
            self._update_actions()

        if params is not None:
            preview(params); done(params)
            return
        dlg = EffectDialog(self, eff.name, eff.params, preview, self.last_params.get(eff.name), icons.icon(icon))
        if dlg.exec():
            done(dlg.values())
        else:
            d.revert()
            if self.canvas and (d.w, d.h) != state["size"]:
                self.canvas.best_fit()

    def do_repeat_effect(self):
        if self.last_effect and self.doc:
            eff, icon, p = self.last_effect
            self.run_effect(eff, icon, p)

    # =============================================================== help
    def do_about(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame
        dlg = QDialog(self)
        dlg.setWindowTitle(f"About {APP_NAME}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 16)
        lay.setSpacing(14)
        top = QHBoxLayout(); top.setSpacing(18)
        logo = QLabel()
        pm = QPixmap.fromImage(icons.app_icon_image(192)); pm.setDevicePixelRatio(2.0)
        logo.setPixmap(pm); logo.setAlignment(Qt.AlignTop)
        top.addWidget(logo)
        head = QVBoxLayout(); head.setSpacing(2)
        title = QLabel(f"<span style='font-size:22pt; font-weight:600'>{APP_NAME}</span>")
        ver = QLabel(f"Version {VERSION}")
        ver.setStyleSheet("font-size: 10pt;")
        tag = QLabel("A friendly, capable image editor for Linux,<br>with a familiar-style layout.")
        tag.setStyleSheet("font-size: 11pt;")
        head.addStretch(1); head.addWidget(title); head.addWidget(ver); head.addSpacing(6); head.addWidget(tag); head.addStretch(1)
        top.addLayout(head, 1)
        lay.addLayout(top)
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
        lay.addWidget(line)
        feats = [
            ("Tools", "Selections, Magic Wand, Magnetic Lasso, brushes, Clone Stamp, Remove, Red Eye, "
                      "Blur, Sharpen, Smudge, Dodge, Burn, Sponge, Text and Shapes."),
            ("Layers", "Blend modes, opacity and editable text layers, saved in OpenRaster (.ora) format."),
            ("Adjustments", "Levels, Curves, Hue / Saturation, Auto-Level and more."),
            ("Effects", "Artistic, Blurs, Borders and frames, Distort, Photo and Render (Clouds, Lens Flare, "
                        "Lighting Effects, fractals, Starfield, Rain and Snow), and more."),
            ("Smart fixes", "Content-Aware Fill and the Remove tool."),
            ("Everything else", "Unlimited history, the Navigator, rulers and guides, printing and scanning."),
        ]
        rows = "".join(f"<tr><td style='padding:2px 12px 2px 0; white-space:nowrap'><b>{a}</b></td>"
                       f"<td style='padding:2px 0'>{b}</td></tr>" for a, b in feats)
        body = QLabel(f"<table>{rows}</table>")
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        lay.addWidget(body)
        credits = QLabel("Made by <b>bobwellapps</b>. Copyright \u00a9 2026 bobwellapps.<br>"
                         "Paintwell is free software, licensed under the GNU General Public "
                         "License, version 3 or later (GPL-3.0-or-later). It comes with no warranty.<br>"
                         "The Paintwell name and icon may not be used for other projects.<br>"
                         "Built with Python, NumPy and Qt for Python (PySide6). "
                         "Qt is used under the GNU LGPL v3.")
        credits.setWordWrap(True)
        credits.setStyleSheet("font-size: 9pt;")
        lay.addWidget(credits)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        lic = bb.addButton("Licences...", QDialogButtonBox.ActionRole)
        lic.clicked.connect(self._show_licences)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        body.setFixedWidth(510); credits.setFixedWidth(510)
        lay.setSizeConstraint(QVBoxLayout.SetFixedSize)
        dlg.exec()

    def _show_licences(self):
        here = os.path.dirname(os.path.abspath(__file__))
        cands = [os.path.join(here, "..", "..", "..", "usr", "share", "doc", "paintwell"),
                 os.path.join(os.environ.get("APPDIR", ""), "usr", "share", "doc", "paintwell"),
                 os.path.join(os.environ.get("SNAP", ""), "usr", "share", "doc", "paintwell")]
        folder = next((c for c in cands if os.path.isfile(os.path.join(c, "THIRD-PARTY-NOTICES.txt"))), None)
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QDialogButtonBox, QTabWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Licences")
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        tabs = QTabWidget()
        files = ["THIRD-PARTY-NOTICES.txt", "LGPL-3.0.txt", "GPL-3.0.txt", "Python-LICENSE.txt", "NumPy-LICENSE.txt", "LGPL-2.1.txt", "Apache-2.0.txt"]
        for fn in files:
            text = None
            if folder:
                try:
                    with open(os.path.join(folder, fn), encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    pass
            if text is None:
                if fn != files[0]:
                    continue
                text = ("Licence files are installed with the AppImage build.\n\n"
                        "Qt / PySide6: LGPL-3.0   Python: PSF Licence   NumPy: BSD-3-Clause")
            ed = QPlainTextEdit(text); ed.setReadOnly(True)
            ed.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            tabs.addTab(ed, fn.replace(".txt", "").replace("THIRD-PARTY-NOTICES", "Notices"))
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(dlg.reject); lay.addWidget(bb)
        dlg.exec()

    def do_shortcuts(self):
        rows = [("Tools", ""), ("S", "Selection tools (press again to cycle)"), ("M", "Move tools"),
                ("Z / H", "Zoom / Pan"), ("F / G", "Paint Bucket / Gradient"), ("B / E / P", "Paintbrush / Eraser, Magic Eraser / Pencil"),
                ("K", "Colour Picker"), ("L", "Clone Stamp"), ("C", "Crop (Enter to crop, Esc to reset)"), ("J", "Remove / Red Eye"), ("R", "Recolour, Blur, Sharpen, Smudge (press again to switch)"), ("T", "Text"), ("O", "Line/Curve, Shapes"), ("D", "Dodge, Burn, Sponge (press again to switch)"),
                ("X", "Swap primary and secondary colours"), ("[  ]", "Brush size"), ("Space / middle drag", "Pan the view"),
                ("Mouse wheel", "Zoom in / out at the cursor"), ("Shift or Alt + wheel", "Scroll left / right"), ("Ctrl + wheel", "Scroll up / down"),
                ("Shift+Backspace", "Content-Aware Fill (fill the selection from its surroundings)"), ("Ctrl+Alt+R", "Show / hide rulers"), ("Drag from a ruler", "Add a guide (drag it back to remove)"),
                ("Ctrl+;  /  Ctrl+Shift+;", "Show guides / snap to guides"), ("Enter / Esc", "Finish / cancel shapes, lines, text, gradients"),
                ("Selection", ""), ("Ctrl + drag", "Add to selection"), ("Alt or right-drag", "Subtract from selection"),
                ("Ctrl+A / Ctrl+D / Ctrl+I", "Select all / Deselect / Invert"), ("Delete / Backspace", "Erase / fill selection"),
                ("Windows", ""), ("F5 F6 F7 F8 F9", "Tools, History, Layers, Colours, Navigator")]
        html = "<table cellspacing=4>" + "".join(
            f"<tr><td colspan=2><b>{a}</b></td></tr>" if not b else f"<tr><td><code>{a}</code></td><td>{b}</td></tr>"
            for a, b in rows) + "</table>"
        QMessageBox.information(self, "Keyboard Shortcuts", html)


# =================================================================== theme
def apply_theme(app, key):
    app.setStyle(QStyleFactory.create("Fusion"))
    dark = key == "dark"
    if key == "system":
        hint = QGuiApplication.styleHints().colorScheme()
        dark = hint == Qt.ColorScheme.Dark
    from . import icons as _icons
    _icons.set_dark(dark)
    pal = QPalette()
    if dark:
        win, base, text, btn = QColor(43, 43, 46), QColor(30, 30, 32), QColor(235, 235, 238), QColor(52, 52, 56)
        hl = QColor(64, 150, 240)
        alt = QColor(38, 38, 41)
    else:
        win, base, text, btn = QColor(243, 243, 245), QColor(255, 255, 255), QColor(25, 25, 28), QColor(250, 250, 252)
        hl = QColor(0, 103, 192)
        alt = QColor(247, 247, 249)
    pal.setColor(QPalette.Window, win)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, alt)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, btn)
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, base)
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Highlight, hl)
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.Link, hl)
    dim = QColor(text); dim.setAlpha(110)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, dim)
    pal.setColor(QPalette.PlaceholderText, dim)
    app.setPalette(pal)
    border = "#45454a" if dark else "#d6d6dc"
    app.setStyleSheet(f"""
        QToolBar {{ border: none; spacing: 2px; padding: 2px; }}
        QToolBar#options_toolbar {{ border-top: 1px solid {border}; border-bottom: 1px solid {border}; }}
        QDockWidget {{ titlebar-close-icon: none; }}
        QDockWidget::title {{ padding: 4px 6px; background: {alt.name()}; border-bottom: 1px solid {border}; }}
        QTabBar::tab {{ padding: 2px 4px; margin: 1px; border: 1px solid transparent; border-radius: 4px; }}
        QTabBar::tab:selected {{ border: 1px solid {hl.name()}; background: {hl.name()}33; }}
        QStatusBar {{ border-top: 1px solid {border}; }}
        QStatusBar QLabel {{ padding: 0 6px; }}
    """)
