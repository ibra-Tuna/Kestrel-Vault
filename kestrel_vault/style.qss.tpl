/* Kestrel Vault stylesheet template. $tokens are filled in by theme.build_stylesheet. */

* { font-family: "Segoe UI Variable", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", system-ui, sans-serif; }
QWidget { color: $fg; font-size: 13px; }
QMainWindow, QDialog { background: $bg; }
QLabel { background: transparent; }

/* ---- label roles (set with setProperty("role", ...)) ---- */
QLabel[role="brand"]   { font-size: 15px; font-weight: 700; }
QLabel[role="title"]   { font-size: 26px; font-weight: 700; }
QLabel[role="h1"]      { font-size: 20px; font-weight: 700; }
QLabel[role="h2"]      { font-size: 15px; font-weight: 600; }
QLabel[role="muted"]   { color: $sub; }
QLabel[role="small"]   { color: $sub; font-size: 11px; }
QLabel[role="section"] { color: $sub; font-size: 11px; font-weight: 700; }
QLabel[role="error"]   { color: $danger; }
QLabel[role="warn"]    { color: $warning; }
QLabel[role="ok"]      { color: $success; }
QLabel[role="code"]    { font-family: "Cascadia Mono", "JetBrains Mono", Consolas, Menlo, monospace; font-size: 26px; font-weight: 700; }

/* ---- surfaces ---- */
QFrame[card="true"]  { background: $card; border: 1px solid $border; border-radius: 14px; }
QFrame[card="inner"] { background: $panel; border: 1px solid $border; border-radius: 10px; }
QFrame[bar="true"]   { background: $panel; border: none; border-bottom: 1px solid $border; }
QFrame#unlockCard    { background: $panel; border: 1px solid $border; border-radius: 20px; }
QFrame#generated     { background: $panel; border: 1px solid $border; border-radius: 12px; }

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {
    background: $panel; border: 1px solid $border; border-radius: 10px;
    padding: 8px 12px; min-height: 20px;
    selection-background-color: $accent; selection-color: $on_accent;
}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover { border-color: $border_hover; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:read-only { background: $bg; }
QLineEdit:disabled, QComboBox:disabled { color: $sub; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox::down-arrow { image: url($chevron_icon); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: $panel; color: $fg; border: 1px solid $border; border-radius: 10px;
    padding: 6px; outline: none; selection-background-color: $accent; selection-color: $on_accent;
}
QComboBox QAbstractItemView::item {
    color: $fg;
    padding: 7px 10px; border-radius: 6px; min-height: 20px;
}
QComboBox QAbstractItemView::item:selected {
    background: $accent; color: $on_accent;
}
QComboBox QAbstractItemView::item:hover:!selected {
    background: $panel_hover;
}

/* ---- buttons ---- */
QPushButton {
    background: $panel; border: 1px solid $border; border-radius: 10px;
    padding: 8px 16px; min-height: 20px; font-weight: 500;
}
QPushButton:hover    { background: $panel_hover; border-color: $border_hover; }
QPushButton:pressed  { background: $border; }
QPushButton:focus    { border-color: $accent; }
QPushButton:disabled { color: $sub; background: $panel; border-color: $border; }

QPushButton[variant="primary"] { background: $accent; color: $on_accent; border: 1px solid $accent; font-weight: 600; }
QPushButton[variant="primary"]:hover   { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_press; border-color: $accent_press; }
QPushButton[variant="primary"]:disabled { background: $border; border-color: $border; color: $sub; }

QPushButton[variant="danger"] { background: $danger; color: #ffffff; border: 1px solid $danger; font-weight: 600; }
QPushButton[variant="danger"]:hover { background: $danger_hover; border-color: $danger_hover; }
QPushButton[variant="danger"]:disabled { background: $border; border-color: $border; color: $sub; }

QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; }
QPushButton[variant="ghost"]:hover { background: $panel_hover; }

QPushButton[variant="icon"] { background: transparent; border: none; padding: 0; min-height: 0; border-radius: 8px; }
QPushButton[variant="icon"]:hover   { background: $panel_hover; }
QPushButton[variant="icon"]:pressed { background: $border; }

QPushButton[variant="nav"] { background: transparent; border: none; color: $sub; padding: 8px 14px; border-radius: 10px; font-weight: 600; }
QPushButton[variant="nav"]:hover   { background: $panel_hover; color: $fg; }
QPushButton[variant="nav"]:checked { background: $accent; color: $on_accent; }

/* ---- lists ---- */
QListView, QListWidget { background: $panel; border: 1px solid $border; border-radius: 12px; padding: 4px; outline: none; }
QListView#entryList, QListWidget#sideList { background: transparent; border: none; padding: 0; }
QListWidget#plainList::item { padding: 8px 10px; border-radius: 8px; }
QListWidget#plainList::item:hover { background: $panel_hover; }
QListWidget#plainList::item:selected { background: $accent; color: $on_accent; }

/* ---- scrollbars ---- */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $border_hover; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 4px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: $border_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- sliders, progress, checkboxes ---- */
QSlider::groove:horizontal { height: 6px; background: $border; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal { background: $fg; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; border: 2px solid $accent; }
QSlider::handle:horizontal:hover { background: $accent; }

QProgressBar { background: $border; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QProgressBar[level="0"]::chunk { background: #ef4444; }
QProgressBar[level="1"]::chunk { background: #f97316; }
QProgressBar[level="2"]::chunk { background: #eab308; }
QProgressBar[level="3"]::chunk { background: #22c55e; }
QProgressBar[level="4"]::chunk { background: #16a34a; }
QProgressBar[level="warn"]::chunk { background: #ef4444; }

QCheckBox { spacing: 10px; padding: 3px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 2px solid $border_hover; background: $panel; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check_icon); }

/* ---- menus, status, tooltips, splitter ---- */
QMenuBar { background: $panel; padding: 4px 8px; border-bottom: 1px solid $border; }
QMenuBar::item { background: transparent; padding: 5px 12px; border-radius: 6px; }
QMenuBar::item:selected { background: $accent; color: $on_accent; }
QMenu { background: $panel; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 18px; border-radius: 6px; }
QMenu::item:selected { background: $accent; color: $on_accent; }
QMenu::item:disabled { color: $sub; background: transparent; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QStatusBar { background: $panel; color: $sub; border-top: 1px solid $border; }
QStatusBar QLabel { color: $sub; padding: 0 6px; }
QStatusBar::item { border: none; }
QToolTip { background: $panel; color: $fg; border: 1px solid $border; padding: 6px 10px; font-size: 12px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 8px; }
QSplitter::handle:hover { background: $border; }
QMessageBox QLabel { color: $fg; }