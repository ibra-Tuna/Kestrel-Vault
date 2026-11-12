"""Main window: navigation, menus, auto-lock and the glue between screens."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QStackedWidget, QStatusBar, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..crypto import totp_code
from ..models import Entry
from ..services import Curtain, ToastManager, clipboard, settings, theme
from ..session import VaultSession
from ..storage import VaultFile
from .common import IconButton, IconLabel, label
from .generator import GeneratorTab
from .health import HealthTab
from .settings_tab import SettingsTab
from .unlock import UnlockWidget
from .vault import VaultTab

FIELD_LABELS = {"password": "Password", "username": "Username", "url": "Website", "totp": "One-time code"}


def find_icon() -> Path | None:
    """Locate app.ico in dev mode (repo root) and in frozen mode (_MEIPASS)."""
    candidates: list[Path] = []

    # Frozen one-file / one-dir: PyInstaller sets sys._MEIPASS.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "kestrel_vault" / "app.ico")

    # Dev mode: <package_root>/app.ico, then <repo>/app.ico.
    pkg = Path(__file__).resolve().parent.parent
    candidates.append(pkg / "app.ico")
    candidates.append(pkg.parent / "app.ico")

    for c in candidates:
        if c.is_file():
            return c
    return None


class MainPage(QWidget):
    PAGES = [("vault", "Vault", "vault"), ("generator", "Generator", "dice"),
             ("health", "Health", "activity"), ("settings", "Settings", "sliders")]

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(20, 14, 20, 6)
        bar.setSpacing(6)
        bar.addWidget(IconLabel("shield", 22, "accent"))
        brand = label("Kestrel Vault", "brand")
        bar.addWidget(brand)
        bar.addSpacing(18)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav: dict[str, IconButton] = {}
        for key, text, icon in self.PAGES:
            b = IconButton(icon, text, "nav", checkable=True)
            self.group.addButton(b)
            self.nav[key] = b
            bar.addWidget(b)
            b.clicked.connect(lambda _c=False, k=key: self.show_page(k))
        bar.addStretch(1)
        self.lock_btn = IconButton("lock", "Lock", tooltip="Lock the vault (Ctrl+L)")
        self.lock_btn.clicked.connect(lambda: main.lock_vault(toast=True))
        bar.addWidget(self.lock_btn)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        self.vault_tab = VaultTab(main)
        self.generator_tab = GeneratorTab(main)
        self.health_tab = HealthTab(main)
        self.settings_tab = SettingsTab(main)
        self.pages = {"vault": self.vault_tab, "generator": self.generator_tab,
                      "health": self.health_tab, "settings": self.settings_tab}
        for key, _t, _i in self.PAGES:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)
        self.curtain = Curtain(self.stack)
        self.nav["vault"].setChecked(True)

    def current_key(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, v in self.pages.items() if v is w), "vault")

    def show_page(self, key: str) -> None:
        if key == self.current_key():
            self.nav[key].setChecked(True)
            return
        self.nav[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        if key == "health":
            self.health_tab.refresh()
        self.curtain.play(theme().colors["bg"], settings().reduced_motion)
        self.main.update_actions()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.curtain.setGeometry(self.stack.rect())


class MainWindow(QMainWindow):
    def __init__(self, store: VaultFile | None = None) -> None:  # noqa: F821
        super().__init__()
        self.store = store or VaultFile()
        self.session = VaultSession(self.store)
        self.setWindowTitle("Kestrel Vault")
        self.setMinimumSize(900, 600)
        self.resize(1180, 760)
        icon_path = find_icon()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.last_activity = time.monotonic()

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.unlock_page = UnlockWidget(self)
        self.main_page = MainPage(self)
        self.stack.addWidget(self.unlock_page)
        self.stack.addWidget(self.main_page)
        self.vault_tab = self.main_page.vault_tab
        self.curtain = Curtain(self.stack)

        self.toasts = ToastManager(self.stack, settings(), theme())
        self._build_status_bar()
        self._build_menu()

        self.session.persistFailed.connect(lambda m: self.toast(m, "#ef4444"))
        self.session.changed.connect(self._update_status)
        self.vault_tab.selectionChanged.connect(self.update_actions)
        clipboard().countdown.connect(self._on_clip_countdown)

        self._idle = QTimer(self)
        self._idle.setInterval(5000)
        self._idle.timeout.connect(self._check_idle)
        self._idle.start()
        self._blur = QTimer(self)
        self._blur.setSingleShot(True)
        self._blur.timeout.connect(self._blur_lock)
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)

        geom = settings().get("window_geometry", "")
        if geom:
            try:
                self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            except Exception:
                pass
        self.vault_tab.restore_layout()
        theme().apply()
        self._show_unlock(animate=False)

    # ---------------------------------------------------------------- chrome
    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        self.status_left = QLabel()
        self.status_clip = QLabel()
        sb.addWidget(self.status_left, 1)
        sb.addPermanentWidget(self.status_clip)
        self._update_status()

    def _update_status(self) -> None:
        if self.session.unlocked:
            n = len(self.session.live())
            self.status_left.setText(f"{n} {'entry' if n == 1 else 'entries'}  ·  Unlocked")
        else:
            self.status_left.setText("Locked")

    def _on_clip_countdown(self, seconds: int) -> None:
        self.status_clip.setText(f"Clipboard clears in {seconds}s" if seconds > 0 else "")

    def _action(self, menu, text: str, slot, shortcut: str | None = None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_menu(self) -> None:
        mb = self.menuBar()
        vt, mp = self.vault_tab, self.main_page
        self._vault_actions: list[QAction] = []
        self._entry_actions: list[QAction] = []
        fm = mb.addMenu("&File")
        self._vault_actions.append(self._action(fm, "&New entry", vt.add_entry, "Ctrl+N"))
        self._vault_actions.append(self._action(fm, "&Export backup…", lambda: mp.settings_tab.export_backup()))
        self._vault_actions.append(self._action(fm, "&Restore backup…", lambda: mp.settings_tab.import_backup()))
        self._vault_actions.append(self._action(fm, "&Import CSV…", lambda: mp.settings_tab.import_csv()))
        fm.addSeparator()
        self._action(fm, "&Quit", self.close, "Ctrl+Q")

        em = mb.addMenu("&Edit")
        self.undo_action = self._action(em, "&Undo delete", vt.undo_delete, "Ctrl+Z")
        em.addSeparator()
        self._entry_actions += [
            self._action(em, "Copy &password", lambda: vt.copy_current("password"), "Ctrl+Shift+C"),
            self._action(em, "Copy &username", lambda: vt.copy_current("username"), "Ctrl+Shift+U"),
            self._action(em, "Copy one-time &code", lambda: vt.copy_current("totp"), "Ctrl+Shift+T"),
            self._action(em, "&Edit selected", vt.edit_current, "Ctrl+E"),
            self._action(em, "&Delete selected", vt.delete_current, "Del"),
        ]
        em.addSeparator()
        self._vault_actions.append(self._action(em, "&Search", lambda: (self.show_page("vault"), vt.focus_search()), "Ctrl+F"))

        vm = mb.addMenu("&View")
        self._action(vm, "Toggle &sidebar", lambda: (self.show_page("vault"), vt.toggle_sidebar()), "Ctrl+B")
        self._action(vm, "&Vault", lambda: self.show_page("vault"), "Ctrl+1")
        self._action(vm, "&Generator", lambda: self.show_page("generator"), "Ctrl+2")
        self._action(vm, "&Health", lambda: self.show_page("health"), "Ctrl+3")
        self._action(vm, "&Settings", lambda: self.show_page("settings"), "Ctrl+4")
        self._action(vm, "&New password", lambda: (self.show_page("generator"), mp.generator_tab.generate()), "Ctrl+G")

        vam = mb.addMenu("&Vault")
        self._action(vam, "&Lock", lambda: self.lock_vault(toast=True), "Ctrl+L")
        self._action(vam, "&Change master password…", lambda: mp.settings_tab.change_master())
        hm = mb.addMenu("&Help")
        self._action(hm, "&Keyboard shortcuts", self._show_shortcuts)
        self._action(hm, "&About", self._show_about)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts",
            "Ctrl+N          New entry\nCtrl+F          Search\nCtrl+L          Lock vault\n"
            "Ctrl+B          Toggle sidebar\nCtrl+1–4        Switch page\nCtrl+G          New generated password\n"
            "Ctrl+Shift+C    Copy password\nCtrl+Shift+U    Copy username\nCtrl+Shift+T    Copy one-time code\n"
            "Ctrl+E          Edit selected\nDel             Move to Trash\nCtrl+Z          Undo delete\n"
            "Ctrl+Q          Quit")

    def _show_about(self) -> None:
        QMessageBox.about(self, "About Kestrel Vault",
            f"<h3>Kestrel Vault {__version__}</h3><p>Encrypted, local password manager.</p>"
            "<p>Fernet · Argon2id · diceware · PySide6 (LGPL)</p>")

    def update_actions(self) -> None:
        unlocked = self.session.unlocked
        on_vault = unlocked and self.main_page.current_key() == "vault"
        entry = self.vault_tab.current_entry() if on_vault else None
        for a in self._vault_actions:
            a.setEnabled(unlocked)
        for a in self._entry_actions:
            a.setEnabled(entry is not None)
        if entry is not None and entry.is_deleted:
            for a in self._entry_actions:
                a.setEnabled(False)
        self.undo_action.setEnabled(on_vault and self.session.can_undo())

    # ---------------------------------------------------------------- public API used by screens
    def toast(self, message: str, color: str | None = None) -> None:
        self.toasts.show(message, color)

    def show_page(self, key: str) -> None:
        if self.session.unlocked:
            self.main_page.show_page(key)

    def show_entry(self, entry_id: str) -> None:
        self.main_page.show_page("vault")
        self.vault_tab.select_entry(entry_id)

    def copy_text(self, text: str, what: str) -> None:
        clipboard().copy(text, int(settings().get("clipboard_clear_seconds", 30)) or None)
        self.toast(f"{what} copied")

    def copy_secret(self, entry: Entry, field: str) -> None:
        if field == "totp":
            value = totp_code(entry.totp)
        else:
            value = getattr(entry, field, "")
        if not value:
            self.toast(f"No {FIELD_LABELS.get(field, field).lower()} to copy", "#64748b")
            return
        self.copy_text(value, FIELD_LABELS.get(field, field))
        self.session.mark_used(entry.id)

    # ---------------------------------------------------------------- lock / unlock
    def on_unlocked(self) -> None:
        self.last_activity = time.monotonic()
        self.vault_tab.reset()
        self.vault_tab.refresh()
        self.vault_tab.restore_layout()
        self.main_page.show_page("vault")
        self.stack.setCurrentWidget(self.main_page)
        self.curtain.play(theme().colors["bg"], settings().reduced_motion)
        self._update_status()
        self.update_actions()
        self.toast("Vault unlocked")

    def lock_vault(self, toast: bool = False, message: str | None = None) -> None:
        if not self.session.unlocked and self.stack.currentWidget() is self.unlock_page and not message:
            return
        if self.session.unlocked:
            self.vault_tab.save_layout()
        self.session.lock()
        self._close_modals()
        clipboard().clear_now()
        self.vault_tab.reset()
        self.main_page.generator_tab.reset()
        self.main_page.health_tab.reset()
        self._show_unlock(animate=True)
        if message:
            self.unlock_page.error.setText(message)
        if toast:
            self.toast("Vault locked", "#64748b")

    def _show_unlock(self, animate: bool) -> None:
        self.unlock_page.clear()
        self.stack.setCurrentWidget(self.unlock_page)
        if animate:
            self.curtain.play(theme().colors["bg"], settings().reduced_motion)
        self.unlock_page.focus_input()
        self._update_status()
        self.update_actions()

    def _close_modals(self) -> None:
        for _ in range(5):
            w = QApplication.activeModalWidget()
            if isinstance(w, QDialog):
                w.done(0)  # skips the "discard changes?" prompt on purpose
            elif w is not None:
                w.close()
            else:
                break

    # ---------------------------------------------------------------- auto-lock
    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                            QEvent.Type.MouseMove):
            self.last_activity = time.monotonic()
        return super().eventFilter(obj, event)

    def _check_idle(self) -> None:
        minutes = int(settings().get("auto_lock_minutes", 10) or 0)
        if self.session.unlocked and minutes and time.monotonic() - self.last_activity > minutes * 60:
            self.lock_vault(toast=True)

    def _on_app_state(self, state) -> None:
        secs = int(settings().get("lock_on_blur_seconds", 0) or 0)
        if not self.session.unlocked or not secs:
            return
        if state == Qt.ApplicationState.ApplicationActive:
            self._blur.stop()
        else:
            self._blur.start(secs * 1000)

    def _blur_lock(self) -> None:
        if self.session.unlocked and not self.isActiveWindow():
            self.lock_vault(toast=False)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and settings().get("lock_on_minimize", True) and self.session.unlocked):
            QTimer.singleShot(0, lambda: self.lock_vault(toast=False))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.curtain.setGeometry(self.stack.rect())

    # ---------------------------------------------------------------- shutdown
    def closeEvent(self, event) -> None:
        try:
            self.session.persist_now()
            self.vault_tab.save_layout()
            settings().set("window_geometry", bytes(self.saveGeometry().toHex()).decode())
            settings().flush()
            clipboard().clear_now()
        finally:
            super().closeEvent(event)