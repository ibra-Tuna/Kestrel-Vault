"""Key derivation, AES-256-GCM envelope encryption, recovery keys and TOTP. No Qt imports."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KDF_PARAMS: dict[str, Any] = {"type": "argon2id", "t": 3, "m": 262144, "p": 4}
_KDF_LIMITS = {"t": (1, 10), "m": (8192, 524288), "p": (1, 8)}

_GCM_VERSION = 0x01
_GCM_NONCE_LEN = 12
_GCM_MIN_LEN = 1 + _GCM_NONCE_LEN + 16  # version + nonce + 128-bit GCM tag
_AAD_VAULT = b"kestrel-vault:v4:vault"
_AAD_DEK = b"kestrel-vault:v4:dek"
_AAD_RECOVERY = b"kestrel-vault:v4:recovery-dek"
_RECOVERY_PREFIX = "KVLT"
_RECOVERY_BYTES = 20  # 160 bits -> 32 Base32 chars -> KVLT + 8 groups of 4


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


def derive_raw_key(password: str, salt: bytes, params: dict[str, Any] | None = None) -> bytes:
    """Derive a raw 32-byte key using Argon2id."""
    from argon2 import low_level  # imported lazily so the core is testable without it

    p = normalize_kdf(params)
    return low_level.hash_secret_raw(
        secret=password.encode("utf-8"), salt=salt,
        time_cost=p["t"], memory_cost=p["m"], parallelism=p["p"],
        hash_len=32, type=low_level.Type.ID,
    )


def derive_key(password: str, salt: bytes, params: dict[str, Any] | None = None) -> bytes:
    """Derive a urlsafe-base64 32-byte key (Fernet-compatible representation)."""
    return base64.urlsafe_b64encode(derive_raw_key(password, salt, params))


def fernet_key_from_raw(raw_key: bytes) -> bytes:
    """Encode a raw 32-byte key into the urlsafe-base64 format expected by Fernet."""
    return base64.urlsafe_b64encode(raw_key)


def generate_dek() -> bytes:
    """Generate a random 256-bit Data Encryption Key."""
    return os.urandom(32)


# ---------------------------------------------------------------- AES-256-GCM & envelope encryption
def _is_gcm_shaped(blob: str) -> bytes | None:
    if not isinstance(blob, str) or not blob:
        return None
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    except (ValueError, TypeError):
        return None
    if len(raw) < _GCM_MIN_LEN or raw[0] != _GCM_VERSION:
        return None
    return raw


def encrypt_gcm(key: bytes, data: Any, aad: bytes = _AAD_VAULT) -> str:
    """Serialize `data` as compact JSON and encrypt with AES-256-GCM."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key.")
    nonce = os.urandom(_GCM_NONCE_LEN)
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return base64.urlsafe_b64encode(bytes([_GCM_VERSION]) + nonce + ct).decode("ascii")


def decrypt_gcm(key: bytes, blob: str, aad: bytes = _AAD_VAULT) -> Any:
    """Decrypt an AES-256-GCM JSON payload, distinguishing corruption from bad keys."""
    if not blob:
        return {}
    raw = _is_gcm_shaped(blob)
    if raw is None:
        raise CorruptVaultError("The vault data is malformed.")
    nonce = raw[1:1 + _GCM_NONCE_LEN]
    ct = raw[1 + _GCM_NONCE_LEN:]
    try:
        pt = AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise WrongPasswordError("Incorrect master password.") from exc
    except (ValueError, TypeError) as exc:
        raise CorruptVaultError("The vault data is malformed.") from exc
    try:
        return json.loads(pt.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CorruptVaultError("The vault data is malformed.") from exc


def wrap_key(kek: bytes, dek: bytes, aad: bytes = _AAD_DEK) -> str:
    """Encrypt a 32-byte DEK under a 32-byte KEK using AES-256-GCM."""
    if len(kek) != 32 or len(dek) != 32:
        raise ValueError("Both KEK and DEK must be 32 bytes.")
    nonce = os.urandom(_GCM_NONCE_LEN)
    ct = AESGCM(kek).encrypt(nonce, dek, aad)
    return base64.urlsafe_b64encode(bytes([_GCM_VERSION]) + nonce + ct).decode("ascii")


def unwrap_key(kek: bytes, blob: str, aad: bytes = _AAD_DEK) -> bytes:
    """Decrypt a wrapped DEK. Raises WrongPasswordError if authentication fails."""
    raw = _is_gcm_shaped(blob)
    if raw is None:
        raise CorruptVaultError("The vault key envelope is malformed.")
    nonce = raw[1:1 + _GCM_NONCE_LEN]
    ct = raw[1 + _GCM_NONCE_LEN:]
    try:
        dek = AESGCM(kek).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise WrongPasswordError("Incorrect master password.") from exc
    except (ValueError, TypeError) as exc:
        raise CorruptVaultError("The vault key envelope is malformed.") from exc
    if len(dek) != 32:
        raise CorruptVaultError("The unwrapped vault key has an unexpected length.")
    return dek


# ---------------------------------------------------------------- recovery kit
def generate_recovery_key() -> str:
    """Generate a high-entropy 160-bit human-readable recovery code."""
    raw = os.urandom(_RECOVERY_BYTES)
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=")
    groups = [b32[i:i + 4] for i in range(0, len(b32), 4)]
    return f"{_RECOVERY_PREFIX}-" + "-".join(groups)


def normalize_recovery_key(code: str) -> bytes:
    """Parse a formatted recovery key back into raw bytes. Raises ValueError on bad format."""
    cleaned = re.sub(r"[\s-]", "", (code or "").strip()).upper()
    if cleaned.startswith(_RECOVERY_PREFIX):
        cleaned = cleaned[len(_RECOVERY_PREFIX):]
    if not cleaned or not re.fullmatch(r"[A-Z2-7]+", cleaned):
        raise ValueError("Recovery key format is invalid.")
    pad = "=" * ((8 - len(cleaned) % 8) % 8)
    raw = base64.b32decode(cleaned + pad)
    if len(raw) != _RECOVERY_BYTES:
        raise ValueError("Recovery key length is invalid.")
    return raw


def derive_recovery_kek(code: str, salt: bytes) -> bytes:
    """Derive a 32-byte KEK from the 160-bit recovery key via HKDF-SHA256."""
    raw = normalize_recovery_key(code)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"kestrel-vault:v4:recovery-kek",
    )
    return hkdf.derive(raw)


def wrap_dek_with_recovery(code: str, dek: bytes, salt: bytes) -> str:
    rkek = derive_recovery_kek(code, salt)
    return wrap_key(rkek, dek, aad=_AAD_RECOVERY)


def unwrap_dek_with_recovery(code: str, wrapped_dek: str, salt: bytes) -> bytes:
    try:
        rkek = derive_recovery_kek(code, salt)
    except ValueError as exc:
        raise WrongPasswordError("Invalid recovery key.") from exc
    try:
        return unwrap_key(rkek, wrapped_dek, aad=_AAD_RECOVERY)
    except WrongPasswordError as exc:
        raise WrongPasswordError("Invalid recovery key.") from exc


# ---------------------------------------------------------------- legacy Fernet (v1-v3)
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
