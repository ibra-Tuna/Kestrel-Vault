"""On-disk storage: atomic writes, backups, portable mode, lockout and settings. No Qt imports."""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import APP_SLUG, LEGACY_SLUGS
from .crypto import (KDF_PARAMS, CorruptVaultError, VaultError, WrongPasswordError, decrypt_dict,
                     decrypt_gcm, encrypt_gcm, fernet_key_from_raw, generate_dek, normalize_kdf,
                     unwrap_dek_with_recovery, unwrap_key, wrap_key)
from .models import Entry, entries_from_blob, entries_to_blob

SCHEMA_VERSION = 4
CIPHER_V4 = "aes-256-gcm"
log = logging.getLogger(__name__)

_DATA_DIR_OVERRIDE: Path | None = None


def set_data_dir(path: Path | str | None) -> None:
    """Override the storage directory for the current process (CLI/portable mode)."""
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = Path(path).expanduser().resolve() if path else None


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable_mode() -> bool:
    if _DATA_DIR_OVERRIDE is not None or os.environ.get("KESTREL_VAULT_DATA_DIR"):
        return True
    base = _app_base_dir()
    if (base / "portable.flag").is_file():
        return True
    return bool(getattr(sys, "frozen", False) and (base / "vault.json").is_file())


def data_dir() -> Path:
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    env_dir = os.environ.get("KESTREL_VAULT_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    base_app = _app_base_dir()
    if (base_app / "portable.flag").is_file():
        return base_app / "KestrelVaultData"
    if getattr(sys, "frozen", False) and (base_app / "vault.json").is_file():
        return base_app
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:  # Linux and macOS
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_SLUG


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file, fsync, then rename so a crash never leaves a half file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # FIX: antivirus / indexers can briefly hold the destination on Windows.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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
    data: dict[str, Any] = field(default_factory=dict)
    schema: int = SCHEMA_VERSION
    cipher: str = CIPHER_V4

    @property
    def salt_b64(self) -> str:
        return base64.b64encode(self.salt).decode("ascii")

    @property
    def has_recovery(self) -> bool:
        return bool(self.data.get("recovery_salt") and self.data.get("recovery_wrapped_dek"))


def serialize(kek: bytes, dek: bytes, salt_b64: str, kdf: dict[str, Any], entries: Any,
              recovery_salt_b64: str | None = None,
              recovery_wrapped_dek: str | None = None) -> dict[str, Any]:
    """Serialize the vault in v4 AES-256-GCM envelope format."""
    out: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "cipher": CIPHER_V4,
        "salt": salt_b64,
        "kdf": dict(kdf),
        "wrapped_dek": wrap_key(kek, dek),
        "vault_blob": encrypt_gcm(dek, entries_to_blob(entries)),
    }
    if recovery_salt_b64 and recovery_wrapped_dek:
        out["recovery_salt"] = recovery_salt_b64
        out["recovery_wrapped_dek"] = recovery_wrapped_dek
    return out


