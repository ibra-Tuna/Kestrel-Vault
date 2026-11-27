"""Main window: merged title/menu bar, left-rail navigation, animated tabs."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import (QByteArray, QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSize,
                           Qt, QTimer)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..crypto import totp_code
from ..models import Entry
from ..services import Curtain, ToastManager, clipboard, settings, theme
from ..session import VaultSession
from ..storage import VaultFile
from ..theme import is_dark
from .common import (AnimatedStack, IconButton, IconLabel, _TabTransitionOverlay, _snapshot,
                     label)
from .generator import GeneratorTab
from .health import HealthTab
from .settings_tab import SettingsTab
from .title_bar import HTCAPTION, MergedTitleBar
from .unlock import UnlockWidget
from .vault import VaultTab

FIELD_LABELS = {"password": "Password", "username": "Username", "url": "Website", "totp": "One-time code"}
_RAIL_WIDTH = 208       # expanded width of the navigation rail
_RAIL_ANIM_MS = 180     # slide duration for the rail
_WM_NCHITTEST = 0x0084
_EDGE_MARGIN = 6  # px from the window edge that starts a native resize
_EDGE_LEFT = Qt.Edge.LeftEdge.value
_EDGE_RIGHT = Qt.Edge.RightEdge.value
_EDGE_TOP = Qt.Edge.TopEdge.value
_EDGE_BOTTOM = Qt.Edge.BottomEdge.value
_HT_EDGE = {
    _EDGE_LEFT: 10, _EDGE_RIGHT: 11, _EDGE_TOP: 12, _EDGE_BOTTOM: 15,
    _EDGE_LEFT | _EDGE_TOP: 13, _EDGE_RIGHT | _EDGE_TOP: 14,
    _EDGE_LEFT | _EDGE_BOTTOM: 16, _EDGE_RIGHT | _EDGE_BOTTOM: 17,
}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _WinMSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt", wintypes.POINT)]
else:
    _WinMSG = None  # type: ignore[assignment]


def find_icon() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "kestrel_vault" / "app.ico")
    pkg = Path(__file__).resolve().parent.parent
    candidates.append(pkg / "app.ico")
    candidates.append(pkg.parent / "app.ico")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _apply_win_titlebar(window: QMainWindow, dark: bool) -> None:
    """Best-effort Windows 10/11 dark mode for the remaining native frame."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


