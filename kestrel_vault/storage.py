"""On-disk storage: atomic writes, backups, lockout and settings. No Qt imports."""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import APP_SLUG, LEGACY_SLUGS
from .crypto import (CorruptVaultError, VaultError, WrongPasswordError, decrypt_dict,
                     encrypt_dict, normalize_kdf)
from .models import Entry, entries_from_blob, entries_to_blob

SCHEMA_VERSION = 3
log = logging.getLogger(__name__)


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:  # Linux and macOS: same location the original single-file version used
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_SLUG


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file, fsync, then rename so a crash never leaves a half file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else dict(default)
    except FileNotFoundError:
        return dict(default)
    except (OSError, ValueError) as exc:
        log.error("Could not read %s: %s", path.name, type(exc).__name__)
        return dict(default)


# ---------------------------------------------------------------- vault file
@dataclass
class VaultHeader:
    salt: bytes
    kdf: dict[str, Any]
    data: dict[str, Any]

    @property
    def salt_b64(self) -> str:
        return base64.b64encode(self.salt).decode("ascii")


def serialize(key: bytes, salt_b64: str, kdf: dict[str, Any], entries: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION, "salt": salt_b64, "kdf": kdf,
        "canary": encrypt_dict(key, {"status": "verified", "ts": datetime.now().isoformat(timespec="seconds")}),
        "vault_blob": encrypt_dict(key, entries_to_blob(entries)),
    }


