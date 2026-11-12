"""The unlocked vault: in-memory entries plus debounced, atomic persistence."""
from __future__ import annotations

import gc
import logging

from PySide6.QtCore import QObject, Signal

from .crypto import KDF_PARAMS
from .models import Entry, expired_trash, now_iso
from .services import Debouncer
from .storage import VaultFile, VaultHeader, serialize

log = logging.getLogger(__name__)


class VaultSession(QObject):
    changed = Signal()
    persistFailed = Signal(str)

    def __init__(self, store: VaultFile) -> None:
        super().__init__()
        self.store = store
        self.key: bytes | None = None
        self.header: VaultHeader | None = None
        self.entries: dict[str, Entry] = {}
        self._undo: list[str] = []
        self._saver = Debouncer(self, 600, self.persist_now)

    # ---- lifecycle
    @property
    def unlocked(self) -> bool:
        return self.key is not None

    def create(self, key: bytes, header: VaultHeader) -> None:
        """Create a brand-new vault in one atomic write. Raises OSError on failure."""
        self.store.write(serialize(key, header.salt_b64, header.kdf, []), keep_backup=False)
        self.key, self.header, self.entries, self._undo = key, header, {}, []

    def open(self, key: bytes, header: VaultHeader, entries: list[Entry], migrated: bool) -> None:
        self.key, self.header = key, header
        self.entries = {e.id: e for e in entries}
        self._undo = []
        for e in expired_trash(list(self.entries.values())):
            del self.entries[e.id]
        if migrated:
            try:
                self.store.backup_legacy()
            except OSError as exc:
                log.warning("Could not back up legacy vault: %s", type(exc).__name__)
            self.persist_now()

    def lock(self) -> None:
        self._saver.flush()
        self.key = None
        self.header = None
        self.entries = {}
        self._undo = []
        gc.collect()

    # ---- persistence
    def persist_now(self) -> bool:
        self._saver.cancel()
        if not self.unlocked or self.header is None:
            return False
        try:
            self.store.write(serialize(self.key, self.header.salt_b64, self.header.kdf, self.entries))
            return True
        except OSError as exc:
            log.error("Saving failed: %s", type(exc).__name__)
            self.persistFailed.emit("Could not save the vault. Check disk space and permissions.")
            return False

    def persist_soon(self) -> None:
        self._saver.trigger()

    def change_master(self, new_key: bytes, new_salt_b64: str) -> None:
        """Re-encrypt under a new key. The file is only replaced if the write succeeds."""
        assert self.header is not None
        import base64
        data = serialize(new_key, new_salt_b64, dict(KDF_PARAMS), self.entries)
        self.store.write(data)
        self.key = new_key
        self.header = VaultHeader(base64.b64decode(new_salt_b64), dict(KDF_PARAMS), data)

    # ---- queries
    def get(self, entry_id: str | None) -> Entry | None:
        return self.entries.get(entry_id) if entry_id else None

    def live(self) -> list[Entry]:
        return [e for e in self.entries.values() if not e.is_deleted]

    def trashed(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.is_deleted]

    def all_tags(self) -> list[str]:
        tags = {t for e in self.live() for t in e.tags}
        return sorted(tags, key=str.lower)

    # ---- mutations
    def add(self, entry: Entry) -> None:
        entry.pw_changed = entry.pw_changed or now_iso()
        self.entries[entry.id] = entry
        self._after_change()

    def add_many(self, entries: list[Entry]) -> None:
        for e in entries:
            e.pw_changed = e.pw_changed or None
            self.entries[e.id] = e
        self._after_change()

    def update(self, entry: Entry) -> None:
        entry.touch()
        self._after_change()

    def set_favorite(self, entry_id: str, value: bool) -> None:
        e = self.get(entry_id)
        if e:
            e.favorite = value
            self._after_change()

    def mark_used(self, entry_id: str) -> None:
        e = self.get(entry_id)
        if e:
            e.last_used = now_iso()
            self.persist_soon()  # no `changed`: copying must not reshuffle the list

    def soft_delete(self, entry_id: str) -> None:
        e = self.get(entry_id)
        if e and not e.is_deleted:
            e.deleted_at = now_iso()
            self._undo.append(entry_id)
            self._after_change()

    def can_undo(self) -> bool:
        return any(self.entries.get(i) and self.entries[i].is_deleted for i in self._undo)

    def undo_delete(self) -> Entry | None:
        while self._undo:
            e = self.entries.get(self._undo.pop())
            if e and e.is_deleted:
                e.deleted_at = None
                self._after_change()
                return e
        return None

    def restore(self, entry_id: str) -> None:
        e = self.get(entry_id)
        if e and e.is_deleted:
            e.deleted_at = None
            self._after_change()

    def purge(self, entry_id: str) -> None:
        if self.entries.pop(entry_id, None) is not None:
            self._after_change()

    def empty_trash(self) -> int:
        ids = [e.id for e in self.trashed()]
        for i in ids:
            del self.entries[i]
        if ids:
            self._after_change()
        return len(ids)

    def _after_change(self) -> None:
        self.persist_soon()
        self.changed.emit()