class _RailHost(QScrollArea):
    """Clips the fixed-width rail while its host width is animated."""

    def __init__(self, rail: QWidget) -> None:
        super().__init__()
        self._rail = rail
        self.setObjectName("navRailHost")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(False)
        self.setContentsMargins(0, 0, 0, 0)
        self.viewport().setContentsMargins(0, 0, 0, 0)
        self.setWidget(rail)
        self.setMinimumWidth(0)
        self.setMaximumWidth(_RAIL_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(_RAIL_WIDTH, super().sizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        height = max(self.viewport().height(), self._rail.sizeHint().height())
        self._rail.setGeometry(0, 0, _RAIL_WIDTH, height)


class MainPage(QWidget):
    PAGES = [("vault", "Vault", "vault"), ("generator", "Generator", "dice"),
             ("health", "Health", "activity"), ("settings", "Settings", "sliders")]

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.rail = QFrame()
        self.rail.setObjectName("navRail")
        self.rail.setFixedWidth(_RAIL_WIDTH)
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(12, 14, 12, 14)
        rl.setSpacing(4)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav: dict[str, IconButton] = {}
        for index, (key, text, icon) in enumerate(self.PAGES, start=1):
            b = IconButton(icon, text, "nav", checkable=True, tooltip=f"{text} (Ctrl+{index})")
            b.setMinimumHeight(40)
            self.group.addButton(b)
            self.nav[key] = b
            rl.addWidget(b)
            b.clicked.connect(lambda _c=False, k=key: self.show_page(k))

        rl.addStretch(1)

        # ---- footer: status read-outs and the lock action, pinned to the bottom
        separator = QFrame()
        separator.setObjectName("railSeparator")
        separator.setFixedHeight(1)
        rl.addWidget(separator)

        footer = QVBoxLayout()
        footer.setContentsMargins(0, 10, 0, 0)
        footer.setSpacing(6)

        # Status text lines up with the nav labels (rail margin + button padding).
        status = QVBoxLayout()
        status.setContentsMargins(14, 0, 14, 0)
        status.setSpacing(4)
        self.rail_clip = label("", "small")
        self.rail_clip.setVisible(False)
        self.rail_lock = label("", "small")
        self.rail_lock.setToolTip("Auto-lock after inactivity (Settings → Security)")
        self.rail_lock.setVisible(False)
        count_row = QHBoxLayout()
        count_row.setContentsMargins(0, 0, 0, 0)
        count_row.setSpacing(8)
        count_row.addWidget(IconLabel("info", 15, "sub"))
        self.rail_count = label("0 entries", "small")
        count_row.addWidget(self.rail_count, 1)
        status.addWidget(self.rail_clip)
        status.addWidget(self.rail_lock)
        status.addLayout(count_row)
        footer.addLayout(status)
        footer.addSpacing(6)

        self.lock_btn = IconButton("lock", "Lock", "nav", tooltip="Lock the vault (Ctrl+L)")
        self.lock_btn.setMinimumHeight(40)
        self.lock_btn.setAccessibleDescription("Locks the vault and returns to the unlock screen")
        self.lock_btn.clicked.connect(lambda: main.lock_vault())
        footer.addWidget(self.lock_btn)

        rl.addLayout(footer)
        self.rail_host = _RailHost(self.rail)
        self._rail_visible = True
        self._rail_anim: QPropertyAnimation | None = None
        root.addWidget(self.rail_host)

        self.stack = AnimatedStack()
        self.vault_tab = VaultTab(main)
        self.generator_tab = GeneratorTab(main)
        self.health_tab = HealthTab(main)
        self.settings_tab = SettingsTab(main)
        self.pages = {"vault": self.vault_tab, "generator": self.generator_tab,
                      "health": self.health_tab, "settings": self.settings_tab}
        for key, _t, _i in self.PAGES:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)
        self.nav["vault"].setChecked(True)

    def current_key(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, v in self.pages.items() if v is w), "vault")

    def show_page(self, key: str) -> None:
        cur_key = self.current_key()
        if key == cur_key:
            self.nav[key].setChecked(True)
            return
        order = [k for k, _t, _i in self.PAGES]
        direction = 1 if order.index(key) >= order.index(cur_key) else -1
        self.nav[key].setChecked(True)
        if key == "health":
            self.health_tab.refresh()
        self.stack.switch_to(self.pages[key], direction=direction)
        self.main.update_actions()

    def set_rail_visible(self, visible: bool, animate: bool = True) -> None:
        """Show/hide the rail with a horizontal slide (instant when reduced motion)."""
        host = self.rail_host
        self._rail_visible = visible
        settings().set("sidebar_visible", visible)
        title_bar = getattr(self.main, "title_bar", None)
        if title_bar is not None:
            title_bar.setRailVisible(visible)

        anim = self._rail_anim
        if anim is not None:
            try:
                anim.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            anim.stop()
            self._rail_anim = None

        target = _RAIL_WIDTH if visible else 0
        if not animate or settings().reduced_motion:
            host.setMaximumWidth(target)
            host.setVisible(visible)
            return

        if visible:
            host.setVisible(True)
        start = host.maximumWidth()   # 0 while collapsed, 208 while expanded
        if start == target:
            host.setVisible(visible)
            return

        self._rail_anim = QPropertyAnimation(host, b"maximumWidth", self)
        self._rail_anim.setDuration(_RAIL_ANIM_MS)
        self._rail_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._rail_anim.setStartValue(start)
        self._rail_anim.setEndValue(target)
        self._rail_anim.finished.connect(lambda: self._on_rail_anim_finished(visible))
        self._rail_anim.start()

    def _on_rail_anim_finished(self, visible: bool) -> None:
        self._rail_anim = None
        self.rail_host.setMaximumWidth(_RAIL_WIDTH if visible else 0)
        self.rail_host.setVisible(visible)

    def toggle_rail(self) -> None:
        self.set_rail_visible(not self._rail_visible)


