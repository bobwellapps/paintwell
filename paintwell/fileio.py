"""Loading and saving images, including layered OpenRaster (.ora)."""
import json
import os
import shutil
import zipfile
import xml.etree.ElementTree as ET
from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QPointF, QRect
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QPainter, QColor

from . import jpegmeta
from .document import Document, Layer, TextItem
from .imaging import FMT, new_image

ORA_OPS = {
    "Normal": "svg:src-over", "Multiply": "svg:multiply", "Additive": "svg:plus",
    "Colour Burn": "svg:color-burn", "Colour Dodge": "svg:color-dodge", "Overlay": "svg:overlay",
    "Difference": "svg:difference", "Exclusion": "svg:exclusion", "Lighten": "svg:lighten",
    "Darken": "svg:darken", "Screen": "svg:screen", "Hard Light": "svg:hard-light", "Soft Light": "svg:soft-light",
}
ORA_OPS_REV = {v: k for k, v in ORA_OPS.items()}


def read_formats():
    return sorted({bytes(f).decode() for f in QImageReader.supportedImageFormats()})


def write_formats():
    return sorted({bytes(f).decode() for f in QImageWriter.supportedImageFormats()})


def open_filter():
    exts = set(read_formats()) | {"ora"}
    pats = " ".join(f"*.{e}" for e in sorted(exts))
    return f"All images ({pats});;OpenRaster layered (*.ora);;PNG (*.png);;JPEG (*.jpg *.jpeg);;All files (*)"


SAVE_TYPES = [("PNG", "png"), ("OpenRaster (layers)", "ora"), ("JPEG", "jpg"), ("BMP", "bmp"),
              ("WebP", "webp"), ("TIFF", "tiff"), ("GIF", "gif"), ("ICO", "ico"), ("TGA", "tga")]


def save_filter():
    avail = set(write_formats()) | {"ora"}
    parts = []
    for label, ext in SAVE_TYPES:
        if ext in avail or (ext == "jpg" and "jpeg" in avail) or (ext == "tiff" and "tif" in avail):
            parts.append(f"{label} (*.{ext})")
    return ";;".join(parts)


def _png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def _img_from_bytes(data):
    img = QImage()
    img.loadFromData(data)
    return img.convertToFormat(FMT)


def load(path):
    if path.lower().endswith(".ora"):
        return load_ora(path)
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        raise IOError(reader.errorString() or "Unsupported or damaged image file")
    img = img.convertToFormat(FMT)
    doc = Document(img.width(), img.height(), layers=[Layer(img, "Background")], path=path)
    doc.source_stat = _stat(path)
    if path.lower().endswith((".jpg", ".jpeg")):
        doc.jpeg_quality = jpeg_quality(path)         # what Save suggests
        doc.jpeg_meta = jpegmeta.read(path)           # camera, date, colour profile: kept on Save
    return doc


def load_ora(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("stack.xml"))
        w, h = int(root.get("w")), int(root.get("h"))
        layers = []

        def walk(stack):
            for el in stack:
                if el.tag == "stack":
                    walk(el)
                elif el.tag == "layer":
                    src = el.get("src")
                    if not src:
                        continue
                    img = _img_from_bytes(z.read(src))
                    canvas = new_image(w, h)
                    p = QPainter(canvas)
                    p.drawImage(int(float(el.get("x", 0))), int(float(el.get("y", 0))), img)
                    p.end()
                    L = Layer(canvas, el.get("name", "Layer"),
                              el.get("visibility", "visible") != "hidden",
                              int(round(float(el.get("opacity", 1)) * 255)),
                              ORA_OPS_REV.get(el.get("composite-op", "svg:src-over"), "Normal"))
                    meta = el.get("paintwell-text")
                    if meta:
                        try:
                            t = json.loads(meta)
                            col = QColor.fromRgba(int(t["color"].lstrip("#"), 16))
                            L.text = TextItem(list(t["lines"]), dict(t["opts"]), col,
                                              QPointF(*t["pos"]), QRect(0, 0, w, h), None, QImage(L.image))
                        except (ValueError, KeyError, TypeError):
                            pass
                    layers.append(L)
        walk(root.find("stack"))
    if not layers:
        raise IOError("The OpenRaster file contains no layers")
    layers.reverse()  # stack.xml lists top layer first
    return Document(w, h, layers=layers, path=path)


def _temp_beside(path):
    """A hidden file next to path to write into first. File managers (Dolphin and others)
    watch the folder: if they saw the real file while it was still being written, they'd read
    its size and make its thumbnail from a half-written file and not look again."""
    folder, name = os.path.split(os.path.abspath(path))
    return os.path.join(folder, f".{name}.paintwell-{os.getpid()}.part")


