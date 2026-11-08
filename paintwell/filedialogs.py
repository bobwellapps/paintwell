"""Open / Save dialogs with a typed Location bar (like a Windows address bar) and an image preview.

Qt's own file dialog is used (not the desktop's), so it looks and works the same everywhere and
always lets you type or paste a path."""
import os
from PySide6.QtCore import Qt, QDir, QUrl, QStandardPaths, QTimer, QSize
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QTreeView, QHeaderView, QFileDialog, QLineEdit, QCompleter, QFileSystemModel, QLabel, QComboBox,
                               QSplitter, QVBoxLayout, QWidget, QFrame, QSizePolicy)


class _Preview(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setFrameShape(QFrame.StyledPanel)
        self.pic.setMinimumSize(170, 150)
        self.pic.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setWordWrap(True)
        lay.addWidget(self.pic, 1)
        lay.addWidget(self.info)
        self.path = None

    def show_file(self, path):
        self.path = path
        if not path or not os.path.isfile(path):
            self.pic.clear(); self.pic.setText("No preview"); self.info.clear()
            return
        r = QImageReader(path)
        r.setAutoTransform(True)
        size = r.size()
        box = self.pic.size() - QSize(8, 8)
        if size.isValid() and size.width() > 0:
            r.setScaledSize(size.scaled(box.boundedTo(size), Qt.KeepAspectRatio))
        img = r.read()
        if img.isNull():
            self.pic.clear(); self.pic.setText("No preview")
            self.info.setText(os.path.basename(path))
            return
        self.pic.setPixmap(QPixmap.fromImage(img))
        kb = os.path.getsize(path) / 1024
        sz = f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
        dims = f"{size.width()} × {size.height()} px, " if size.isValid() else ""
        self.info.setText(f"{dims}{sz}")


class PathFileDialog(QFileDialog):
    def __init__(self, parent, title, directory, filters, save=False, folder=False, multi=False):
        super().__init__(parent, title, directory, filters or "")
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        self.save = save
        if folder:
            self.setFileMode(QFileDialog.Directory)
            self.setOption(QFileDialog.ShowDirsOnly, True)
        elif save:
            self.setAcceptMode(QFileDialog.AcceptSave)
            self.setFileMode(QFileDialog.AnyFile)
        else:
            self.setFileMode(QFileDialog.ExistingFiles if multi else QFileDialog.ExistingFile)
        self.setViewMode(QFileDialog.Detail)
        self._sidebar()
        self._location_bar()
        self.preview = None
        if not folder:
            self._add_preview()
        tv = self.findChild(QTreeView, "treeView")
        if tv is not None:
            h = tv.header()
            h.setStretchLastSection(False)
            h.setSectionResizeMode(0, QHeaderView.Stretch)
            for i in (1, 2, 3):
                h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.resize(980, 600)

    # ---------------------------------------------------------------- sidebar
    def _sidebar(self):
        urls = [QUrl.fromLocalFile(QDir.homePath())]
        for loc in (QStandardPaths.DesktopLocation, QStandardPaths.DocumentsLocation,
                    QStandardPaths.DownloadLocation, QStandardPaths.PicturesLocation):
            p = QStandardPaths.writableLocation(loc)
            if p and os.path.isdir(p) and QUrl.fromLocalFile(p) not in urls:
                urls.append(QUrl.fromLocalFile(p))
        urls.append(QUrl.fromLocalFile("/"))
        for media in ("/media/" + os.environ.get("USER", ""), "/run/media/" + os.environ.get("USER", ""), "/mnt"):
            if os.path.isdir(media):
                urls.append(QUrl.fromLocalFile(media))
        self.setSidebarUrls(urls)

    # ---------------------------------------------------------------- location bar
    def _location_bar(self):
        combo = self.findChild(QComboBox, "lookInCombo")
        label = self.findChild(QLabel, "lookInLabel")
        self.location = QLineEdit()
        self.location.setObjectName("locationEdit")
        self.location.setPlaceholderText("Type or paste a folder or file path, then press Enter")
        self.location.setClearButtonEnabled(True)
        model = QFileSystemModel(self.location)
        model.setRootPath("")
        model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot | QDir.Hidden)
        comp = QCompleter(model, self.location)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        comp.setCaseSensitivity(Qt.CaseSensitive)
        self.location.setCompleter(comp)
        self.location.returnPressed.connect(self._go)
        if combo is not None and combo.parentWidget() is not None and combo.parentWidget().layout() is not None:
            combo.parentWidget().layout().replaceWidget(combo, self.location)
            combo.hide()
        else:   # fallback: put it in the main layout's top row
            self.layout().addWidget(self.location, 0, 1)
        if label is not None:
            label.setText("Location:")
            label.setBuddy(self.location)
        self.directoryEntered.connect(self._show_dir)
        self._show_dir(self.directory().absolutePath())

    def _show_dir(self, path):
        if not self.location.hasFocus():
            self.location.setText(path)

    def _go(self):
        text = os.path.expandvars(os.path.expanduser(self.location.text().strip()))
        if text.startswith("file://"):
            text = QUrl(text).toLocalFile()
        if not text:
            return
        if not os.path.isabs(text):
            text = os.path.join(self.directory().absolutePath(), text)
        text = os.path.normpath(text)
        if os.path.isdir(text):
            self.setDirectory(text)
            self.location.setText(text)
            return
        parent = os.path.dirname(text)
        if self.save and os.path.isdir(parent):
            self.setDirectory(parent)
            self.selectFile(os.path.basename(text))
            self.accept()
            return
        if os.path.isfile(text):
            self.setDirectory(parent)
            self.selectFile(text)
            self.accept()
            return
        self.location.setStyleSheet("QLineEdit { background: #ffd8d8; color: black; }")
        self.location.setToolTip("That folder or file doesn't exist")
        QTimer.singleShot(1200, lambda: (self.location.setStyleSheet(""), self.location.setToolTip("")))

    # ---------------------------------------------------------------- preview
    def _add_preview(self):
        sp = self.findChild(QSplitter, "splitter")
        if sp is None:
            return
        self.preview = _Preview()
        sp.addWidget(self.preview)
        sp.setStretchFactor(0, 0); sp.setStretchFactor(1, 1); sp.setStretchFactor(2, 0)
        sp.setSizes([150, 600, 200])
        self.currentChanged.connect(self.preview.show_file)
        self.preview.show_file(None)


def _start(directory):
    return directory if directory and os.path.exists(directory) else os.path.expanduser("~")


def open_files(parent, title, directory, filters):
    d = PathFileDialog(parent, title, _start(directory), filters, multi=True)
    return d.selectedFiles() if d.exec() else []


def open_file(parent, title, directory, filters):
    d = PathFileDialog(parent, title, _start(directory), filters)
    files = d.selectedFiles() if d.exec() else []
    return files[0] if files else ""


def save_file(parent, title, start, filters, selected_filter=""):
    """start may be a folder or a full file path. Returns (path, chosen_filter)."""
    folder = start if os.path.isdir(start or "") else os.path.dirname(start or "")
    d = PathFileDialog(parent, title, _start(folder), filters, save=True)
    if start and not os.path.isdir(start):
        d.selectFile(os.path.basename(start))
    if selected_filter:
        d.selectNameFilter(selected_filter)
    if not d.exec():
        return "", ""
    files = d.selectedFiles()
    return (files[0] if files else ""), d.selectedNameFilter()


def choose_folder(parent, title, directory):
    d = PathFileDialog(parent, title, _start(directory), "", folder=True)
    files = d.selectedFiles() if d.exec() else []
    return files[0] if files else ""
