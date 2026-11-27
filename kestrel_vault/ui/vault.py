"""The vault page: two-pane entry list, folder filter, detail panel and slide-in editor."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import (QAbstractAnimation, QAbstractListModel, QByteArray, QEasingCurve,
                            QModelIndex, QPoint, QPropertyAnimation, QRect, QSize, Qt, QUrl, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QFontMetrics,
                           QKeySequence, QPainter, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QSlider,
                               QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from .. import icons
from ..crypto import generate_totp, is_valid_totp, parse_totp, totp_remaining
from ..models import TRASH_DAYS, Entry
from ..passwords import build_classes, generate_password
from ..services import Debouncer, settings, theme
from ..theme import GAP, PAD
from .common import (AnimatedMenu, Avatar, EmptyState, FieldRow, IconButton, IconLabel,
                     PasswordField, SmoothListView, SmoothScrollArea, StrengthMeter, card, label,
                     repolish, section_label)
from .dialogs import HistoryDialog, confirm, labeled

if TYPE_CHECKING:
    from .main_window import MainWindow

SORT_MODES = ["Name A→Z", "Name Z→A", "Recently modified", "Recently created", "Favorites first"]


def safe_url(raw: str) -> QUrl | None:
    """Only ever open http(s) links; never file:, javascript: or custom schemes."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    url = QUrl(raw)
    return url if url.isValid() and url.scheme().lower() in ("http", "https") and url.host() else None


def _draw_highlighted_text(painter: QPainter, rect: QRect, text: str, query: str,
                           fg: QColor, highlight_bg: QColor) -> None:
    fm = QFontMetrics(painter.font())
    elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    if query:
        idx = elided.lower().find(query)
        if idx >= 0:
            before = elided[:idx]
            match = elided[idx:idx + len(query)]
            x_off = fm.horizontalAdvance(before)
            m_w = max(4, fm.horizontalAdvance(match))
            hi_rect = QRect(rect.left() + x_off - 1, rect.top() + 2,
                            min(m_w + 2, rect.width() - x_off + 1), rect.height() - 4)
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight_bg)
            painter.drawRoundedRect(hi_rect, 3, 3)
            painter.restore()
    painter.setPen(fg)
    painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, elided)


# ---------------------------------------------------------------- list model & delegate
class EntryListModel(QAbstractListModel):
    """Holds only entry ids; the Entry objects stay in the session, so locking the
    vault drops every reference instead of leaving copies inside Qt item data."""

    def __init__(self, lookup) -> None:
        super().__init__()
        self._ids: list[str] = []
        self._lookup = lookup
        self._query: str = ""

    @property
    def query(self) -> str:
        return self._query

    def set_query(self, query: str) -> None:
        self._query = query.strip().lower()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        if role == Qt.ItemDataRole.UserRole:
            return self._ids[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            e = self._lookup(self._ids[index.row()])
            return e.name if e else ""
        return None

    def set_ids(self, ids: list[str]) -> None:
        self.beginResetModel()
        self._ids = list(ids)
        self.endResetModel()

    def ids(self) -> list[str]:
        return list(self._ids)

    def id_at(self, row: int) -> str | None:
        return self._ids[row] if 0 <= row < len(self._ids) else None

    def row_of(self, entry_id: str | None) -> int:
        try:
            return self._ids.index(entry_id)  # type: ignore[arg-type]
        except ValueError:
            return -1

    def entry(self, index: QModelIndex) -> Entry | None:
        eid = self.id_at(index.row())
        return self._lookup(eid) if eid else None


class EntryDelegate(QStyledItemDelegate):
    ROW_H = 54

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        model = index.model()
        entry = model.entry(index)
        if entry is None:
            return
        query = getattr(model, "query", "")
        c = theme().colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            bg = QColor(c["row_selected_bg"])
            fg = QColor(c["row_selected_fg"])
            sub = QColor(c["sub"])
            hi = QColor(c["accent"])
            hi.setAlphaF(0.32)
        else:
            bg = QColor(c["panel_hover"]) if hovered else QColor(0, 0, 0, 0)
            fg, sub = QColor(c["fg"]), QColor(c["sub"])
            hi = QColor(c["accent"])
            hi.setAlphaF(0.28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 8, 8)

        # avatar
        from .common import name_color
        cy = rect.center().y()
        ax = rect.left() + 12
        painter.setBrush(name_color(entry.name))
        painter.drawEllipse(QRect(ax, cy - 16, 32, 32))
        painter.setPen(QColor("white"))
        f = painter.font()
        f.setBold(True)
        f.setPixelSize(13)
        painter.setFont(f)
        painter.drawText(QRect(ax, cy - 16, 32, 32), Qt.AlignmentFlag.AlignCenter, (entry.name[:1] or "?").upper())

        # right-hand status dot & favorite star (never show passwords in the list)
        right = rect.right() - 14
        if not entry.is_deleted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(entry.strength.color))
            painter.drawEllipse(QPoint(right - 4, cy), 4, 4)
            right -= 18
        if entry.favorite and not entry.is_deleted:
            star = icons.pixmap("star-fill", "#f59e0b", 14)
            painter.drawPixmap(right - 14, cy - 7, star)
            right -= 20

        # title (plus subtle match subtitle while searching or in trash)
        tx = ax + 32 + 12
        width = max(20, right - tx)
        show_sub = bool(query) or entry.is_deleted
        if show_sub:
            subtitle = (f"Deleted {entry.deleted_at[:10]}" if entry.is_deleted and entry.deleted_at
                        else (entry.username or entry.host))
        else:
            subtitle = ""

        f.setBold(selected)
        f.setPixelSize(14)
        painter.setFont(f)
        if subtitle:
            _draw_highlighted_text(painter, QRect(tx, rect.top() + 6, width, 20), entry.name, query, fg, hi)
            f.setBold(False)
            f.setPixelSize(11)
            painter.setFont(f)
            _draw_highlighted_text(painter, QRect(tx, rect.top() + 27, width, 16), subtitle, query, sub, hi)
        else:
            _draw_highlighted_text(painter, QRect(tx, rect.top(), width, rect.height()), entry.name, query, fg, hi)
        painter.restore()


