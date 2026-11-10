import os
import sys


def _crash_log():
    """Write Python tracebacks to ~/.cache/paintwell/crash.log if Paintwell crashes."""
    import faulthandler, time, traceback
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "paintwell")
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "crash.log")
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
        f = open(path, "a", buffering=1)
    except OSError:
        return
    f.write(f"\n=== Paintwell started {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"(platform: {os.environ.get('XDG_SESSION_TYPE', '?')}) ===\n")
    faulthandler.enable(file=f, all_threads=True)

    def hook(t, v, tb):
        f.write("Unhandled error:\n" + "".join(traceback.format_exception(t, v, tb)))
        sys.__excepthook__(t, v, tb)
    sys.excepthook = hook
    globals()["_crash_file"] = f  # keep it open for the whole run


def main():
    _crash_log()
    from PySide6.QtCore import Qt, QSettings
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    from .mainwindow import MainWindow, apply_theme, APP_NAME

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    from .british import install as _british
    _british(app)
    from .dragspin import install as _drag_spins
    _drag_spins(app)   # click and slide left/right on any number box to change it
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("paintwell")
    apply_theme(app, QSettings(APP_NAME, APP_NAME).value("theme", "system"))
    files = []
    for a in sys.argv[1:]:
        if a.startswith("file://"):
            from PySide6.QtCore import QUrl
            a = QUrl(a).toLocalFile()
        if not a.startswith("-") and os.path.exists(a):
            files.append(a)
    from . import instance
    selftest = bool(os.environ.get("PAINTWELL_SELFTEST"))
    # opening a picture while Paintwell is already running: hand it over and quit
    if files and not selftest and instance.send_to_running(files):
        sys.stdout.flush()
        os._exit(0)
    w = MainWindow(files)
    w.show()
    if not selftest:
        def open_passed(paths, token, startup_id, win=w):
            for p in paths:
                if os.path.exists(p):
                    win.open_path(p)
            instance.bring_to_front(win, token, startup_id)
        app._instance_server = instance.InstanceServer(open_passed, app)   # keep it alive
    shot = os.environ.get("PAINTWELL_SELFTEST")
    if shot:  # used by the build to check the packaged app starts and renders
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda win=w: (win.grab().save(shot), print("platform:", app.platformName()),
                                               [d.mark_saved() for d, c in win.docs], app.quit()))
    ret = app.exec()
    # tear the window down while Qt is still alive (avoids crashes at interpreter exit)
    from PySide6.QtCore import QEvent
    w.settings.sync()
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    del w
    # Skip interpreter shutdown: on some Wayland setups Qt objects are destroyed in the wrong
    # order and the process segfaults after the window has closed. Everything is saved by now.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(ret)


if __name__ == "__main__":
    main()