def validate_backup(data: Any) -> dict[str, Any]:
    """Raise ValueError if `data` is not a usable vault file."""
    if not isinstance(data, dict):
        raise ValueError("This is not a Kestrel Vault backup.")
    try:
        salt = base64.b64decode(str(data.get("salt", "")), validate=True)
    except (ValueError, TypeError):
        salt = b""
    if len(salt) < 8:
        raise ValueError("The backup has no valid salt.")
    if not isinstance(data.get("vault_blob"), str) or not data["vault_blob"]:
        raise ValueError("The backup contains no vault data.")
    try:
        schema = int(data.get("schema", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("The backup has an invalid schema version.") from exc
    if schema > SCHEMA_VERSION:
        raise ValueError("The backup was made by a newer version of Kestrel Vault.")
    return data


class VaultFile:
    def __init__(self, directory: Path | None = None) -> None:
        self.dir = Path(directory) if directory else data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._import_legacy()
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.path = self.dir / "vault.json"
        self.bak = self.dir / "vault.json.bak"
        self.legacy_bak = self.dir / "vault.v2.bak"
        self.settings_path = self.dir / "settings.json"
        self.lockout_path = self.dir / "lockout.json"

    def _import_legacy(self) -> None:
        """First run under the new name: copy (never move) an old data folder's files."""
        if (self.dir / "vault.json").exists() or self.dir.name != APP_SLUG:
            return
        for slug in LEGACY_SLUGS:
            old = self.dir.parent / slug
            if (old / "vault.json").exists():
                for name in ("vault.json", "settings.json"):
                    if (old / name).exists():
                        shutil.copy2(old / name, self.dir / name)
                log.info("Imported data from the previous app folder")
                return

    # ---- reading
    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CorruptVaultError(
                f"The vault file could not be read ({type(exc).__name__}). "
                f"A backup may exist at {self.bak.name}.") from exc
        if not isinstance(data, dict):
            raise CorruptVaultError("The vault file has an unexpected structure.")
        return data

    def is_new(self) -> bool:
        try:
            return not self.read().get("vault_blob")
        except VaultError:
            return False  # a damaged file is not a new vault; don't offer to overwrite it

    def load_header(self) -> VaultHeader:
        data = self.read()
        try:
            salt = base64.b64decode(str(data.get("salt", "")), validate=True)
        except (ValueError, TypeError) as exc:
            raise CorruptVaultError("The vault file has an invalid salt.") from exc
        if len(salt) < 8:
            raise CorruptVaultError("The vault file has an invalid salt.")
        try:
            schema = int(data.get("schema", 1))
        except (TypeError, ValueError) as exc:
            raise CorruptVaultError("The vault file has an invalid schema version.") from exc
        if schema > SCHEMA_VERSION:
            raise CorruptVaultError("This vault was created by a newer version of Kestrel Vault.")
        return VaultHeader(salt, normalize_kdf(data.get("kdf")), data)

    def new_header(self) -> VaultHeader:
        from .crypto import KDF_PARAMS
        return VaultHeader(os.urandom(16), dict(KDF_PARAMS), {})

    @staticmethod
    def open_with_key(header: VaultHeader, key: bytes) -> tuple[list[Entry], bool]:
        """Decrypt with `key`. Returns (entries, needs_migration). Never writes."""
        data = header.data
        canary = data.get("canary")
        if canary:
            res = decrypt_dict(key, canary)
            if not isinstance(res, dict) or res.get("status") != "verified":
                raise WrongPasswordError("Incorrect master password.")
        blob = data.get("vault_blob")
        obj = decrypt_dict(key, blob) if blob else {}
        if not isinstance(obj, dict):
            raise CorruptVaultError("The vault data has an unexpected structure.")
        migrated = int(data.get("schema", 1)) < SCHEMA_VERSION
        return entries_from_blob(obj), migrated

    # ---- writing
    def write(self, data: dict[str, Any], keep_backup: bool = True) -> None:
        if keep_backup and self.path.exists():
            try:
                shutil.copy2(self.path, self.bak)
            except OSError as exc:
                log.warning("Could not refresh backup: %s", type(exc).__name__)
        atomic_write(self.path, json.dumps(data, indent=1))

    def backup_legacy(self) -> None:
        if self.path.exists() and not self.legacy_bak.exists():
            shutil.copy2(self.path, self.legacy_bak)

    def snapshot_before_import(self) -> Path | None:
        if not self.path.exists():
            return None
        dest = self.dir / f"vault.pre-import-{datetime.now():%Y%m%d-%H%M%S}.json"
        shutil.copy2(self.path, dest)
        return dest

    def export_to(self, dest: str | Path) -> None:
        shutil.copy2(self.path, dest)

    def wipe(self) -> None:
        """Remove the vault and every backup this app created. User-chosen exports remain."""
        targets = [self.path, self.bak, self.legacy_bak, self.lockout_path]
        targets += list(self.dir.glob("vault.pre-import-*.json"))
        for p in targets:
            try:
                p.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- lockout
class LockoutStore:
    """Persists failed unlock attempts so restarting the app does not reset the delay."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def delay_for(failed: int) -> int:
        return 0 if failed < 3 else min(300, 5 * 2 ** (failed - 3))

    def load(self) -> tuple[int, float]:
        d = load_json(self.path, {})
        try:
            return max(0, int(d.get("failed", 0))), float(d.get("until", 0.0))
        except (TypeError, ValueError):
            return 0, 0.0

    def remaining(self) -> int:
        _, until = self.load()
        return max(0, int(until - time.time() + 0.999))

    def record_failure(self) -> tuple[int, int]:
        failed, _ = self.load()
        failed += 1
        delay = self.delay_for(failed)
        self._save(failed, time.time() + delay if delay else 0.0)
        return failed, delay

    def reset(self) -> None:
        if self.path.exists():
            self._save(0, 0.0)

    def _save(self, failed: int, until: float) -> None:
        try:
            atomic_write(self.path, json.dumps({"failed": failed, "until": until}))
        except OSError as exc:
            log.warning("Could not persist lockout: %s", type(exc).__name__)


# ---------------------------------------------------------------- settings
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme_name": "Dark", "accent": "#6366f1",
    "auto_lock_minutes": 10, "lock_on_minimize": True, "lock_on_blur_seconds": 0,
    "clipboard_clear_seconds": 30, "reduced_motion": False,
    "sort_mode": "Name A→Z", "sidebar_visible": True,
    "window_geometry": "", "splitter_outer": "", "splitter_inner": "",
    "gen_mode": "Random", "gen_length": 20, "gen_upper": True, "gen_lower": True,
    "gen_digits": True, "gen_symbols": True, "gen_ambiguous": False,
    "gen_words": 5, "gen_sep": "-", "gen_capitalize": True, "gen_number": False,
}


def load_settings(path: Path, themes: list[str] | None = None) -> dict[str, Any]:
    s = load_json(path, DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    # v2.2 stored "never" as None, which then failed to round-trip.
    if s.get("clipboard_clear_seconds") is None:
        s["clipboard_clear_seconds"] = 0
    if "generator_length" in s:  # old key
        s["gen_length"] = int(s.pop("generator_length") or s["gen_length"])
    if themes and s.get("theme_name") not in themes:
        s["theme_name"] = "Dark"
    return s


def save_settings(path: Path, data: dict[str, Any]) -> None:
    try:
        atomic_write(path, json.dumps(data, indent=2))
    except OSError as exc:
        log.warning("Could not save settings: %s", type(exc).__name__)
