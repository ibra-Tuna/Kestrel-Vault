"""Key derivation, vault encryption and TOTP. No Qt imports, fully testable."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet, InvalidToken

KDF_PARAMS: dict[str, Any] = {"type": "argon2id", "t": 3, "m": 65536, "p": 4}
_KDF_LIMITS = {"t": (1, 10), "m": (8192, 262144), "p": (1, 8)}


class VaultError(Exception):
    """Base class for vault problems that should be shown to the user."""


class WrongPasswordError(VaultError):
    """The derived key could not decrypt the vault."""


class CorruptVaultError(VaultError):
    """The vault file is unreadable, malformed or from a newer version."""


# ---------------------------------------------------------------- KDF
def normalize_kdf(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return sane Argon2id parameters, clamping anything read from disk."""
    out = dict(KDF_PARAMS)
    if isinstance(params, dict):
        for key, (lo, hi) in _KDF_LIMITS.items():
            try:
                out[key] = min(max(int(params.get(key, out[key])), lo), hi)
            except (TypeError, ValueError):
                pass
    return out


def derive_key(password: str, salt: bytes, params: dict[str, Any] | None = None) -> bytes:
    from argon2 import low_level  # imported lazily so the core is testable without it

    p = normalize_kdf(params)
    raw = low_level.hash_secret_raw(
        secret=password.encode("utf-8"), salt=salt,
        time_cost=p["t"], memory_cost=p["m"], parallelism=p["p"],
        hash_len=32, type=low_level.Type.ID,
    )
    return base64.urlsafe_b64encode(raw)


# ---------------------------------------------------------------- encryption
def encrypt_dict(key: bytes, data: Any) -> str:
    return Fernet(key).encrypt(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _is_fernet_shaped(blob: str) -> bool:
    """Fernet raises InvalidToken for both a wrong key and malformed data.
    Checking the envelope first lets us tell damage apart from a bad password."""
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return len(raw) >= 73 and raw[0] == 0x80


def decrypt_dict(key: bytes, blob: str) -> Any:
    if not blob:
        return {}
    if not isinstance(blob, str) or not _is_fernet_shaped(blob):
        raise CorruptVaultError("The vault data is malformed.")
    try:
        raw = Fernet(key).decrypt(blob.encode("ascii"))
    except InvalidToken as exc:
        raise WrongPasswordError("Incorrect master password.") from exc
    except (ValueError, TypeError) as exc:
        raise CorruptVaultError("The vault data is malformed.") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise CorruptVaultError("The vault data is malformed.") from exc


# ---------------------------------------------------------------- TOTP
@dataclass(frozen=True)
class TotpParams:
    secret: bytes
    period: int = 30
    digits: int = 6
    algorithm: str = "sha1"


_ALGOS = {"sha1", "sha256", "sha512"}


def parse_totp(value: str) -> TotpParams | None:
    """Accept a bare Base32 secret or an otpauth:// URI. Returns None if invalid."""
    v = (value or "").strip()
    if not v:
        return None
    period, digits, algo = 30, 6, "sha1"
    if v.lower().startswith("otpauth://"):
        try:
            q = parse_qs(urlparse(v).query)
            v = q.get("secret", [""])[0]
            period = int(q.get("period", ["30"])[0])
            digits = int(q.get("digits", ["6"])[0])
            algo = q.get("algorithm", ["SHA1"])[0].lower()
        except (ValueError, IndexError):
            return None
    cleaned = re.sub(r"[\s-]", "", v).upper().rstrip("=")
    if not cleaned or not re.fullmatch(r"[A-Z2-7]+", cleaned):
        return None
    try:
        raw = base64.b32decode(cleaned + "=" * ((8 - len(cleaned) % 8) % 8))
    except (ValueError, TypeError):
        return None
    if algo not in _ALGOS or not (5 <= period <= 300) or digits not in (6, 7, 8):
        return None
    return TotpParams(raw, period, digits, algo)


def generate_totp(params: TotpParams, at: float | None = None) -> str:
    t = int(time.time() if at is None else at)
    mac = hmac.new(params.secret, struct.pack(">Q", t // params.period),
                   getattr(hashlib, params.algorithm)).digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** params.digits)
    return f"{num:0{params.digits}d}"


def totp_code(value: str, at: float | None = None) -> str:
    p = parse_totp(value)
    return generate_totp(p, at) if p else ""


def totp_remaining(period: int = 30, at: float | None = None) -> int:
    t = int(time.time() if at is None else at)
    return period - (t % period)


def is_valid_totp(value: str) -> bool:
    return parse_totp(value) is not None