def _finish(tmp, path):
    """Move the finished temporary file into place in one step, keeping the permissions of a
    file being saved over."""
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save_ora(doc, path):
    tmp = _temp_beside(path)
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", compress_type=zipfile.ZIP_STORED)
        image = ET.Element("image", {"version": "0.0.3", "w": str(doc.w), "h": str(doc.h)})
        stack = ET.SubElement(image, "stack")
        for i in range(len(doc.layers) - 1, -1, -1):
            L = doc.layers[i]
            name = f"data/layer{i}.png"
            z.writestr(name, _png_bytes(L.image), compress_type=zipfile.ZIP_DEFLATED)
            attrs = {
                "src": name, "name": L.name, "x": "0", "y": "0",
                "opacity": f"{L.opacity / 255:.4f}", "visibility": "visible" if L.visible else "hidden",
                "composite-op": ORA_OPS.get(L.blend, "svg:src-over")}
            t = L.text_item()
            if t is not None:
                # Paintwell's own extra: the text, so it can still be edited after reopening the file
                # (other programs ignore it and just see the picture layer)
                attrs["paintwell-text"] = json.dumps({
                    "lines": t.lines, "opts": t.opts, "color": "#%08x" % t.color.rgba(),
                    "pos": [t.pos.x(), t.pos.y()]})
            ET.SubElement(stack, "layer", attrs)
        z.writestr("stack.xml", ET.tostring(image, encoding="utf-8", xml_declaration=True))
        flat = doc.flattened()
        z.writestr("mergedimage.png", _png_bytes(flat))
        th = flat.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        z.writestr("Thumbnails/thumbnail.png", _png_bytes(th))
    _finish(tmp, path)


def _stat(path):
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _kind(ext):
    return {"jpeg": "jpg"}.get(ext.lower().lstrip("."), ext.lower().lstrip("."))


