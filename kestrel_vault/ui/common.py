"""Reusable widgets shared by the screens."""
from __future__ import annotations

import hashlib
import sys

import shiboken6
from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QSize, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QAction, QColor, QKeyEvent, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QMenu, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

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


# ---------------------------------------------------------------- smooth scrolling
class _SmoothWheelHelper:
    """Interpolates discrete mouse-wheel scrolling smoothly while leaving precision trackpads native."""

    def _init_smooth_scroll(self) -> None:
        self._scroll_anim: QPropertyAnimation | None = None
        self._scroll_target: int = 0

    def _handle_smooth_wheel(self, event, super_wheel) -> None:
        bar = self.verticalScrollBar()  # type: ignore[attr-defined]
        dy = event.angleDelta().y()
        if (settings().reduced_motion or not event.pixelDelta().isNull()
                or dy == 0 or bar.maximum() <= bar.minimum()):
            super_wheel(event)
            return

        step = -dy / 120.0 * 88.0
        running = self._scroll_anim is not None and self._scroll_anim.state() == QPropertyAnimation.State.Running
        base = self._scroll_target if running else bar.value()
        target = int(round(max(bar.minimum(), min(bar.maximum(), base + step))))
        self._scroll_target = target

        if self._scroll_anim is not None:
            self._scroll_anim.stop()
        anim = QPropertyAnimation(bar, b"value", self)  # type: ignore[arg-type]
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()
        self._scroll_anim = anim
        event.accept()


class SmoothScrollArea(QScrollArea, _SmoothWheelHelper):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_smooth_scroll()

    def wheelEvent(self, event) -> None:
        self._handle_smooth_wheel(event, super().wheelEvent)


class SmoothListView(QListView, _SmoothWheelHelper):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_smooth_scroll()
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def wheelEvent(self, event) -> None:
        self._handle_smooth_wheel(event, super().wheelEvent)


