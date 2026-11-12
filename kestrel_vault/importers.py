"""CSV import (Bitwarden, 1Password, Chrome/Edge/Firefox style exports)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .models import Entry

_ALIASES = {
    "name": ("name", "title"),
    "url": ("url", "login_uri", "website", "web site", "origin"),
    "username": ("username", "login_username", "login", "user", "login name"),
    "password": ("password", "login_password"),
    "notes": ("notes", "note", "extra"),
    "totp": ("login_totp", "totp", "otpauth", "one-time password"),
    "tags": ("folder", "grouping", "tags"),
}


@dataclass
class ImportResult:
    entries: list[Entry] = field(default_factory=list)
    skipped: int = 0
    duplicates: int = 0


def _pick(row: dict[str, str], key: str) -> str:
    for alias in _ALIASES[key]:
        v = row.get(alias)
        if v:
            return v.strip()
    return ""


def parse_csv(text: str, existing: list[Entry] | None = None) -> ImportResult:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty or has no header row.")
    headers = {h.strip().lower() for h in reader.fieldnames if h}
    if not headers & set(_ALIASES["password"]):
        raise ValueError("No password column found. Expected a Bitwarden, 1Password or browser export.")

    seen = {(e.name, e.username, e.url, e.password) for e in (existing or [])}
    result = ImportResult()
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        kind = row.get("type", "").strip().lower()
        if kind and kind not in ("login", "password"):
            result.skipped += 1
            continue
        password, username = _pick(row, "password"), _pick(row, "username")
        if not password and not username:
            result.skipped += 1
            continue
        entry = Entry(name="", username=username, password=password,
                      url=_pick(row, "url"), notes=_pick(row, "notes"), totp=_pick(row, "totp"))
        entry.name = _pick(row, "name") or entry.host or username or "Imported"
        folder = _pick(row, "tags")
        if folder:
            entry.tags = [t.strip() for t in folder.split(",") if t.strip()]
        entry.pw_changed = None
        key = (entry.name, entry.username, entry.url, entry.password)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)
        result.entries.append(entry)
    return result
