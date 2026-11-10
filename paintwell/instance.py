"""Single instance for opening files.

If Paintwell is already running, a new launch with files passes them to it over a local
socket and exits.
"""
import json
import os
import tempfile

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, f"paintwell-{os.getuid()}.sock")


def send_to_running(files):
    """Give these files to an already running Paintwell. True if one took them."""
    s = QLocalSocket()
    s.connectToServer(socket_path())
    if not s.waitForConnected(700):
        return False
    msg = {"files": [os.path.abspath(f) for f in files],
           # lets the running Paintwell come to the front on Wayland (and X11)
           "token": os.environ.get("XDG_ACTIVATION_TOKEN", ""),
           "startup_id": os.environ.get("DESKTOP_STARTUP_ID", "")}
    s.write(json.dumps(msg).encode("utf-8") + b"\n")
    s.flush()
    s.waitForBytesWritten(1500)
    ok = s.waitForReadyRead(3000) and bytes(s.readAll()).startswith(b"ok")
    s.disconnectFromServer()
    return ok


class InstanceServer(QObject):
    """Listens for later launches and opens the files they pass on."""

    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self.on_files = on_files
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        path = socket_path()
        if not self.server.listen(path):
            QLocalServer.removeServer(path)          # left behind by a Paintwell that crashed
            self.server.listen(path)
        self.server.newConnection.connect(self._new_connection)

    def _new_connection(self):
        while self.server.hasPendingConnections():
            c = self.server.nextPendingConnection()
            c.setProperty("buf", b"")
            c.readyRead.connect(lambda c=c: self._read(c))
            c.disconnected.connect(c.deleteLater)

    def _read(self, c):
        buf = c.property("buf") + bytes(c.readAll())
        c.setProperty("buf", buf)
        if b"\n" not in buf:
            return
        try:
            msg = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except ValueError:
            msg = {}
        c.write(b"ok\n")
        c.flush()
        self.on_files(msg.get("files") or [], msg.get("token") or "", msg.get("startup_id") or "")


def bring_to_front(w, token="", startup_id=""):
    if token:
        os.environ["XDG_ACTIVATION_TOKEN"] = token     # Qt uses it for the next activation
    if startup_id:
        os.environ["DESKTOP_STARTUP_ID"] = startup_id
    if w.isMinimized():
        w.showNormal()
    w.show()
    w.raise_()
    w.activateWindow()