def rewrap_vault(existing: dict[str, Any], new_kek: bytes, new_salt_b64: str,
                 new_kdf: dict[str, Any], dek: bytes) -> dict[str, Any]:
    """Update KEK/salt/KDF and re-wrap the existing DEK without re-encrypting vault_blob."""
    out = dict(existing)
    out["schema"] = SCHEMA_VERSION
    out["cipher"] = CIPHER_V4
    out["salt"] = new_salt_b64
    out["kdf"] = dict(new_kdf)
    out["wrapped_dek"] = wrap_key(new_kek, dek)
    out.pop("canary", None)
    return out


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
    if schema < 1 or schema > SCHEMA_VERSION:
        raise ValueError("The backup was made by a newer version of Kestrel Vault.")
    if schema >= 4 and (not isinstance(data.get("wrapped_dek"), str) or not data["wrapped_dek"]):
        raise ValueError("The backup is missing its key envelope.")
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
            return False

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
        if schema < 1 or schema > SCHEMA_VERSION:
            raise CorruptVaultError("This vault was created by a newer version of Kestrel Vault.")
        cipher = str(data.get("cipher") or ("fernet" if schema < 4 else CIPHER_V4))
        return VaultHeader(salt, normalize_kdf(data.get("kdf")), data, schema=schema, cipher=cipher)

    def new_header(self) -> VaultHeader:
        return VaultHeader(os.urandom(16), dict(KDF_PARAMS), {}, schema=SCHEMA_VERSION, cipher=CIPHER_V4)

    @staticmethod
    def open_with_key(header: VaultHeader, raw_key: bytes) -> tuple[list[Entry], bytes, bool]:
        """Decrypt with raw 32-byte `raw_key`. Returns (entries, dek, needs_migration). Never writes."""
        data = header.data
        if header.schema < 4:
            fkey = fernet_key_from_raw(raw_key)
            canary = data.get("canary")
            if canary:
                res = decrypt_dict(fkey, canary)
                if not isinstance(res, dict) or res.get("status") != "verified":
                    raise WrongPasswordError("Incorrect master password.")
            blob = data.get("vault_blob")
            obj = decrypt_dict(fkey, blob) if blob else {}
            if not isinstance(obj, dict):
                raise CorruptVaultError("The vault data has an unexpected structure.")
            return entries_from_blob(obj), generate_dek(), True

        wrapped = data.get("wrapped_dek")
        if not isinstance(wrapped, str) or not wrapped:
            raise CorruptVaultError("The vault file is missing its encrypted key.")
        dek = unwrap_key(raw_key, wrapped)
        blob = data.get("vault_blob")
        obj = decrypt_gcm(dek, blob) if blob else {}
        if not isinstance(obj, dict):
            raise CorruptVaultError("The vault data has an unexpected structure.")
        needs_kdf_upgrade = header.kdf != KDF_PARAMS
        migrated = header.schema < SCHEMA_VERSION or needs_kdf_upgrade
        return entries_from_blob(obj), dek, migrated

    @staticmethod
    def open_with_recovery(header: VaultHeader, recovery_code: str) -> tuple[list[Entry], bytes]:
        """Unlock a v4 vault using its offline recovery key."""
        data = header.data
        rsalt_b64 = data.get("recovery_salt")
        rwrapped = data.get("recovery_wrapped_dek")
        if not isinstance(rsalt_b64, str) or not isinstance(rwrapped, str):
            raise CorruptVaultError("This vault does not have a recovery key configured.")
        try:
            rsalt = base64.b64decode(rsalt_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise CorruptVaultError("The recovery salt is malformed.") from exc
        dek = unwrap_dek_with_recovery(recovery_code, rwrapped, rsalt)
        blob = data.get("vault_blob")
        obj = decrypt_gcm(dek, blob) if blob else {}
        if not isinstance(obj, dict):
            raise CorruptVaultError("The vault data has an unexpected structure.")
        return entries_from_blob(obj), dek

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
        dest_p = Path(dest)
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dest_p, self.path.read_text(encoding="utf-8"))

    def wipe(self) -> None:
        """Remove the vault and every backup this app created. User exports remain."""
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
    "theme_name": "Dark", "accent": "#4F8EF7",
    "auto_lock_minutes": 10, "lock_on_blur_seconds": 0,
    "clipboard_clear_seconds": 30, "reduced_motion": False,
    "sort_mode": "Name A→Z", "sidebar_visible": True,
    "window_geometry": "", "splitter_outer": "", "splitter_inner": "",
    "gen_mode": "Random", "gen_length": 20, "gen_upper": True, "gen_lower": True,
    "gen_digits": True, "gen_symbols": True, "gen_ambiguous": False,
    "gen_words": 5, "gen_sep": "-", "gen_capitalize": True, "gen_number": False,
    "last_backup_at": "", "backup_reminder_dismissed_at": "",
}


def load_settings(path: Path, themes: list[str] | None = None) -> dict[str, Any]:
    s = load_json(path, DEFAULT_SETTINGS)
    # Legacy "null" meant "never clear the clipboard"; handle it before the
    # type coercion below turns it into the default of 30 seconds.
    if s.get("clipboard_clear_seconds") is None:
        s["clipboard_clear_seconds"] = 0
    # FIX: coerce values to the type of their default so a hand-edited or
    # older-schema settings.json cannot break the UI (e.g. "10" vs 10 in a
    # QComboBox would silently select row 0 instead of "10 minutes").
    for k, default in DEFAULT_SETTINGS.items():
        if k not in s:
            s[k] = default
            continue
        v = s[k]
        if type(v) is type(default):
            continue
        try:
            if isinstance(default, bool):
                if isinstance(v, str):
                    s[k] = v.strip().lower() in ("1", "true", "yes", "on")
                else:
                    s[k] = bool(v)
            elif isinstance(default, int):
                s[k] = int(v)
            elif isinstance(default, str):
                s[k] = str(v)
        except (TypeError, ValueError):
            s[k] = default
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