class MainWindow(QMainWindow):
    def __init__(self, store: VaultFile | None = None) -> None:
        super().__init__()
        self.store = store or VaultFile()
        self.session = VaultSession(self.store)
        self.setWindowTitle("Kestrel Vault")

        # ---- frameless look with Windows' thick frame kept for resize / snap
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if sys.platform == "win32":
            flags |= (Qt.WindowType.WindowMinimizeButtonHint
                      | Qt.WindowType.WindowMaximizeButtonHint
                      | Qt.WindowType.WindowSystemMenuHint)
        self.setWindowFlags(flags)
        self.setObjectName("mainWindow")
        self.setMinimumSize(900, 600)
        self.resize(1160, 740)
        icon_path = find_icon()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.last_activity = time.monotonic()
        self._theme_overlay: _TabTransitionOverlay | None = None

        # ---- central: merged title/menu bar + page stack
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title_bar = MergedTitleBar(self, "Kestrel Vault")
        outer.addWidget(self.title_bar)
        self.menuBar().hide()  # commands live in the merged bar above
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.unlock_page = UnlockWidget(self)
        self.main_page = MainPage(self)
        self.stack.addWidget(self.unlock_page)
        self.stack.addWidget(self.main_page)
        self.vault_tab = self.main_page.vault_tab
        self.curtain = Curtain(self.stack)

        self.toasts = ToastManager(self.stack, settings(), theme())
        self._build_menu()
        self._native_frame_ready = False

        self.session.persistFailed.connect(lambda m: self.toast(m, "danger"))
        self.session.changed.connect(self._update_status)
        self.vault_tab.selectionChanged.connect(self.update_actions)
        clipboard().countdown.connect(self._on_clip_countdown)
        clipboard().cleared.connect(lambda: self.toast("Clipboard cleared ✓", "neutral"))
        theme().changed.connect(self._on_theme_changed)

        self._idle = QTimer(self)
        self._idle.setInterval(1000)
        self._idle.timeout.connect(self._check_idle)
        self._idle.start()
        self._blur = QTimer(self)
        self._blur.setSingleShot(True)
        self._blur.timeout.connect(self._blur_lock)
        self._keyboard_focus = False
        self._event_app = QApplication.instance()
        if self._event_app is not None:
            self._event_app.installEventFilter(self)
            self._event_app.applicationStateChanged.connect(self._on_app_state)
            self._event_app.focusChanged.connect(self._on_focus_changed)
            self._event_app.aboutToQuit.connect(self._detach_event_filter)

        geom = settings().get("window_geometry", "")
        if geom:
            try:
                self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            except Exception:
                pass
        self.vault_tab.restore_layout()
        self.main_page.set_rail_visible(bool(settings().get("sidebar_visible", True)), animate=False)
        theme().apply()
        self._show_unlock(animate=False)

    # ---------------------------------------------------------------- native window behaviour
    def _apply_native_window_bits(self) -> None:
        """Give the frameless window a real thick frame for resize / snap."""
        if sys.platform != "win32" or self._native_frame_ready:
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            WS_THICKFRAME = 0x00040000
            WS_SYSMENU = 0x00080000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_CLIPSIBLINGS = 0x04000000
            WS_CLIPCHILDREN = 0x02000000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            style = user32.GetWindowLongW(hwnd, -16) & 0xffffffff
            style = (style | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX
                     | WS_MAXIMIZEBOX | WS_CLIPSIBLINGS | WS_CLIPCHILDREN)
            user32.SetWindowLongW(hwnd, -16, style)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )

            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
            )
            self._native_frame_ready = True
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        """Let Windows hit-test our custom bar as a real caption."""
        if (sys.platform == "win32" and _WinMSG is not None
                and bytes(eventType) == b"windows_generic_MSG"):
            msg = ctypes.cast(int(message), ctypes.POINTER(_WinMSG)).contents
            if msg.message == _WM_NCHITTEST:
                x = msg.lParam & 0xffff
                y = (msg.lParam >> 16) & 0xffff
                if x >= 0x8000:
                    x -= 0x10000
                if y >= 0x8000:
                    y -= 0x10000
                pos = QPoint(x, y)
                region = self.title_bar.hit_test(pos)
                if region is not None:
                    return True, region
                edges = self._edges_at(pos)
                if edges is not None and edges in _HT_EDGE:
                    return True, _HT_EDGE[edges]
        return super().nativeEvent(eventType, message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_native_window_bits()

    # ---------------------------------------------------------------- theme
    def _on_theme_changed(self) -> None:
        _apply_win_titlebar(self, is_dark(theme().colors["bg"]))
        self.update()

    def apply_theme(self) -> None:
        """Apply a pending theme/accent change with a cross-fade when visible."""
        self._clear_theme_overlay()
        if settings().reduced_motion or not self.isVisible() or self.width() <= 0:
            theme().apply()
            return
        old_pm = _snapshot(self, theme().colors["bg"])
        theme().apply()
        new_pm = _snapshot(self, theme().colors["bg"])
        overlay = _TabTransitionOverlay(self, old_pm, new_pm, direction=0, shift=0)
        overlay.finished.connect(self._on_theme_overlay_finished)
        self._theme_overlay = overlay

    def _clear_theme_overlay(self) -> None:
        overlay = self._theme_overlay
        self._theme_overlay = None
        if overlay is None:
            return
        try:
            overlay.stop_now()
        except RuntimeError:
            pass

    def _on_theme_overlay_finished(self) -> None:
        if self._theme_overlay is self.sender():
            self._theme_overlay = None

    # ---------------------------------------------------------------- status
    def _update_status(self) -> None:
        if self.session.unlocked:
            n = len(self.session.live())
            self.main_page.rail_count.setText(f"{n} {'entry' if n == 1 else 'entries'}")
            self._update_lock_countdown()
        else:
            self.main_page.rail_count.setText("Locked")
            self.main_page.rail_lock.setVisible(False)

    def _update_lock_countdown(self) -> None:
        if not self.session.unlocked:
            self.main_page.rail_lock.setVisible(False)
            return
        minutes = int(settings().get("auto_lock_minutes", 10) or 0)
        if not minutes:
            self.main_page.rail_lock.setVisible(False)
            return
        rem = max(0, int(minutes * 60 - (time.monotonic() - self.last_activity)))
        m, s = divmod(rem, 60)
        self.main_page.rail_lock.setText(f"Auto-lock in {m}:{s:02d}")
        self.main_page.rail_lock.setVisible(True)

    def _on_clip_countdown(self, seconds: int) -> None:
        lbl = self.main_page.rail_clip
        if seconds > 0:
            lbl.setText(f"Clipboard: {seconds}s")
            lbl.setVisible(True)
        else:
            lbl.setVisible(False)

    # ---------------------------------------------------------------- menus
    def _action(self, menu, text: str, slot, shortcut: str | None = None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        self.addAction(a)
        return a

    def _build_menu(self) -> None:
        mb = self.title_bar.menu_bar
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
        self._action(vm, "Toggle &navigation rail", mp.toggle_rail, "Ctrl+B")
        self._action(vm, "&Vault", lambda: self.show_page("vault"), "Ctrl+1")
        self._action(vm, "&Generator", lambda: self.show_page("generator"), "Ctrl+2")
        self._action(vm, "&Health", lambda: self.show_page("health"), "Ctrl+3")
        self._action(vm, "&Settings", lambda: self.show_page("settings"), "Ctrl+4")
        self._action(vm, "&New password", lambda: (self.show_page("generator"), mp.generator_tab.generate()), "Ctrl+G")

        vam = mb.addMenu("&Vault")
        self._action(vam, "&Lock", lambda: self.lock_vault(), "Ctrl+L")
        self._action(vam, "&Change master password…", lambda: mp.settings_tab.change_master())
        hm = mb.addMenu("&Help")
        self._action(hm, "&Keyboard shortcuts", self._show_shortcuts)
        self._action(hm, "&About", self._show_about)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts",
            "Ctrl+N          New entry\nCtrl+F          Search\nCtrl+L          Lock vault\n"
            "Ctrl+B          Toggle navigation rail\nCtrl+1–4        Switch page\nCtrl+G          New generated password\n"
            "Ctrl+C          Copy password (in list)\nCtrl+Shift+C    Copy password\n"
            "Ctrl+Shift+U    Copy username\nCtrl+Shift+T    Copy one-time code\n"
            "Ctrl+E          Edit selected\nDel             Move to Trash\nCtrl+Z          Undo delete\n"
            "Ctrl+Q          Quit")

    def _show_about(self) -> None:
        QMessageBox.about(self, "About Kestrel Vault",
            f"<h3>Kestrel Vault {__version__}</h3><p>Encrypted, local password manager.</p>"
            "<p>AES-256-GCM · Argon2id (256 MiB) · diceware · PySide6 (LGPL)</p>")

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

    # ---------------------------------------------------------------- public API
    def toast(self, message: str, color: str | None = None) -> None:
        self.toasts.show(message, color)

    def show_page(self, key: str) -> None:
        if self.session.unlocked:
            self.main_page.show_page(key)

    def show_entry(self, entry_id: str) -> None:
        self.main_page.show_page("vault")
        self.vault_tab.select_entry(entry_id)

    def copy_text(self, text: str, what: str) -> None:
        secs = int(settings().get("clipboard_clear_seconds", 30)) or None
        clipboard().copy(text, secs)
        if secs:
            self.toast(f"{what} copied — clears in {secs}s")
        else:
            self.toast(f"{what} copied")

    def copy_secret(self, entry: Entry, field: str) -> None:
        if field == "totp":
            value = totp_code(entry.totp)
        else:
            value = getattr(entry, field, "")
        if not value:
            self.toast(f"No {FIELD_LABELS.get(field, field).lower()} to copy", "neutral")
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

    def lock_vault(self, message: str | None = None) -> None:
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
            self.unlock_page.set_error(message)

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
                w.done(0)
            elif w is not None:
                w.close()
            else:
                break

    # ---------------------------------------------------------------- auto-lock & filter
    def _detach_event_filter(self) -> None:
        app = self._event_app
        if app is None:
            return
        self._event_app = None
        try:
            app.removeEventFilter(self)
            app.focusChanged.disconnect(self._on_focus_changed)
        except (RuntimeError, TypeError):
            pass

    def _update_focus_ring(self) -> None:
        """Show the focus ring only when focus came from the keyboard."""
        app = self._event_app or QApplication.instance()
        widget = app.focusWidget() if app is not None else None
        if not isinstance(widget, QWidget):
            return
        value = "true" if self._keyboard_focus else "false"
        if widget.property("keyboardFocus") == value:
            return
        widget.setProperty("keyboardFocus", value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _on_focus_changed(self, _old, _new) -> None:
        self._update_focus_ring()

    def _edges_at(self, global_pos: QPoint) -> int | None:
        """Return the Qt.Edge flags touched by a global position, or None."""
        if self.isMaximized() or self.isFullScreen():
            return None
        local = self.mapFromGlobal(global_pos)
        x, y, m = local.x(), local.y(), _EDGE_MARGIN
        edges = 0
        if x < m:
            edges |= _EDGE_LEFT
        if x >= self.width() - m:
            edges |= _EDGE_RIGHT
        if y < m:
            edges |= _EDGE_TOP
        if y >= self.height() - m:
            edges |= _EDGE_BOTTOM
        return edges or None

    def eventFilter(self, obj, event) -> bool:
        # This filter is installed on QApplication. Do not delegate to
        # super().eventFilter(): during interpreter shutdown that can recurse
        # through destroyed wrappers. The base implementation only returns
        # False anyway.
        try:
            et = event.type()
            if et in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress,
                      QEvent.Type.Wheel, QEvent.Type.MouseMove):
                self.last_activity = time.monotonic()
            if et == QEvent.Type.MouseButtonPress:
                self._keyboard_focus = False
                self._update_focus_ring()
            elif et == QEvent.Type.KeyPress:
                self._keyboard_focus = True
                self._update_focus_ring()

            if (et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
                    and isinstance(obj, QWidget) and obj.window() is self
                    and self.windowHandle() is not None
                    and event.button() == Qt.MouseButton.LeftButton):
                gpos = event.globalPosition().toPoint()
                bar = self.title_bar
                if bar.isVisible() and bar.rect().contains(bar.mapFromGlobal(gpos)):
                    if bar.hit_test(gpos) == HTCAPTION:
                        if et == QEvent.Type.MouseButtonDblClick:
                            bar.max_btn.toggleMaximized()
                        else:
                            self.windowHandle().startSystemMove()
                        return True
                    return False  # menu / buttons handle their own clicks
                edges = self._edges_at(gpos)
                if edges is not None and self.windowHandle().startSystemResize(Qt.Edge(edges)):
                    return True
        except (RuntimeError, AttributeError, TypeError):
            pass
        return False

    def _check_idle(self) -> None:
        if not self.session.unlocked:
            return
        self._update_lock_countdown()
        minutes = int(settings().get("auto_lock_minutes", 10) or 0)
        if minutes and time.monotonic() - self.last_activity > minutes * 60:
            self.lock_vault()

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
            self.lock_vault()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.max_btn._retint()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.curtain.setGeometry(self.stack.rect())
        overlay = self._theme_overlay
        if overlay is not None:
            try:
                overlay.setGeometry(self.rect())
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- shutdown
    def closeEvent(self, event) -> None:
        self._detach_event_filter()
        try:
            self.session.persist_now()
            self.vault_tab.save_layout()
            settings().set("window_geometry", bytes(self.saveGeometry().toHex()).decode())
            settings().flush()
            clipboard().clear_now()
        finally:
            super().closeEvent(event)