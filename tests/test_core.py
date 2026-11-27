import ast
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrel_vault import cli, crypto, importers, models, passwords, security, storage  # noqa: E402
from kestrel_vault.models import Entry  # noqa: E402


# ------------------------------------------------------------ crypto / TOTP
def test_gcm_roundtrip_and_wrong_key():
    k1, k2 = crypto.generate_dek(), crypto.generate_dek()
    blob = crypto.encrypt_gcm(k1, {"a": 1})
    assert crypto.decrypt_gcm(k1, blob) == {"a": 1}
    with pytest.raises(crypto.WrongPasswordError):
        crypto.decrypt_gcm(k2, blob)


def test_gcm_garbage_blob_is_not_reported_as_wrong_password():
    with pytest.raises(crypto.CorruptVaultError):
        crypto.decrypt_gcm(crypto.generate_dek(), "###not-base64###")


def test_envelope_wrap_and_unwrap():
    kek1, kek2 = crypto.generate_dek(), crypto.generate_dek()
    dek = crypto.generate_dek()
    wrapped = crypto.wrap_key(kek1, dek)
    assert crypto.unwrap_key(kek1, wrapped) == dek
    with pytest.raises(crypto.WrongPasswordError):
        crypto.unwrap_key(kek2, wrapped)


def test_recovery_key_roundtrip_and_wrong_code():
    code = crypto.generate_recovery_key()
    assert code.startswith("KVLT-")
    dek = crypto.generate_dek()
    salt = os.urandom(16)
    wrapped = crypto.wrap_dek_with_recovery(code, dek, salt)
    assert crypto.unwrap_dek_with_recovery(code.lower().replace("-", " "), wrapped, salt) == dek
    with pytest.raises(crypto.WrongPasswordError):
        crypto.unwrap_dek_with_recovery("KVLT-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA", wrapped, salt)


def test_roundtrip_and_wrong_key_legacy_fernet():
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    blob = crypto.encrypt_dict(k1, {"a": 1})
    assert crypto.decrypt_dict(k1, blob) == {"a": 1}
    with pytest.raises(crypto.WrongPasswordError):
        crypto.decrypt_dict(k2, blob)


def test_garbage_blob_is_not_reported_as_wrong_password():
    with pytest.raises(crypto.CorruptVaultError):
        crypto.decrypt_dict(Fernet.generate_key(), "###not-base64###")


def test_kdf_params_default_256mib_and_clamped():
    assert crypto.KDF_PARAMS["m"] == 262144
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


