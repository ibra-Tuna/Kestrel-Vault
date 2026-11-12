"""Password / passphrase generator tab."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..passwords import (build_classes, entropy_label, entropy_passphrase, entropy_random,
                         generate_passphrase, generate_password, wordlist)
from ..services import settings
from ..theme import PAD
from .common import IconButton, StrengthMeter, card, label

if TYPE_CHECKING:
    from .main_window import MainWindow

SEPARATORS = {"Hyphen  -": "-", "Space": " ", "Dot  .": ".", "Underscore  _": "_", "None": ""}


class GeneratorTab(QWidget):
    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        s = settings()
        self._loading = True

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
        holder.setMaximumWidth(680)
        holder.setMinimumWidth(320)
        holder.setLayout(col)
        page.addWidget(holder, 100)
        page.addStretch(1)

        col.addWidget(label("Generator", "h1"))

        # --- output
        out = QFrame()
        out.setObjectName("generated")
        ol = QVBoxLayout(out)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.output = QLineEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(46)
        self.output.setStyleSheet("font-family:'Cascadia Mono','JetBrains Mono',Consolas,Menlo,monospace;"
                                  "font-size:16px; letter-spacing:0.5px;")
        row.addWidget(self.output, 1)
        self.regen = IconButton("refresh", tooltip="Generate again (Ctrl+G)")
        self.copy_btn = IconButton("copy", tooltip="Copy")
        row.addWidget(self.regen)
        row.addWidget(self.copy_btn)
        ol.addLayout(row)
        self.meter = StrengthMeter()
        ol.addWidget(self.meter)
        self.entropy = label("", "small")
        ol.addWidget(self.entropy)
        self.error = label("", "error", wrap=True)
        ol.addWidget(self.error)
        col.addWidget(out)

        # --- options
        opts = card()
        grid = QVBoxLayout(opts)
        grid.setContentsMargins(20, 18, 20, 18)
        grid.setSpacing(14)

        self.mode = QComboBox()
        self.mode.addItems(["Random", "Passphrase"])
        self.mode.setCurrentText(s.get("gen_mode", "Random"))
        grid.addLayout(self._row("Type", self.mode))

        self.stack = QStackedWidget()
        grid.addWidget(self.stack)

        # random page
        rp = QWidget()
        rl = QVBoxLayout(rp)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        self.length = QSlider(Qt.Orientation.Horizontal)
        self.length.setRange(8, 64)
        self.length.setValue(int(s.get("gen_length", 20)))
        self.length_label = label("", "h2")
        self.length_label.setMinimumWidth(32)
        lrow = QHBoxLayout()
        lrow.addWidget(label("Length", "muted"))
        lrow.addWidget(self.length, 1)
        lrow.addWidget(self.length_label)
        rl.addLayout(lrow)
        self.upper = QCheckBox("Uppercase  A–Z")
        self.lower = QCheckBox("Lowercase  a–z")
        self.digits = QCheckBox("Digits  0–9")
        self.symbols = QCheckBox("Symbols  !@#$")
        self.ambiguous = QCheckBox("Avoid look-alikes  (O 0 I l 1)")
        for cb, key in ((self.upper, "gen_upper"), (self.lower, "gen_lower"), (self.digits, "gen_digits"),
                        (self.symbols, "gen_symbols"), (self.ambiguous, "gen_ambiguous")):
            cb.setChecked(bool(s.get(key, False)))
        checks = QGridLayout()
        checks.setHorizontalSpacing(24)
        checks.setVerticalSpacing(6)
        for i, cb in enumerate((self.upper, self.lower, self.digits, self.symbols)):
            checks.addWidget(cb, i // 2, i % 2)
        rl.addLayout(checks)
        rl.addWidget(self.ambiguous)
        self.stack.addWidget(rp)

        # passphrase page
        pp = QWidget()
        pl = QVBoxLayout(pp)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)
        self.words = QSpinBox()
        self.words.setRange(3, 12)
        self.words.setValue(int(s.get("gen_words", 5)))
        self.words.setFixedWidth(90)
        self.sep = QComboBox()
        self.sep.addItems(list(SEPARATORS))
        current = s.get("gen_sep", "-")
        for name, value in SEPARATORS.items():
            if value == current:
                self.sep.setCurrentText(name)
        self.sep.setMinimumWidth(150)
        self.capitalize = QCheckBox("Capitalize words")
        self.capitalize.setChecked(bool(s.get("gen_capitalize", True)))
        self.number = QCheckBox("Append a number")
        self.number.setChecked(bool(s.get("gen_number", False)))
        pl.addLayout(self._row("Words", self.words))
        pl.addLayout(self._row("Separator", self.sep))
        pl.addWidget(self.capitalize)
        pl.addWidget(self.number)
        self.stack.addWidget(pp)
        col.addWidget(opts)
        col.addStretch(1)

        self.mode.currentTextChanged.connect(self._on_change)
        for sig in (self.length.valueChanged, self.upper.toggled, self.lower.toggled, self.digits.toggled,
                    self.symbols.toggled, self.ambiguous.toggled, self.words.valueChanged,
                    self.sep.currentTextChanged, self.capitalize.toggled, self.number.toggled):
            sig.connect(self._on_change)
        self.regen.clicked.connect(self.generate)
        self.copy_btn.clicked.connect(self.copy)
        self._loading = False
        self._sync_mode()
        self.generate()

    @staticmethod
    def _row(text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        lbl = label(text, "muted")
        lbl.setMinimumWidth(80)
        row.addWidget(lbl)
        row.addWidget(widget)
        row.addStretch(1)
        return row

    def _sync_mode(self) -> None:
        self.stack.setCurrentIndex(0 if self.mode.currentText() == "Random" else 1)
        self.length_label.setText(str(self.length.value()))

    def _on_change(self, *_: object) -> None:
        if self._loading:
            return
        self._sync_mode()
        self.generate()
        self._persist()

    def _persist(self) -> None:
        s = settings()
        s.set("gen_mode", self.mode.currentText())
        s.set("gen_length", self.length.value())
        s.set("gen_upper", self.upper.isChecked())
        s.set("gen_lower", self.lower.isChecked())
        s.set("gen_digits", self.digits.isChecked())
        s.set("gen_symbols", self.symbols.isChecked())
        s.set("gen_ambiguous", self.ambiguous.isChecked())
        s.set("gen_words", self.words.value())
        s.set("gen_sep", SEPARATORS[self.sep.currentText()])
        s.set("gen_capitalize", self.capitalize.isChecked())
        s.set("gen_number", self.number.isChecked())

    def generate(self) -> None:
        self.error.setText("")
        bits = 0.0
        if self.mode.currentText() == "Random":
            classes = build_classes(self.upper.isChecked(), self.lower.isChecked(), self.digits.isChecked(),
                                    self.symbols.isChecked(), self.ambiguous.isChecked())
            if not classes:
                self.output.clear()
                self.error.setText("Select at least one character type.")
                self.meter.set_password("")
                self.entropy.setText("")
                return
            pw = generate_password(self.length.value(), classes)
            bits = entropy_random(len("".join(classes)), len(pw))
        else:
            try:
                pw = generate_passphrase(self.words.value(), SEPARATORS[self.sep.currentText()],
                                         self.capitalize.isChecked(), self.number.isChecked())
                bits = entropy_passphrase(self.words.value(), len(wordlist()), self.number.isChecked())
            except RuntimeError as exc:
                self.output.clear()
                self.error.setText(str(exc))
                return
        self.output.setText(pw)
        self.output.setCursorPosition(0)
        self.meter.set_password(pw)
        text, color = entropy_label(bits)
        self.entropy.setText(text)
        self.entropy.setStyleSheet(f"color:{color};")

    def copy(self) -> None:
        if self.output.text():
            self.main.copy_text(self.output.text(), "Generated password")

    def reset(self) -> None:
        self.output.clear()
        self.meter.set_password("")
