"""Files dropped onto Paintwell.

Normally a drop carries plain file addresses (file:///home/…).  When Paintwell runs sealed off
as a snap (or Flatpak), file managers such as Dolphin can instead hand the files over through
the desktop's "file transfer" portal: the drop then carries a key, and the portal turns that
key into paths Paintwell is allowed to read.  Both kinds are handled here."""
import os
import sys

PORTAL_KEY_FORMATS = ("application/vnd.portal.filetransfer", "application/vnd.portal.files")


def _debug(*a):
    if os.environ.get("PAINTWELL_DEBUG"):
        print("[drop]", *a, file=sys.stderr, flush=True)


def can_accept(mime):
    if mime.hasUrls():
        return True
    if any(mime.hasFormat(f) for f in PORTAL_KEY_FORMATS):
        return True
    return mime.hasText() and mime.text().lstrip().startswith(("file://", "/"))


def _portal_key(mime):
    for f in PORTAL_KEY_FORMATS:
        if mime.hasFormat(f):
            key = bytes(mime.data(f)).decode("utf-8", "replace").strip("\0").strip()
            if key:
                return key
    return None


def _retrieve_from_portal(key):
    """Ask the desktop's file transfer portal for the files behind a drop key."""
    try:
        from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage
    except Exception as ex:
        _debug("no QtDBus:", ex)
        return []
    msg = QDBusMessage.createMethodCall("org.freedesktop.portal.Documents",
                                        "/org/freedesktop/portal/documents",
                                        "org.freedesktop.portal.FileTransfer", "RetrieveFiles")
    msg.setArguments([key, {}])
    reply = QDBusConnection.sessionBus().call(msg, QDBus.CallMode.Block, 5000)
    if reply.type() != QDBusMessage.ReplyMessage or not reply.arguments():
        _debug("portal said:", reply.errorName(), reply.errorMessage())
        return []
    paths = [str(p) for p in reply.arguments()[0]]
    _debug("portal gave:", paths)
    return paths


def dropped_paths(mime):
    """(paths Paintwell can read, paths it was given but can't read)."""
    _debug("formats:", list(mime.formats()))
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    if not paths and mime.hasText():
        from PySide6.QtCore import QUrl
        for line in mime.text().splitlines():
            line = line.strip()
            if line.startswith("file://"):
                paths.append(QUrl(line).toLocalFile())
            elif line.startswith("/"):
                paths.append(line)
    readable = [p for p in paths if os.path.isfile(p) and os.access(p, os.R_OK)]
    key = _portal_key(mime)
    if key and (not paths or len(readable) < len(paths)):
        got = [p for p in _retrieve_from_portal(key) if os.path.isfile(p) and os.access(p, os.R_OK)]
        if got:
            return got, []
    _debug("paths:", paths, "readable:", readable)
    return readable, [p for p in paths if p not in readable]
