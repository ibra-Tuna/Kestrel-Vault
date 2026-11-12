"""Qt-side shared services: settings, theme, clipboard, toasts, transitions, workers."""
from __future__ import annotations

import hashlib
import struct
import sys
from typing import Any, Callable

from PySide6.QtCore import (QByteArray, QEasingCurve, QMimeData, QObject, QPoint, QPropertyAnimation,
                            Qt, QThread, QTimer, QVariantAnimation, QEvent, Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from . import theme as theme_mod
from .storage import VaultFile, load_settings, save_settings


# ---------------------------------------------------------------- debounce
class Debouncer(QObject):
    """Coalesce rapid calls into one. `flush()` runs it now if a call is pending."""

    def __init__(self, parent: QObject | None, ms: int, fn: Callable[[], None]) -> None:
        super().__init__(parent)
        self._fn = fn
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(ms)
        self._timer.timeout.connect(self._fn)

    def trigger(self, *_: Any) -> None:
        self._timer.start()

    def pending(self) -> bool:
        return self._timer.isActive()

    def flush(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._fn()

    def cancel(self) -> None:
        self._timer.stop()


# ---------------------------------------------------------------- settings
class Settings(QObject):
    changed = Signal(str)

    def __init__(self, store: VaultFile) -> None:
        super().__init__()
        self._path = store.settings_path
        self._data = load_settings(self._path, list(theme_mod.THEMES))
        self._saver = Debouncer(self, 500, self.flush)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.changed.emit(key)
        self._saver.trigger()

    def flush(self) -> None:
        self._saver.cancel()
        save_settings(self._path, self._data)

    @property
    def reduced_motion(self) -> bool:
        return bool(self._data.get("reduced_motion"))


# ---------------------------------------------------------------- theme
class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, settings: Settings, cache_dir) -> None:
        super().__init__()
        self._settings = settings
        self._cache = cache_dir
        self.colors: dict[str, str] = {}
        self.refresh_tokens()

    def refresh_tokens(self) -> None:
        self.colors = theme_mod.tokens(self._settings["theme_name"], self._settings["accent"], self._cache)

    def apply(self) -> None:
        self.refresh_tokens()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_mod.build_stylesheet(self.colors))
        self.changed.emit()

    def icon_color(self, variant: str = "", checked: bool = False) -> str:
        c = self.colors
        if variant == "primary":
            return c["on_accent"]
        if variant == "danger":
            return "#ffffff"
        if variant == "nav":
            return c["on_accent"] if checked else c["sub"]
        return c["fg"]


# ---------------------------------------------------------------- clipboard
def _sensitive_hints() -> list[tuple[str, bytes]]:
    """Ask clipboard managers/history not to record the value. Best effort per platform."""
    if sys.platform == "win32":
        return [("ExcludeClipboardContentFromMonitorProcessing", b"\x01"),
                ("CanIncludeInClipboardHistory", struct.pack("<I", 0)),
                ("CanUploadToCloudClipboard", struct.pack("<I", 0))]
    if sys.platform == "darwin":
        return [("org.nspasteboard.ConcealedType", b"secret")]
    return [("x-kde-passwordManagerHint", b"secret")]


class ClipboardManager(QObject):
    countdown = Signal(int)   # seconds left, or -1 when idle
    cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._digest: bytes | None = None
        self._remaining = 0

    @staticmethod
    def _hash(text: str) -> bytes:
        return hashlib.sha256(text.encode("utf-8")).digest()

    def copy(self, text: str, seconds: int | None) -> None:
        mime = QMimeData()
        mime.setText(text)
        for fmt, data in _sensitive_hints():
            mime.setData(fmt, QByteArray(data))
        QApplication.clipboard().setMimeData(mime)
        self._digest = self._hash(text)  # keep a hash, not the secret itself
        if not seconds or seconds <= 0:
            self._timer.stop()
            self.countdown.emit(-1)
            return
        self._remaining = seconds
        self.countdown.emit(seconds)
        self._timer.start()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self.clear_now()
        else:
            self.countdown.emit(self._remaining)

    def clear_now(self) -> None:
        """Clear the clipboard, but only if it still holds what we put there."""
        self._timer.stop()
        cb = QApplication.clipboard()
        if self._digest is not None and self._hash(cb.text()) == self._digest:
            cb.clear()
            self.cleared.emit()
        self._digest = None
        self.countdown.emit(-1)


