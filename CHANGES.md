# Changelog

All notable changes to Kestrel Vault (the fork) are documented here.
The upstream project's history is at
https://github.com/Krahzin/BNDGG-Vault.

## [3.0.0] — 2026

### Added

- **New PySide6 interface.** Sidebar navigation, four tabs
  (Vault, Generator, Health, Settings), animated page transitions.
- **12 themes and 10 accent colours**, switchable at runtime.
- **Vault schema v3.** Entries are a JSON list keyed by stable UUIDs.
  The v2 dict-keyed format is still readable and is migrated on first
  unlock.
- **Password health report.** Weak (score ≤ 1), reused (identical password
  fingerprint across entries), and old (> 365 days) passwords, scored 0–100.
- **Generator tab.** Random passwords (slider length, character classes,
  ambiguous-character filter) and diceware passphrases (words, separator,
  capitalisation, optional trailing number). Live entropy display.
- **`otpauth://` TOTP support** with per-entry refresh bar; bare Base32
  secrets still accepted.
- **CSV import** for Bitwarden, 1Password, and browser exports, with
  duplicate detection against the current vault.
- **Encrypted backup export/restore** with a pre-import snapshot of the
  current vault.
- **Auto-lock options:** idle timer, lock on minimize, lock on window blur.
- **Sensitive clipboard handling.** Auto-clear after a configurable delay,
  plus OS-specific hints asking clipboard managers not to record the value.
- **Instant Caps Lock warning** on the master password field.
- **Persistent lockout** after failed unlock attempts.
- **Rotating log file** at `<data-dir>/logs/kestrel.log`.

### Changed

- **KDF defaults:** Argon2id `t=3, m=64 MiB, p=4`. Older vaults keep their
  original parameters (read from the file).
- **Vault file layout:** contains a separate *canary* record, so a wrong
  master password raises `WrongPasswordError` instead of a generic
  corrupt-file error.
- **Atomic saves:** write to `vault.json.tmp`, `fsync`, then `os.replace`.
- **Icons:** emoji replaced by an inline SVG set that follows the active theme.
- **Package name:** `bndgg_vault` → `kestrel_vault`. Data folder
  `BNDGGVault` → `KestrelVault`, with a one-time copy on first run.

### Removed

- Emoji-based iconography (poor cross-platform rendering).

### Security

- Tightened Fernet envelope validation: malformed blobs now raise
  `CorruptVaultError` instead of being mistaken for a wrong password.
- Sanitised `otpauth://` URIs before parsing.
- Only `http://` and `https://` URLs can be opened from an entry.
- Lockout state and vault writes both go through the atomic-write helper.

## Pre-fork history

See [BNDGG-Vault](https://github.com/Krahzin/BNDGG-Vault).