# ---------------------------------------------------------------- folder dropdown
class FolderCombo(QComboBox):
    """Folder/tag selector placed at the top of the entry list (matches UI demo)."""

    LIBRARY = [("__all__", "All entries", "library"), ("__fav__", "Favorites", "star"),
               ("__recent__", "Recently used", "clock"), ("__trash__", "Trash", "trash")]

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(38)
        self.setIconSize(QSize(16, 16))
        self._tags: list[str] | None = None
        self._counts: dict[str, int] = {}
        theme().changed.connect(self._retint)

    def current_key(self) -> str:
        return self.currentData() or "__all__"

    def sync(self, counts: dict[str, int], tags: list[str]) -> None:
        self._counts = dict(counts)
        if self._tags is not None and tags == self._tags:
            self._update_labels()
            return
        previous = self.current_key()
        self.blockSignals(True)
        self.clear()
        col = theme().colors["fg"]
        for key, text, ic in self.LIBRARY:
            self.addItem(icons.icon(ic, col, 16), text, key)
        for t in tags:
            self.addItem(icons.icon("tag", col, 16), f"#{t}", f"tag:{t}")
        self._tags = list(tags)
        self._update_labels()
        idx = self.findData(previous)
        self.setCurrentIndex(idx if idx >= 0 else 0)
        self.blockSignals(False)

    def _update_labels(self) -> None:
        names = {k: text for k, text, _ in self.LIBRARY}
        for i in range(self.count()):
            key = self.itemData(i)
            base = names.get(key) or (f"#{key[4:]}" if isinstance(key, str) and key.startswith("tag:") else "")
            cnt = self._counts.get(key, 0)
            self.setItemText(i, f"{base}  ({cnt})" if cnt and key != "__all__" else base)

    def _retint(self) -> None:
        col = theme().colors["fg"]
        ic_map = {k: ic for k, _, ic in self.LIBRARY}
        for i in range(self.count()):
            key = self.itemData(i)
            name = ic_map.get(key, "tag")
            self.setItemIcon(i, icons.icon(name, col, 16))

    def select_key(self, key: str) -> None:
        idx = self.findData(key)
        if idx >= 0:
            self.setCurrentIndex(idx)


# ---------------------------------------------------------------- password generator popover
class GeneratorPopover(QFrame):
    """Lightweight inline popover for generating a password right beside the edit field."""
    passwordPicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("popoverCard")
        self.setMinimumWidth(300)

        col = QVBoxLayout(self)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(label("Generate password", "h2"))
        head.addStretch(1)
        self.len_lbl = label("20", "small")
        head.addWidget(self.len_lbl)
        col.addLayout(head)

        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setProperty("mono", "true")
        col.addWidget(self.preview)

        self.length = QSlider(Qt.Orientation.Horizontal)
        self.length.setRange(8, 48)
        self.length.setValue(max(12, int(settings().get("gen_length", 20))))
        col.addWidget(self.length)

        opts = QHBoxLayout()
        opts.setSpacing(8)
        self.upper = QCheckBox("A–Z")
        self.lower = QCheckBox("a–z")
        self.digits = QCheckBox("0–9")
        self.symbols = QCheckBox("!@#")
        for cb, default in ((self.upper, True), (self.lower, True), (self.digits, True), (self.symbols, True)):
            cb.setChecked(default)
            cb.toggled.connect(self._regen)
            opts.addWidget(cb)
        col.addLayout(opts)

        self.length.valueChanged.connect(self._on_len)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        regen = IconButton("refresh", "New")
        regen.clicked.connect(self._regen)
        use_btn = IconButton("check", "Use password", "primary")
        use_btn.clicked.connect(self._use)
        btns.addWidget(regen)
        btns.addStretch(1)
        btns.addWidget(use_btn)
        col.addLayout(btns)
        self._regen()

    def _on_len(self, val: int) -> None:
        self.len_lbl.setText(f"{val} chars")
        self._regen()

    def _regen(self) -> None:
        self.len_lbl.setText(f"{self.length.value()} chars")
        classes = build_classes(
            self.upper.isChecked(), self.lower.isChecked(),
            self.digits.isChecked(), self.symbols.isChecked(),
            bool(settings().get("gen_ambiguous", False)),
        )
        if not classes:
            self.lower.setChecked(True)
            classes = build_classes(False, True, False, False)
        self.preview.setText(generate_password(self.length.value(), classes))
        self.preview.setCursorPosition(0)

    def _use(self) -> None:
        if self.preview.text():
            self.passwordPicked.emit(self.preview.text())
        self.hide()

    def open_below(self, anchor: QWidget) -> None:
        self._regen()
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        self.move(pos)
        self.show()


