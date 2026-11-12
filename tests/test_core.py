import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrel_vault import crypto, models, passwords, storage, importers  # noqa: E402
from kestrel_vault.models import Entry  # noqa: E402


# ------------------------------------------------------------ crypto / TOTP
def test_roundtrip_and_wrong_key():
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    blob = crypto.encrypt_dict(k1, {"a": 1})
    assert crypto.decrypt_dict(k1, blob) == {"a": 1}
    with pytest.raises(crypto.WrongPasswordError):
        crypto.decrypt_dict(k2, blob)


def test_garbage_blob_is_not_reported_as_wrong_password():
    with pytest.raises(crypto.CorruptVaultError):
        crypto.decrypt_dict(Fernet.generate_key(), "###not-base64###")


def test_kdf_params_are_clamped():
    p = crypto.normalize_kdf({"t": 9999, "m": 1, "p": "x"})
    assert p["t"] == 10 and p["m"] == 8192 and p["p"] == 4


def test_totp_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    uri = f"otpauth://totp/x?secret={secret}&digits=8"
    assert crypto.totp_code(uri, at=59) == "94287082"
    assert crypto.totp_code(uri, at=1111111109) == "07081804"
    assert crypto.totp_code(uri, at=20000000000) == "65353130"


def test_totp_validation_and_spaces():
    assert crypto.is_valid_totp("gezd gnbv gy3t qojq")
    assert not crypto.is_valid_totp("not base32!")
    assert not crypto.is_valid_totp("")
    assert crypto.totp_code("???") == ""
    assert 1 <= crypto.totp_remaining() <= 30


# ------------------------------------------------------------ passwords
def test_fallback_rates_common_patterns_low():
    assert passwords.estimate("Password1!").score <= 1
    assert passwords.estimate("qwerty123456").score <= 1
    assert passwords.estimate("aaaaaaaaaaaa").score <= 1


def test_random_passwords_rate_high():
    pw = passwords.generate_password(20, passwords.build_classes(True, True, True, True))
    assert passwords.estimate(pw).score >= 3


def test_master_password_rules():
    assert passwords.check_master_password("short") is not None
    assert passwords.check_master_password("Password1!") is not None
    assert passwords.check_master_password("v9#Lq2!xRt7@Wm") is None


def test_generate_password_contains_every_class():
    classes = passwords.build_classes(True, True, True, True)
    for _ in range(50):
        pw = passwords.generate_password(8, classes)
        assert len(pw) == 8
        assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in passwords.SYMBOLS for c in pw)


def test_ambiguous_characters_excluded():
    classes = passwords.build_classes(True, True, True, False, exclude_ambiguous=True)
    assert not set("".join(classes)) & set(passwords.AMBIGUOUS)


def test_passphrase_with_explicit_wordlist():
    words = ["alpha", "bravo", "charlie", "delta"]
    phrase = passwords.generate_passphrase(4, "-", True, True, source=words)
    parts = phrase.split("-")
    assert len(parts) == 5 and parts[-1].isdigit()
    assert all(p[0].isupper() for p in parts[:4])
    assert passwords.entropy_passphrase(4, 7776, False) == pytest.approx(51.7, abs=0.1)


# ------------------------------------------------------------ models
def test_legacy_blob_migrates_to_entries_with_ids():
    legacy = {"GitHub": {"username": "me", "password": "pw", "tags": ["dev"], "modified": "2024-01-01T00:00:00"},
              "Bad": "not a dict"}
    entries = models.entries_from_blob(legacy)
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "GitHub" and e.username == "me" and len(e.id) == 32
    assert e.created == "2024-01-01T00:00:00"


def test_blob_roundtrip_preserves_fields():
    e = Entry(name="X", username="u", password="p", tags=["a"], favorite=True)
    e.set_password("p2")
    back = models.entries_from_blob(models.entries_to_blob([e]))[0]
    assert back.to_dict() == e.to_dict()
    assert back.history[-1]["password"] == "p"


def test_entry_named_entries_is_still_legacy():
    legacy = {"entries": {"username": "u", "password": "p"}}
    out = models.entries_from_blob(legacy)
    assert len(out) == 1 and out[0].name == "entries"


def test_history_is_capped():
    e = Entry(name="X", password="0")
    for i in range(1, 40):
        e.set_password(str(i))
    assert len(e.history) == models.HISTORY_LIMIT


def test_health_report():
    old = (datetime.now() - timedelta(days=500)).isoformat()
    a = Entry(name="a", password="Password1!")
    b = Entry(name="b", password="v9#Lq2!xRt7@Wm")
    c = Entry(name="c", password="v9#Lq2!xRt7@Wm")
    d = Entry(name="d", password="zK4$wQp8&nBv2#Lx", pw_changed=old)
    gone = Entry(name="gone", password="Password1!", deleted_at=models.now_iso())
    r = models.health_report([a, b, c, d, gone])
    assert r.total == 4
    assert [e.name for e in r.weak] == ["a"]
    assert sorted(e.name for e in r.reused) == ["b", "c"]
    assert [e.name for e in r.old] == ["d"]
    assert r.score == 0 or r.score == round(100 * (4 - r.flagged) / 4)


def test_expired_trash():
    old = (datetime.now() - timedelta(days=45)).isoformat()
    keep = Entry(name="k", deleted_at=models.now_iso())
    drop = Entry(name="d", deleted_at=old)
    live = Entry(name="l")
    assert models.expired_trash([keep, drop, live]) == [drop]


# ------------------------------------------------------------ storage
def make_store(tmp_path):
    return storage.VaultFile(tmp_path)


