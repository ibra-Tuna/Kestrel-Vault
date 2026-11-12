"""Modal dialogs: entry editor, master-password change, password history, confirmations."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
                               QCheckBox, QLineEdit, QLabel)

from ..crypto import is_valid_totp
from ..models import Entry
from ..passwords import build_classes, check_master_password, generate_password
from ..services import settings
from .common import IconButton, PasswordField, StrengthMeter, label


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "OK") -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def labeled(text: str, widget: QWidget) -> QWidget:
    holder = QWidget()
    col = QVBoxLayout(holder)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(5)
    col.addWidget(label(text, "small"))
    col.addWidget(widget)
    return holder


def _fit(dialog: QDialog, width: int, height: int) -> None:
    screen = dialog.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry()
    dialog.resize(min(width, avail.width() - 80), min(height, avail.height() - 100))


class EntryDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, entry: Entry | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(440, 420)
        _fit(self, 540, 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(24, 24, 24, 12)
        form.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        e = entry
        self.name_edit = QLineEdit(e.name if e else "")
        self.name_edit.setPlaceholderText("e.g. GitHub")
        self.username_edit = QLineEdit(e.username if e else "")
        self.url_edit = QLineEdit(e.url if e else "")
        self.url_edit.setPlaceholderText("example.com")
        self.pw_edit = PasswordField()
        self.pw_edit.setText(e.password if e else "")
        self.totp_edit = QLineEdit(e.totp if e else "")
        self.totp_edit.setPlaceholderText("Base32 secret or otpauth:// link (optional)")
        self.tags_edit = QLineEdit(", ".join(e.tags) if e else "")
        self.tags_edit.setPlaceholderText("work, finance")
        self.notes_edit = QPlainTextEdit(e.notes if e else "")
        self.notes_edit.setFixedHeight(96)
        self.fav_check = QCheckBox("Mark as favorite")
        self.fav_check.setChecked(bool(e and e.favorite))

        form.addWidget(labeled("Name", self.name_edit))
        form.addWidget(labeled("Username or email", self.username_edit))
        form.addWidget(labeled("Website", self.url_edit))

        pw_row = QHBoxLayout()
        pw_row.setSpacing(6)
        pw_row.addWidget(self.pw_edit, 1)
        gen = IconButton("refresh", tooltip="Generate a strong password")
        gen.clicked.connect(self._generate)
        pw_row.addWidget(gen)
        pw_holder = QWidget()
        pw_holder.setLayout(pw_row)
        pw_row.setContentsMargins(0, 0, 0, 0)
        form.addWidget(labeled("Password", pw_holder))
        self.meter = StrengthMeter()
        form.addWidget(self.meter)
        self.pw_edit.textChanged.connect(self.meter.set_password)
        self.meter.set_password(self.pw_edit.text())

        form.addWidget(labeled("One-time password (2FA)", self.totp_edit))
        self.totp_hint = label("", "small")
        form.addWidget(self.totp_hint)
        self.totp_edit.textChanged.connect(self._check_totp)
        self._check_totp(self.totp_edit.text())

        form.addWidget(labeled("Tags (comma-separated)", self.tags_edit))
        form.addWidget(labeled("Notes", self.notes_edit))
        form.addWidget(self.fav_check)
        form.addStretch(1)

        footer = QWidget()
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(24, 8, 24, 20)
        fl.setSpacing(10)
        self.error = label("", "error", wrap=True)
        fl.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = IconButton("x", "Cancel")
        cancel.clicked.connect(self.reject)
        save = IconButton("check", "Save", "primary")
        save.setDefault(True)
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        fl.addLayout(buttons)
        outer.addWidget(footer)

        self._initial = self.values()
        self.name_edit.setFocus()

    # ---- behaviour
    def _generate(self) -> None:
        classes = build_classes(True, True, True, True, bool(settings().get("gen_ambiguous")))
        length = max(16, int(settings().get("gen_length", 20)))
        self.pw_edit.setText(generate_password(length, classes))
        self.pw_edit.set_revealed(True)

    def _check_totp(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.totp_hint.setText("")
            return
        ok = is_valid_totp(text)
        self.totp_hint.setProperty("role", "ok" if ok else "error")
        self.totp_hint.setText("Valid secret" if ok else "Not a valid Base32 secret or otpauth:// link")
        self.totp_hint.style().unpolish(self.totp_hint)
        self.totp_hint.style().polish(self.totp_hint)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            self.error.setText("Give this entry a name.")
            self.name_edit.setFocus()
            return
        if not self.pw_edit.text():
            self.error.setText("Enter or generate a password.")
            self.pw_edit.setFocus()
            return
        totp = self.totp_edit.text().strip()
        if totp and not is_valid_totp(totp):
            self.error.setText("The one-time password secret is not valid.")
            self.totp_edit.setFocus()
            return
        self.accept()

    def reject(self) -> None:
        if self.values() != self._initial:
            if not confirm(self, "Discard changes?", "You have unsaved changes. Discard them?", "Discard"):
                return
        super().reject()

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "username": self.username_edit.text().strip(),
            "url": self.url_edit.text().strip(),
            "password": self.pw_edit.text(),
            "totp": self.totp_edit.text().strip(),
            "tags": [t.strip() for t in self.tags_edit.text().split(",") if t.strip()],
            "notes": self.notes_edit.toPlainText(),
            "favorite": self.fav_check.isChecked(),
        }


class ChangeMasterDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change master password")
        self.setModal(True)
        self.setMinimumWidth(440)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 24, 24, 20)
        col.setSpacing(14)
        self.current = PasswordField()
        self.new = PasswordField()
        self.confirm = PasswordField()
        col.addWidget(labeled("Current master password", self.current))
        col.addWidget(labeled("New master password", self.new))
        self.meter = StrengthMeter()
        col.addWidget(self.meter)
        col.addWidget(labeled("Confirm new password", self.confirm))
        self.msg = label("", "muted", wrap=True)
        col.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = IconButton("x", "Cancel")
        cancel.clicked.connect(self.reject)
        self.save = IconButton("check", "Change password", "primary")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.save)
        col.addLayout(row)
        for f in (self.current, self.new, self.confirm):
            f.textChanged.connect(self._validate)
        self.new.textChanged.connect(self.meter.set_password)

    def _validate(self) -> None:
        new = self.new.text()
        problem = check_master_password(new) if new else "Choose a new password."
        if not problem and new != self.confirm.text():
            problem = "The new passwords don't match."
        if not problem and not self.current.text():
            problem = "Enter your current password."
        self.msg.setText(problem or "")
        self.save.setEnabled(problem is None)

    def values(self) -> tuple[str, str]:
        return self.current.text(), self.new.text()

    def done(self, result: int) -> None:
        for f in (self.current, self.new, self.confirm):
            f.reset()
        super().done(result)


class HistoryDialog(QDialog):
    def __init__(self, parent: QWidget, entry: Entry, copy_cb: Callable[[str], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{entry.name} — password history")
        self.setModal(True)
        self.setMinimumSize(420, 340)
        self._history = list(reversed(entry.history))
        self._copy_cb = copy_cb
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 24, 24, 20)
        col.setSpacing(12)
        col.addWidget(label(f"{len(self._history)} previous password(s)", "muted"))
        self.list = QListWidget()
        self.list.setObjectName("plainList")
        for h in self._history:
            self.list.addItem(QListWidgetItem(h.get("modified", "")[:19].replace("T", "  ") or "Unknown date"))
        col.addWidget(self.list, 1)
        self.field = PasswordField(read_only=True)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.field, 1)
        self.copy_btn = IconButton("copy", tooltip="Copy this password")
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        col.addLayout(row)
        close = IconButton("x", "Close")
        close.clicked.connect(self.accept)
        brow = QHBoxLayout()
        brow.addStretch(1)
        brow.addWidget(close)
        col.addLayout(brow)
        self.list.currentRowChanged.connect(self._on_row)
        if self._history:
            self.list.setCurrentRow(0)
        else:
            self.copy_btn.setEnabled(False)

    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self._history):
            self.field.setText(self._history[row].get("password", ""))
            self.field.set_revealed(False)

    def _copy(self) -> None:
        if self.field.text():
            self._copy_cb(self.field.text())

    def done(self, result: int) -> None:
        self.field.reset()
        super().done(result)
