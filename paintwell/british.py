"""Gives Qt's own built-in dialogs (colour picker, file dialogs...) British spelling."""
import re
from PySide6.QtCore import QTranslator, QCoreApplication

_WORDS = [("Color", "Colour"), ("color", "colour"), ("Center", "Centre"), ("center", "centre"),
          ("Gray", "Grey"), ("gray", "grey"), ("Customize", "Customise"), ("customize", "customise"),
          ("Organize", "Organise"), ("organize", "organise"), ("Favorite", "Favourite"),
          ("favorite", "favourite"), ("Behavior", "Behaviour"), ("behavior", "behaviour")]
_RX = [(re.compile(r"\b" + a), b) for a, b in _WORDS]


class BritishTranslator(QTranslator):
    def translate(self, context, source, disambiguation=None, n=-1):
        if not source:
            return source or ""
        out = source
        for rx, b in _RX:
            out = rx.sub(b, out)
        return out

    def isEmpty(self):
        return False


def install(app):
    t = BritishTranslator(app)
    QCoreApplication.installTranslator(t)
    return t
