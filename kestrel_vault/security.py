"""Runtime security helpers: log redaction and best-effort memory wiping. No Qt imports."""
from __future__ import annotations

import ctypes
import logging
import re
from typing import Any


class SecretRedactingFilter(logging.Filter):
    """Scrub tokens, keys, passwords and otpauth URIs from every log record."""

    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        # Key=value / JSON-like secret fields
        (re.compile(
            r"(?i)\b(password|passwd|master_password|secret|token|dek|kek|recovery_key|wrapped_dek)"
            r"(\s*[:=]\s*)([^\s,;\"'\}]+|\"[^\"]*\"|'[^']*')"
        ), r"\1\2[REDACTED]"),
        # TOTP otpauth URIs
        (re.compile(r"otpauth://[^\s\"']+"), "otpauth://[REDACTED]"),
        # Recovery keys (KVLT-XXXX-...)
        (re.compile(r"\bKVLT(?:-[A-Z2-7]{4}){4,}\b", re.IGNORECASE), "KVLT-[REDACTED]"),
        # Fernet tokens
        (re.compile(r"\bgAAAAA[A-Za-z0-9_-]{20,}={0,2}\b"), "[REDACTED-TOKEN]"),
        # Long base64 / base64url / hex strings (>= 32 chars) likely to be keys or blobs
        (re.compile(r"\b[A-Za-z0-9+/_-]{36,}={0,2}\b"), "[REDACTED-BLOB]"),
    )

    @classmethod
    def sanitize(cls, text: str) -> str:
        out = text
        for pat, repl in cls._PATTERNS:
            out = pat.sub(repl, out)
        return out

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = self.sanitize(rendered)
        record.args = ()
        if record.exc_text:
            record.exc_text = self.sanitize(record.exc_text)
        return True


def wipe_bytes(buf: bytearray | None) -> None:
    """Overwrite a mutable bytearray with zeros via ctypes.memset."""
    if not buf:
        return
    n = len(buf)
    try:
        arr = (ctypes.c_char * n).from_buffer(buf)
        ctypes.memset(arr, 0, n)
    except (TypeError, ValueError):
        for i in range(n):
            buf[i] = 0


def wipe_entry(entry: Any) -> None:
    """Drop references and clear mutable fields on an Entry object on lock."""
    if entry is None:
        return
    try:
        entry.password = ""
        entry.username = ""
        entry.notes = ""
        entry.totp = ""
        entry.url = ""
        entry.name = ""
        if isinstance(entry.tags, list):
            entry.tags.clear()
        if isinstance(entry.history, list):
            for item in entry.history:
                if isinstance(item, dict):
                    item.clear()
            entry.history.clear()
        entry._strength_cache = None
    except Exception:
        pass
