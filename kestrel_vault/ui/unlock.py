"""Unlock / create-vault / recovery-key screen."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..crypto import (CorruptVaultError, VaultError, WrongPasswordError, derive_raw_key,
                      generate_recovery_key)
from ..passwords import check_master_password
from ..services import run_task, settings
from ..storage import LockoutStore
from .common import IconButton, IconLabel, PasswordField, SmoothScrollArea, StrengthMeter, label
from .dialogs import ChangeMasterDialog, RecoveryKitDialog, confirm

if TYPE_CHECKING:
    from .main_window import MainWindow

log = logging.getLogger(__name__)


def _set_notice(lbl: QLabel, text: str) -> None:
    """Only occupy layout space when a notice actually has text."""
    lbl.setText(text)
    lbl.setVisible(bool(text))


class UnlockWidget(QWidget):
    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self.lockout = LockoutStore(main.store.lockout_path)
        self.creating = False
        self._recovery_mode = False
        self._busy = False
        self._header = None
        self._shake_anim: QPropertyAnimation | None = None
        self._shake_target: QWidget | None = None
        self._shake_origin: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        vbox = QVBoxLayout(host)
        vbox.setContentsMargins(24, 24, 24, 24)
        row = QHBoxLayout()
        row.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("unlockCard")
        self.card.setMinimumWidth(360)
        self.card.setMaximumWidth(384)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.card.setGraphicsEffect(shadow)
        row.addWidget(self.card, 4)
        row.addStretch(1)
        vbox.addStretch(1)
        vbox.addLayout(row)
        vbox.addStretch(1)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(28, 28, 28, 24)
        cl.setSpacing(8)
        mark = IconLabel("shield", 40, "accent")
        cl.addWidget(mark, 0, Qt.AlignmentFlag.AlignCenter)
        brand = label("Kestrel Vault", "title")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(brand)
        self.subtitle = label("", "muted", wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.subtitle)
        cl.addSpacing(8)

        # Standard password field (no distracting custom typing animation).
        self.pw_unlock = PasswordField("Master password")
        self.pw_unlock.setMinimumHeight(42)
        self.pw_create = PasswordField("Master password")
        self.pw_create.setMinimumHeight(42)
        self.confirm_field = PasswordField("Confirm master password")
        self.confirm_field.setMinimumHeight(42)
        self.meter = StrengthMeter()
        self.hint = label("", "muted", wrap=True)
        self.caps = label("", "warn")
        self.caps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attempts = label("", "small")
        self.attempts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button = IconButton("lock", "Unlock", "primary")
        self.button.setMinimumHeight(42)
        self.error = label("", "error", wrap=True)
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recovery_toggle = QPushButton("Unlock with recovery key")
        self.recovery_toggle.setProperty("variant", "link")
        self.recovery_toggle.setCursor(Qt.CursorShape.PointingHandCursor)

        for w in (self.pw_unlock, self.pw_create, self.confirm_field, self.meter,
                  self.hint, self.caps, self.attempts):
            cl.addWidget(w)
        cl.addSpacing(4)
        cl.addWidget(self.button)
        cl.addWidget(self.error)
        cl.addWidget(self.recovery_toggle, 0, Qt.AlignmentFlag.AlignCenter)

        for lbl in (self.hint, self.caps, self.attempts, self.error):
            lbl.setVisible(False)

        self.pw_unlock.returnPressed.connect(self.submit)
        self.pw_create.returnPressed.connect(self.submit)
        self.confirm_field.returnPressed.connect(self.submit)
        self.button.clicked.connect(self.submit)
        self.pw_unlock.textChanged.connect(self._on_text)
        self.pw_create.textChanged.connect(self._on_text)
        self.confirm_field.textChanged.connect(self._on_text)
        self.pw_unlock.capsChanged.connect(self._on_caps)
        self.pw_create.capsChanged.connect(self._on_caps)
        self.confirm_field.capsChanged.connect(self._on_caps)
        self.recovery_toggle.clicked.connect(self._toggle_recovery)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._tick_cooldown)
        self.refresh_mode()

    def _active_pw(self) -> PasswordField:
        return self.pw_create if self.creating else self.pw_unlock

    # ---- mode / state
    def refresh_mode(self) -> None:
        self._stop_shake()
        self.creating = self.main.store.is_new()
        if self.creating:
            self._recovery_mode = False
            self.subtitle.setText("Create a master password to protect your vault.")
            self.button.setText("Create vault")
            self.pw_unlock.setVisible(False)
            self.pw_create.setVisible(True)
            self.recovery_toggle.setVisible(False)
        else:
            self.pw_create.setVisible(False)
            self.pw_unlock.setVisible(True)
            if self._recovery_mode:
                self.subtitle.setText("Enter your offline Recovery Key (KVLT-...).")
                self.pw_unlock.setPlaceholderText("Recovery key")
                self.button.setText("Recover vault")
                self.recovery_toggle.setText("Back to master password")
            else:
                self.subtitle.setText("Enter your master password to unlock.")
                self.pw_unlock.setPlaceholderText("Master password")
                self.button.setText("Unlock")
                self.recovery_toggle.setText("Unlock with recovery key")
            self.recovery_toggle.setVisible(True)
        self.confirm_field.setVisible(self.creating)
        self.meter.setVisible(self.creating)
        if not self.creating:
            _set_notice(self.hint, "")
        _set_notice(self.error, "")
        self._update_attempts()
        self._tick_cooldown()
        self._sync_button()

    def _update_attempts(self) -> None:
        if self.creating:
            _set_notice(self.attempts, "")
            return
        failed, _ = self.lockout.load()
        _set_notice(self.attempts, f"{failed} failed attempt{'s' if failed != 1 else ''}" if failed > 0 else "")

    def _toggle_recovery(self) -> None:
        self._recovery_mode = not self._recovery_mode
        self.pw_unlock.reset()
        self.refresh_mode()
        self.focus_input()

    def set_error(self, text: str) -> None:
        _set_notice(self.error, text)

    def clear(self) -> None:
        self._stop_shake()
        self.pw_unlock.reset()
        self.pw_create.reset()
        self.confirm_field.reset()
        self._recovery_mode = False
        _set_notice(self.error, "")
        _set_notice(self.caps, "")
        self._busy = False
        self.refresh_mode()

    def focus_input(self) -> None:
        QTimer.singleShot(60, self._active_pw().setFocus)

    def _on_caps(self, on: bool) -> None:
        _set_notice(self.caps, "Caps Lock is on" if on else "")

    def _on_text(self) -> None:
        if self.creating:
            pw = self.pw_create.text()
            self.meter.set_password(pw)
            problem = check_master_password(pw) if pw else None
            if not problem and self.confirm_field.text() and pw != self.confirm_field.text():
                problem = "The passwords don't match yet."
            _set_notice(self.hint, problem or "")
        self._sync_button()

    def _sync_button(self) -> None:
        if self._busy or self.lockout.remaining() > 0:
            self.button.setEnabled(False)
            return
        pw = self._active_pw().text()
        if self.creating:
            ok = bool(pw) and check_master_password(pw) is None and pw == self.confirm_field.text()
        else:
            ok = bool(pw)
        self.button.setEnabled(ok)

    # ---- lockout
    def _tick_cooldown(self) -> None:
        rem = self.lockout.remaining()
        if rem > 0:
            if not self._cooldown_timer.isActive():
                self._cooldown_timer.start()
            self.button.setEnabled(False)
            _set_notice(self.error, f"Too many attempts. Try again in {rem}s.")
        else:
            if self._cooldown_timer.isActive():
                self._cooldown_timer.stop()
                _set_notice(self.error, "")
            self._sync_button()

    # ---- submit
    def submit(self) -> None:
        field = self._active_pw()
        if self._busy or self.lockout.remaining() > 0 or not field.text():
            return
        pw = field.text()
        if self.creating:
            if check_master_password(pw) or pw != self.confirm_field.text():
                return
            if not confirm(self, "Create vault",
                           "Create a new vault with this master password?\n\n"
                           "You will be shown an offline Recovery Key once after creation.",
                           "Create vault"):
                return
        try:
            header = self.main.store.new_header() if self.creating else self.main.store.load_header()
        except VaultError as exc:
            _set_notice(self.error, str(exc))
            return
        self._header = header
        self._busy = True
        self.button.setEnabled(False)
        self.button.setText("Working…")
        _set_notice(self.error, "")
        if self._recovery_mode and not self.creating:
            self._recover_with_code(header, pw)
            return
        run_task(derive_raw_key, pw, header.salt, header.kdf,
                 on_done=self._on_key, on_fail=self._on_error)

    def _recover_with_code(self, header, code: str) -> None:
        self._finish_busy()
        try:
            entries, dek = self.main.store.open_with_recovery(header, code)
        except WrongPasswordError:
            self._wrong_password("Invalid recovery key.")
            return
        except CorruptVaultError as exc:
            _set_notice(self.error, str(exc))
            return
        import os
        temp_kek = os.urandom(32)
        self.main.session.open(temp_kek, dek, header, entries, migrated=False)
        self.lockout.reset()
        self.pw_unlock.reset()
        dlg = ChangeMasterDialog(self.main, require_current=False)
        if dlg.exec():
            _, new_pw = dlg.values()
            import base64
            salt = os.urandom(16)
            new_kek = derive_raw_key(new_pw, salt)
            self.main.session.change_master(new_kek, base64.b64encode(salt).decode("ascii"))
            self.main.on_unlocked()
            self.main.toast("Vault recovered and master password updated", "success")
        else:
            self.main.session.lock()
            _set_notice(self.error, "Set a new master password to complete recovery.")

    def _finish_busy(self) -> None:
        self._busy = False
        self.refresh_mode()

    def _on_key(self, raw_key: bytes) -> None:
        header = self._header
        was_creating = self.creating
        self._finish_busy()
        session = self.main.session
        recovery_code: str | None = None
        try:
            if was_creating:
                recovery_code = generate_recovery_key()
                session.create(raw_key, header, recovery_code=recovery_code)
            else:
                entries, dek, migrated = self.main.store.open_with_key(header, raw_key)
                session.open(raw_key, dek, header, entries, migrated)
        except WrongPasswordError:
            self._wrong_password()
            return
        except CorruptVaultError as exc:
            _set_notice(self.error, str(exc))
            return
        except OSError as exc:
            log.error("Create/open failed: %s", type(exc).__name__)
            _set_notice(self.error, "The vault file could not be written. Check permissions and disk space.")
            return
        self.lockout.reset()
        self.pw_unlock.reset()
        self.pw_create.reset()
        self.confirm_field.reset()
        self.main.on_unlocked()
        if was_creating and recovery_code:
            RecoveryKitDialog(self.main, recovery_code).exec()

    def _on_error(self, exc: Exception) -> None:
        self._finish_busy()
        log.error("Key derivation failed: %s", type(exc).__name__)
        if isinstance(exc, ImportError):
            _set_notice(self.error, "The 'argon2-cffi' package is missing. Install it and restart.")
        else:
            _set_notice(self.error, "Could not process the password. See the log for details.")

    def _wrong_password(self, msg: str = "Incorrect master password.") -> None:
        _failed, delay = self.lockout.record_failure()
        self._active_pw().reset()
        self._update_attempts()
        _set_notice(self.error, msg)
        if delay:
            self._tick_cooldown()
        if not settings().reduced_motion:
            self._shake()
        self._active_pw().setFocus()

    def _stop_shake(self) -> None:
        if self._shake_anim is not None and self._shake_anim.state() == QAbstractAnimation.State.Running:
            self._shake_anim.stop()
        if self._shake_target is not None and self._shake_origin is not None:
            self._shake_target.move(self._shake_origin)
        self._shake_target = None
        self._shake_origin = None

    def _shake(self) -> None:
        self._stop_shake()
        if self.card.layout() is not None:
            self.card.layout().activate()
        target = self._active_pw()
        orig = target.pos()
        self._shake_target = target
        self._shake_origin = orig
        a = QPropertyAnimation(target, b"pos", self)
        a.setDuration(220)
        a.setEasingCurve(QEasingCurve.Type.OutSine)
        for t, dx in ((0.0, 0), (0.15, -9), (0.35, 9), (0.55, -6), (0.75, 5), (0.9, -2), (1.0, 0)):
            a.setKeyValueAt(t, orig + QPoint(dx, 0))
        a.finished.connect(self._stop_shake)
        a.start()
        self._shake_anim = a
