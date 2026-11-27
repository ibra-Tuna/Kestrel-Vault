"""Merged title/menu bar.

The window is frameless for a clean look, but keeps Windows' thick resize frame.
This bar supplies the custom caption area and window controls. ``hit_test()`` is
used by ``MainWindow`` so the bar still drags and snaps like a native title bar.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QMenuBar, QSizePolicy,
                              QToolButton, QWidget)

from .. import icons
from ..services import theme
from .common import IconLabel, label

HTCAPTION = 2


class _RailToggle(QToolButton):
    """Sidebar button that lives in the merged bar, always reachable."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName("tb_rail")
        self.setProperty("variant", "titlebar-ctrl")
        self.setFixedSize(36, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setRailVisible(True)
        self.clicked.connect(lambda: window.main_page.toggle_rail())  # type: ignore[attr-defined]
        theme().changed.connect(self._retint)
        self._retint()

    def setRailVisible(self, visible: bool) -> None:
        text = f"{'Hide' if visible else 'Show'} navigation rail (Ctrl+B)"
        self.setToolTip(text)
        self.setAccessibleName(text)

    def _retint(self) -> None:
        self.setIcon(icons.icon("sidebar", theme().colors["sub"], 16))


class _WindowControl(QToolButton):
    """Min / max / close control. Icons follow the theme and hover state."""

    def __init__(self, window: QWidget, name: str, tooltip: str) -> None:
        super().__init__(window)
        self._window = window
        self._name = name
        self._hover = False
        self.setObjectName(f"tb_{name}")
        self.setProperty("variant", "titlebar-close" if name == "close" else "titlebar-ctrl")
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 34)
        theme().changed.connect(self._retint)
        if name == "min":
            self.clicked.connect(window.showMinimized)  # type: ignore[attr-defined]
        elif name == "close":
            self.clicked.connect(window.close)  # type: ignore[attr-defined]
        else:
            self.clicked.connect(self.toggleMaximized)
        self._retint()

    def toggleMaximized(self) -> None:
        window = self._window
        if window.isMaximized():  # type: ignore[attr-defined]
            window.showNormal()  # type: ignore[attr-defined]
        else:
            window.showMaximized()  # type: ignore[attr-defined]
        self._retint()

    def _icon_name(self) -> str:
        if self._name == "max" and self._window.isMaximized():  # type: ignore[attr-defined]
            return "window-restore"
        return f"window-{self._name}"

    def _retint(self) -> None:
        color = "#ffffff" if self._name == "close" and self._hover else theme().colors["sub"]
        self.setIcon(icons.icon(self._icon_name(), color, 14))

    def enterEvent(self, event) -> None:
        self._hover = True
        self._retint()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._retint()
        super().leaveEvent(event)


class MergedTitleBar(QFrame):
    """Title bar holding the rail toggle, menu items, brand and window controls."""

    def __init__(self, window: QWidget, title: str = "Kestrel Vault") -> None:
        super().__init__(window)
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(6)

        self.rail_btn = _RailToggle(window)
        lay.addWidget(self.rail_btn)

        # Own our menu bar so every command stays on the left of this same bar.
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.menu_bar)

        # The name is positioned manually in resizeEvent() so it is centred on
        # the whole window, not just on the space left over by the menus.
        self.brand_host = QWidget(self)
        self.brand_host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_lay = QHBoxLayout(self.brand_host)
        brand_lay.setContentsMargins(0, 0, 0, 0)
        brand_lay.setSpacing(6)
        self._brand_lay = brand_lay
        self.brand_icon = IconLabel("shield", 14, "accent")
        self.brand = label(title, "brand")
        self.brand.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        brand_lay.addWidget(self.brand_icon)
        brand_lay.addWidget(self.brand)

        lay.addStretch(1)

        self.min_btn = _WindowControl(window, "min", "Minimize")
        self.max_btn = _WindowControl(window, "max", "Maximize / restore")
        self.close_btn = _WindowControl(window, "close", "Close")
        for btn in (self.min_btn, self.max_btn, self.close_btn):
            lay.addWidget(btn)

        theme().changed.connect(self._center_brand)
        self._center_brand()

    def _center_brand(self) -> None:
        """Centre the name text on the whole window, not the leftover space."""
        icon_w = self.brand_icon.sizeHint().width()
        text_w = self.brand.sizeHint().width()
        gap = self._brand_lay.spacing()
        host_w = icon_w + gap + text_w
        # Offset by the icon so the text itself lands on the window centre.
        host_x = (self.width() - text_w) // 2 - icon_w - gap
        host_x = max(0, min(host_x, self.width() - host_w))

        # On narrow windows a perfectly centred name would overlap the menu or
        # the window controls. Hide it instead of drawing on top of them.
        left_edge = self.menu_bar.geometry().right() + 8
        right_edge = self.min_btn.geometry().left() - 8
        self.brand_host.setVisible(host_x >= left_edge and host_x + host_w <= right_edge)
        # Nudge the brand up so its 14px text shares the menu text baseline.
        self.brand_host.setGeometry(host_x, -4, host_w, self.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_brand()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._center_brand()

    def setRailVisible(self, visible: bool) -> None:
        self.rail_btn.setRailVisible(visible)

    def retint(self) -> None:
        for btn in (self.rail_btn, self.min_btn, self.max_btn, self.close_btn):
            btn._retint()

    def hit_test(self, global_pos: QPoint) -> int | None:
        """Return a Windows hit-test code for this bar, or None for normal Qt handling."""
        if not self.isVisible():
            return None
        if not self.rect().contains(self.mapFromGlobal(global_pos)):
            return None

        # Every interactive widget stays in client hit-testing so Qt delivers
        # normal clicks. Only the empty caption area is handed to Windows for
        # dragging, Aero Snap and double-click maximize.
        for widget in (self.rail_btn, self.menu_bar, self.min_btn, self.max_btn,
                       self.close_btn):
            if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(global_pos)):
                return None
        return HTCAPTION

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), 38)