# ---------------------------------------------------------------- toasts
class Toast(QLabel):
    def __init__(self, parent: QWidget, message: str, bg: str, fg: str) -> None:
        super().__init__(message, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:12px; padding:11px 20px;"
            "font-weight:600; font-size:13px;")
        self.adjustSize()
        self._anim: QPropertyAnimation | None = None

    def slide_to(self, y: int, reduced: bool, from_below: bool = False) -> None:
        x = (self.parentWidget().width() - self.width()) // 2
        target = QPoint(x, y)
        if reduced:
            self.move(target)
            self.show()
            self.raise_()
            return
        if from_below:
            self.move(x, self.parentWidget().height())
        self.show()
        self.raise_()
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(target)
        self._anim.start()

    def dismiss(self, reduced: bool) -> None:
        if reduced:
            self.deleteLater()
            return
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(self.x(), self.parentWidget().height()))
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


class ToastManager(QObject):
    """Shows toasts anchored to the bottom of `host` (the central area, so the
    status bar is never covered) and passes mouse events through to the UI below."""

    MAX = 3
    SPACING = 8

    def __init__(self, host: QWidget, settings: Settings, theme: ThemeManager) -> None:
        super().__init__(host)
        self._host = host
        self._settings = settings
        self._theme = theme
        self._toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, message: str, color: str | None = None) -> None:
        bg = color or self._theme.colors["accent"]
        fg = theme_mod.on_color(bg)
        while len(self._toasts) >= self.MAX:
            self._toasts.pop(0).dismiss(self._settings.reduced_motion)
        toast = Toast(self._host, message, bg, fg)
        self._toasts.append(toast)
        self._restack(new=toast)
        QTimer.singleShot(2800, lambda t=toast: self._remove(t))

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            toast.dismiss(self._settings.reduced_motion)
            self._restack()

    def _restack(self, new: Toast | None = None) -> None:
        y = self._host.height() - 16
        for t in reversed(self._toasts):
            y -= t.height()
            t.slide_to(y, self._settings.reduced_motion, from_below=(t is new))
            y -= self.SPACING

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._restack()
        return False


# ---------------------------------------------------------------- transitions
class Curtain(QWidget):
    """A flat colour veil that fades out over a page after it is swapped in.

    Fading a flat rectangle is cheap and never touches the live widgets, unlike
    QGraphicsOpacityEffect on a whole page (which blurs text and flickers)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._alpha = 0.0
        self._color = QColor("#000000")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self.hide)
        self.hide()

    def play(self, color: str, reduced: bool) -> None:
        if reduced:
            return
        self._color = QColor(color)
        self.setGeometry(self.parentWidget().rect())
        self._alpha = 0.92
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.92)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_value(self, value: Any) -> None:
        self._alpha = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        c = QColor(self._color)
        c.setAlphaF(max(0.0, min(1.0, self._alpha)))
        painter.fillRect(self.rect(), c)


# ---------------------------------------------------------------- workers
class TaskWorker(QThread):
    """Run a blocking callable (e.g. Argon2) off the UI thread."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn, self._args = fn, args

    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as exc:  # delivered to the UI thread
            self.failed.emit(exc)
            return
        self.succeeded.emit(result)


_running: set[TaskWorker] = set()


def run_task(fn: Callable[..., Any], *args: Any,
             on_done: Callable[[Any], None], on_fail: Callable[[Exception], None]) -> TaskWorker:
    """Start a worker and keep it referenced until it finishes."""
    worker = TaskWorker(fn, *args)
    _running.add(worker)
    worker.succeeded.connect(on_done)
    worker.failed.connect(on_fail)
    worker.finished.connect(lambda w=worker: (_running.discard(w), w.deleteLater()))
    worker.start()
    return worker


# ---------------------------------------------------------------- singletons
_settings: Settings | None = None
_theme: ThemeManager | None = None
_clipboard: ClipboardManager | None = None


def init(store: VaultFile) -> None:
    global _settings, _theme, _clipboard
    _settings = Settings(store)
    _theme = ThemeManager(_settings, store.dir / "cache")
    _clipboard = ClipboardManager()


def settings() -> Settings:
    assert _settings is not None, "services.init() was not called"
    return _settings


def theme() -> ThemeManager:
    assert _theme is not None, "services.init() was not called"
    return _theme


def clipboard() -> ClipboardManager:
    assert _clipboard is not None, "services.init() was not called"
    return _clipboard
