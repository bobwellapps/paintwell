"""Printing: a print preview window (choose printer, paper, orientation) with a choice of
how big the picture prints."""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter, QColor, QImage, QPageLayout
from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox, QToolBar, QWidget, QHBoxLayout

SIZES = [("fit", "Fit to page"), ("fill", "Fill page"), ("300", "Actual size (300 ppi)"),
         ("150", "Actual size (150 ppi)"), ("72", "Actual size (72 ppi)"), ("custom", "Custom ppi")]


def place(img_w, img_h, page: QRectF, mode, ppi=300, printer_dpi=300):
    """Where the picture goes on the page (page and result in printer pixels)."""
    if mode in ("fit", "fill"):
        sx, sy = page.width() / img_w, page.height() / img_h
        s = min(sx, sy) if mode == "fit" else max(sx, sy)
    else:
        s = printer_dpi / float(ppi)                      # picture pixels -> printer pixels
    w, h = img_w * s, img_h * s
    return QRectF(page.x() + (page.width() - w) / 2, page.y() + (page.height() - h) / 2, w, h)


def render(printer, img: QImage, mode, ppi=300):
    """Paint the picture onto the printer's page(s)."""
    p = QPainter()
    if not p.begin(printer):
        return False
    page = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
    page.moveTo(0, 0)
    flat = QImage(img.size(), QImage.Format_RGB32)
    flat.fill(QColor(255, 255, 255))                    # transparent parts print as plain paper
    fp = QPainter(flat)
    fp.drawImage(0, 0, img)
    fp.end()
    target = place(img.width(), img.height(), page, mode, ppi, printer.resolution())
    p.setClipRect(page)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(target, flat)
    p.end()
    return True


def print_document(parent, doc, settings):
    from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
    printer = QPrinter(QPrinter.HighResolution)
    img = doc.flattened()
    # landscape pictures start in landscape
    printer.setPageOrientation(QPageLayout.Landscape if img.width() > img.height() else QPageLayout.Portrait)
    title = (doc.title or "Picture").rsplit(".", 1)[0]
    printer.setDocName(title)
    state = {"mode": settings.value("print_size", "fit"), "ppi": int(settings.value("print_ppi", 300))}
    dlg = QPrintPreviewDialog(printer, parent)
    dlg.setWindowTitle("Print")
    dlg.resize(980, 760)
    dlg.paintRequested.connect(lambda pr: render(pr, img, state["mode"], state["ppi"]
                                                 if state["mode"] == "custom" else
                                                 int(state["mode"]) if state["mode"].isdigit() else 300))
    # our own "Size" choice on the preview's toolbar
    bar = dlg.findChild(QToolBar)
    if bar is not None:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(8, 0, 4, 0)
        row.addWidget(QLabel("Size:"))
        combo = QComboBox()
        for v, t in SIZES:
            combo.addItem(t, v)
        combo.setCurrentIndex(max(0, combo.findData(state["mode"])))
        row.addWidget(combo)
        ppi = QSpinBox(); ppi.setRange(10, 2400); ppi.setSuffix(" ppi"); ppi.setValue(state["ppi"])
        ppi.setToolTip("Picture pixels per inch on paper (higher = smaller print)")
        ppi.setVisible(state["mode"] == "custom")
        row.addWidget(ppi)
        info = QLabel()
        row.addWidget(info)

        def show_size():
            m = state["mode"]
            if m in ("fit", "fill"):
                info.setText("")
                return
            d = state["ppi"] if m == "custom" else int(m)
            info.setText(f"  {img.width() / d * 2.54:.1f} × {img.height() / d * 2.54:.1f} cm")

        def changed(_=None):
            state["mode"] = combo.currentData()
            ppi.setVisible(state["mode"] == "custom")
            settings.setValue("print_size", state["mode"])
            show_size()
            for w in dlg.findChildren(QWidget):
                if w.metaObject().className() == "QPrintPreviewWidget":
                    w.updatePreview()

        def ppi_changed(v):
            state["ppi"] = v
            settings.setValue("print_ppi", v)
            changed()
        combo.currentIndexChanged.connect(changed)
        ppi.valueChanged.connect(ppi_changed)
        show_size()
        bar.addSeparator()
        bar.addWidget(box)
    dlg.exec()
