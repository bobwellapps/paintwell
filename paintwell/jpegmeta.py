"""A photo's details (camera, date, colour profile and so on) kept when it's saved again as a JPEG.

JPEG files keep these in "APP" blocks at the start of the file. Paintwell copies the useful
ones across unchanged, except for a few Exif values that would otherwise be wrong:

- Orientation: Paintwell turns the picture upright when it opens it, so the saved picture is
  already the right way up; Orientation is set back to "normal", or viewers would turn it again.
- The small preview picture inside the Exif block: it would still show the photo as it was
  before any editing, so it's unlinked and viewers no longer use it.
- The width and height recorded by the camera: updated to the saved picture's size.
"""
import struct

EXIF = b"Exif\x00\x00"
XMP = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT = b"http://ns.adobe.com/xmp/extension/\x00"
ICC = b"ICC_PROFILE\x00"
IPTC = b"Photoshop 3.0\x00"


def read(path):
    """The blocks worth keeping from a JPEG file, as complete blocks (marker, length and
    contents) in the order they appear. Empty if the file isn't a JPEG or has none."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4 * 1024 * 1024)
    except OSError:
        return []
    if data[:2] != b"\xff\xd8":
        return []
    keep, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xFF:                 # padding
            i += 1
            continue
        if marker in (0xDA, 0xD9):         # the picture itself starts: no more details
            break
        size = int.from_bytes(data[i + 2:i + 4], "big")
        block = data[i:i + 2 + size]
        body = block[4:]
        if (marker == 0xE1 and (body.startswith(EXIF) or body.startswith(XMP) or body.startswith(XMP_EXT))) \
                or (marker == 0xE2 and body.startswith(ICC)) \
                or (marker == 0xED and body.startswith(IPTC)):
            keep.append(block)
        i += 2 + size
    return keep


def without_details(data):
    """A whole JPEG file (bytes) with the camera, date, location and other details taken out,
    leaving the picture itself and its colour profile exactly as they were."""
    if data[:2] != b"\xff\xd8":
        return data
    out, i = [data[:2]], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xDA, 0xD9):
            break
        size = int.from_bytes(data[i + 2:i + 4], "big")
        block = data[i:i + 2 + size]
        body = block[4:]
        personal = (marker == 0xE1 and (body.startswith(EXIF) or body.startswith(XMP) or body.startswith(XMP_EXT))) \
            or (marker == 0xED and body.startswith(IPTC))
        if not personal:
            out.append(block)
        i += 2 + size
    out.append(data[i:])
    return b"".join(out)


def colour_only(blocks):
    """Just the colour profile: what's kept when Settings says not to keep photo details."""
    return [b for b in blocks or () if b[1] == 0xE2 and b[4:].startswith(ICC)]


def size(blocks):
    return sum(len(b) for b in blocks or ())


def for_saving(blocks, w, h):
    """The blocks to write into a picture saved at w x h (see the module notes)."""
    out = []
    for b in blocks or ():
        if b[1] == 0xE1 and b[4:].startswith(EXIF):
            fixed = _fix_exif(b[4 + len(EXIF):], w, h)
            if fixed is None:
                continue                   # unreadable Exif: leave it out rather than copy it wrong
            body = EXIF + fixed
            b = b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
        out.append(b)
    return out


def insert(jpeg, blocks):
    """jpeg (bytes from the encoder) with blocks added straight after its start marker, where
    the Exif standard puts them."""
    if not blocks or jpeg[:2] != b"\xff\xd8":
        return jpeg
    return jpeg[:2] + b"".join(blocks) + jpeg[2:]


# --- Exif ---
def _fix_exif(tiff, w, h):
    """tiff: the Exif data (a small TIFF file). Returns it with Orientation set to 1, the preview
    picture unlinked and the pixel size updated, or None if it can't be read."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    t = bytearray(tiff)
    try:
        ifd0 = struct.unpack_from(e + "I", t, 4)[0]

        def entries(off):
            n = struct.unpack_from(e + "H", t, off)[0]
            for k in range(n):
                p = off + 2 + 12 * k
                tag, typ, count = struct.unpack_from(e + "HHI", t, p)
                yield p, tag, typ, count

        def set_value(p, typ, value):
            # values that fit in 4 bytes are stored in the entry itself
            if typ == 3:
                struct.pack_into(e + "H", t, p + 8, value)
            elif typ == 4:
                struct.pack_into(e + "I", t, p + 8, value)

        exif_ifd = None
        for p, tag, typ, count in entries(ifd0):
            if tag == 0x0112 and typ == 3 and count == 1:            # Orientation
                set_value(p, typ, 1)
            elif tag == 0x8769:                                       # where the Exif values are
                exif_ifd = struct.unpack_from(e + "I", t, p + 8)[0]
        n0 = struct.unpack_from(e + "H", t, ifd0)[0]
        struct.pack_into(e + "I", t, ifd0 + 2 + 12 * n0, 0)          # unlink the preview picture
        if exif_ifd:
            for p, tag, typ, count in entries(exif_ifd):
                if tag == 0xA002 and count == 1:                      # pixel width
                    set_value(p, typ, w)
                elif tag == 0xA003 and count == 1:                    # pixel height
                    set_value(p, typ, h)
    except (struct.error, IndexError):
        return None
    return bytes(t)
