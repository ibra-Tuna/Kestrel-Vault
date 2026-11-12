"""Reusable widgets shared by the screens."""
from __future__ import annotations

import hashlib
import sys
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import icons
from ..passwords import Strength, estimate
from ..services import settings, theme


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def section_label(text: str) -> QLabel:
    return label(text.upper(), "section")


def card(inner: bool = False) -> QFrame:
    frame = QFrame()
    frame.setProperty("card", "inner" if inner else "true")
    return frame


def name_color(name: str) -> QColor:
    h = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).digest()
    return QColor.fromHsv((h[0] << 8 | h[1]) % 360, 150, 205)


# ---------------------------------------------------------------- icons
class IconLabel(QLabel):
    def __init__(self, name: str, size: int = 18, color_key: str = "sub") -> None:
        super().__init__()
        self._name, self._size, self._key = name, size, color_key
        self.setFixedSize(size, size)
        theme().changed.connect(self.retint)
        self.retint()

    def set_name(self, name: str) -> None:
        self._name = name
        self.retint()

    def retint(self) -> None:
        self.setPixmap(icons.pixmap(self._name, theme().colors[self._key], self._size))


class IconButton(QPushButton):
    """A button whose icon follows the theme. Icon-only buttons get a compact square style."""

    def __init__(self, icon_name: str, text: str = "", variant: str = "", tooltip: str = "",
                 checkable: bool = False, size: int = 36) -> None:
        super().__init__(text)
        self._icon_name = icon_name
        self._variant = variant or ("" if text else "icon")
        self.setProperty("variant", self._variant or "default")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.setCheckable(checkable)
        if tooltip:
            self.setToolTip(tooltip)
        if self._variant == "icon":
            self.setFixedSize(size, size)
        if self._variant == "nav":
            self.toggled.connect(lambda _on: self.retint())
        theme().changed.connect(self.retint)
        self.retint()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self.retint()

    def retint(self) -> None:
        color = theme().icon_color(self._variant, self.isChecked())
        self.setIcon(icons.icon(self._icon_name, color, 18))


# ---------------------------------------------------------------- password field
def caps_from_event(event: QKeyEvent) -> bool | None:
    """Infer Caps Lock from a key press without shelling out. None = can't tell."""
    t = event.text()
    if len(t) != 1 or not t.isalpha():
        return None
    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
    if t.isupper():
        return not shift
    if t.islower():
        return shift
    return None


def system_caps_lock() -> bool | None:
    """Current Caps Lock toggle state, or None if the platform can't tell us.

    GetKeyState's low bit is the toggle state; GetAsyncKeyState's low bit is
    only a one-shot "pressed since last call" flag, which is why the warning
    used to flicker off right after a press. GetKeyState lags behind until
    the thread reads a key message, which the polling timer and the explicit
    Key_CapsLock handler compensate for.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


class PasswordField(QLineEdit):
    capsChanged = Signal(bool)

    def __init__(self, placeholder: str = "", read_only: bool = False) -> None:
        super().__init__()
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setReadOnly(read_only)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self._reveal = QAction(self)
        self._reveal.setCheckable(True)
        self._reveal.setToolTip("Show / hide")
        self._reveal.toggled.connect(self._on_toggle)
        self.addAction(self._reveal, QLineEdit.ActionPosition.TrailingPosition)
        theme().changed.connect(self._retint)
        self._retint()

        # Caps Lock reporting: poll while focused so the warning appears the
        # instant the field is focused (or the key is toggled), not on first key.
        self._caps_state: bool | None = None
        self._caps_timer = QTimer(self)
        self._caps_timer.setInterval(200)
        self._caps_timer.timeout.connect(self._poll_caps)

    def _on_toggle(self, on: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self._retint()

    def _retint(self) -> None:
        name = "eye-off" if self._reveal.isChecked() else "eye"
        self._reveal.setIcon(icons.icon(name, theme().colors["sub"], 18))

    def set_revealed(self, on: bool) -> None:
        self._reveal.setChecked(on)

    def reset(self) -> None:
        self.clear()
        self._reveal.setChecked(False)

    # ---- caps lock
    def _emit_caps(self, state: bool | None) -> None:
        """Emit only when the value actually changes, so listeners don't flicker."""
        if state is None or state == self._caps_state:
            return
        self._caps_state = state
        self.capsChanged.emit(state)

    def _poll_caps(self) -> None:
        self._emit_caps(system_caps_lock())

    def _on_caps_key(self) -> None:
        """CapsLock was pressed while focused: the toggle state just flipped."""
        current = self._caps_state
        if current is None:
            # No tracked state yet; fall back to GetKeyState, which is
            # current now because the CapsLock key message was just read.
            current = bool(system_caps_lock())
        self._emit_caps(not current)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_CapsLock:
            self._on_caps_key()
            return
        state = caps_from_event(event)
        if state is not None:
            self._emit_caps(state)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._emit_caps(system_caps_lock())
        if not self.isReadOnly():
            self._caps_timer.start()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._caps_timer.stop()
        self._emit_caps(False)


