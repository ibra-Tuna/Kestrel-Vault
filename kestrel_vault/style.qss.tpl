/* Kestrel Vault stylesheet template. $tokens are filled in by theme.build_stylesheet. */

* { font-family: "Segoe UI Variable", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", system-ui, sans-serif; }
QWidget { color: $fg; font-size: 14px; }
*:focus { outline: none; }
QMainWindow, QDialog { background: $bg; }
QLabel { background: transparent; }

/* ---- label roles (set with setProperty("role", ...)) ---- */
QLabel[role="brand"]   { font-size: 16px; font-weight: 700; }
QLabel[role="title"]   { font-size: 20px; font-weight: 700; }
QLabel[role="h1"]      { font-size: 20px; font-weight: 700; }
QLabel[role="h2"]      { font-size: 16px; font-weight: 600; }
QLabel[role="muted"]   { color: $sub; }
QLabel[role="small"]   { color: $sub; font-size: 12px; }
QLabel[role="section"] { color: $sub; font-size: 12px; font-weight: 700; }
QLabel[role="error"]   { color: $danger; }
QLabel[role="warn"]    { color: $warning; }
QLabel[role="ok"]      { color: $success; }
QLabel[role="code"]    { font-family: "Cascadia Mono", "JetBrains Mono", Consolas, Menlo, monospace; font-size: 20px; font-weight: 700; }

/* ---- strength & entropy text roles ---- */
QLabel[strength="0"] { color: $danger; font-weight: 600; }
QLabel[strength="1"] { color: $warning; font-weight: 600; }
QLabel[strength="2"] { color: #eab308; font-weight: 600; }
QLabel[strength="3"] { color: $success; font-weight: 600; }
QLabel[strength="4"] { color: $success; font-weight: 700; }

/* ---- strength badge pill ---- */
QFrame#strengthPill { background: $panel; border: 1px solid $border; border-radius: 14px; }
QFrame#strengthPill[strength="0"], QFrame#strengthPill[strength="1"] { background: $danger_tint; border: 1px solid $danger_border; }
QFrame#strengthPill[strength="2"] { background: $warning_tint; border: 1px solid $warning_border; }
QFrame#strengthPill[strength="3"], QFrame#strengthPill[strength="4"] { background: $success_tint; border: 1px solid $success_border; }

/* ---- left navigation rail & surfaces ---- */
QFrame#navRail         { background: $rail; border: none; border-right: 1px solid $border; }
QFrame#railSeparator   { background: $border; border: none; }
QFrame[card="true"]    { background: $panel; border: 1px solid $border; border-radius: 10px; }
QFrame[card="inner"]   { background: $field; border: 1px solid $border; border-radius: 8px; }
QFrame[fieldBox="true"] { background: $field; border: 1px solid $border; border-radius: 6px; }
QFrame[fieldBox="true"]:hover { border-color: $border_hover; }
QFrame#unlockCard      { background: $panel; border: 1px solid $border; border-radius: 10px; }
QFrame#generated       { background: $panel; border: 1px solid $border; border-radius: 10px; }
QFrame#editorPanel     { background: $panel; border: 1px solid $border; border-radius: 10px; }
QFrame#confirmBar      { background: $danger_tint; border: 1px solid $danger_border; border-radius: 8px; }
QFrame#reminderBar     { background: $panel; border: 1px solid $border; border-radius: 8px; }
QFrame#popoverCard     { background: $elevated; border: 1px solid $border; border-radius: 8px; }

/* ---- toasts ---- */
QLabel#toast                  { background: $accent; color: $on_accent; border-radius: 8px; padding: 8px 16px; font-weight: 600; font-size: 14px; }
QLabel#toast[tone="success"]  { background: $success; color: #ffffff; }
QLabel#toast[tone="danger"]   { background: $danger; color: #ffffff; }
QLabel#toast[tone="warning"]  { background: $warning; color: #111111; }
QLabel#toast[tone="neutral"]  { background: $neutral_toast; color: #ffffff; }

/* ---- inputs (visible 2px focus ring) ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {
    background: $field; border: 1px solid $border; border-radius: 6px;
    padding: 8px 12px; min-height: 20px;
    selection-background-color: $accent; selection-color: $on_accent;
}
QLineEdit[inline="true"], QPlainTextEdit[inline="true"] {
    background: transparent; border: none; border-radius: 4px; padding: 8px 10px;
}
QLineEdit[inline="true"][keyboardFocus="true"]:focus, QPlainTextEdit[inline="true"][keyboardFocus="true"]:focus {
    border: none; padding: 8px 10px;
}
QLineEdit[mono="true"] { font-family: "Cascadia Mono", "JetBrains Mono", Consolas, Menlo, monospace; font-size: 16px; }
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover { border-color: $border_hover; }
QLineEdit[keyboardFocus="true"]:focus, QPlainTextEdit[keyboardFocus="true"]:focus, QSpinBox[keyboardFocus="true"]:focus, QComboBox[keyboardFocus="true"]:focus { border: 2px solid $accent; padding: 7px 11px; }
QLineEdit:read-only { background: $field; }
QLineEdit[inline="true"]:read-only { background: transparent; }
QLineEdit:disabled, QComboBox:disabled { color: $sub; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox::down-arrow { image: url($chevron_icon); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: $elevated; color: $fg; border: 1px solid $border; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: $accent; selection-color: $on_accent;
}
QComboBox QAbstractItemView::item { color: $fg; padding: 8px 10px; border-radius: 6px; min-height: 20px; }
QComboBox QAbstractItemView::item:selected { background: $nav_active_bg; color: $nav_active_fg; }
QComboBox QAbstractItemView::item:hover:!selected { background: $panel_hover; }

/* ---- buttons ---- */
QPushButton {
    background: $elevated; border: 1px solid $border; border-radius: 6px;
    padding: 8px 16px; min-height: 20px; font-weight: 500;
}
QPushButton:hover    { background: $panel_hover; border-color: $border_hover; }
QPushButton:pressed  { background: $border; }
QPushButton[keyboardFocus="true"]:focus    { border: 2px solid $accent; padding: 7px 15px; }
QPushButton:disabled { color: $sub; background: $panel; border-color: $border; }

QPushButton[variant="primary"] { background: $accent; color: $on_accent; border: 1px solid $accent; font-weight: 600; }
QPushButton[variant="primary"]:hover   { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_press; border-color: $accent_press; }
QPushButton[variant="primary"][keyboardFocus="true"]:focus   { border: 2px solid $fg; padding: 7px 15px; }
QPushButton[variant="primary"]:disabled { background: $border; border-color: $border; color: $sub; }

QPushButton[variant="danger"] { background: $danger; color: #ffffff; border: 1px solid $danger; font-weight: 600; }
QPushButton[variant="danger"]:hover { background: $danger_hover; border-color: $danger_hover; }
QPushButton[variant="danger"][keyboardFocus="true"]:focus { border: 2px solid $fg; padding: 7px 15px; }
QPushButton[variant="danger"]:disabled { background: $border; border-color: $border; color: $sub; }

QPushButton[variant="danger-outline"] { background: $danger_tint; color: $danger; border: 1px solid $danger_border; font-weight: 600; }
QPushButton[variant="danger-outline"]:hover { background: $danger; color: #ffffff; border-color: $danger; }
QPushButton[variant="danger-outline"][keyboardFocus="true"]:focus { border: 2px solid $danger; padding: 7px 15px; }

QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; }
QPushButton[variant="ghost"]:hover { background: $panel_hover; }
QPushButton[variant="ghost"][keyboardFocus="true"]:focus { border: 2px solid $accent; }

QPushButton[variant="link"] { background: transparent; border: none; color: $accent; padding: 4px 8px; min-height: 0; font-weight: 600; }
QPushButton[variant="link"]:hover { text-decoration: underline; }
QPushButton[variant="link"][keyboardFocus="true"]:focus { border: 2px solid $accent; border-radius: 6px; }

QPushButton[variant="icon"] { background: transparent; border: 1px solid transparent; padding: 0; min-height: 0; border-radius: 6px; }
QPushButton[variant="icon"]:hover   { background: $panel_hover; }
QPushButton[variant="icon"]:pressed { background: $border; }
QPushButton[variant="icon"][keyboardFocus="true"]:focus   { border: 2px solid $accent; }

QToolButton[variant="icon"] { background: transparent; border: 1px solid transparent; padding: 0; border-radius: 4px; }
QToolButton[variant="icon"]:hover   { background: $panel_hover; }
QToolButton[variant="icon"]:pressed { background: $border; }
QToolButton[variant="icon"][keyboardFocus="true"]:focus   { border: 2px solid $accent; }

QPushButton[variant="nav"] {
    background: transparent; border: 1px solid transparent; color: $sub;
    padding: 9px 14px; border-radius: 8px; font-weight: 600; text-align: left;
}
QPushButton[variant="nav"]:hover   { background: $panel_hover; color: $fg; }
QPushButton[variant="nav"]:checked { background: $nav_active_bg; color: $nav_active_fg; }
QPushButton[variant="nav"][keyboardFocus="true"]:focus   { border: 2px solid $accent; padding: 8px 13px; }

/* ---- lists ---- */
QListView, QListWidget { background: $panel; border: 1px solid $border; border-radius: 8px; padding: 4px; }
QListView[keyboardFocus="true"]:focus, QListWidget[keyboardFocus="true"]:focus { border: 2px solid $accent; }
QListView#entryList { background: transparent; border: 2px solid transparent; border-radius: 6px; padding: 2px; }
QListWidget#themeList { background: transparent; border: none; padding: 0; }
QListView#entryList[keyboardFocus="true"]:focus { border: 2px solid $accent; }
QListWidget#plainList::item { padding: 8px 10px; border-radius: 6px; }
QListWidget#plainList::item:hover { background: $panel_hover; }
QListWidget#plainList::item:selected { background: $nav_active_bg; color: $nav_active_fg; }

/* ---- scrollbars ---- */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $border_hover; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 4px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: $border_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- sliders, progress, checkboxes ---- */
QSlider::groove:horizontal { height: 6px; background: $border; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal { background: $fg; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; border: 2px solid $accent; }
QSlider::handle:horizontal:hover { background: $accent; }
QSlider[keyboardFocus="true"]:focus { border: 2px solid $accent; border-radius: 4px; }

QProgressBar { background: $border; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QProgressBar[level="0"]::chunk { background: $danger; }
QProgressBar[level="1"]::chunk { background: $warning; }
QProgressBar[level="2"]::chunk { background: #eab308; }
QProgressBar[level="3"]::chunk { background: $success; }
QProgressBar[level="4"]::chunk { background: #16a34a; }
QProgressBar[level="warn"]::chunk { background: $danger; }

QCheckBox { spacing: 10px; padding: 4px 2px; border-radius: 4px; }
QCheckBox[keyboardFocus="true"]:focus { border: 2px solid $accent; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 2px solid $border_hover; background: $field; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check_icon); }

/* ---- merged title/menu bar ---- */
QFrame#titleBar {
    background: $rail;
    border: none;
    border-bottom: 1px solid $border;
}
QFrame#titleBar QLabel[role="brand"] { font-size: 14px; }
QMenuBar {
    background: transparent;
    border: none;
    padding: 0;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 7px;
    border-radius: 5px;
}
QToolButton[variant="titlebar-ctrl"], QToolButton[variant="titlebar-close"] {
    background: transparent;
    border: none;
    padding: 0;
    border-radius: 0;
}
QToolButton[variant="titlebar-ctrl"]:hover { background: $panel_hover; }
QToolButton[variant="titlebar-close"]:hover { background: $danger; }
QToolButton[variant="titlebar-ctrl"][keyboardFocus="true"]:focus,
QToolButton[variant="titlebar-close"][keyboardFocus="true"]:focus {
    border: 2px solid $accent;
    border-radius: 4px;
}

/* ---- menus, status, tooltips, splitter ---- */
QMenuBar::item:selected { background: $nav_active_bg; color: $nav_active_fg; }
QMenu { background: $elevated; border: 1px solid $border_hover; border-radius: 10px; padding: 6px; }
QMenu::icon { left: 10px; }
QMenu::item { padding: 9px 28px 9px 18px; min-height: 20px; border-radius: 6px; }
QMenu::item:selected { background: $nav_active_bg; color: $nav_active_fg; }
QMenu::item:disabled { color: $sub; background: transparent; }
QMenu::separator { height: 1px; background: $border; margin: 6px 10px; }
QStatusBar { background: $rail; color: $sub; border-top: 1px solid $border; }
QStatusBar QLabel { color: $sub; font-size: 12px; padding: 0 8px; }
QStatusBar::item { border: none; }
QToolTip { background: $elevated; color: $fg; border: 1px solid $border; border-radius: 4px; padding: 6px 10px; font-size: 12px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:hover { background: $border; }
QMessageBox QLabel { color: $fg; }