# ------------------------------------------------------------ security & network isolation
def test_network_isolation_no_network_imports():
    forbidden = {
        "requests", "httpx", "aiohttp", "urllib.request", "urllib3",
        "socket", "http", "http.client", "ftplib", "smtplib", "ssl", "webbrowser",
    }
    pkg_dir = Path(__file__).resolve().parent.parent / "kestrel_vault"
    violations: list[str] = []
    for py_file in pkg_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden or alias.name.split(".")[0] in {"requests", "httpx", "aiohttp", "urllib3", "socket", "http", "ftplib", "smtplib", "ssl", "webbrowser"}:
                        violations.append(f"{py_file.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod in forbidden or mod.split(".")[0] in {"requests", "httpx", "aiohttp", "urllib3", "socket", "http", "ftplib", "smtplib", "ssl", "webbrowser"}:
                    violations.append(f"{py_file.name}: from {mod} import ...")
    assert violations == []


def test_log_redacting_filter():
    flt = security.SecretRedactingFilter()
    rec = logging.LogRecord(
        "test", logging.ERROR, "x.py", 1,
        "password=supersecret token=gAAAAA123456789012345678901234567890 uri=otpauth://totp/a?secret=ABC key=KVLT-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH",
        (), None,
    )
    assert flt.filter(rec)
    assert "supersecret" not in rec.msg
    assert "gAAAAA1234567890" not in rec.msg
    assert "secret=ABC" not in rec.msg
    assert "KVLT-AAAA" not in rec.msg


def test_wipe_bytes_zeroes_buffer():
    buf = bytearray(b"sensitive-key-material-32-bytes!")
    security.wipe_bytes(buf)
    assert buf == bytearray(len(buf))


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


# ------------------------------------------------------------ storage & CLI
def make_store(tmp_path):
    return storage.VaultFile(tmp_path)


def test_new_v4_vault_flow_and_open(tmp_path):
    store = make_store(tmp_path)
    assert store.is_new()
    kek = crypto.generate_dek()
    dek = crypto.generate_dek()
    header = store.new_header()
    rec = crypto.generate_recovery_key()
    rsalt = os.urandom(16)
    data = storage.serialize(
        kek, dek, header.salt_b64, header.kdf, [Entry(name="A", password="x")],
        base64.b64encode(rsalt).decode(), crypto.wrap_dek_with_recovery(rec, dek, rsalt),
    )
    store.write(data)
    assert not store.is_new()
    h2 = store.load_header()
    entries, dek2, migrated = store.open_with_key(h2, kek)
    assert [e.name for e in entries] == ["A"] and dek2 == dek and not migrated
    entries_rec, dek_rec = store.open_with_recovery(h2, rec)
    assert [e.name for e in entries_rec] == ["A"] and dek_rec == dek


def test_rewrap_vault_changes_kek_without_reencrypting_blob():
    kek1, kek2, dek = crypto.generate_dek(), crypto.generate_dek(), crypto.generate_dek()
    salt1 = base64.b64encode(os.urandom(16)).decode()
    salt2 = base64.b64encode(os.urandom(16)).decode()
    v4 = storage.serialize(kek1, dek, salt1, crypto.KDF_PARAMS, [Entry(name="Site", password="pw")])
    rewrapped = storage.rewrap_vault(v4, kek2, salt2, crypto.KDF_PARAMS, dek)
    assert rewrapped["vault_blob"] == v4["vault_blob"]
    assert crypto.unwrap_key(kek2, rewrapped["wrapped_dek"]) == dek


def test_kdf_mismatch_triggers_migration_flag(tmp_path):
    store = make_store(tmp_path)
    kek, dek = crypto.generate_dek(), crypto.generate_dek()
    salt = base64.b64encode(os.urandom(16)).decode()
    old_kdf = {"type": "argon2id", "t": 3, "m": 65536, "p": 4}
    store.write(storage.serialize(kek, dek, salt, old_kdf, []))
    entries, dek2, migrated = store.open_with_key(store.load_header(), kek)
    assert entries == [] and dek2 == dek and migrated


def test_wrong_key_does_not_modify_file(tmp_path):
    store = make_store(tmp_path)
    kek, dek = crypto.generate_dek(), crypto.generate_dek()
    header = store.new_header()
    store.write(storage.serialize(kek, dek, header.salt_b64, header.kdf, []))
    before = store.path.read_bytes()
    with pytest.raises(crypto.WrongPasswordError):
        store.open_with_key(store.load_header(), crypto.generate_dek())
    assert store.path.read_bytes() == before


def test_legacy_v2_file_is_readable_and_marked_for_migration(tmp_path):
    store = make_store(tmp_path)
    raw = crypto.generate_dek()
    fkey = crypto.fernet_key_from_raw(raw)
    salt = base64.b64encode(os.urandom(16)).decode()
    legacy = {"schema": 2, "salt": salt, "kdf": {"type": "argon2id", "t": 3, "m": 65536, "p": 4},
              "canary": crypto.encrypt_dict(fkey, {"status": "verified"}),
              "vault_blob": crypto.encrypt_dict(fkey, {"Site": {"username": "u", "password": "p"}})}
    store.path.write_text(json.dumps(legacy))
    entries, new_dek, migrated = store.open_with_key(store.load_header(), raw)
    assert migrated and len(new_dek) == 32 and entries[0].name == "Site"
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
    good_v2 = {"salt": base64.b64encode(os.urandom(16)).decode(), "vault_blob": "abc", "schema": 2}
    good_v4 = {"salt": base64.b64encode(os.urandom(16)).decode(), "vault_blob": "abc", "wrapped_dek": "xyz", "schema": 4}
    assert storage.validate_backup(good_v2) is good_v2
    assert storage.validate_backup(good_v4) is good_v4
    for bad in ({}, {"salt": "!!", "vault_blob": "a"}, {**good_v2, "vault_blob": ""},
                {**good_v4, "wrapped_dek": ""}, {**good_v2, "schema": 99}, {**good_v2, "schema": "x"}, [1, 2]):
        with pytest.raises(ValueError):
            storage.validate_backup(bad)


def test_cli_backup_verify_restore(tmp_path):
    data_dir = tmp_path / "vault_data"
    backup_file = tmp_path / "backup.json"
    store = storage.VaultFile(data_dir)
    kek, dek = crypto.generate_dek(), crypto.generate_dek()
    header = store.new_header()
    store.write(storage.serialize(kek, dek, header.salt_b64, header.kdf, [Entry(name="CLI", password="secret")]))

    try:
        handled, code = cli.run_cli(["--data-dir", str(data_dir), "verify"])
        assert handled and code == 0

        handled, code = cli.run_cli(["--data-dir", str(data_dir), "backup", str(backup_file)])
        assert handled and code == 0 and backup_file.is_file()

        store.path.unlink()
        handled, code = cli.run_cli(["--data-dir", str(data_dir), "restore", str(backup_file), "--force"])
        assert handled and code == 0 and store.path.is_file()
    finally:
        storage.set_data_dir(None)


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
