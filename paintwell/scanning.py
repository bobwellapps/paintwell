"""Scanning through SANE (the standard Linux scanner system) using its scanimage tool."""
import os
import re
import shutil
import subprocess
import threading

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                               QProgressBar, QWidget, QSizePolicy, QMessageBox, QDialogButtonBox)

MODE_NAMES = {"color": "Colour", "colour": "Colour", "gray": "Greyscale", "grey": "Greyscale",
              "grayscale": "Greyscale", "lineart": "Black & white", "binary": "Black & white",
              "halftone": "Halftone", "true gray": "Greyscale"}
NICE_DPI = [75, 100, 150, 200, 300, 400, 600, 1200, 2400]


def scanimage_path():
    return shutil.which("scanimage")


def clean_env():
    """Environment for running scanimage.

    AppImage: scanimage is the system's own, so remove the AppImage's Python and library
    settings, which could confuse it.
    Snap: scanimage and its scanner drivers are inside the snap and need the snap's library
    settings, so leave the environment as it is."""
    env = dict(os.environ)
    if env.get("SNAP") and not env.get("APPIMAGE"):
        return env
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "LD_LIBRARY_PATH", "LD_PRELOAD",
              "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "SSL_CERT_FILE", "APPDIR", "APPIMAGE", "ARGV0", "OWD"):
        env.pop(k, None)
    return env


def _decode(b):
    """scanimage's output as text.  Scanner drivers sometimes print bytes that aren't valid
    UTF-8 (odd characters in device names, binary bits from network scanner discovery):
    those are replaced instead of making the whole answer unreadable."""
    if not b:
        return ""
    # surrogateescape keeps the original bytes, so a device name with an odd byte in it can be
    # handed back to scanimage exactly as it was (subprocess turns it back into the same bytes)
    return b.decode("utf-8", "surrogateescape")


