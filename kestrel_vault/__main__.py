"""Entry point: `python -m kestrel_vault`."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler


def _setup_logging() -> None:
    from .storage import data_dir
    try:
        log_dir = data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_dir / "kestrel.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)  # never log secrets; call sites log only exception types
    except OSError:
        pass


def _icon_base():
    """Where the package's bundled resources live (source dir or _MEIPASS)."""
    from pathlib import Path
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "kestrel_vault"
    return Path(__file__).resolve().parent


def main() -> int:
    _setup_logging()

    # Windows: give the process its own identity so the taskbar groups and
    # labels it as "Kestrel Vault" instead of python.exe. Harmless elsewhere.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Kestrel.KestrelVault.3"
            )
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QIcon

    app = QApplication(sys.argv)
    app.setApplicationName("Kestrel Vault")
    app.setOrganizationName("Kestrel")

    # Default icon for every top-level window and dialog.
    base = _icon_base()
    for p in (base / "app.ico", base.parent / "app.ico"):
        if p.is_file():
            app.setWindowIcon(QIcon(str(p)))
            break

    missing = []
    for mod, pkg in (("argon2", "argon2-cffi"), ("cryptography", "cryptography")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        QMessageBox.critical(None, "Missing packages", "Install the required packages first:\n\npip install " + " ".join(missing))
        return 1

    from . import services
    from .storage import VaultFile
    from .ui.main_window import MainWindow

    store = VaultFile()
    services.init(store)
    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())