# ---------------------------------------------------------------- animated tab stack
def _snapshot(widget: QWidget, bg_color: str) -> QPixmap:
    """Render `widget` onto an opaque pixmap pre-filled with `bg_color`.

    QWidget.grab() alone returns transparent pixels wherever the widget
    inherits its background from a parent, which is what caused the 'flash
    to a different colour' bug during tab transitions. Rendering only the
    children onto a pre-filled pixmap guarantees full opacity.
    """
    dpr = widget.devicePixelRatioF()
    size = widget.size()
    pm = QPixmap(max(1, int(size.width() * dpr)), max(1, int(size.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(bg_color))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    widget.render(painter, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    return pm


class _TabTransitionOverlay(QWidget):
    """Cross-fades and softly slides between cached, opaque page snapshots.

    Both pixmaps are pre-composited onto an opaque background so the animation
    can never reveal an unexpected colour. The new page is painted first at
    (0, 0) with full opacity so every pixel of the widget is covered at every
    animation phase, then the old page slides up/out on top.
    """

    finished = Signal()   # emitted before deleteLater() so the stack can drop its ref

    def __init__(self, parent: QWidget, old_pm: QPixmap, new_pm: QPixmap, direction: int,
                 shift: float = 18.0) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._old = old_pm
        self._new = new_pm
        self._dir = 1 if direction >= 0 else -1
        self._shift = shift
        self._p = 0.0
        self._done = False
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self._finish)
        self._anim.start()

    def _on_tick(self, value) -> None:
        self._p = float(value)
        self.update()

    def stop_now(self) -> None:
        if self._done:
            return
        try:
            self._anim.stop()
        except RuntimeError:
            pass
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self.hide()
        self.finished.emit()
        self.deleteLater()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        # Source mode replaces destination pixels instead of blending, so
        # nothing from the widget's previous state or palette can leak through.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(theme().colors["bg"]))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # New page at its final position, fully opaque — guarantees full coverage.
        p.setOpacity(1.0)
        p.drawPixmap(0, 0, self._new)

        # Old page slides up/out and fades. Because it's opaque, no gap can
        # appear: whatever it stops covering is the new page underneath.
        p.setOpacity(max(0.0, 1.0 - self._p))
        p.drawPixmap(QPointF(0.0, -self._shift * self._dir * self._p), self._old)


class AnimatedStack(QStackedWidget):
    """QStackedWidget with directional slide + fade transition between tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay: _TabTransitionOverlay | None = None

    def _clear_overlay(self) -> None:
        ov = self._overlay
        self._overlay = None
        if ov is None:
            return
        if not shiboken6.isValid(ov):
            return
        ov.stop_now()

    def _on_overlay_finished(self) -> None:
        if self._overlay is self.sender():
            self._overlay = None

    def switch_to(self, widget: QWidget, direction: int = 1) -> None:
        if widget is self.currentWidget():
            return
        self._clear_overlay()
        if settings().reduced_motion or not self.isVisible() or self.width() <= 0 or self.currentWidget() is None:
            self.setCurrentWidget(widget)
            return
        bg = theme().colors["bg"]
        old_pm = _snapshot(self.currentWidget(), bg)
        self.setCurrentWidget(widget)
        new_pm = _snapshot(widget, bg)
        overlay = _TabTransitionOverlay(self, old_pm, new_pm, direction)
        overlay.finished.connect(self._on_overlay_finished)
        self._overlay = overlay

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        ov = self._overlay
        if ov is not None and shiboken6.isValid(ov):
            ov.setGeometry(self.rect())


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
                 checkable: bool = False, size: int = 34) -> None:
        super().__init__(text)
        self._icon_name = icon_name
        self._variant = variant or ("" if text else "icon")
        self.setProperty("variant", self._variant or "default")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.setCheckable(checkable)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(text or tooltip)
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


class AnimatedMenu(QMenu):
    """Popup menu with a short fade-in, skipped when reduced motion is enabled.

    ``popup_at()`` keeps the whole popup inside the available screen area so
    its contents are never clipped by a display edge.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._open_anim: QPropertyAnimation | None = None

    def popup_at(self, pos: QPoint) -> None:
        """Exec at `pos`, nudged inside the screen so nothing is clipped."""
        self.exec(self._clamped(pos))

    def _clamped(self, pos: QPoint) -> QPoint:
        screen = self.screen() or QApplication.screenAt(pos)
        if screen is None:
            return pos
        avail = screen.availableGeometry()
        size = self.sizeHint()
        left = avail.left() + 4
        top = avail.top() + 4
        x = min(max(pos.x(), left), max(left, avail.right() - size.width() - 4))
        y = min(max(pos.y(), top), max(top, avail.bottom() - size.height() - 4))
        return QPoint(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if settings().reduced_motion or not self.isVisible():
            return
        self._open_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._open_anim.setDuration(130)
        self._open_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_anim.setStartValue(0.0)
        self._open_anim.setEndValue(1.0)
        self._open_anim.start()


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
    """Current Caps Lock toggle state, or None if the platform can't tell us."""
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


class PasswordField(QLineEdit):
    """Password input with toggleable reveal or hold-to-reveal."""
    capsChanged = Signal(bool)

    def __init__(self, placeholder: str = "", read_only: bool = False,
                 hold_to_reveal: bool = False, inline: bool = False) -> None:
        super().__init__()
        self._hold_to_reveal = hold_to_reveal
        self._revealed = False
        self.setReadOnly(read_only)
        if inline:
            self.setProperty("inline", "true")
        if placeholder:
            self.setPlaceholderText(placeholder)

        if hold_to_reveal:
            self._apply_echo(False)
            self._hold_btn: QToolButton | None = QToolButton(self)
            self._hold_btn.setProperty("variant", "icon")
            self._hold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._hold_btn.setFixedSize(28, 28)
            self._hold_btn.setIconSize(QSize(18, 18))
            self._hold_btn.setToolTip("Hold to reveal password")
            self._hold_btn.setAccessibleName("Hold to reveal password")
            self._hold_btn.pressed.connect(lambda: self.set_revealed(True))
            self._hold_btn.released.connect(lambda: self.set_revealed(False))
            self.setTextMargins(0, 0, 32, 0)
            self._reveal: QAction | None = None
            app = QApplication.instance()
            if app is not None:
                app.applicationStateChanged.connect(self._on_app_state)
        else:
            self._hold_btn = None
            self._reveal = QAction(self)
            self._reveal.setCheckable(True)
            self._reveal.setToolTip("Show / hide")
            self._reveal.toggled.connect(self._on_toggle)
            self.addAction(self._reveal, QLineEdit.ActionPosition.TrailingPosition)
            self._apply_echo(False)

        theme().changed.connect(self._retint)
        self._retint()

        self._caps_state: bool | None = None
        self._caps_timer = QTimer(self)
        self._caps_timer.setInterval(200)
        self._caps_timer.timeout.connect(self._poll_caps)

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive and self._revealed:
            self.set_revealed(False)

    def _apply_echo(self, revealed: bool) -> None:
        self._revealed = revealed
        self.setEchoMode(QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password)

    def _on_toggle(self, on: bool) -> None:
        self._apply_echo(on)
        self._retint()

    def _retint(self) -> None:
        name = "eye-off" if self._revealed else "eye"
        ic = icons.icon(name, theme().colors["sub"], 18)
        if self._reveal is not None:
            self._reveal.setIcon(ic)
        if self._hold_btn is not None:
            self._hold_btn.setIcon(ic)

    def set_revealed(self, on: bool) -> None:
        if self._reveal is not None:
            self._reveal.setChecked(on)
        else:
            self._apply_echo(on)
            self._retint()

    def reset(self) -> None:
        self.clear()
        self.set_revealed(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._hold_btn is not None:
            x = self.width() - self._hold_btn.width() - 4
            y = (self.height() - self._hold_btn.height()) // 2
            self._hold_btn.move(x, y)

    # ---- caps lock
    def _emit_caps(self, state: bool | None) -> None:
        if state is None or state == self._caps_state:
            return
        self._caps_state = state
        self.capsChanged.emit(state)

    def _poll_caps(self) -> None:
        self._emit_caps(system_caps_lock())

    def _on_caps_key(self) -> None:
        current = self._caps_state
        if current is None:
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
        if self._hold_to_reveal and self._revealed:
            self.set_revealed(False)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._hold_to_reveal and self._revealed:
            self.set_revealed(False)


# ---------------------------------------------------------------- strength meter
class StrengthMeter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.text = QLabel("")
        self.text.setMinimumWidth(84)
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
            self.text.setProperty("strength", level)
            repolish(self.bar)
            repolish(self.text)
            self._level = level
        self.text.setText(s.label)
        if self._anim is not None:
            self._anim.stop()
        if settings().reduced_motion:
            self.bar.setValue(s.pct)
        else:
            self._anim = QPropertyAnimation(self.bar, b"value", self)
            self._anim.setDuration(180)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.setStartValue(self.bar.value())
            self._anim.setEndValue(s.pct)
            self._anim.start()
        return s


# ---------------------------------------------------------------- read-only field row (inline box style)
class FieldRow(QWidget):
    """Label above a recessed read-only box with inline eye/copy/open actions."""

    copyRequested = Signal()
    openRequested = Signal()

    def __init__(self, title: str, secret: bool = False, can_copy: bool = True,
                 can_open: bool = False, multiline: bool = False) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(label(title, "small"))

        box = QFrame()
        box.setProperty("fieldBox", "true")
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 2, 6, 2)
        row.setSpacing(2)

        if multiline:
            self.field: QLineEdit | QPlainTextEdit = QPlainTextEdit()
            self.field.setReadOnly(True)
            self.field.setProperty("inline", "true")
            self.field.setFixedHeight(92)
        elif secret:
            self.field = PasswordField(read_only=True, hold_to_reveal=True, inline=True)
        else:
            self.field = QLineEdit()
            self.field.setReadOnly(True)
            self.field.setProperty("inline", "true")
        row.addWidget(self.field, 1)

        self._copy_btn: IconButton | None = None
        if can_open:
            op = IconButton("external", tooltip="Open in browser", size=30)
            op.clicked.connect(self.openRequested)
            row.addWidget(op, 0, Qt.AlignmentFlag.AlignTop if multiline else Qt.AlignmentFlag.AlignVCenter)
        if can_copy:
            self._copy_btn = IconButton("copy", tooltip=f"Copy {title.lower()}", size=30)
            self._copy_btn.clicked.connect(self.copyRequested)
            row.addWidget(self._copy_btn, 0, Qt.AlignmentFlag.AlignTop if multiline else Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(box)

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
        QTimer.singleShot(1400, lambda: self._copy_btn.set_icon_name("copy"))


# ---------------------------------------------------------------- empty state
class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str = "", text: str = "") -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.setSpacing(8)
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
        col.addSpacing(8)
        col.addWidget(self.action, 0, Qt.AlignmentFlag.AlignCenter)

    def set_content(self, icon_name: str, title: str, text: str,
                    action: bool = False, action_text: str = "Add entry",
                    action_icon: str = "plus") -> None:
        self.icon.set_name(icon_name)
        self.title.setText(title)
        self.text.setText(text)
        self.action.setText(action_text)
        self.action.set_icon_name(action_icon)
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