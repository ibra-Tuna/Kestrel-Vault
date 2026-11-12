"""The vault page: sidebar, entry list, detail panel and the actions between them."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (QAbstractListModel, QByteArray, QModelIndex, QPoint, QRect, QSize, Qt,
                            QUrl, Signal)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMenu, QScrollArea,
                               QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from .. import icons
from ..crypto import generate_totp, parse_totp, totp_remaining
from ..models import TRASH_DAYS, Entry
from ..services import settings, theme
from ..theme import GAP, PAD, on_color
from .common import (Avatar, EmptyState, FieldRow, IconButton, IconLabel, card, label, repolish,
                     section_label)
from .dialogs import EntryDialog, HistoryDialog, confirm

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


# ---------------------------------------------------------------- list model
class EntryListModel(QAbstractListModel):
    """Holds only entry ids; the Entry objects stay in the session, so locking the
    vault drops every reference instead of leaving copies inside Qt item data."""

    def __init__(self, lookup) -> None:
        super().__init__()
        self._ids: list[str] = []
        self._lookup = lookup

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
    ROW_H = 60

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        entry = index.model().entry(index)
        if entry is None:
            return
        c = theme().colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            bg, fg, sub = QColor(c["accent"]), QColor(c["on_accent"]), QColor(c["on_accent"])
            sub.setAlphaF(0.8)
        else:
            bg = QColor(c["panel_hover"]) if hovered else QColor(0, 0, 0, 0)
            fg, sub = QColor(c["fg"]), QColor(c["sub"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 10, 10)

        # avatar
        from .common import name_color
        cy = rect.center().y()
        ax = rect.left() + 12
        painter.setBrush(name_color(entry.name))
        painter.drawEllipse(QRect(ax, cy - 17, 34, 34))
        painter.setPen(QColor("white"))
        f = painter.font()
        f.setBold(True)
        f.setPixelSize(14)
        painter.setFont(f)
        painter.drawText(QRect(ax, cy - 17, 34, 34), Qt.AlignmentFlag.AlignCenter, (entry.name[:1] or "?").upper())

        # right-hand decorations
        right = rect.right() - 12
        if not entry.is_deleted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(entry.strength.color))
            painter.drawEllipse(QPoint(right - 4, cy), 4, 4)
            right -= 18
        if entry.favorite and not entry.is_deleted:
            star = icons.pixmap("star-fill", "#f59e0b", 16)
            painter.drawPixmap(right - 16, cy - 8, star)
            right -= 22

        # text
        tx = ax + 34 + 12
        width = max(20, right - tx)
        f.setBold(True)
        f.setPixelSize(13)
        painter.setFont(f)
        painter.setPen(fg)
        fm = QFontMetrics(f)
        painter.drawText(QRect(tx, rect.top() + 9, width, 20), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(entry.name, Qt.TextElideMode.ElideRight, width))
        subtitle = (f"Deleted {entry.deleted_at[:10]}" if entry.is_deleted and entry.deleted_at
                    else (entry.username or entry.host))
        if subtitle:
            f.setBold(False)
            f.setPixelSize(11)
            painter.setFont(f)
            painter.setPen(sub)
            fm = QFontMetrics(f)
            painter.drawText(QRect(tx, rect.top() + 29, width, 18), Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(subtitle, Qt.TextElideMode.ElideRight, width))
        painter.restore()


# ---------------------------------------------------------------- sidebar
KEY_ROLE = Qt.ItemDataRole.UserRole
ICON_ROLE = Qt.ItemDataRole.UserRole + 1
COUNT_ROLE = Qt.ItemDataRole.UserRole + 2
HEADER_ROLE = Qt.ItemDataRole.UserRole + 3


class SidebarDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:
        c = theme().colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        f = painter.font()
        if index.data(HEADER_ROLE):
            f.setPixelSize(10)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(c["sub"]))
            painter.drawText(rect.adjusted(10, 8, -6, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             str(index.data(Qt.ItemDataRole.DisplayRole)).upper())
            painter.restore()
            return
        r = rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.setBrush(QColor(c["accent"]))
            fg = QColor(c["on_accent"])
        else:
            painter.setBrush(QColor(c["panel_hover"]) if hovered else QColor(0, 0, 0, 0))
            fg = QColor(c["fg"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(r, 8, 8)
        icon_name = index.data(ICON_ROLE)
        if icon_name:
            pm = icons.pixmap(icon_name, fg.name(), 16)
            painter.drawPixmap(r.left() + 10, r.center().y() - 8, pm)
        f.setPixelSize(13)
        f.setBold(selected)
        painter.setFont(f)
        painter.setPen(fg)
        count = index.data(COUNT_ROLE)
        count_w = 34 if count else 0
        painter.drawText(QRect(r.left() + 36, r.top(), r.width() - 36 - count_w - 6, r.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(f).elidedText(str(index.data(Qt.ItemDataRole.DisplayRole)),
                                                    Qt.TextElideMode.ElideRight, r.width() - 36 - count_w - 6))
        if count:
            f.setPixelSize(11)
            f.setBold(False)
            painter.setFont(f)
            muted = QColor(fg)
            muted.setAlphaF(0.65)
            painter.setPen(muted)
            painter.drawText(QRect(r.right() - count_w - 6, r.top(), count_w, r.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(count))
        painter.restore()


class SidebarList(QListWidget):
    folderChanged = Signal(str)

    LIBRARY = [("__all__", "All entries", "library"), ("__fav__", "Favorites", "star"),
               ("__recent__", "Recently used", "clock"), ("__trash__", "Trash", "trash")]

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sideList")
        self.setItemDelegate(SidebarDelegate(self))
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tags: list[str] | None = None
        self.currentItemChanged.connect(self._on_current)

    def current_key(self) -> str | None:
        it = self.currentItem()
        return it.data(KEY_ROLE) if it is not None else None

    def _add_header(self, text: str) -> None:
        it = QListWidgetItem(text)
        it.setData(HEADER_ROLE, True)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        it.setSizeHint(QSize(0, 34))
        self.addItem(it)

    def _add(self, key: str, text: str, icon: str, count: int) -> None:
        it = QListWidgetItem(text)
        it.setData(KEY_ROLE, key)
        it.setData(ICON_ROLE, icon)
        it.setData(COUNT_ROLE, count or None)
        it.setSizeHint(QSize(0, 38))
        self.addItem(it)

    def sync(self, counts: dict[str, int], tags: list[str]) -> None:
        if self._tags is None or tags != self._tags:
            self._rebuild(counts, tags)
            return
        for i in range(self.count()):
            it = self.item(i)
            key = it.data(KEY_ROLE)
            if key in counts:
                it.setData(COUNT_ROLE, counts[key] or None)
        self.viewport().update()

    def _rebuild(self, counts: dict[str, int], tags: list[str]) -> None:
        previous = self.current_key() or "__all__"
        self.blockSignals(True)
        self.clear()
        self._add_header("Library")
        for key, text, icon in self.LIBRARY:
            self._add(key, text, icon, counts.get(key, 0))
        if tags:
            self._add_header("Tags")
            for t in tags:
                self._add(f"tag:{t}", t, "tag", counts.get(f"tag:{t}", 0))
        self._tags = list(tags)
        found = self.select_key(previous)
        self.blockSignals(False)
        if not found:
            self.select_key("__all__")  # emits folderChanged via currentItemChanged

    def select_key(self, key: str) -> bool:
        for i in range(self.count()):
            if self.item(i).data(KEY_ROLE) == key:
                self.setCurrentRow(i)
                return True
        return False

    def _on_current(self, cur: QListWidgetItem | None, _prev) -> None:
        if cur is not None and cur.data(KEY_ROLE):
            self.folderChanged.emit(cur.data(KEY_ROLE))


# ---------------------------------------------------------------- detail panel
class DetailPanel(QWidget):
    editRequested = Signal()
    deleteRequested = Signal()
    favoriteToggled = Signal()
    restoreRequested = Signal()
    purgeRequested = Signal()
    historyRequested = Signal()
    openUrlRequested = Signal()
    copyRequested = Signal(str)

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self._entry_id: str | None = None
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 24, 24, 16)
        bl.setSpacing(16)
        scroll.setWidget(body)
        pl.addWidget(scroll, 1)

        head = QHBoxLayout()
        head.setSpacing(14)
        self.avatar = Avatar(52)
        head.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label("", "h1", wrap=True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subtitle = label("", "muted", wrap=True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.badge = label("")
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
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
        tc.setContentsMargins(16, 12, 16, 14)
        tc.setSpacing(6)
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
        from PySide6.QtWidgets import QProgressBar
        self.totp_bar = QProgressBar()
        self.totp_bar.setTextVisible(False)
        tc.addWidget(self.totp_bar)
        bl.addWidget(self.totp_card)

        self.meta = label("", "small")
        bl.addWidget(self.meta)
        bl.addStretch(1)

        # --- actions (pinned below the scroll area so they never clip)
        bar = QWidget()
        al = QHBoxLayout(bar)
        al.setContentsMargins(20, 12, 20, 16)
        al.setSpacing(8)
        self.edit_btn = IconButton("edit", "Edit")
        self.fav_btn = IconButton("star", "Favorite")
        self.hist_btn = IconButton("clock", tooltip="Password history")
        self.restore_btn = IconButton("undo", "Restore", "primary")
        self.del_btn = IconButton("trash", "Delete", "danger")
        self.purge_btn = IconButton("trash", "Delete forever", "danger")
        self.edit_btn.clicked.connect(self.editRequested)
        self.fav_btn.clicked.connect(self.favoriteToggled)
        self.hist_btn.clicked.connect(self.historyRequested)
        self.restore_btn.clicked.connect(self.restoreRequested)
        self.del_btn.clicked.connect(self.deleteRequested)
        self.purge_btn.clicked.connect(self.purgeRequested)
        for w in (self.edit_btn, self.fav_btn, self.hist_btn, self.restore_btn):
            al.addWidget(w)
        al.addStretch(1)
        al.addWidget(self.del_btn)
        al.addWidget(self.purge_btn)
        pl.addWidget(bar)
        self.stack.addWidget(self.page)

    # ---- public
    def set_empty_text(self, icon: str, title: str, text: str) -> None:
        self.empty_state.set_content(icon, title, text)

    def show_entry(self, entry: Entry | None) -> None:
        if entry is None:
            self._entry_id = None
            self._totp = None
            self._timer.stop()
            for row in (self.username, self.password, self.url, self.notes):
                row.clear_value()
            self.code.clear()
            self.stack.setCurrentWidget(self.empty)
            return
        self._entry_id = entry.id
        self.avatar.set_name(entry.name)
        self.title.setText(entry.name)
        if entry.is_deleted:
            sub = f"In Trash · removed automatically after {TRASH_DAYS} days"
        else:
            sub = entry.host
        self.subtitle.setText(sub)
        self.subtitle.setVisible(bool(sub))
        s = entry.strength
        self.badge.setText(f"{s.label} password" if not entry.is_deleted else "")
        self.badge.setStyleSheet(f"color:{s.color}; font-weight:600;")

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
        self.meta.setText(f"Modified {entry.modified[:16].replace('T', ' ')}   ·   Created {entry.created[:10]}")
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


# ---------------------------------------------------------------- the tab
class VaultTab(QWidget):
    selectionChanged = Signal()

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self.session = main.session
        self._folder = "__all__"

        root = QHBoxLayout(self)
        root.setContentsMargins(PAD, 8, PAD, 16)
        self.outer = QSplitter(Qt.Orientation.Horizontal)
        self.outer.setChildrenCollapsible(False)
        self.outer.setHandleWidth(10)
        root.addWidget(self.outer)

        self.sidebar = SidebarList()
        self.sidebar.setMinimumWidth(176)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.folderChanged.connect(self.set_folder)
        self.outer.addWidget(self.sidebar)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(GAP)
        self.outer.addWidget(right)

        bar = QHBoxLayout()
        bar.setSpacing(GAP)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, username, website, notes, tags")
        self.search.setClearButtonEnabled(True)
        self._search_action = QAction(self)
        self.search.addAction(self._search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_MODES)
        self.sort_combo.setFixedWidth(176)
        mode = settings().get("sort_mode", SORT_MODES[0])
        self.sort_combo.setCurrentText(mode if mode in SORT_MODES else SORT_MODES[0])
        self.add_btn = IconButton("plus", "Add entry", "primary")
        self.trash_btn = IconButton("trash", "Empty trash", "danger")
        self.trash_btn.setVisible(False)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.sort_combo)
        bar.addWidget(self.trash_btn)
        bar.addWidget(self.add_btn)
        rv.addLayout(bar)

        self.inner = QSplitter(Qt.Orientation.Horizontal)
        self.inner.setChildrenCollapsible(False)
        self.inner.setHandleWidth(10)
        rv.addWidget(self.inner, 1)

        list_card = card()
        lc = QVBoxLayout(list_card)
        lc.setContentsMargins(6, 6, 6, 6)
        self.list_stack = QStackedWidget()
        self.model = EntryListModel(self.session.get)
        self.list = QListView()
        self.list.setObjectName("entryList")
        self.list.setModel(self.model)
        self.list.setItemDelegate(EntryDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty = EmptyState("vault")
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.empty)
        lc.addWidget(self.list_stack)
        list_card.setMinimumWidth(290)
        self.inner.addWidget(list_card)

        self.detail = DetailPanel(main)
        self.detail.setMinimumWidth(340)
        self.inner.addWidget(self.detail)
        self.inner.setStretchFactor(0, 4)
        self.inner.setStretchFactor(1, 5)
        self.outer.setStretchFactor(0, 0)
        self.outer.setStretchFactor(1, 1)
        self.outer.setSizes([210, 900])
        self.inner.setSizes([340, 480])

        # debounce keystrokes: filter the list only, never the whole window
        from ..services import Debouncer
        self._filter_timer = Debouncer(self, 150, self._apply)
        self.search.textChanged.connect(self._filter_timer.trigger)
        self.sort_combo.currentTextChanged.connect(self._on_sort)
        self.add_btn.clicked.connect(self.add_entry)
        self.empty.action.clicked.connect(self.add_entry)
        self.trash_btn.clicked.connect(self.empty_trash)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.doubleClicked.connect(lambda _i: self.copy_current("password"))
        self.list.selectionModel().currentChanged.connect(self._on_current)

        d = self.detail
        d.editRequested.connect(self.edit_current)
        d.deleteRequested.connect(self.delete_current)
        d.favoriteToggled.connect(self.toggle_favorite)
        d.restoreRequested.connect(self.restore_current)
        d.purgeRequested.connect(self.purge_current)
        d.historyRequested.connect(self.show_history)
        d.openUrlRequested.connect(self.open_url)
        d.copyRequested.connect(self.copy_current)

        self.session.changed.connect(self.refresh)
        theme().changed.connect(self._on_theme)
        self._on_theme()

    # ---- theme / persistence of layout
    def _on_theme(self) -> None:
        from .. import icons as _i
        self._search_action.setIcon(_i.icon("search", theme().colors["sub"], 16))
        self.list.viewport().update()
        self.sidebar.viewport().update()

    def save_layout(self) -> None:
        settings().set("splitter_outer", bytes(self.outer.saveState().toHex()).decode())
        settings().set("splitter_inner", bytes(self.inner.saveState().toHex()).decode())

    def restore_layout(self) -> None:
        for key, splitter in (("splitter_outer", self.outer), ("splitter_inner", self.inner)):
            raw = settings().get(key, "")
            if raw:
                splitter.restoreState(QByteArray.fromHex(raw.encode()))
        self.sidebar.setVisible(bool(settings().get("sidebar_visible", True)))

    def toggle_sidebar(self) -> None:
        visible = not self.sidebar.isVisible()
        self.sidebar.setVisible(visible)
        settings().set("sidebar_visible", visible)

    # ---- state
    def reset(self) -> None:
        self.search.clear()
        self._filter_timer.cancel()
        self._folder = "__all__"
        self.trash_btn.setVisible(False)
        self.sidebar.blockSignals(True)
        self.sidebar.clear()          # don't leave tag names behind in a locked app
        self.sidebar.blockSignals(False)
        self.sidebar._tags = None
        self.model.set_ids([])
        self.detail.show_entry(None)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def current_id(self) -> str | None:
        idx = self.list.currentIndex()
        return self.model.id_at(idx.row()) if idx.isValid() else None

    def current_entry(self) -> Entry | None:
        return self.session.get(self.current_id())

    def _on_sort(self, mode: str) -> None:
        settings().set("sort_mode", mode)
        self._apply()

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
        self.sidebar.sync(counts, tags)
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
        mode = self.sort_combo.currentText()
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
            self.empty.set_content("search", "No matches", f"Nothing matches “{q}”.")
        elif not self.session.entries:
            self.empty.set_content("vault", "Your vault is empty",
                                   "Add your first login to get started. Everything is encrypted on this device.", True)
        elif f == "__trash__":
            self.empty.set_content("trash", "Trash is empty", "Deleted entries wait here for 30 days.")
        elif f == "__fav__":
            self.empty.set_content("star", "No favorites yet", "Star an entry to pin it here.")
        elif f == "__recent__":
            self.empty.set_content("clock", "Nothing used recently", "Entries you copy from show up here.")
        else:
            self.empty.set_content("tag", "Nothing here", "No entries in this view.")
        self.list_stack.setCurrentWidget(self.empty)
        self.detail.set_empty_text("vault", "Nothing selected", "Choose an entry to see its details.")

    def _on_current(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        self.detail.show_entry(self.current_entry() if cur.isValid() else None)
        self.selectionChanged.emit()

    def select_entry(self, entry_id: str) -> None:
        """Jump to an entry from anywhere (health report, after adding...)."""
        self.search.clear()
        self._filter_timer.cancel()
        if self.model.row_of(entry_id) < 0 or entry_id not in {e.id for e in self._filtered()}:
            self.sidebar.select_key("__all__")
            self._folder = "__all__"
            self.trash_btn.setVisible(False)
        e = self.session.get(entry_id)
        if e is not None and e.is_deleted:
            self.sidebar.select_key("__trash__")
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
        dlg = EntryDialog(self.main, "Add entry")
        if dlg.exec():
            entry = Entry(name="")
            self._apply_values(entry, dlg.values())
            self.session.add(entry)
            self.select_entry(entry.id)
            self.main.toast(f"Added “{entry.name}”", "#22c55e")

    def edit_current(self) -> None:
        entry = self.current_entry()
        if entry is None or entry.is_deleted:
            return
        dlg = EntryDialog(self.main, "Edit entry", entry)
        if dlg.exec():
            self._apply_values(entry, dlg.values())
            self.session.update(entry)
            self.main.toast("Saved", "#22c55e")

    def delete_current(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if entry.is_deleted:
            self.purge_current()
            return
        name = entry.name
        self.session.soft_delete(entry.id)
        self.main.toast(f"Moved “{name}” to Trash · Ctrl+Z to undo", "#64748b")
        self.main.update_actions()

    def restore_current(self) -> None:
        entry = self.current_entry()
        if entry is not None and entry.is_deleted:
            self.session.restore(entry.id)
            self.main.toast(f"Restored “{entry.name}”", "#22c55e")

    def purge_current(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if confirm(self.main, "Delete forever", f"Permanently delete “{entry.name}”? This cannot be undone.",
                   "Delete forever"):
            self.session.purge(entry.id)

    def empty_trash(self) -> None:
        n = len(self.session.trashed())
        if n and confirm(self.main, "Empty trash", f"Permanently delete {n} item(s)? This cannot be undone.",
                         "Empty trash"):
            self.session.empty_trash()
            self.main.toast("Trash emptied", "#64748b")

    def undo_delete(self) -> None:
        entry = self.session.undo_delete()
        if entry is not None:
            self.main.toast(f"Restored “{entry.name}”", "#22c55e")
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
            self.main.toast("That address can't be opened", "#ef4444")
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
        menu = QMenu(self)
        if entry.is_deleted:
            menu.addAction("Restore", self.restore_current)
            menu.addAction("Delete forever", self.purge_current)
        else:
            menu.addAction("Copy password", lambda: self.copy_current("password"))
            menu.addAction("Copy username", lambda: self.copy_current("username"))
            if entry.totp:
                menu.addAction("Copy one-time code", lambda: self.copy_current("totp"))
            if safe_url(entry.url):
                menu.addAction("Open website", self.open_url)
            menu.addSeparator()
            menu.addAction("Edit…", self.edit_current)
            menu.addAction("Remove from favorites" if entry.favorite else "Add to favorites", self.toggle_favorite)
            if entry.history:
                menu.addAction("Password history…", self.show_history)
            menu.addSeparator()
            menu.addAction("Move to Trash", self.delete_current)
        menu.exec(self.list.viewport().mapToGlobal(pos))