def shown(text):
    """Text safe to show on screen (odd bytes become a replacement mark)."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def run_scanimage(args, timeout=60):
    """(stdout text, stderr text) of scanimage with these arguments."""
    r = subprocess.run([scanimage_path()] + list(args), capture_output=True, timeout=timeout, env=clean_env())
    return _decode(r.stdout), _decode(r.stderr)


_DEV_LINE = re.compile(r"device\s+[`'\u2018\u201c\"](.+?)['\u2019\u201d\"]\s+is\s+an?\s+(.+)$")


def list_devices(timeout=60):
    """[(device name, description)] of the scanners SANE can see."""
    devs, _ = list_devices_verbose(timeout)
    return devs


def list_devices_verbose(timeout=60):
    """([(device name, description)], what scanimage said on its error output)."""
    # a fixed, machine-readable format first (name<TAB>vendor model type), the usual text second
    out, err = run_scanimage(["-f", "%d\t%v %m (%t)%n"], timeout)
    devs = []
    for line in out.splitlines():
        if "\t" in line:
            name, desc = line.split("\t", 1)
            name, desc = name.strip(), desc.strip()
            if name:
                devs.append((name, desc.replace(" ()", "")))
    if not devs:
        out2, err2 = run_scanimage(["-L"], timeout)
        err = err or err2
        for line in out2.splitlines():
            m = _DEV_LINE.search(line.strip())
            if m:
                devs.append((m.group(1), m.group(2)))
    seen, unique = set(), []
    for d in devs:
        if d[0] not in seen:
            seen.add(d[0]); unique.append(d)
    return unique, err


def device_options(dev, timeout=60):
    """What the scanner offers: {'mode': [...], 'mode_default', 'resolution': [...], 'res_default',
    'source': [...], 'geometry': {'l': (lo, hi), ...} in mm, 'depth': [...]}"""
    txt, _ = run_scanimage(["-d", dev, "-A"], timeout)
    opts = {"mode": [], "resolution": [], "source": [], "geometry": {}, "depth": []}
    for line in txt.splitlines():
        s = line.strip()
        m = re.match(r"--mode\s+(.+?)\s+\[(.*?)\]", s)
        if m:
            opts["mode"] = [v.strip() for v in m.group(1).split("|")]
            opts["mode_default"] = m.group(2)
            continue
        m = re.match(r"--source\s+(.+?)\s+\[(.*?)\]", s)
        if m:
            opts["source"] = [v.strip() for v in m.group(1).split("|")]
            opts["source_default"] = m.group(2)
            continue
        m = re.match(r"--depth\s+(.+?)\s+\[", s)
        if m:
            opts["depth"] = [v.strip() for v in m.group(1).split("|")]
            continue
        m = re.match(r"--resolution\s+(.+?)\s+\[(.*?)\]", s)
        if m:
            spec, default = m.group(1), m.group(2)
            rng = re.match(r"([\d.]+)\.\.([\d.]+)\s*dpi", spec)
            if rng:
                lo, hi = float(rng.group(1)), float(rng.group(2))
                vals = [d for d in NICE_DPI if lo <= d <= hi] or [int(lo)]
            else:
                vals = [int(float(v)) for v in re.findall(r"[\d.]+", spec)]
            opts["resolution"] = vals
            try:
                opts["res_default"] = int(float(re.findall(r"[\d.]+", default)[0]))
            except IndexError:
                pass
            continue
        m = re.match(r"-([ltxy])\s+([\d.]+)\.\.([\d.]+)\s*mm", s)
        if m:
            opts["geometry"][m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return opts


def nice_mode(m):
    return MODE_NAMES.get(m.lower(), m)


class ScanJob(QObject):
    """Runs one scanimage scan in the background, reporting progress."""
    progress = Signal(float)
    done = Signal(object, str)       # QImage or None, error text

    def __init__(self, dev, mode, dpi, source=None, area=None, depth8=False):
        super().__init__()
        self.args = [scanimage_path(), "-d", dev, "--format=pnm", "--progress"]
        if mode:
            self.args += ["--mode", mode]
        if dpi:
            self.args += ["--resolution", str(int(dpi))]
        if source:
            self.args += ["--source", source]
        if depth8:
            self.args += ["--depth", "8"]
        if area is not None:
            l, t, x, y = area
            self.args += ["-l", f"{l:.1f}", "-t", f"{t:.1f}", "-x", f"{x:.1f}", "-y", f"{y:.1f}"]
        self.proc = None
        self.cancelled = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _run(self):
        try:
            self.proc = subprocess.Popen(self.args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=clean_env())
            chunks = []

            def read_out():
                chunks.append(self.proc.stdout.read())
            t = threading.Thread(target=read_out, daemon=True)
            t.start()
            err = b""
            buf = b""
            while True:
                b = self.proc.stderr.read(64)
                if not b:
                    break
                err += b
                buf += b
                for m in re.finditer(rb"Progress:\s*([\d.]+)%", buf):
                    self.progress.emit(float(m.group(1)) / 100.0)
                buf = buf[-32:]
            self.proc.wait()
            t.join()
            if self.cancelled:
                self.done.emit(None, "")
                return
            data = b"".join(chunks)
            img = QImage()
            if data:
                img.loadFromData(data)
            if img.isNull():
                msg = re.sub(rb"Progress:\s*[\d.]+%", b"", err).decode("utf-8", "replace").strip()
                msg = "\n".join(l for l in msg.splitlines() if l.strip())[-400:]
                self.done.emit(None, msg or "The scanner didn't send a picture.")
                return
            self.done.emit(img.convertToFormat(QImage.Format_ARGB32_Premultiplied), "")
        except Exception as ex:          # e.g. scanimage vanished
            self.done.emit(None, str(ex))


class PreviewArea(QWidget):
    """The preview scan, with a box you can drag to scan just part of the glass."""
    def __init__(self):
        super().__init__()
        self.img = None
        self.sel = None          # selection as fractions of the glass (QRectF in 0..1)
        self.start = None
        self.setMinimumSize(260, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.aspect = 210 / 297.0
        self.message = "Press Preview to see what's on the scanner glass."

    def glass_rect(self):
        W, H = self.width() - 16, self.height() - 16
        a = self.aspect
        w = min(W, H * a)
        h = w / a
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window().color().darker(108))
        g = self.glass_rect()
        p.fillRect(g, QColor(255, 255, 255))
        if self.img is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(g, self.img)
        else:
            p.setPen(QColor(120, 120, 120))
            p.drawText(g.adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, self.message)
        p.setPen(QPen(QColor(0, 0, 0, 80), 1)); p.setBrush(Qt.NoBrush); p.drawRect(g)
        if self.sel is not None:
            r = QRectF(g.x() + self.sel.x() * g.width(), g.y() + self.sel.y() * g.height(),
                       self.sel.width() * g.width(), self.sel.height() * g.height())
            p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 80))
            from PySide6.QtGui import QPainterPath
            outer = QPainterPath(); outer.addRect(g)
            inner = QPainterPath(); inner.addRect(r)
            p.drawPath(outer.subtracted(inner))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255), 3)); p.drawRect(r)
            p.setPen(QPen(QColor(30, 120, 220), 1.5, Qt.DashLine)); p.drawRect(r)
        p.end()

    def _frac(self, pos):
        g = self.glass_rect()
        return QPointF(min(1, max(0, (pos.x() - g.x()) / g.width())), min(1, max(0, (pos.y() - g.y()) / g.height())))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.img is not None:
            self.start = self._frac(e.position())
            self.sel = None
            self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.sel = QRectF(self.start, self._frac(e.position())).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if self.start is not None:
            self.start = None
            if self.sel is not None and (self.sel.width() < 0.01 or self.sel.height() < 0.01):
                self.sel = None
            self.update()


class ScanDialog(QDialog):
    """Pick a scanner and its settings, preview, choose the area, scan."""
    finished_scan = Signal(object)

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Scan")
        self.settings = settings
        self.opts = {}
        self.job = None
        self.result = None
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        grid = QGridLayout()
        grid.addWidget(QLabel("Scanner:"), 0, 0)
        self.dev = QComboBox(); self.dev.setMinimumWidth(240)
        grid.addWidget(self.dev, 0, 1)
        self.refresh = QPushButton("Find scanners")
        grid.addWidget(self.refresh, 1, 1)
        grid.addWidget(QLabel("Mode:"), 2, 0)
        self.mode = QComboBox(); grid.addWidget(self.mode, 2, 1)
        grid.addWidget(QLabel("Resolution:"), 3, 0)
        self.res = QComboBox(); grid.addWidget(self.res, 3, 1)
        self.src_label = QLabel("Source:"); grid.addWidget(self.src_label, 4, 0)
        self.src = QComboBox(); grid.addWidget(self.src, 4, 1)
        left.addLayout(grid)
        self.size_info = QLabel(); self.size_info.setStyleSheet("color: palette(mid);")
        left.addWidget(self.size_info)
        left.addStretch(1)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 3 + 4)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        left.addWidget(self.status)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setVisible(False)
        left.addWidget(self.bar)
        btns = QHBoxLayout()
        self.preview_btn = QPushButton("Preview")
        self.scan_btn = QPushButton("Scan"); self.scan_btn.setDefault(True)
        self.cancel_btn = QPushButton("Close")
        btns.addWidget(self.preview_btn); btns.addStretch(1); btns.addWidget(self.scan_btn); btns.addWidget(self.cancel_btn)
        left.addLayout(btns)
        lay.addLayout(left)
        self.preview = PreviewArea()
        self.preview.setToolTip("Drag a box to scan only part of the glass (click to clear it)")
        lay.addWidget(self.preview, 1)
        self.refresh.clicked.connect(self.find_devices)
        self.dev.currentIndexChanged.connect(self.device_changed)
        self.res.currentIndexChanged.connect(lambda _: self._update_size())
        self.preview_btn.clicked.connect(lambda: self.scan(preview=True))
        self.scan_btn.clicked.connect(lambda: self.scan(preview=False))
        self.cancel_btn.clicked.connect(self._close_or_cancel)
        self.resize(820, 560)
        self._set_busy(False)
        QTimer.singleShot(0, self.find_devices)

    # ---------------------------------------------------------------- background helpers
    def _bg(self, fn, then, wait_text):
        self.status.setText(wait_text)
        self._set_busy(True, scanning=False)
        box = {}

        def run():
            try:
                box["r"] = fn()
            except Exception as ex:
                box["e"] = str(ex)
        t = threading.Thread(target=run, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                QTimer.singleShot(100, poll)
                return
            self._set_busy(False)
            then(box.get("r"), box.get("e"))
        QTimer.singleShot(100, poll)

    def _set_busy(self, busy, scanning=False):
        self.busy = busy
        for w in (self.dev, self.refresh, self.mode, self.res, self.src, self.preview_btn, self.scan_btn):
            w.setEnabled(not busy)
        if not busy:
            has = self.dev.count() > 0 and self.current_device() is not None
            self.preview_btn.setEnabled(has)
            self.scan_btn.setEnabled(has)
        self.cancel_btn.setText("Cancel" if scanning else "Close")
        self.bar.setVisible(scanning)

    # ---------------------------------------------------------------- scanners
    def current_device(self):
        k = self.dev.currentData()
        devices = getattr(self, "devices", [])
        if k is None or not (0 <= int(k) < len(devices)):
            return None
        return devices[int(k)][0]

    def find_devices(self):
        self.dev.clear()
        self._bg(list_devices_verbose, self._got_devices, "Looking for scanners… (this can take a little while)")

    def _got_devices(self, result, err):
        devs, said = result if result else ([], "")
        self.dev.blockSignals(True)
        self.dev.clear()
        self.devices = list(devs or [])
        for k, (name, desc) in enumerate(self.devices):
            self.dev.addItem(shown(desc), k)        # the real name stays in self.devices
            self.dev.setItemData(k, shown(name), Qt.ToolTipRole)
        self.dev.blockSignals(False)
        if err:
            self.status.setText("Couldn't look for scanners: " + err)
        elif not devs:
            hint = ""
            said = " ".join(shown(said or "").split())
            if said:
                hint = "\n\nThe scanner system said: " + (said[:240] + "…" if len(said) > 240 else said)
            self.status.setText("No scanner found. Check it is plugged in and switched on (network scanners "
                                "can take a moment to appear), then press Find scanners. Network scanners "
                                "also need the sane-airscan package." + hint)
        else:
            last = self.settings.value("scan_device", "")
            i = next((k for k, (name, _) in enumerate(self.devices) if shown(name) == last), -1)
            self.dev.setCurrentIndex(i if i >= 0 else 0)
            self.device_changed()
        self._set_busy(False)

    def device_changed(self, *_):
        dev = self.current_device()
        if not dev:
            return
        self.preview.img = None
        self.preview.sel = None
        self.preview.update()
        self._bg(lambda: device_options(dev), self._got_options, "Reading the scanner's settings…")

    def _got_options(self, opts, err):
        self.opts = opts or {}
        if err:
            self.status.setText("Couldn't read the scanner's settings: " + err)
        else:
            self.status.setText("Ready. Preview first to choose an area, or just press Scan.")
        self.mode.clear()
        modes = self.opts.get("mode", [])
        for m in modes:
            self.mode.addItem(nice_mode(m), m)
        want = self.settings.value("scan_mode", "") or next((m for m in modes if m.lower() in ("color", "colour")), "")
        i = self.mode.findData(want)
        if i < 0:
            i = self.mode.findData(self.opts.get("mode_default"))
        self.mode.setCurrentIndex(max(0, i))
        self.mode.setVisible(bool(modes))
        self.res.clear()
        for d in self.opts.get("resolution", []):
            self.res.addItem(f"{d} dpi", d)
        want = int(self.settings.value("scan_dpi", 300))
        i = self.res.findData(want)
        if i < 0 and self.res.count():
            # the nearest one the scanner has
            vals = [self.res.itemData(k) for k in range(self.res.count())]
            i = min(range(len(vals)), key=lambda k: abs(vals[k] - want))
        self.res.setCurrentIndex(max(0, i))
        self.src.clear()
        for s in self.opts.get("source", []):
            self.src.addItem(s, s)
        has_src = self.src.count() > 1
        self.src.setVisible(has_src)
        self.src_label.setVisible(has_src)
        g = self.opts.get("geometry", {})
        if "x" in g and "y" in g and g["x"][1] > 0 and g["y"][1] > 0:
            self.preview.aspect = g["x"][1] / g["y"][1]
        self.preview.update()
        self._update_size()
        self._set_busy(False)

    def _update_size(self):
        g = self.opts.get("geometry", {})
        dpi = self.res.currentData()
        if not dpi or "x" not in g or "y" not in g:
            self.size_info.setText("")
            return
        x, y = g["x"][1], g["y"][1]
        if self.preview.sel is not None:
            x, y = x * self.preview.sel.width(), y * self.preview.sel.height()
        self.size_info.setText(f"About {round(x / 25.4 * dpi)} × {round(y / 25.4 * dpi)} pixels")

    # ---------------------------------------------------------------- scanning
    def _area(self):
        g = self.opts.get("geometry", {})
        if self.preview.sel is None or not all(k in g for k in "xy"):
            return None
        W, H = g["x"][1], g["y"][1]
        s = self.preview.sel
        return (s.x() * W, s.y() * H, s.width() * W, s.height() * H)

    def scan(self, preview):
        dev = self.current_device()
        if not dev:
            return
        mode = self.mode.currentData()
        if preview:
            res = self.opts.get("resolution") or [75]
            dpi = min(res, key=lambda d: abs(d - 75))
            area = None
            # full glass for the preview
            g = self.opts.get("geometry", {})
            if all(k in g for k in "xy"):
                area = (0.0, 0.0, g["x"][1], g["y"][1])
        else:
            dpi = self.res.currentData()
            area = self._area()
            if area is None:
                g = self.opts.get("geometry", {})
                if all(k in g for k in "xy"):
                    area = (0.0, 0.0, g["x"][1], g["y"][1])
            self.settings.setValue("scan_device", shown(dev))
            self.settings.setValue("scan_mode", mode or "")
            if dpi:
                self.settings.setValue("scan_dpi", dpi)
        depth8 = "8" in self.opts.get("depth", []) and (mode or "").lower() not in ("lineart", "binary")
        src = self.src.currentData() if self.src.isVisible() else None
        self.job = ScanJob(dev, mode, dpi, src, area, depth8)
        self.job.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.job.done.connect(lambda img, err, preview=preview: self._scanned(img, err, preview))
        self.bar.setValue(0)
        self._set_busy(True, scanning=True)
        self.status.setText("Previewing…" if preview else "Scanning…")
        self.job.start()

    def _scanned(self, img, err, preview):
        self.job = None
        self._set_busy(False)
        if img is None:
            self.status.setText(("Scan failed: " + err) if err else "Scan cancelled.")
            return
        if preview:
            self.preview.img = img
            self.preview.sel = None
            self.preview.update()
            self.status.setText("Drag a box on the preview to scan just that part, then press Scan.")
            return
        self.result = img
        self.accept()

    def _close_or_cancel(self):
        if self.job is not None:
            self.job.cancel()
            self.status.setText("Cancelling…")
        else:
            self.reject()

    def reject(self):
        if self.job is not None:
            self.job.cancel()
        super().reject()


def scan(parent, settings):
    """Show the Scan window; returns the scanned QImage, or None."""
    if not scanimage_path():
        QMessageBox.information(
            parent, "Scan",
            "Scanning uses SANE, the standard Linux scanner system, which doesn't seem to be installed.\n\n"
            "Install it from your software centre or with your package manager:\n"
            "  • Debian, Ubuntu, Kubuntu, Mint:  sudo apt install sane-utils\n"
            "  • Fedora:  sudo dnf install sane-backends\n"
            "  • Arch, Manjaro:  sudo pacman -S sane\n"
            "  • openSUSE:  sudo zypper install sane-backends\n\n"
            "Some scanners also need their maker's driver (e.g. HP: hplip; Epson: epsonscan2).")
        return None
    dlg = ScanDialog(parent, settings)
    if dlg.exec() == QDialog.Accepted:
        return dlg.result
    return None
