"""Entry model, legacy migration and vault health analysis. No Qt imports."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from .passwords import Strength, estimate

HISTORY_LIMIT = 20
TRASH_DAYS = 30
OLD_PASSWORD_DAYS = 365


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Entry:
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    totp: str = ""
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    created: str = field(default_factory=now_iso)
    modified: str = field(default_factory=now_iso)
    pw_changed: str | None = None
    last_used: str | None = None
    deleted_at: str | None = None
    _strength_cache: Any = field(default=None, repr=False, compare=False)

    # ---- derived
    @property
    def strength(self) -> Strength:
        cached = self._strength_cache
        if cached is not None and cached[0] == self.password:
            return cached[1]
        s = estimate(self.password)
        self._strength_cache = (self.password, s)
        return s

    @property
    def host(self) -> str:
        if not self.url:
            return ""
        url = self.url if "://" in self.url else "https://" + self.url
        try:
            return urlparse(url).hostname or ""
        except ValueError:
            return ""

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def haystack(self) -> str:
        return " ".join([self.name, self.username, self.url, self.notes, " ".join(self.tags)]).lower()

    def password_fingerprint(self) -> str:
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest()

    # ---- mutation helpers
    def set_password(self, new: str) -> None:
        if new == self.password:
            return
        if self.password:
            self.history.append({"password": self.password, "modified": now_iso()})
            self.history = self.history[-HISTORY_LIMIT:]
        self.password = new
        self.pw_changed = now_iso()

    def touch(self) -> None:
        self.modified = now_iso()

    # ---- serialisation
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "username": self.username,
            "password": self.password, "url": self.url, "notes": self.notes,
            "totp": self.totp, "tags": list(self.tags), "favorite": self.favorite,
            "history": list(self.history), "created": self.created,
            "modified": self.modified, "pw_changed": self.pw_changed,
            "last_used": self.last_used, "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], name: str | None = None) -> "Entry":
        def s(key: str) -> str:
            v = d.get(key)
            return v if isinstance(v, str) else ""

        def opt(key: str) -> str | None:
            v = d.get(key)
            return v if isinstance(v, str) and v else None

        tags = [str(t).strip() for t in (d.get("tags") or []) if str(t).strip()]
        history = [h for h in (d.get("history") or [])
                   if isinstance(h, dict) and isinstance(h.get("password"), str)]
        created = opt("created") or opt("modified") or now_iso()
        entry_id = d.get("id") if isinstance(d.get("id"), str) and d.get("id") else uuid.uuid4().hex
        return cls(
            name=s("name") or name or "Untitled", id=entry_id,
            username=s("username"), password=s("password"), url=s("url"),
            notes=s("notes"), totp=s("totp"), tags=tags,
            favorite=bool(d.get("favorite", False)), history=history[-HISTORY_LIMIT:],
            created=created, modified=opt("modified") or created,
            pw_changed=opt("pw_changed"), last_used=opt("last_used"),
            deleted_at=opt("deleted_at"),
        )


# ---------------------------------------------------------------- blob format
def entries_from_blob(obj: dict[str, Any]) -> list[Entry]:
    """Understand both the v3 format ({"entries": [...]}) and the legacy
    v2 format (a dict keyed by entry name)."""
    if isinstance(obj.get("entries"), list):
        return [Entry.from_dict(e) for e in obj["entries"] if isinstance(e, dict)]
    return [Entry.from_dict(e, name=n) for n, e in obj.items() if isinstance(e, dict)]


def entries_to_blob(entries: list[Entry] | dict[str, Entry]) -> dict[str, Any]:
    items = entries.values() if isinstance(entries, dict) else entries
    return {"entries": [e.to_dict() for e in items]}


def expired_trash(entries: list[Entry], days: int = TRASH_DAYS) -> list[Entry]:
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for e in entries:
        when = _parse_dt(e.deleted_at)
        if when is not None and when < cutoff:
            out.append(e)
    return out


# ---------------------------------------------------------------- health
@dataclass
class HealthReport:
    total: int = 0
    weak: list[Entry] = field(default_factory=list)
    reused: list[Entry] = field(default_factory=list)
    old: list[Entry] = field(default_factory=list)

    @property
    def flagged(self) -> int:
        return len({e.id for e in self.weak + self.reused + self.old})

    @property
    def score(self) -> int:
        return 100 if self.total == 0 else round(100 * (self.total - self.flagged) / self.total)


def health_report(entries: list[Entry]) -> HealthReport:
    live = [e for e in entries if not e.is_deleted and e.password]
    report = HealthReport(total=len(live))
    counts: dict[str, int] = {}
    for e in live:
        fp = e.password_fingerprint()
        counts[fp] = counts.get(fp, 0) + 1
    cutoff = datetime.now() - timedelta(days=OLD_PASSWORD_DAYS)
    for e in live:
        if e.strength.score <= 1:
            report.weak.append(e)
        if counts[e.password_fingerprint()] > 1:
            report.reused.append(e)
        changed = _parse_dt(e.pw_changed) or _parse_dt(e.created)
        if changed is not None and changed < cutoff:
            report.old.append(e)
    return report
