"""Loading and saving images, including layered OpenRaster (.ora)."""
import json
import os
import zipfile
import xml.etree.ElementTree as ET
from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QPointF, QRect
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QPainter, QColor

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
    return Document(img.width(), img.height(), layers=[Layer(img, "Background")], path=path)


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


def save_ora(doc, path):
    tmp = path + ".tmp"
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
    os.replace(tmp, path)


def save(doc, path, quality=-1):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "ora":
        save_ora(doc, path)
        return
    save_image(doc.flattened(), path, quality)


def save_image(img, path, quality=-1):
    """Save one flat QImage (used by Save and by Split Image)."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("jpg", "jpeg", "bmp"):
        # no alpha: composite over white like most editors do
        bg = QImage(img.size(), QImage.Format_RGB32)
        bg.fill(Qt.white)
        p = QPainter(bg)
        p.drawImage(0, 0, img)
        p.end()
        img = bg
    writer = QImageWriter(path)
    if ext == "tga":
        writer.setFormat(b"tga")
    if quality >= 0:
        writer.setQuality(quality)
    if not writer.write(img):
        raise IOError(writer.errorString())