# ---------------------------------------------------------------- slide-in entry editor
class EntryEditorPanel(QFrame):
    """Slide-in right panel for creating or editing an entry without leaving context."""
    saved = Signal(dict)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("editorPanel")
        self._entry_id: str | None = None
        self._initial: dict | None = None
        self._popover = GeneratorPopover(self)
        self._popover.passwordPicked.connect(self._apply_generated)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(24, 16, 16, 12)
        self.heading = label("New entry", "h1")
        self.close_btn = IconButton("x", tooltip="Close (Esc)")
        self.close_btn.clicked.connect(self.request_close)
        head.addWidget(self.heading, 1)
        head.addWidget(self.close_btn)
        outer.addLayout(head)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(24, 8, 24, 16)
        form.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Group 1: Identity
        form.addWidget(section_label("Identity"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. GitHub")
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("user@example.com")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://github.com")
        form.addWidget(labeled("Name", self.name_edit))
        form.addWidget(labeled("Username or email", self.username_edit))
        form.addWidget(labeled("Website", self.url_edit))

        # Group 2: Secret
        form.addSpacing(4)
        form.addWidget(section_label("Secret"))
        self.pw_edit = PasswordField("Password")
        pw_row = QHBoxLayout()
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.setSpacing(8)
        pw_row.addWidget(self.pw_edit, 1)
        self.gen_btn = IconButton("dice", "Generate", tooltip="Open password generator")
        self.gen_btn.clicked.connect(lambda: self._popover.open_below(self.gen_btn))
        pw_row.addWidget(self.gen_btn)
        pw_holder = QWidget()
        pw_holder.setLayout(pw_row)
        form.addWidget(labeled("Password", pw_holder))
        self.meter = StrengthMeter()
        form.addWidget(self.meter)
        self.pw_edit.textChanged.connect(self.meter.set_password)

        self.totp_edit = QLineEdit()
        self.totp_edit.setPlaceholderText("Base32 secret or otpauth:// link (optional)")
        form.addWidget(labeled("One-time password (2FA)", self.totp_edit))
        self.totp_hint = label("", "small")
        form.addWidget(self.totp_hint)
        self.totp_edit.textChanged.connect(self._check_totp)

        # Group 3: Organization & Notes
        form.addSpacing(4)
        form.addWidget(section_label("Notes & Organization"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("work, finance")
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setFixedHeight(96)
        self.fav_check = QCheckBox("Mark as favorite")
        form.addWidget(labeled("Tags (comma-separated)", self.tags_edit))
        form.addWidget(labeled("Notes", self.notes_edit))
        form.addWidget(self.fav_check)
        form.addStretch(1)

        footer = QWidget()
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(24, 8, 24, 16)
        fl.setSpacing(8)
        self.error = label("", "error", wrap=True)
        fl.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_btn = IconButton("x", "Cancel")
        self.cancel_btn.clicked.connect(self.request_close)
        self.save_btn = IconButton("check", "Save", "primary")
        self.save_btn.clicked.connect(self._submit)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        fl.addLayout(buttons)
        outer.addWidget(footer)

    def load(self, entry: Entry | None = None) -> None:
        self._entry_id = entry.id if entry else None
        self.heading.setText("Edit entry" if entry else "New entry")
        self.name_edit.setText(entry.name if entry else "")
        self.username_edit.setText(entry.username if entry else "")
        self.url_edit.setText(entry.url if entry else "")
        self.pw_edit.reset()
        self.pw_edit.setText(entry.password if entry else "")
        self.totp_edit.setText(entry.totp if entry else "")
        self.tags_edit.setText(", ".join(entry.tags) if entry else "")
        self.notes_edit.setPlainText(entry.notes if entry else "")
        self.fav_check.setChecked(bool(entry and entry.favorite))
        self.error.setText("")
        self.meter.set_password(self.pw_edit.text())
        self._check_totp(self.totp_edit.text())
        self._initial = self.values()
        self.name_edit.setFocus()

    def clear_secrets(self) -> None:
        self._entry_id = None
        self.name_edit.clear()
        self.username_edit.clear()
        self.url_edit.clear()
        self.pw_edit.reset()
        self.totp_edit.clear()
        self.tags_edit.clear()
        self.notes_edit.clear()
        self.fav_check.setChecked(False)
        self._initial = None

    @property
    def entry_id(self) -> str | None:
        return self._entry_id

    def _apply_generated(self, pw: str) -> None:
        self.pw_edit.setText(pw)
        self.pw_edit.set_revealed(True)

    def _check_totp(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.totp_hint.setText("")
            return
        ok = is_valid_totp(text)
        self.totp_hint.setProperty("role", "ok" if ok else "error")
        self.totp_hint.setText("Valid secret" if ok else "Not a valid Base32 secret or otpauth:// link")
        repolish(self.totp_hint)

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

    def _submit(self) -> None:
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
        self.saved.emit(self.values())

    def request_close(self) -> None:
        # FIX: `if self._initial` was falsy for new (empty) entries, so the
        # discard prompt never fired on a brand-new entry after typing.
        if self._initial is not None and self.values() != self._initial:
            if not confirm(self, "Discard changes?", "You have unsaved changes. Discard them?", "Discard"):
                return
        self.cancelled.emit()


# ---------------------------------------------------------------- strength badge pill
class StrengthBadge(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("strengthPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 14, 6)
        lay.setSpacing(6)
        self.icon = IconLabel("shield-check", 15, "success")
        self.text = label("")
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self._score = -1
        theme().changed.connect(self._retint)

    def set_strength(self, score: int, text: str) -> None:
        self._score = score
        if not text:
            self.setVisible(False)
            return
        self.setVisible(True)
        level = str(score) if score >= 0 else ""
        self.setProperty("strength", level)
        self.text.setProperty("strength", level)
        self.text.setText(text)
        repolish(self)
        repolish(self.text)
        self._retint()

    def _retint(self) -> None:
        key = "success" if self._score >= 3 else "warning" if self._score >= 1 else "danger"
        self.icon._key = key
        self.icon.set_name("shield-check" if self._score >= 3 else "shield")


# ---------------------------------------------------------------- detail panel
class DetailPanel(QWidget):
    editRequested = Signal()
    deleteRequested = Signal()
    favoriteToggled = Signal()
    restoreRequested = Signal()
    purgeConfirmed = Signal()
    historyRequested = Signal()
    openUrlRequested = Signal()
    copyRequested = Signal(str)

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self._entry_id: str | None = None
        self._entry_name: str = ""
        self._confirm_mode: str = ""  # "trash" or "purge"
        self._totp = None
        self._warn = False
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_totp)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # --- empty page
        self.empty = QFrame()
        self.empty.setProperty("card", "true")
        el = QVBoxLayout(self.empty)
        self.empty_state = EmptyState("vault", "Nothing selected", "Choose an entry to see its details.")
        el.addWidget(self.empty_state)
        self.stack.addWidget(self.empty)

        # --- detail page
        self.page = QFrame()
        self.page.setProperty("card", "true")
        pl = QVBoxLayout(self.page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 24, 24, 16)
        bl.setSpacing(18)
        scroll.setWidget(body)
        pl.addWidget(scroll, 1)

        head = QHBoxLayout()
        head.setSpacing(16)
        self.avatar = Avatar(52)
        head.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label("", "h1", wrap=True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subtitle = label("", "muted", wrap=True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.badge = StrengthBadge()
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addLayout(head)

        self.username = FieldRow("Username")
        self.password = FieldRow("Password", secret=True)
        self.url = FieldRow("Website", can_open=True)
        self.notes = FieldRow("Notes", can_copy=False, multiline=True)
        self.username.copyRequested.connect(lambda: self._copy("username", self.username))
        self.password.copyRequested.connect(lambda: self._copy("password", self.password))
        self.url.copyRequested.connect(lambda: self._copy("url", self.url))
        self.url.openRequested.connect(self.openUrlRequested)
        for w in (self.username, self.password, self.url):
            bl.addWidget(w)

        self.tags_box = QWidget()
        tl = QVBoxLayout(self.tags_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(label("Tags", "small"))
        self.tags_text = label("", "muted", wrap=True)
        tl.addWidget(self.tags_text)
        bl.addWidget(self.tags_box)
        bl.addWidget(self.notes)

        self.totp_card = card(inner=True)
        tc = QVBoxLayout(self.totp_card)
        tc.setContentsMargins(16, 12, 16, 16)
        tc.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label("One-time password", "small"))
        top.addStretch(1)
        self.totp_caption = label("", "small")
        top.addWidget(self.totp_caption)
        copy_totp = IconButton("copy", tooltip="Copy code", size=30)
        copy_totp.clicked.connect(lambda: self.copyRequested.emit("totp"))
        top.addWidget(copy_totp)
        tc.addLayout(top)
        self.code = label("", "code")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tc.addWidget(self.code)
        self.totp_bar = QProgressBar()
        self.totp_bar.setTextVisible(False)
        tc.addWidget(self.totp_bar)
        bl.addWidget(self.totp_card)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        meta_row.addWidget(IconLabel("clock", 14, "sub"))
        self.meta = label("", "small")
        meta_row.addWidget(self.meta, 1)
        bl.addLayout(meta_row)
        bl.addStretch(1)

        # --- inline delete confirmation bar (Cancel focused by default, never Delete)
        self.confirm_bar = QFrame()
        self.confirm_bar.setObjectName("confirmBar")
        self.confirm_bar.setMaximumHeight(0)
        self.confirm_bar.setVisible(False)
        self._confirm_anim: QPropertyAnimation | None = None
        cbl = QHBoxLayout(self.confirm_bar)
        cbl.setContentsMargins(16, 10, 16, 10)
        cbl.setSpacing(8)
        self.confirm_msg = label("", wrap=True)
        self.confirm_cancel = IconButton("x", "Cancel")
        self.confirm_go = IconButton("trash", "Delete", "danger")
        self.confirm_go.setAutoDefault(False)
        self.confirm_cancel.clicked.connect(self.hide_confirm)
        self.confirm_go.clicked.connect(self._execute_confirm)
        cbl.addWidget(self.confirm_msg, 1)
        cbl.addWidget(self.confirm_cancel)
        cbl.addWidget(self.confirm_go)
        cwrap = QVBoxLayout()
        cwrap.setContentsMargins(20, 0, 20, 8)
        cwrap.addWidget(self.confirm_bar)
        pl.addLayout(cwrap)

        # --- actions (pinned below the scroll area, outlined Delete button on the right)
        bar = QWidget()
        al = QHBoxLayout(bar)
        al.setContentsMargins(24, 8, 24, 20)
        al.setSpacing(10)
        self.edit_btn = IconButton("edit", "Edit")
        self.fav_btn = IconButton("star", "Favorite")
        self.hist_btn = IconButton("clock", tooltip="Password history")
        self.restore_btn = IconButton("undo", "Restore", "primary")
        self.del_btn = IconButton("trash", "Delete", "danger-outline")
        self.purge_btn = IconButton("trash", "Delete forever", "danger-outline")
        self.edit_btn.clicked.connect(self.editRequested)
        self.fav_btn.clicked.connect(self.favoriteToggled)
        self.hist_btn.clicked.connect(self.historyRequested)
        self.restore_btn.clicked.connect(self.restoreRequested)
        self.del_btn.clicked.connect(lambda: self.prompt_delete("trash"))
        self.purge_btn.clicked.connect(lambda: self.prompt_delete("purge"))
        for w in (self.edit_btn, self.fav_btn, self.hist_btn, self.restore_btn):
            al.addWidget(w)
        al.addStretch(1)
        al.addWidget(self.del_btn)
        al.addWidget(self.purge_btn)
        pl.addWidget(bar)
        self.stack.addWidget(self.page)

    # ---- inline delete confirmation
    def _stop_confirm_anim(self) -> None:
        if self._confirm_anim is not None:
            self._confirm_anim.stop()
            self._confirm_anim = None

    def prompt_delete(self, mode: str = "trash") -> None:
        if not self._entry_id:
            return
        self._stop_confirm_anim()
        self._confirm_mode = mode
        if mode == "purge":
            self.confirm_msg.setText(f"Permanently delete “{self._entry_name}”? This cannot be undone.")
            self.confirm_go.setText("Delete forever")
        else:
            self.confirm_msg.setText(f"Move “{self._entry_name}” to Trash?")
            self.confirm_go.setText("Move to Trash")
        target_h = max(48, self.confirm_bar.sizeHint().height())
        self.confirm_bar.setVisible(True)
        if settings().reduced_motion:
            self.confirm_bar.setMaximumHeight(16777215)
        else:
            a = QPropertyAnimation(self.confirm_bar, b"maximumHeight", self)
            a.setDuration(160)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.setStartValue(self.confirm_bar.height() if self.confirm_bar.isVisible() else 0)
            a.setEndValue(target_h)
            a.finished.connect(lambda: self.confirm_bar.setMaximumHeight(16777215) if self._confirm_mode else None)
            a.start()
            self._confirm_anim = a
        self.confirm_cancel.setFocus()

    def hide_confirm(self) -> None:
        self._confirm_mode = ""
        self._stop_confirm_anim()
        if not self.confirm_bar.isVisible():
            self.confirm_bar.setMaximumHeight(0)
            return
        if settings().reduced_motion:
            self.confirm_bar.setMaximumHeight(0)
            self.confirm_bar.setVisible(False)
            return
        a = QPropertyAnimation(self.confirm_bar, b"maximumHeight", self)
        a.setDuration(150)
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.setStartValue(self.confirm_bar.height())
        a.setEndValue(0)
        a.finished.connect(lambda: self.confirm_bar.setVisible(False) if not self._confirm_mode else None)
        a.start()
        self._confirm_anim = a

    def _execute_confirm(self) -> None:
        mode = self._confirm_mode
        self.hide_confirm()
        if mode == "purge":
            self.purgeConfirmed.emit()
        elif mode == "trash":
            self.deleteRequested.emit()

    # ---- public
    def set_empty_text(self, icon: str, title: str, text: str) -> None:
        self.empty_state.set_content(icon, title, text)

    def show_entry(self, entry: Entry | None) -> None:
        self.hide_confirm()
        if entry is None:
            self._entry_id = None
            self._entry_name = ""
            self._totp = None
            self._timer.stop()
            for row in (self.username, self.password, self.url, self.notes):
                row.clear_value()
            self.code.clear()
            self.stack.setCurrentWidget(self.empty)
            return
        self._entry_id = entry.id
        self._entry_name = entry.name
        self.avatar.set_name(entry.name)
        self.title.setText(entry.name)
        if entry.is_deleted:
            sub = f"In Trash · removed automatically after {TRASH_DAYS} days"
        else:
            sub = entry.host
        self.subtitle.setText(sub)
        self.subtitle.setVisible(bool(sub))
        s = entry.strength
        if entry.is_deleted:
            self.badge.set_strength(-1, "")
        else:
            self.badge.set_strength(s.score, f"{s.label} password" if s.label else "")

        self.username.set_value(entry.username)
        self.username.setVisible(bool(entry.username))
        self.password.set_value(entry.password)
        self.url.set_value(entry.url)
        self.url.setVisible(bool(entry.url))
        self.tags_text.setText("   ".join("#" + t for t in entry.tags))
        self.tags_box.setVisible(bool(entry.tags))
        self.notes.set_value(entry.notes)
        self.notes.setVisible(bool(entry.notes))

        self._totp = parse_totp(entry.totp)
        self.totp_card.setVisible(self._totp is not None)
        if self._totp is not None:
            self._warn = False
            self.totp_bar.setProperty("level", "")
            repolish(self.totp_bar)
            self._tick_totp()
            if self.isVisible():
                self._timer.start()
        else:
            self._timer.stop()

        trashed = entry.is_deleted
        for w in (self.edit_btn, self.fav_btn, self.hist_btn, self.del_btn):
            w.setVisible(not trashed)
        self.hist_btn.setVisible(not trashed and bool(entry.history))
        self.restore_btn.setVisible(trashed)
        self.purge_btn.setVisible(trashed)
        self.fav_btn.setText("Favorited" if entry.favorite else "Favorite")
        self.fav_btn.set_icon_name("star-fill" if entry.favorite else "star")
        self.meta.setText(f"Modified: {entry.modified[:16].replace('T', ' ')}  ·  Created: {entry.created[:10]}")
        self.stack.setCurrentWidget(self.page)

    # ---- internals
    def _copy(self, field: str, row: FieldRow) -> None:
        self.copyRequested.emit(field)
        row.flash_copied()

    def _tick_totp(self) -> None:
        p = self._totp
        if p is None:
            self._timer.stop()
            return
        code = generate_totp(p)
        half = len(code) // 2
        rem = totp_remaining(p.period)
        self.code.setText(f"{code[:half]} {code[half:]}")
        self.totp_caption.setText(f"refreshes in {rem}s")
        self.totp_bar.setRange(0, p.period)
        self.totp_bar.setValue(rem)
        warn = rem <= 5
        if warn != self._warn:
            self._warn = warn
            self.totp_bar.setProperty("level", "warn" if warn else "")
            repolish(self.totp_bar)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._totp is not None:
            self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()  # no ticking (or visible codes) while another page is showing


# ---------------------------------------------------------------- right-side host with slide-in editor
class DetailHost(QWidget):
    """Holds the detail panel and slides the EntryEditorPanel in from the right."""

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.detail = DetailPanel(main)
        lay.addWidget(self.detail)

        self.editor = EntryEditorPanel(self)
        self.editor.hide()
        self._anim: QPropertyAnimation | None = None
        self._editing = False

    @property
    def editing(self) -> bool:
        return self._editing

    def open_editor(self, entry: Entry | None = None) -> None:
        was_visible = self.editor.isVisible()
        start_pos = self.editor.pos() if was_visible else QPoint(self.width(), 0)
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self.editor.load(entry)
        self.editor.resize(self.size())
        self.editor.raise_()
        self._editing = True
        if settings().reduced_motion:
            self.editor.move(0, 0)
            self.editor.show()
            return
        self.editor.move(start_pos)
        self.editor.show()
        self._anim = QPropertyAnimation(self.editor, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(QPoint(0, 0))
        self._anim.start()

    def close_editor(self, clear: bool = True) -> None:
        if not self._editing and not self.editor.isVisible():
            return
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._editing = False
        if settings().reduced_motion:
            self.editor.hide()
            if clear:
                self.editor.clear_secrets()
            return
        self._anim = QPropertyAnimation(self.editor, b"pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(self.editor.pos())
        self._anim.setEndValue(QPoint(self.width(), 0))

        def _done() -> None:
            if not self._editing:
                self.editor.hide()
                if clear:
                    self.editor.clear_secrets()

        self._anim.finished.connect(_done)
        self._anim.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not (self._editing or self.editor.isVisible()):
            return
        if self._anim is not None and self._anim.state() == QAbstractAnimation.State.Running:
            self.editor.resize(self.size())
            self._anim.setEndValue(QPoint(0, 0) if self._editing else QPoint(self.width(), 0))
        elif self._editing:
            self.editor.setGeometry(self.rect())


# ---------------------------------------------------------------- the tab (two-pane layout)
class VaultTab(QWidget):
    selectionChanged = Signal()

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self.session = main.session
        self._folder = "__all__"
        self._sort_mode = settings().get("sort_mode", SORT_MODES[0])
        if self._sort_mode not in SORT_MODES:
            self._sort_mode = SORT_MODES[0]

        root = QVBoxLayout(self)
        root.setContentsMargins(PAD, 16, PAD, 20)
        root.setSpacing(14)

        # Backup reminder banner
        self.reminder_bar = QFrame()
        self.reminder_bar.setObjectName("reminderBar")
        self.reminder_bar.setVisible(False)
        rbl = QHBoxLayout(self.reminder_bar)
        rbl.setContentsMargins(16, 8, 12, 8)
        rbl.setSpacing(8)
        rbl.addWidget(IconLabel("info", 18, "accent"))
        rbl.addWidget(label("No recent encrypted backup — save a copy on external media.", "muted", wrap=True), 1)
        exp_now = IconButton("download", "Export backup")
        dismiss_btn = IconButton("x", tooltip="Remind me later")
        exp_now.clicked.connect(lambda: self.main.main_page.settings_tab.export_backup())
        dismiss_btn.clicked.connect(self._dismiss_backup_reminder)
        rbl.addWidget(exp_now)
        rbl.addWidget(dismiss_btn)
        root.addWidget(self.reminder_bar)

        # Top bar: search input + Add entry (matches demo)
        bar = QHBoxLayout()
        bar.setSpacing(GAP)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search for an entry…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(40)
        self._search_action = QAction(self)
        self.search.addAction(self._search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.trash_btn = IconButton("trash", "Empty trash", "danger")
        self.trash_btn.setMinimumHeight(40)
        self.trash_btn.setVisible(False)
        self.add_btn = IconButton("plus", "Add entry", "primary")
        self.add_btn.setMinimumHeight(40)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.trash_btn)
        bar.addWidget(self.add_btn)
        root.addLayout(bar)

        # Two-pane body: entry list card + detail host
        self.inner = QSplitter(Qt.Orientation.Horizontal)
        self.inner.setChildrenCollapsible(False)
        self.inner.setHandleWidth(12)
        root.addWidget(self.inner, 1)

        list_card = card()
        lc = QVBoxLayout(list_card)
        lc.setContentsMargins(12, 12, 12, 10)
        lc.setSpacing(10)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.folder_combo = FolderCombo()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.sort_btn = IconButton("sliders", tooltip="Sort entries", size=38)
        self.sort_btn.clicked.connect(self._open_sort_menu)
        filter_row.addWidget(self.folder_combo, 1)
        filter_row.addWidget(self.sort_btn)
        lc.addLayout(filter_row)

        self.list_stack = QStackedWidget()
        self.model = EntryListModel(self.session.get)
        self.list = SmoothListView()
        self.list.setObjectName("entryList")
        self.list.setModel(self.model)
        self.list.setItemDelegate(EntryDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty = EmptyState("vault")
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.empty)
        lc.addWidget(self.list_stack, 1)
        list_card.setMinimumWidth(260)
        self.inner.addWidget(list_card)

        self.detail_host = DetailHost(main)
        self.detail = self.detail_host.detail
        self.detail_host.setMinimumWidth(360)
        self.inner.addWidget(self.detail_host)
        self.inner.setStretchFactor(0, 38)
        self.inner.setStretchFactor(1, 62)
        self.inner.setSizes([310, 560])

        # debounce keystrokes: filter the list only, never the whole window
        self._filter_timer = Debouncer(self, 120, self._apply)
        self.search.textChanged.connect(self._filter_timer.trigger)
        self.add_btn.clicked.connect(self.add_entry)
        self.empty.action.clicked.connect(self._on_empty_action)
        self.trash_btn.clicked.connect(self.empty_trash)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.doubleClicked.connect(lambda _i: self.copy_current("password"))
        self.list.activated.connect(self._focus_detail)
        self.list.selectionModel().currentChanged.connect(self._on_current)

        sc_copy = QShortcut(QKeySequence("Ctrl+C"), self.list)
        sc_copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc_copy.activated.connect(lambda: self.copy_current("password"))

        sc_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc_esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_esc.activated.connect(self._on_escape)

        d = self.detail
        d.editRequested.connect(self.edit_current)
        d.deleteRequested.connect(self._soft_delete_confirmed)
        d.favoriteToggled.connect(self.toggle_favorite)
        d.restoreRequested.connect(self.restore_current)
        d.purgeConfirmed.connect(self._purge_confirmed)
        d.historyRequested.connect(self.show_history)
        d.openUrlRequested.connect(self.open_url)
        d.copyRequested.connect(self.copy_current)

        self.detail_host.editor.saved.connect(self._on_editor_saved)
        self.detail_host.editor.cancelled.connect(lambda: self.detail_host.close_editor(clear=True))

        self.session.changed.connect(self.refresh)
        theme().changed.connect(self._on_theme)
        self._on_theme()

    # ---- theme / persistence of layout
    def _on_theme(self) -> None:
        from .. import icons as _i
        self._search_action.setIcon(_i.icon("search", theme().colors["sub"], 16))
        self.list.viewport().update()

    def save_layout(self) -> None:
        settings().set("splitter_inner", bytes(self.inner.saveState().toHex()).decode())

    def restore_layout(self) -> None:
        raw = settings().get("splitter_inner", "")
        if raw:
            self.inner.restoreState(QByteArray.fromHex(raw.encode()))

    @staticmethod
    def _menu_item(menu: QMenu, icon_name: str, text: str, slot, shortcut: str = "") -> QAction:
        caption = f"{text}\t{shortcut}" if shortcut else text
        action = menu.addAction(icons.icon(icon_name, theme().colors["sub"], 16), caption)
        action.triggered.connect(slot)
        return action

    def _open_sort_menu(self) -> None:
        menu = AnimatedMenu(self)
        menu.setObjectName("sortMenu")
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        for mode in SORT_MODES:
            act = menu.addAction(mode)
            act.setCheckable(True)
            act.setChecked(mode == self._sort_mode)
            grp.addAction(act)
            act.triggered.connect(lambda _c=False, m=mode: self._on_sort(m))
        menu.popup_at(self.sort_btn.mapToGlobal(QPoint(0, self.sort_btn.height() + 4)))

    # ---- backup guidance
    def refresh_backup_banner(self) -> None:
        if not self.session.unlocked or len(self.session.live()) < 3:
            self.reminder_bar.setVisible(False)
            return
        now = datetime.now()
        dismissed = settings().get("backup_reminder_dismissed_at", "")
        if dismissed:
            try:
                if now - datetime.fromisoformat(dismissed) < timedelta(days=7):
                    self.reminder_bar.setVisible(False)
                    return
            except ValueError:
                pass
        last_bak = settings().get("last_backup_at", "")
        if last_bak:
            try:
                if now - datetime.fromisoformat(last_bak) < timedelta(days=30):
                    self.reminder_bar.setVisible(False)
                    return
            except ValueError:
                pass
        self.reminder_bar.setVisible(True)

    def _dismiss_backup_reminder(self) -> None:
        settings().set("backup_reminder_dismissed_at", datetime.now().isoformat(timespec="seconds"))
        self.reminder_bar.setVisible(False)

    # ---- state
    def reset(self) -> None:
        self.detail_host.close_editor(clear=True)
        self.detail.hide_confirm()
        self.reminder_bar.setVisible(False)
        self.search.clear()
        self._filter_timer.cancel()
        self._folder = "__all__"
        self.trash_btn.setVisible(False)
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo._tags = None
        self.folder_combo.blockSignals(False)
        self.model.set_query("")
        self.model.set_ids([])
        self.detail.show_entry(None)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _focus_detail(self) -> None:
        if self.current_entry() is not None:
            self.detail.edit_btn.setFocus()

    def _on_escape(self) -> None:
        if self.detail.confirm_bar.isVisible():
            self.detail.hide_confirm()
        elif self.detail_host.editing:
            self.detail_host.editor.request_close()
        elif self.search.text():
            self.search.clear()
            self.list.setFocus()

    def current_id(self) -> str | None:
        idx = self.list.currentIndex()
        return self.model.id_at(idx.row()) if idx.isValid() else None

    def current_entry(self) -> Entry | None:
        return self.session.get(self.current_id())

    def _on_sort(self, mode: str) -> None:
        self._sort_mode = mode
        settings().set("sort_mode", mode)
        self._apply()

    def _on_folder_changed(self, _idx: int) -> None:
        self.set_folder(self.folder_combo.current_key())

    def set_folder(self, key: str) -> None:
        self._folder = key
        self.trash_btn.setVisible(key == "__trash__")
        self._apply()

    def refresh(self) -> None:
        """Full refresh after the data changed. Keeps the user's selection."""
        live = self.session.live()
        counts = {
            "__all__": len(live),
            "__fav__": sum(1 for e in live if e.favorite),
            "__recent__": min(15, sum(1 for e in live if e.last_used)),
            "__trash__": len(self.session.trashed()),
        }
        tags = self.session.all_tags()
        for t in tags:
            counts[f"tag:{t}"] = sum(1 for e in live if t in e.tags)
        self.folder_combo.sync(counts, tags)
        self.refresh_backup_banner()
        self._apply()

    def _filtered(self) -> list[Entry]:
        s, folder = self.session, self._folder
        pool = s.trashed() if folder == "__trash__" else s.live()
        if folder == "__fav__":
            pool = [e for e in pool if e.favorite]
        elif folder == "__recent__":
            pool = [e for e in pool if e.last_used]
        elif folder.startswith("tag:"):
            tag = folder[4:]
            pool = [e for e in pool if tag in e.tags]
        term = self.search.text().strip().lower()
        if term:
            pool = [e for e in pool if term in e.haystack()]
        mode = self._sort_mode
        if mode == "Name A→Z":
            pool.sort(key=lambda e: e.name.lower())
        elif mode == "Name Z→A":
            pool.sort(key=lambda e: e.name.lower(), reverse=True)
        elif mode == "Recently modified":
            pool.sort(key=lambda e: e.modified, reverse=True)
        elif mode == "Recently created":
            pool.sort(key=lambda e: e.created, reverse=True)
        elif mode == "Favorites first":
            pool.sort(key=lambda e: (not e.favorite, e.name.lower()))
        if folder == "__recent__":
            pool.sort(key=lambda e: e.last_used or "", reverse=True)
            pool = pool[:15]
        return pool

    def _apply(self, select_id: str | None = None) -> None:
        if not self.session.unlocked:
            return
        keep = select_id or self.current_id()
        rows = self._filtered()
        ids = [e.id for e in rows]
        self.model.set_query(self.search.text())
        sm = self.list.selectionModel()
        sm.blockSignals(True)  # avoid a flash of the empty detail page during the reset
        self.model.set_ids(ids)
        row = self.model.row_of(keep)
        if row < 0 and ids:
            row = 0
        if row >= 0:
            self.list.setCurrentIndex(self.model.index(row, 0))
            self.list.scrollTo(self.model.index(row, 0))
        sm.blockSignals(False)

        if ids:
            self.list_stack.setCurrentWidget(self.list)
        else:
            self._show_empty()
        self.detail.show_entry(self.current_entry() if ids else None)
        self.selectionChanged.emit()

    def _show_empty(self) -> None:
        q = self.search.text().strip()
        f = self._folder
        if q:
            self.empty.set_content("search", "No matches", f"Nothing matches “{q}”.",
                                   True, "Clear search", "x")
        elif not self.session.entries:
            self.empty.set_content("vault", "Your vault is empty",
                                   "Add your first login to get started. Everything is encrypted on this device.",
                                   True, "Add entry", "plus")
        elif f == "__trash__":
            self.empty.set_content("trash", "Trash is empty", "Deleted entries wait here for 30 days.")
        elif f == "__fav__":
            self.empty.set_content("star", "No favorites yet", "Star an entry to pin it here.",
                                   True, "View all entries", "library")
        elif f == "__recent__":
            self.empty.set_content("clock", "Nothing used recently", "Entries you copy from show up here.",
                                   True, "View all entries", "library")
        else:
            self.empty.set_content("tag", "Nothing here", "No entries in this view.",
                                   True, "Add entry", "plus")
        self.list_stack.setCurrentWidget(self.empty)
        self.detail.set_empty_text("vault", "Nothing selected", "Choose an entry to see its details.")

    def _on_empty_action(self) -> None:
        if self.search.text().strip():
            self.search.clear()
        elif self._folder in ("__fav__", "__recent__"):
            self.folder_combo.select_key("__all__")
        else:
            self.add_entry()

    def _on_current(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        if self.detail_host.editing:
            self.detail_host.close_editor(clear=True)
        self.detail.show_entry(self.current_entry() if cur.isValid() else None)
        self.selectionChanged.emit()

    def select_entry(self, entry_id: str) -> None:
        """Jump to an entry from anywhere (health report, after adding...)."""
        self.search.clear()
        self._filter_timer.cancel()
        if self.model.row_of(entry_id) < 0 or entry_id not in {e.id for e in self._filtered()}:
            self.folder_combo.select_key("__all__")
            self._folder = "__all__"
            self.trash_btn.setVisible(False)
        e = self.session.get(entry_id)
        if e is not None and e.is_deleted:
            self.folder_combo.select_key("__trash__")
            self._folder = "__trash__"
            self.trash_btn.setVisible(True)
        self._apply(select_id=entry_id)

    # ---- actions
    @staticmethod
    def _apply_values(entry: Entry, v: dict) -> None:
        entry.name = v["name"]
        entry.username = v["username"]
        entry.url = v["url"]
        entry.totp = v["totp"]
        entry.tags = v["tags"]
        entry.notes = v["notes"]
        entry.favorite = v["favorite"]
        entry.set_password(v["password"])

    def add_entry(self) -> None:
        if not self.session.unlocked:
            return
        self.detail_host.open_editor(None)

    def edit_current(self) -> None:
        entry = self.current_entry()
        if entry is None or entry.is_deleted:
            return
        self.detail_host.open_editor(entry)

    def _on_editor_saved(self, values: dict) -> None:
        eid = self.detail_host.editor.entry_id
        self.detail_host.close_editor(clear=True)
        if eid is None:
            entry = Entry(name="")
            self._apply_values(entry, values)
            self.session.add(entry)
            self.select_entry(entry.id)
            self.main.toast(f"Added “{entry.name}”", "success")
        else:
            entry = self.session.get(eid)
            if entry is not None:
                self._apply_values(entry, values)
                self.session.update(entry)
                self.main.toast("Saved", "success")

    def delete_current(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        self.detail.prompt_delete("purge" if entry.is_deleted else "trash")

    def _soft_delete_confirmed(self) -> None:
        entry = self.current_entry()
        if entry is None or entry.is_deleted:
            return
        name = entry.name
        self.session.soft_delete(entry.id)
        self.main.toast(f"Moved “{name}” to Trash · Ctrl+Z to undo", "neutral")
        self.main.update_actions()

    def restore_current(self) -> None:
        entry = self.current_entry()
        if entry is not None and entry.is_deleted:
            self.session.restore(entry.id)
            self.main.toast(f"Restored “{entry.name}”", "success")

    def purge_current(self) -> None:
        entry = self.current_entry()
        if entry is not None:
            self.detail.prompt_delete("purge")

    def _purge_confirmed(self) -> None:
        entry = self.current_entry()
        if entry is not None:
            self.session.purge(entry.id)
            self.main.toast("Deleted permanently", "neutral")

    def empty_trash(self) -> None:
        n = len(self.session.trashed())
        if n and confirm(self.main, "Empty trash", f"Permanently delete {n} item(s)? This cannot be undone.",
                         "Empty trash"):
            self.session.empty_trash()
            self.main.toast("Trash emptied", "neutral")

    def undo_delete(self) -> None:
        entry = self.session.undo_delete()
        if entry is not None:
            self.main.toast(f"Restored “{entry.name}”", "success")
            self.main.update_actions()

    def toggle_favorite(self) -> None:
        entry = self.current_entry()
        if entry is not None and not entry.is_deleted:
            self.session.set_favorite(entry.id, not entry.favorite)

    def copy_current(self, field: str) -> None:
        entry = self.current_entry()
        if entry is not None:
            self.main.copy_secret(entry, field)

    def open_url(self) -> None:
        entry = self.current_entry()
        url = safe_url(entry.url) if entry else None
        if url is None:
            self.main.toast("That address can't be opened", "danger")
            return
        QDesktopServices.openUrl(url)

    def show_history(self) -> None:
        entry = self.current_entry()
        if entry is not None:
            HistoryDialog(self.main, entry, lambda pw: self.main.copy_text(pw, "Old password")).exec()

    def _context_menu(self, pos: QPoint) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        self.list.setCurrentIndex(idx)
        entry = self.current_entry()
        if entry is None:
            return
        menu = AnimatedMenu(self)
        menu.setObjectName("entryMenu")
        if entry.is_deleted:
            self._menu_item(menu, "undo", "Restore", self.restore_current)
            self._menu_item(menu, "trash", "Delete forever", self.purge_current)
        else:
            self._menu_item(menu, "copy", "Copy password",
                            lambda: self.copy_current("password"), "Ctrl+Shift+C")
            self._menu_item(menu, "copy", "Copy username",
                            lambda: self.copy_current("username"), "Ctrl+Shift+U")
            if entry.totp:
                self._menu_item(menu, "clock", "Copy one-time code",
                                lambda: self.copy_current("totp"), "Ctrl+Shift+T")
            if safe_url(entry.url):
                self._menu_item(menu, "external", "Open website", self.open_url)
            menu.addSeparator()
            self._menu_item(menu, "edit", "Edit…", self.edit_current, "Ctrl+E")
            self._menu_item(menu, "star-fill" if entry.favorite else "star",
                            "Remove from favorites" if entry.favorite else "Add to favorites",
                            self.toggle_favorite)
            if entry.history:
                self._menu_item(menu, "clock", "Password history…", self.show_history)
            menu.addSeparator()
            self._menu_item(menu, "trash", "Move to Trash", self.delete_current, "Del")
        menu.popup_at(self.list.viewport().mapToGlobal(pos))
