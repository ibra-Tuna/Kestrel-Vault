"""The unlocked vault: in-memory entries plus debounced, atomic persistence."""
from __future__ import annotations

import base64
import gc
import hmac
import logging
import os

from PySide6.QtCore import QObject, Signal

from .crypto import KDF_PARAMS, generate_dek, wrap_dek_with_recovery
from .models import Entry, expired_trash, now_iso
from .security import wipe_bytes, wipe_entry
from .services import Debouncer
from .storage import VaultFile, VaultHeader, rewrap_vault, serialize

log = logging.getLogger(__name__)


class VaultSession(QObject):
    changed = Signal()
    persistFailed = Signal(str)

    def __init__(self, store: VaultFile) -> None:
        super().__init__()
        self.store = store
        self._kek: bytearray | None = None
        self._dek: bytearray | None = None
        self.header: VaultHeader | None = None
        self.recovery_salt_b64: str | None = None
        self.recovery_wrapped_dek: str | None = None
        self.entries: dict[str, Entry] = {}
        self._undo: list[str] = []
        self._saver = Debouncer(self, 600, self.persist_now)

    # ---- lifecycle
    @property
    def unlocked(self) -> bool:
        return self._kek is not None and self._dek is not None

    @property
    def has_recovery(self) -> bool:
        return bool(self.recovery_salt_b64 and self.recovery_wrapped_dek)

    def verify_kek(self, candidate: bytes) -> bool:
        if self._kek is None:
            return False
        return hmac.compare_digest(bytes(self._kek), candidate)

    def create(self, raw_kek: bytes, header: VaultHeader, recovery_code: str | None = None) -> None:
        """Create a brand-new v4 vault in one atomic write. Raises OSError on failure."""
        dek = generate_dek()
        rsalt_b64: str | None = None
        rwrapped: str | None = None
        if recovery_code:
            rsalt = os.urandom(16)
            rsalt_b64 = base64.b64encode(rsalt).decode("ascii")
            rwrapped = wrap_dek_with_recovery(recovery_code, dek, rsalt)
        data = serialize(raw_kek, dek, header.salt_b64, header.kdf, [], rsalt_b64, rwrapped)
        self.store.write(data, keep_backup=False)
        header.data = data
        self._kek = bytearray(raw_kek)
        self._dek = bytearray(dek)
        self.header = header
        self.recovery_salt_b64 = rsalt_b64
        self.recovery_wrapped_dek = rwrapped
        self.entries = {}
        self._undo = []

    def open(self, raw_kek: bytes, dek: bytes, header: VaultHeader,
             entries: list[Entry], migrated: bool) -> None:
        self._kek = bytearray(raw_kek)
        self._dek = bytearray(dek)
        self.header = header
        self.recovery_salt_b64 = header.data.get("recovery_salt") or None
        self.recovery_wrapped_dek = header.data.get("recovery_wrapped_dek") or None
        self.entries = {e.id: e for e in entries}
        self._undo = []

        # FIX: wipe + persist expired-trash purges (was: silent `del`, no save).
        purged = False
        for e in expired_trash(list(self.entries.values())):
            wipe_entry(self.entries.pop(e.id, None))
            purged = True

        if migrated:
            if header.schema < 4:
                try:
                    self.store.backup_legacy()
                except OSError as exc:
                    log.warning("Could not back up legacy vault: %s", type(exc).__name__)
            self.header = VaultHeader(
                header.salt, dict(KDF_PARAMS), header.data,
                schema=4, cipher="aes-256-gcm",
            )
            self.persist_now()
        elif purged:
            self.persist_now()

    def lock(self) -> None:
        self._saver.flush()
        wipe_bytes(self._kek)
        wipe_bytes(self._dek)
        self._kek = None
        self._dek = None
        self.header = None
        self.recovery_salt_b64 = None
        self.recovery_wrapped_dek = None
        for entry in self.entries.values():
            wipe_entry(entry)
        self.entries.clear()
        self._undo.clear()
        gc.collect()

    # ---- persistence
    def persist_now(self) -> bool:
        self._saver.cancel()
        if not self.unlocked or self.header is None:
            return False
        assert self._kek is not None and self._dek is not None
        try:
            data = serialize(
                bytes(self._kek), bytes(self._dek),
                self.header.salt_b64, self.header.kdf, self.entries,
                self.recovery_salt_b64, self.recovery_wrapped_dek,
            )
            self.store.write(data)
            self.header.data = data
            return True
        except OSError as exc:
            log.error("Saving failed: %s", type(exc).__name__)
            self.persistFailed.emit("Could not save the vault. Check disk space and permissions.")
            return False

    def persist_soon(self) -> None:
        self._saver.trigger()

    def change_master(self, new_raw_kek: bytes, new_salt_b64: str) -> None:
        """Re-wrap the DEK under a new master KEK without re-encrypting the vault payload."""
        assert self.header is not None and self._dek is not None
        dek_bytes = bytes(self._dek)
        if self.header.data.get("wrapped_dek") and self.header.data.get("vault_blob"):
            data = rewrap_vault(self.header.data, new_raw_kek, new_salt_b64, dict(KDF_PARAMS), dek_bytes)
        else:
            data = serialize(
                new_raw_kek, dek_bytes, new_salt_b64, dict(KDF_PARAMS), self.entries,
                self.recovery_salt_b64, self.recovery_wrapped_dek,
            )
        self.store.write(data)
        wipe_bytes(self._kek)
        self._kek = bytearray(new_raw_kek)
        self.header = VaultHeader(base64.b64decode(new_salt_b64), dict(KDF_PARAMS), data)

    def set_recovery_key(self, recovery_code: str) -> None:
        """Bind a new offline recovery key to the current DEK and persist."""
        assert self._dek is not None
        rsalt = os.urandom(16)
        self.recovery_salt_b64 = base64.b64encode(rsalt).decode("ascii")
        self.recovery_wrapped_dek = wrap_dek_with_recovery(recovery_code, bytes(self._dek), rsalt)
        self.persist_now()

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
        # FIX: dead `e.pw_changed = e.pw_changed or None` removed. Imported
        # entries keep pw_changed=None on purpose so health_report falls back
        # to `created` for staleness.
        for e in entries:
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
            # FIX: bound the undo stack so a long session cannot leak memory.
            if len(self._undo) > 100:
                self._undo = self._undo[-100:]
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
        entry = self.entries.pop(entry_id, None)
        if entry is not None:
            wipe_entry(entry)
            self._after_change()

    def empty_trash(self) -> int:
        ids = [e.id for e in self.trashed()]
        for i in ids:
            wipe_entry(self.entries.pop(i, None))
        if ids:
            self._after_change()
        return len(ids)

    def _after_change(self) -> None:
        self.persist_soon()
        self.changed.emit()