def unchanged_copy(doc, path, quality=-1, keep_details=True):
    """The bytes to save when the picture hasn't been changed since it was opened (or last
    saved): its own file, as it is, rather than compressing it all over again. That keeps the
    size the same and loses no quality. Only for the same format, a JPEG at its own quality or
    a PNG, and only while the file on disk is still the one that was opened. Else None."""
    src = getattr(doc, "path", None)
    if not src or doc.modified or getattr(doc, "source_stat", None) is None:
        return None
    kind = _kind(os.path.splitext(path)[1])
    if kind != _kind(os.path.splitext(src)[1]) or kind not in ("jpg", "png"):
        return None
    if kind == "jpg" and (quality < 0 or quality != getattr(doc, "jpeg_quality", None)):
        return None
    if len(doc.layers) != 1 or _stat(src) != doc.source_stat:
        return None
    try:
        with open(src, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if kind == "jpg" and not keep_details:
        data = jpegmeta.without_details(data)
    return data


def save(doc, path, quality=-1, progressive=False, keep_details=True):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "ora":
        save_ora(doc, path)
        doc.source_stat = _stat(path)
        return
    data = unchanged_copy(doc, path, quality, keep_details)
    if data is not None:
        tmp = _temp_beside(path)
        with open(tmp, "wb") as fh:
            fh.write(data)
        _finish(tmp, path)
        doc.source_stat = _stat(path)
        return
    meta = getattr(doc, "jpeg_meta", None) if ext in ("jpg", "jpeg") else None
    if meta and not keep_details:
        meta = jpegmeta.colour_only(meta)   # the colour profile isn't personal: always kept
    save_image(doc.flattened(), path, quality, meta, progressive)
    # the saved file is now the picture's own file
    doc.source_stat = _stat(path)
    if _kind(ext) == "jpg":
        doc.jpeg_quality = quality if quality >= 0 else doc.__dict__.get("jpeg_quality")


def _flat_for(img, ext):
    """What actually gets written for ext: JPEG and BMP have no transparency."""
    if ext in ("jpg", "jpeg", "bmp"):
        # no alpha: composite over white like most editors do
        bg = QImage(img.size(), QImage.Format_RGB32)
        bg.fill(Qt.white)
        p = QPainter(bg)
        p.drawImage(0, 0, img)
        p.end()
        return bg
    return img


def _settings(writer, ext, quality, progressive=False):
    if quality >= 0:
        writer.setQuality(quality)
    if ext in ("jpg", "jpeg"):
        # progressive (off unless turned on in Settings): the picture loads blurry first, then
        # sharpens, on web pages
        writer.setProgressiveScanWrite(bool(progressive))
        # a plain (baseline) JPEG, as Paint.NET, Photoshop, phones and cameras save by default,
        # which anything can open; its code tables are worked out for each picture
        # ("optimised"), which makes it smaller without changing a single pixel
        writer.setOptimizedWrite(True)


def encoded_size(img, ext, quality=-1, progressive=False):
    """How many bytes img takes saved as ext, without writing a file."""
    ext = {"jpeg": "jpg", "ora": "png"}.get(ext, ext)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    writer = QImageWriter(buf, ext.encode())
    _settings(writer, ext, quality, progressive)
    if not writer.write(_flat_for(img, ext)):
        raise IOError(writer.errorString())
    return buf.size()


# the standard JPEG brightness table at quality 50, in the order JPEG files store it (zig-zag)
_STD_LUMA = [16, 11, 12, 14, 12, 10, 16, 14, 13, 14, 18, 17, 16, 19, 24, 40, 26, 24, 22, 22, 24,
             49, 35, 37, 29, 40, 58, 51, 61, 60, 57, 51, 56, 55, 64, 72, 92, 78, 64, 68, 87, 69,
             55, 56, 80, 109, 81, 87, 95, 98, 103, 104, 103, 62, 77, 113, 121, 112, 100, 120,
             92, 101, 103, 99]


def jpeg_quality(path):
    """Roughly the quality (1-100) a JPEG file was saved at, from its brightness table, or None.
    Cameras and most programs use the standard table scaled by quality, so this matches the
    number other programs show to within a point or two."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4 * 1024 * 1024)
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xDB:                                  # quantisation tables
            j, end = i + 4, i + 2 + size
            while j < end:
                precision, table_id = data[j] >> 4, data[j] & 15
                n = 128 if precision else 64
                vals = (list(data[j + 1:j + 1 + n]) if not precision else
                        [int.from_bytes(data[j + 1 + 2 * k:j + 3 + 2 * k], "big") for k in range(64)])
                if table_id == 0 and len(vals) == 64:
                    if max(vals) == 1:
                        return 100
                    scale = sum(v * 100 / t for v, t in zip(vals, _STD_LUMA)) / 64
                    q = (200 - scale) / 2 if scale <= 100 else 5000 / scale
                    return max(1, min(100, int(round(q))))
                j += 1 + n
        elif marker in (0xDA, 0xD9):                        # the image data itself starts
            break
        i += 2 + size
    return None


# up to this many pixels the size is worked out exactly; JPEG and WebP are quick to save,
# so they're done exactly for nearly any photo
ESTIMATE_PIXELS = {"jpg": 50_000_000, "webp": 30_000_000}
ESTIMATE_PIXELS_OTHER = 12_000_000
TILE = 512


def estimate_size(images, ext, quality, w, h, smooth=True, progressive=False):
    """Bytes the picture would take saved as ext after resizing to w x h. images is the
    flattened picture, or every layer for OpenRaster. Up to ESTIMATE_PIXELS it's worked out
    exactly. Bigger than that, 16 tiles spread over the picture are resized and saved, and
    the total worked out from those, so it's close but not exact. Returns (bytes, exact)."""
    mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    if w * h <= ESTIMATE_PIXELS.get({"jpeg": "jpg"}.get(ext, ext), ESTIMATE_PIXELS_OTHER):
        total = 0
        for img in images:
            if (w, h) != (img.width(), img.height()):
                img = img.scaled(w, h, Qt.IgnoreAspectRatio, mode)
            total += encoded_size(img, ext, quality, progressive)
        return total, True
    n = 4
    tw, th = min(TILE, w // n), min(TILE, h // n)
    total = 0
    for img in images:
        sx, sy = w / img.width(), h / img.height()
        for gy in range(n):
            for gx in range(n):
                # a tile in the middle of each cell of a 4 x 4 grid over the resized picture
                tx = int((gx + 0.5) * w / n - tw / 2)
                ty = int((gy + 0.5) * h / n - th / 2)
                # the part of the original it comes from, with a little extra round the edge
                m = 2
                x0 = max(0, int(tx / sx) - m); y0 = max(0, int(ty / sy) - m)
                x1 = min(img.width(), int((tx + tw) / sx) + m + 1)
                y1 = min(img.height(), int((ty + th) / sy) + m + 1)
                part = img.copy(x0, y0, x1 - x0, y1 - y0)
                part = part.scaled(max(1, round(part.width() * sx)), max(1, round(part.height() * sy)),
                                   Qt.IgnoreAspectRatio, mode)
                ox, oy = round(tx - x0 * sx), round(ty - y0 * sy)
                total += encoded_size(part.copy(ox, oy, tw, th), ext, quality, progressive)
    return int(total * (w * h) / (n * n * tw * th)), False


def save_image(img, path, quality=-1, meta=None, progressive=False):
    """Save one flat QImage (used by Save and by Split Image). meta: a JPEG photo's details
    (jpegmeta.read), written into a JPEG."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    img = _flat_for(img, ext)
    tmp = _temp_beside(path)
    if meta and ext in ("jpg", "jpeg"):
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        writer = QImageWriter(buf, b"jpg")
        _settings(writer, ext, quality, progressive)
        if not writer.write(img):
            raise IOError(writer.errorString())
        data = jpegmeta.insert(bytes(buf.data()), jpegmeta.for_saving(meta, img.width(), img.height()))
        with open(tmp, "wb") as fh:
            fh.write(data)
        _finish(tmp, path)
        return
    writer = QImageWriter(tmp)
    # the temporary name doesn't end in .jpg etc., so say which format to write
    writer.setFormat({"jpeg": b"jpg", "tif": b"tiff"}.get(ext, ext.encode()))
    _settings(writer, ext, quality, progressive)
    ok = writer.write(img)
    err = writer.errorString()
    writer.setFileName("")                 # close the file before moving it
    if not ok:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise IOError(err)
    _finish(tmp, path)
