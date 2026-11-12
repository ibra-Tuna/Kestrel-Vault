"""Password health report tab (weak, reused and old passwords)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QScrollArea,
                               QVBoxLayout, QWidget)

from ..models import health_report
from ..services import theme
from ..theme import PAD
from .common import IconButton, IconLabel, card, label

if TYPE_CHECKING:
    from .main_window import MainWindow


class HealthSection(QFrame):
    entrySelected = Signal(str)

    def __init__(self, title: str, blurb: str) -> None:
        super().__init__()
        self.setProperty("card", "true")
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(8)
        head = QHBoxLayout()
        self.title = label(title, "h2")
        self.count = label("", "muted")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.count)
        col.addLayout(head)
        col.addWidget(label(blurb, "muted", wrap=True))
        self.list = QListWidget()
        self.list.setObjectName("plainList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(lambda it: self.entrySelected.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.itemClicked.connect(lambda it: self.entrySelected.emit(it.data(Qt.ItemDataRole.UserRole)))
        col.addWidget(self.list)
        self.ok = label("Nothing to fix here.", "ok")
        col.addWidget(self.ok)

    def set_entries(self, entries) -> None:
        self.list.clear()
        for e in sorted(entries, key=lambda x: x.name.lower()):
            it = QListWidgetItem(f"{e.name}   ·   {e.username or e.host}".rstrip(" ·"))
            it.setData(Qt.ItemDataRole.UserRole, e.id)
            self.list.addItem(it)
        n = len(entries)
        self.count.setText(str(n) if n else "")
        self.list.setVisible(n > 0)
        self.ok.setVisible(n == 0)
        self.list.setFixedHeight(min(n, 6) * 38 + 8 if n else 0)


class HealthTab(QWidget):
    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        page = QHBoxLayout(host)
        page.setContentsMargins(PAD, 8, PAD, PAD)
        page.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(16)
        holder = QWidget()
        holder.setMaximumWidth(720)
        holder.setMinimumWidth(320)
        holder.setLayout(col)
        page.addWidget(holder, 100)
        page.addStretch(1)

        col.addWidget(label("Password health", "h1"))
        summary = card()
        sl = QHBoxLayout(summary)
        sl.setContentsMargins(24, 20, 24, 20)
        sl.setSpacing(20)
        self.score = label("–", "title")
        self.score.setMinimumWidth(110)
        sl.addWidget(self.score)
        txt = QVBoxLayout()
        self.headline = label("", "h2")
        self.detail = label("", "muted", wrap=True)
        txt.addWidget(self.headline)
        txt.addWidget(self.detail)
        sl.addLayout(txt, 1)
        col.addWidget(summary)

        self.weak = HealthSection("Weak passwords", "These are easy to guess. Replace them with generated ones.")
        self.reused = HealthSection("Reused passwords", "The same password on several sites means one leak exposes all of them.")
        self.old = HealthSection("Old passwords", "Not changed in over a year.")
        for s in (self.weak, self.reused, self.old):
            s.entrySelected.connect(main.show_entry)
            col.addWidget(s)
        col.addStretch(1)

    def refresh(self) -> None:
        if not self.main.session.unlocked:
            return
        report = health_report(list(self.main.session.entries.values()))
        if report.total == 0:
            self.score.setText("–")
            self.headline.setText("No passwords to analyse yet")
            self.detail.setText("Add some entries and a report will appear here.")
        else:
            color = "#22c55e" if report.score >= 80 else "#f59e0b" if report.score >= 50 else "#ef4444"
            self.score.setText(f"{report.score}%")
            self.score.setStyleSheet(f"color:{color};")
            self.headline.setText("Looking good" if report.flagged == 0
                                  else f"{report.flagged} of {report.total} passwords need attention")
            self.detail.setText("Select an entry below to open it. Reports are computed on this device only.")
        self.weak.set_entries(report.weak)
        self.reused.set_entries(report.reused)
        self.old.set_entries(report.old)

    def reset(self) -> None:
        for s in (self.weak, self.reused, self.old):
            s.set_entries([])
        self.score.setText("–")