# ---------------------------------------------------------------- strength meter
class StrengthMeter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.text = QLabel("")
        self.text.setMinimumWidth(78)
        self.text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.bar, 1)
        row.addWidget(self.text)
        self._anim: QPropertyAnimation | None = None
        self._level: str | None = None

    def set_password(self, pw: str) -> Strength:
        s = estimate(pw)
        level = str(s.score) if s.score >= 0 else ""
        if level != self._level:
            self.bar.setProperty("level", level)
            repolish(self.bar)
            self._level = level
        self.text.setText(s.label)
        self.text.setStyleSheet(f"color:{s.color}; font-weight:600;" if s.label else "")
        if self._anim is not None:
            self._anim.stop()
        if settings().reduced_motion:
            self.bar.setValue(s.pct)
        else:
            self._anim = QPropertyAnimation(self.bar, b"value", self)
            self._anim.setDuration(240)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.setStartValue(self.bar.value())
            self._anim.setEndValue(s.pct)
            self._anim.start()
        return s


# ---------------------------------------------------------------- read-only field row
class FieldRow(QWidget):
    """Label above a read-only value, with copy / open buttons. Built once, updated often."""

    copyRequested = Signal()
    openRequested = Signal()

    def __init__(self, title: str, secret: bool = False, can_copy: bool = True,
                 can_open: bool = False, multiline: bool = False) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(label(title, "small"))
        row = QHBoxLayout()
        row.setSpacing(6)
        if multiline:
            self.field: QLineEdit | QPlainTextEdit = QPlainTextEdit()
            self.field.setReadOnly(True)
            self.field.setFixedHeight(96)
        elif secret:
            self.field = PasswordField(read_only=True)
        else:
            self.field = QLineEdit()
            self.field.setReadOnly(True)
        row.addWidget(self.field, 1)
        self._copy_btn: IconButton | None = None
        if can_copy:
            self._copy_btn = IconButton("copy", tooltip=f"Copy {title.lower()}")
            self._copy_btn.clicked.connect(self.copyRequested)
            row.addWidget(self._copy_btn, 0, Qt.AlignmentFlag.AlignTop if multiline else Qt.AlignmentFlag.AlignVCenter)
        if can_open:
            op = IconButton("external", tooltip="Open in browser")
            op.clicked.connect(self.openRequested)
            row.addWidget(op)
        col.addLayout(row)

    def set_value(self, text: str) -> None:
        if isinstance(self.field, QPlainTextEdit):
            if self.field.toPlainText() != text:
                self.field.setPlainText(text)
        else:
            self.field.setText(text)
            self.field.setCursorPosition(0)
        if isinstance(self.field, PasswordField):
            self.field.set_revealed(False)

    def clear_value(self) -> None:
        self.set_value("")

    def flash_copied(self) -> None:
        if self._copy_btn is None:
            return
        self._copy_btn.set_icon_name("check")
        QTimer.singleShot(1200, lambda: self._copy_btn.set_icon_name("copy"))


# ---------------------------------------------------------------- empty state
class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str = "", text: str = "") -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.setSpacing(10)
        self.icon = IconLabel(icon_name, 44)
        self.title = label(title, "h2")
        self.text = label(text, "muted", wrap=True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMaximumWidth(300)
        self.action = IconButton("plus", "Add entry", "primary")
        self.action.setVisible(False)
        col.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.title)
        col.addWidget(self.text, 0, Qt.AlignmentFlag.AlignCenter)
        col.addSpacing(6)
        col.addWidget(self.action, 0, Qt.AlignmentFlag.AlignCenter)

    def set_content(self, icon_name: str, title: str, text: str, action: bool = False) -> None:
        self.icon.set_name(icon_name)
        self.title.setText(title)
        self.text.setText(text)
        self.action.setVisible(action)


# ---------------------------------------------------------------- avatar
class Avatar(QWidget):
    def __init__(self, size: int = 48) -> None:
        super().__init__()
        self._name = ""
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(name_color(self._name))
        p.drawEllipse(self.rect())
        p.setPen(QColor("white"))
        f = p.font()
        f.setBold(True)
        f.setPixelSize(int(self.height() * 0.42))
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, (self._name[:1] or "?").upper())


def hbox(*widgets: QWidget | int, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if isinstance(w, int):
            lay.addStretch(w)
        else:
            lay.addWidget(w)
    return lay