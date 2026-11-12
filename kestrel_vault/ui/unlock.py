"""Unlock / create-vault screen."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..crypto import CorruptVaultError, VaultError, WrongPasswordError, derive_key
from ..passwords import check_master_password
from ..services import run_task, settings
from ..storage import LockoutStore
from .common import IconButton, IconLabel, PasswordField, StrengthMeter, label
from .dialogs import confirm

if TYPE_CHECKING:
    from .main_window import MainWindow

log = logging.getLogger(__name__)


class UnlockWidget(QWidget):
    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self.lockout = LockoutStore(main.store.lockout_path)
        self.creating = False
        self._busy = False
        self._header = None
        self._shake_anim: QPropertyAnimation | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
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
        self.card.setMaximumWidth(460)
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
        cl.setContentsMargins(36, 40, 36, 36)
        cl.setSpacing(12)
        mark = IconLabel("shield", 44, "accent")
        cl.addWidget(mark, 0, Qt.AlignmentFlag.AlignCenter)
        brand = label("Kestrel Vault", "title")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(brand)
        self.subtitle = label("", "muted", wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.subtitle)
        cl.addSpacing(8)

        self.pw = PasswordField("Master password")
        self.pw.setMinimumHeight(44)
        self.confirm_field = PasswordField("Confirm master password")
        self.confirm_field.setMinimumHeight(44)
        self.meter = StrengthMeter()
        self.hint = label("", "muted", wrap=True)
        self.caps = label("", "warn")
        self.caps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button = IconButton("lock", "Unlock", "primary")
        self.button.setMinimumHeight(46)
        self.error = label("", "error", wrap=True)
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self.pw, self.confirm_field, self.meter, self.hint, self.caps, self.button, self.error):
            cl.addWidget(w)

        self.pw.returnPressed.connect(self.submit)
        self.confirm_field.returnPressed.connect(self.submit)
        self.button.clicked.connect(self.submit)
        self.pw.textChanged.connect(self._on_text)
        self.confirm_field.textChanged.connect(self._on_text)
        self.pw.capsChanged.connect(self._on_caps)
        self.confirm_field.capsChanged.connect(self._on_caps)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._tick_cooldown)
        self.refresh_mode()

    # ---- mode / state
    def refresh_mode(self) -> None:
        self.creating = self.main.store.is_new()
        if self.creating:
            self.subtitle.setText("Create a master password to protect your vault.\n"
                                  "It cannot be recovered if you forget it.")
            self.button.setText("Create vault")
        else:
            self.subtitle.setText("Enter your master password to unlock.")
            self.button.setText("Unlock")
        for w in (self.confirm_field, self.meter, self.hint):
            w.setVisible(self.creating)
        self.error.setText("")
        self._tick_cooldown()
        self._sync_button()

    def clear(self) -> None:
        self.pw.reset()
        self.confirm_field.reset()
        self.error.setText("")
        self.caps.setText("")
        self._busy = False
        self.refresh_mode()

    def focus_input(self) -> None:
        QTimer.singleShot(60, self.pw.setFocus)

    def _on_caps(self, on: bool) -> None:
        self.caps.setText("Caps Lock is on" if on else "")

    def _on_text(self) -> None:
        if self.creating:
            pw = self.pw.text()
            self.meter.set_password(pw)
            problem = check_master_password(pw) if pw else None
            if not problem and self.confirm_field.text() and pw != self.confirm_field.text():
                problem = "The passwords don't match yet."
            self.hint.setText(problem or "")
        self._sync_button()

    def _sync_button(self) -> None:
        if self._busy or self.lockout.remaining() > 0:
            self.button.setEnabled(False)
            return
        pw = self.pw.text()
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
            self.error.setText(f"Too many attempts. Try again in {rem}s.")
        else:
            if self._cooldown_timer.isActive():
                self._cooldown_timer.stop()
                self.error.setText("")
            self._sync_button()

    # ---- submit
    def submit(self) -> None:
        if self._busy or self.lockout.remaining() > 0 or not self.pw.text():
            return
        pw = self.pw.text()
        if self.creating:
            if check_master_password(pw) or pw != self.confirm_field.text():
                return
            if not confirm(self, "Create vault",
                           "Create a new vault with this master password?\n\n"
                           "There is no recovery option. If you forget it, the data is lost.",
                           "Create vault"):
                return
        try:
            header = self.main.store.new_header() if self.creating else self.main.store.load_header()
        except VaultError as exc:
            self.error.setText(str(exc))
            return
        self._header = header
        self._busy = True
        self.button.setEnabled(False)
        self.button.setText("Working…")
        self.error.setText("")
        run_task(derive_key, pw, header.salt, header.kdf,
                 on_done=self._on_key, on_fail=self._on_error)

    def _finish_busy(self) -> None:
        self._busy = False
        self.refresh_mode()

    def _on_key(self, key: bytes) -> None:
        header = self._header
        self._finish_busy()
        session = self.main.session
        try:
            if self.creating:
                session.create(key, header)
            else:
                entries, migrated = self.main.store.open_with_key(header, key)
                session.open(key, header, entries, migrated)
        except WrongPasswordError:
            self._wrong_password()
            return
        except CorruptVaultError as exc:
            self.error.setText(str(exc))
            return
        except OSError as exc:
            log.error("Create/open failed: %s", type(exc).__name__)
            self.error.setText("The vault file could not be written. Check permissions and disk space.")
            return
        self.lockout.reset()
        self.pw.reset()
        self.confirm_field.reset()
        self.main.on_unlocked()

    def _on_error(self, exc: Exception) -> None:
        self._finish_busy()
        log.error("Key derivation failed: %s", type(exc).__name__)
        if isinstance(exc, ImportError):
            self.error.setText("The 'argon2-cffi' package is missing. Install it and restart.")
        else:
            self.error.setText("Could not process the password. See the log for details.")

    def _wrong_password(self) -> None:
        failed, delay = self.lockout.record_failure()
        self.pw.reset()
        self.error.setText("Incorrect master password.")
        if delay:
            self._tick_cooldown()
        if not settings().reduced_motion:
            self._shake()
        self.pw.setFocus()

    def _shake(self) -> None:
        orig = self.pw.pos()
        a = QPropertyAnimation(self.pw, b"pos", self)
        a.setDuration(320)
        a.setEasingCurve(QEasingCurve.Type.Linear)
        a.setKeyValueAt(0.0, orig)
        a.setKeyValueAt(0.2, orig + QPoint(-9, 0))
        a.setKeyValueAt(0.4, orig + QPoint(9, 0))
        a.setKeyValueAt(0.6, orig + QPoint(-6, 0))
        a.setKeyValueAt(0.8, orig + QPoint(4, 0))
        a.setKeyValueAt(1.0, orig)
        a.start()
        self._shake_anim = a