def test_new_vault_flow_and_open(tmp_path):
    store = make_store(tmp_path)
    assert store.is_new()
    key = Fernet.generate_key()
    header = store.new_header()
    store.write(storage.serialize(key, header.salt_b64, header.kdf, [Entry(name="A", password="x")]))
    assert not store.is_new()
    h2 = store.load_header()
    entries, migrated = store.open_with_key(h2, key)
    assert [e.name for e in entries] == ["A"] and not migrated


def test_wrong_key_does_not_modify_file(tmp_path):
    store = make_store(tmp_path)
    key = Fernet.generate_key()
    header = store.new_header()
    store.write(storage.serialize(key, header.salt_b64, header.kdf, []))
    before = store.path.read_bytes()
    with pytest.raises(crypto.WrongPasswordError):
        store.open_with_key(store.load_header(), Fernet.generate_key())
    assert store.path.read_bytes() == before


def test_legacy_v2_file_is_readable(tmp_path):
    store = make_store(tmp_path)
    key = Fernet.generate_key()
    salt = base64.b64encode(os.urandom(16)).decode()
    legacy = {"schema": 2, "salt": salt, "kdf": crypto.KDF_PARAMS,
              "canary": crypto.encrypt_dict(key, {"status": "verified"}),
              "vault_blob": crypto.encrypt_dict(key, {"Site": {"username": "u", "password": "p"}})}
    store.path.write_text(json.dumps(legacy))
    entries, migrated = store.open_with_key(store.load_header(), key)
    assert migrated and entries[0].name == "Site"
    store.backup_legacy()
    assert store.legacy_bak.exists()


def test_corrupt_file_is_reported_as_corrupt_not_new(tmp_path):
    store = make_store(tmp_path)
    store.path.write_text("{ this is not json")
    assert not store.is_new()
    with pytest.raises(crypto.CorruptVaultError):
        store.load_header()


def test_newer_schema_refused(tmp_path):
    store = make_store(tmp_path)
    store.path.write_text(json.dumps({"schema": 99, "salt": base64.b64encode(os.urandom(16)).decode(),
                                      "vault_blob": "x"}))
    with pytest.raises(crypto.CorruptVaultError):
        store.load_header()


def test_write_keeps_backup_and_is_atomic(tmp_path):
    store = make_store(tmp_path)
    store.write({"v": 1})
    store.write({"v": 2})
    assert json.loads(store.path.read_text())["v"] == 2
    assert json.loads(store.bak.read_text())["v"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_validate_backup():
    good = {"salt": base64.b64encode(os.urandom(16)).decode(), "vault_blob": "abc", "schema": 2}
    assert storage.validate_backup(good) is good
    for bad in ({}, {"salt": "!!", "vault_blob": "a"}, {**good, "vault_blob": ""},
                {**good, "schema": 99}, {**good, "schema": "x"}, [1, 2]):
        with pytest.raises(ValueError):
            storage.validate_backup(bad)


def test_wipe_removes_backups(tmp_path):
    store = make_store(tmp_path)
    store.write({"a": 1}); store.write({"a": 2})
    store.snapshot_before_import()
    store.wipe()
    assert not store.path.exists() and not store.bak.exists()
    assert not list(tmp_path.glob("vault.pre-import-*"))


def test_lockout_persists_and_escalates(tmp_path):
    lock = storage.LockoutStore(tmp_path / "lockout.json")
    assert lock.remaining() == 0
    for _ in range(2):
        assert lock.record_failure()[1] == 0
    failed, delay = lock.record_failure()
    assert failed == 3 and delay == 5
    assert storage.LockoutStore(tmp_path / "lockout.json").remaining() > 0  # survives restart
    assert storage.LockoutStore.delay_for(20) == 300
    lock.reset()
    assert lock.remaining() == 0 and lock.load()[0] == 0


def test_settings_migration(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"clipboard_clear_seconds": None, "generator_length": 24, "theme_name": "Nope"}))
    s = storage.load_settings(p, themes=["Dark", "Light"])
    assert s["clipboard_clear_seconds"] == 0 and s["gen_length"] == 24 and s["theme_name"] == "Dark"


# ------------------------------------------------------------ importers
def test_bitwarden_csv():
    text = ("folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,login_password,login_totp\n"
            "Work,1,login,GitHub,,,0,https://github.com,me,hunter2,\n"
            ",0,note,Secret note,hi,,0,,,,\n")
    r = importers.parse_csv(text)
    assert len(r.entries) == 1 and r.skipped == 1
    e = r.entries[0]
    assert (e.name, e.username, e.password, e.tags) == ("GitHub", "me", "hunter2", ["Work"])


def test_chrome_csv_and_dedupe():
    text = "name,url,username,password,note\nsite,https://a.com,u,p,\nsite,https://a.com,u,p,\n"
    existing = [Entry(name="site", username="u", url="https://a.com", password="p")]
    r = importers.parse_csv(text, existing)
    assert r.entries == [] and r.duplicates == 2


def test_csv_without_password_column_rejected():
    with pytest.raises(ValueError):
        importers.parse_csv("a,b\n1,2\n")


def test_legacy_data_folder_is_copied_not_moved(tmp_path):
    old = tmp_path / "BNDGGVault"; old.mkdir()
    (old / "vault.json").write_text('{"salt": "x"}')
    (old / "settings.json").write_text("{}")
    store = storage.VaultFile(tmp_path / "KestrelVault")
    assert (store.dir / "vault.json").read_text() == '{"salt": "x"}'
    assert (old / "vault.json").exists()
    (store.dir / "vault.json").write_text("new")
    storage.VaultFile(tmp_path / "KestrelVault")          # must not overwrite
    assert (store.dir / "vault.json").read_text() == "new"
