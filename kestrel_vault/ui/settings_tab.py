"""Settings tab: appearance, security, data."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from ..crypto import CorruptVaultError, VaultError, WrongPasswordError, derive_key
from ..importers import parse_csv
from ..services import run_task, settings, theme
from ..storage import validate_backup
from ..theme import ACCENTS, PAD, THEMES
from .common import IconButton, PasswordField, card, label, section_label
from .dialogs import ChangeMasterDialog, confirm

if TYPE_CHECKING:
    from .main_window import MainWindow

log = logging.getLogger(__name__)

LOCK_CHOICES = [("Never", 0), ("1 minute", 1), ("5 minutes", 5), ("10 minutes", 10), ("30 minutes", 30), ("1 hour", 60)]
CLIP_CHOICES = [("Never", 0), ("10 seconds", 10), ("20 seconds", 20), ("30 seconds", 30), ("1 minute", 60), ("2 minutes", 120)]
BLUR_CHOICES = [("Never", 0), ("30 seconds", 30), ("1 minute", 60), ("5 minutes", 300)]


class ThemeDelegate(QStyledItemDelegate):
    """Paints each theme as a swatch card instead of embedding widgets in the list."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(150, 78)

    def paint(self, painter: QPainter, option, index) -> None:
        name = index.data(Qt.ItemDataRole.DisplayRole)
        t = THEMES[name]
        accent = QColor(theme().colors["accent"])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setBrush(QColor(t["bg"]))
        pen = QColor(theme().colors["accent"]) if selected else QColor(t["border"] if not hovered else theme().colors["border_hover"])
        painter.setPen(pen)
        if selected:
            p = painter.pen()
            p.setWidth(2)
            painter.setPen(p)
        painter.drawRoundedRect(r, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["panel"]))
        painter.drawRoundedRect(QRect(r.left() + 10, r.top() + 10, r.width() - 20, 22), 6, 6)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRect(r.left() + 16, r.top() + 17, 26, 8), 4, 4)
        painter.setPen(QColor(t["fg"]))
        f = painter.font()
        f.setPixelSize(12)
        f.setBold(selected)
        painter.setFont(f)
        painter.drawText(QRect(r.left() + 12, r.bottom() - 30, r.width() - 24, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        painter.restore()


class SwatchButton(QPushButton):
    def __init__(self, name: str, color: str) -> None:
        super().__init__()
        self.hex = color
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Accent colour {name}")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.hex)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRect(5, 5, 24, 24))
        if self.isChecked():
            pen = QColor(theme().colors["fg"])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(pen)
            p.drawEllipse(QRect(2, 2, 30, 30))
        elif self.underMouse():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(theme().colors["border_hover"]))
            p.drawEllipse(QRect(2, 2, 30, 30))


class SettingsTab(QWidget):
    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        s = settings()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        page = QHBoxLayout(host)
        page.setContentsMargins(PAD, 8, PAD, PAD)
        page.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(18)
        holder = QWidget()
        holder.setMaximumWidth(720)
        holder.setMinimumWidth(320)
        holder.setLayout(col)
        page.addWidget(holder, 100)
        page.addStretch(1)
        col.addWidget(label("Settings", "h1"))

        # ---- appearance
        ap, apl = self._section("Appearance")
        apl.addWidget(label("Theme", "small"))
        self.theme_list = QListWidget()
        self.theme_list.setObjectName("plainList")
        self.theme_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.theme_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.theme_list.setMovement(QListWidget.Movement.Static)
        self.theme_list.setWrapping(True)
        self.theme_list.setMouseTracking(True)
        self.theme_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.theme_list.setItemDelegate(ThemeDelegate(self.theme_list))
        self.theme_list.setFrameShape(QFrame.Shape.NoFrame)
        self.theme_list.setStyleSheet("background: transparent;")
        self.theme_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.theme_list.setSpacing(0)
        for name in THEMES:
            it = QListWidgetItem(name)
            it.setSizeHint(QSize(150, 78))
            self.theme_list.addItem(it)
        rows = (len(THEMES) + 3) // 4
        self.theme_list.setMinimumHeight(rows * 80)
        self.theme_list.setCurrentRow(list(THEMES).index(s["theme_name"]))
        self.theme_list.currentRowChanged.connect(self._on_theme)
        apl.addWidget(self.theme_list)
        apl.addWidget(label("Accent colour", "small"))
        sw = QHBoxLayout()
        sw.setSpacing(4)
        self.swatches: list[SwatchButton] = []
        for name, hexv in ACCENTS:
            b = SwatchButton(name, hexv)
            b.setChecked(hexv.lower() == s["accent"].lower())
            b.clicked.connect(lambda _c=False, h=hexv: self._on_accent(h))
            self.swatches.append(b)
            sw.addWidget(b)
        sw.addStretch(1)
        apl.addLayout(sw)
        self.reduced = QCheckBox("Reduce motion (no animations)")
        self.reduced.setChecked(bool(s.get("reduced_motion")))
        self.reduced.toggled.connect(lambda v: settings().set("reduced_motion", v))
        apl.addWidget(self.reduced)
        col.addWidget(ap)

        # ---- security
        sec, sl = self._section("Security")
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(1, 1)
        self.auto_lock = self._combo(LOCK_CHOICES, s["auto_lock_minutes"], "auto_lock_minutes")
        self.clip = self._combo(CLIP_CHOICES, s["clipboard_clear_seconds"], "clipboard_clear_seconds")
        self.blur = self._combo(BLUR_CHOICES, s["lock_on_blur_seconds"], "lock_on_blur_seconds")
        for i, (text, hint, w) in enumerate((
            ("Auto-lock", "Lock after this much inactivity.", self.auto_lock),
            ("Clear clipboard", "Remove copied secrets after a delay.", self.clip),
            ("Lock when in background", "Lock if the window loses focus for this long.", self.blur),
        )):
            lab = QVBoxLayout()
            lab.setSpacing(1)
            lab.addWidget(label(text, "h2"))
            lab.addWidget(label(hint, "small", wrap=True))
            grid.addLayout(lab, i, 0)
            grid.addWidget(w, i, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sl.addLayout(grid)
        self.on_min = QCheckBox("Lock when the window is minimized")
        self.on_min.setChecked(bool(s.get("lock_on_minimize")))
        self.on_min.toggled.connect(lambda v: settings().set("lock_on_minimize", v))
        sl.addWidget(self.on_min)
        self.change_btn = IconButton("key", "Change master password")
        self.change_btn.clicked.connect(self.change_master)
        sl.addWidget(self.change_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(sec)

        # ---- data
        data, dl = self._section("Data")
        dl.addWidget(label("Backups are encrypted copies of your vault. CSV import reads Bitwarden, "
                           "1Password and browser exports; delete those files afterwards, they are not encrypted.",
                           "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.export_btn = IconButton("download", "Export backup")
        self.import_btn = IconButton("upload", "Restore backup")
        self.csv_btn = IconButton("file", "Import CSV")
        for b in (self.export_btn, self.import_btn, self.csv_btn):
            row.addWidget(b)
        row.addStretch(1)
        dl.addLayout(row)
        self.export_btn.clicked.connect(self.export_backup)
        self.import_btn.clicked.connect(self.import_backup)
        self.csv_btn.clicked.connect(self.import_csv)
        col.addWidget(data)

        danger, ddl = self._section("Danger zone")
        self.wipe_btn = IconButton("trash", "Delete vault and all backups", "danger")
        self.wipe_btn.clicked.connect(self.wipe_vault)
        ddl.addWidget(self.wipe_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(danger)
        col.addStretch(1)
        self._import_worker = None

    # ---- builders
    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = card()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(12)
        lay.addWidget(label(title, "h2"))
        return frame, lay

    @staticmethod
    def _combo(choices, current, key) -> QComboBox:
        cb = QComboBox()
        cb.setMinimumWidth(150)
        for text, value in choices:
            cb.addItem(text, value)
        idx = max(0, cb.findData(current))
        cb.setCurrentIndex(idx)
        cb.currentIndexChanged.connect(lambda _i, c=cb, k=key: settings().set(k, c.currentData()))
        return cb

    # ---- appearance
    def _on_theme(self, row: int) -> None:
        if row < 0:
            return
        settings().set("theme_name", list(THEMES)[row])
        theme().apply()

    def _on_accent(self, hexv: str) -> None:
        for b in self.swatches:
            b.setChecked(b.hex == hexv)
        settings().set("accent", hexv)
        theme().apply()

    # ---- master password
    def change_master(self) -> None:
        sess = self.main.session
        if not sess.unlocked:
            return
        dlg = ChangeMasterDialog(self.main)
        if not dlg.exec():
            return
        current, new = dlg.values()
        header = sess.header
        self.change_btn.setEnabled(False)

        def work() -> tuple[bool, bytes | None, str]:
            import base64, os
            key = derive_key(current, header.salt, header.kdf)
            if key != sess.key:
                return False, None, ""
            salt = os.urandom(16)
            from ..crypto import KDF_PARAMS
            return True, derive_key(new, salt, KDF_PARAMS), base64.b64encode(salt).decode()

        def done(res) -> None:
            self.change_btn.setEnabled(True)
            ok, key, salt_b64 = res
            if not ok:
                QMessageBox.warning(self.main, "Change master password", "The current password is incorrect.")
                return
            try:
                sess.change_master(key, salt_b64)
            except OSError:
                QMessageBox.critical(self.main, "Change master password",
                                     "The vault could not be saved. Your old password still works.")
                return
            self.main.toast("Master password changed", "#22c55e")

        def failed(exc: Exception) -> None:
            self.change_btn.setEnabled(True)
            log.error("Master change failed: %s", type(exc).__name__)
            QMessageBox.critical(self.main, "Change master password", "Something went wrong. Nothing was changed.")

        run_task(work, on_done=done, on_fail=failed)

    # ---- data
    def export_backup(self) -> None:
        sess = self.main.session
        if not sess.unlocked:
            return
        sess.persist_now()
        path, _ = QFileDialog.getSaveFileName(self.main, "Export encrypted backup", "kestrel-vault-backup.json",
                                              "Vault backup (*.json)")
        if not path:
            return
        try:
            self.main.store.export_to(path)
        except OSError as exc:
            QMessageBox.critical(self.main, "Export failed", f"Could not write the file: {exc.strerror or 'error'}")
            return
        self.main.toast("Backup exported", "#22c55e")

    def import_backup(self) -> None:
        sess = self.main.session
        if not sess.unlocked:
            return
        path, _ = QFileDialog.getOpenFileName(self.main, "Restore backup", "", "Vault backup (*.json)")
        if not path:
            return
        import json
        try:
            with open(path, encoding="utf-8") as fh:
                data = validate_backup(json.load(fh))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self.main, "Restore failed", f"This file is not a valid vault backup.\n\n{exc}")
            return
        if not confirm(self.main, "Restore backup",
                       "This replaces your current vault with the backup.\n"
                       "A copy of the current vault is saved first.\n\nContinue?", "Restore"):
            return
        sess.persist_now()
        try:
            self.main.store.snapshot_before_import()
            from ..storage import atomic_write
            atomic_write(self.main.store.path, json.dumps(data, indent=1))
        except OSError as exc:
            QMessageBox.critical(self.main, "Restore failed", f"Could not write the vault: {exc.strerror or 'error'}")
            return
        sess.lock()
        self.main.lock_vault(message="Backup restored. Unlock with the backup's master password.")

    def import_csv(self) -> None:
        sess = self.main.session
        if not sess.unlocked:
            return
        path, _ = QFileDialog.getOpenFileName(self.main, "Import CSV", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                result = parse_csv(fh.read(), list(sess.entries.values()))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            QMessageBox.critical(self.main, "Import failed", str(exc))
            return
        if not result.entries:
            QMessageBox.information(self.main, "Import CSV",
                                    f"Nothing new to import ({result.duplicates} duplicates, {result.skipped} skipped).")
            return
        sess.add_many(result.entries)
        self.main.toast(f"Imported {len(result.entries)} entries", "#22c55e")

    def wipe_vault(self) -> None:
        if not confirm(self.main, "Delete everything",
                       "This permanently deletes your vault and all automatic backups from this device.\n"
                       "Exports you saved elsewhere are not touched.", "Continue"):
            return
        box = QMessageBox(self.main)
        box.setWindowTitle("Confirm with your password")
        box.setText("Type your master password to confirm.")
        field = PasswordField("Master password")
        box.layout().addWidget(field, 1, 1)
        go = box.addButton("Delete everything", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            field.reset()
            return
        pw = field.text()
        field.reset()
        sess, header = self.main.session, self.main.session.header
        self.wipe_btn.setEnabled(False)

        def done(key) -> None:
            self.wipe_btn.setEnabled(True)
            if key != sess.key:
                QMessageBox.warning(self.main, "Delete everything", "Incorrect master password.")
                return
            sess.lock()
            self.main.store.wipe()
            self.main.lock_vault(message="Vault deleted. You can create a new one.")

        run_task(derive_key, pw, header.salt, header.kdf, on_done=done,
                 on_fail=lambda e: (self.wipe_btn.setEnabled(True),
                                    QMessageBox.critical(self.main, "Delete everything", "Could not verify the password.")))
