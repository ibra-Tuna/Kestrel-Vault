# Kestrel Vault Threat Model

This document defines what Kestrel Vault is designed to defend against, what lies outside its threat boundary, and how its cryptographic and runtime controls work.

## 1. Security Architecture Summary

- **Key Derivation Function (KDF):** Argon2id (`t=3`, `m=262144` KiB / 256 MiB, `p=4`), 16-byte random salt per vault. Parameters stored in the vault header are clamped within safe bounds on load (`t ∈ [1, 10]`, `m ∈ [8192, 524288]`, `p ∈ [1, 8]`) and transparently upgraded on unlock if a vault uses legacy parameters.
- **Authenticated Encryption & Envelope Model (Schema v4):**
  - Master password → Argon2id → **KEK** (256-bit Key Encryption Key).
  - Random 256-bit **DEK** (Data Encryption Key) encrypts the vault payload (`vault_blob`) with **AES-256-GCM** (random 96-bit nonce per write, explicit AAD binding).
  - The KEK wraps the DEK (`wrapped_dek`) with AES-256-GCM. Master password rotation re-wraps the DEK without re-encrypting the vault payload.
  - Legacy v1–v3 Fernet vaults (`CBC + HMAC-SHA256`) remain readable and are automatically migrated to v4 AES-256-GCM on next unlock (preserving a `.bak` copy).
- **Offline Recovery Kit:** Optional 160-bit CSPRNG recovery key (`KVLT-...`) derives a separate recovery KEK via HKDF-SHA256 to wrap the DEK (`recovery_wrapped_dek`). No server, escrow, or vendor backdoor exists.
- **Zero Network Footprint:** Kestrel Vault makes zero outbound network calls; an AST-based CI test enforces that no networking libraries (`requests`, `socket`, `http.client`, `urllib.request`, etc.) are imported anywhere in `kestrel_vault/`.

## 2. Threats Defended Against

| Threat | Mitigation |
| :--- | :--- |
| **Stolen laptop / offline disk & backup analysis** | Vault and backups are encrypted with AES-256-GCM; master password is protected by Argon2id (`m=256 MiB`). |
| **Tampered or corrupted vault file** | AES-256-GCM authentication tags detect bit flips/truncation; atomic writes (`temp + fsync + os.replace`) prevent partial files. |
| **Online / UI brute-force guessing** | Persistent exponential lockout (`lockout.json`) starting at 3 failures up to 300s cooldown; survives app restarts. |
| **Shoulder-surfing & length inference** | Unlock field uses fixed-width dots (never leaks password length); detail passwords use hold-to-reveal and auto-hide on focus loss; clipboard auto-clears with verify-before-clear and OS concealed-clipboard hints. |
| **Unattended unlocked session** | Configurable inactivity auto-lock countdown, lock-on-minimize, and optional lock-on-blur; locking wipes key buffers (`ctypes.memset`), clears entry fields, resets UI state, and triggers `gc.collect()`. |
| **Accidental secret leakage in logs** | Call sites log only exception class names; `SecretRedactingFilter` scrubs tokens, `otpauth://` URIs, recovery codes, and key-value secrets before log records hit disk. |
| **Shared or untrusted host storage** | Portable mode (`--data-dir`, `--portable`, or `portable.flag`) keeps `vault.json`, settings, and logs off `%APPDATA%` onto user-chosen removable media. |

## 3. Threats Out of Scope (Not Defended Against)

- **Compromised OS, kernel, or hypervisor:** A local administrator, rootkit, or malicious driver can read process memory while the vault is unlocked, intercept keystrokes, or capture screenshots.
- **Keyloggers & screen scrapers:** No local desktop application can prevent a privileged keylogger from capturing the master password as it is typed.
- **Live memory scraping while unlocked (and Python string caveats):** Although KEK/DEK buffers are stored in `bytearray` and wiped with `ctypes.memset` on lock, Python `str` objects (and Qt widget C++ buffers) are immutable or allocator-managed and may persist in freed heap pages until overwritten by the OS/Python allocator.
- **Physical coercion or weak user-chosen master passwords** (mitigated only by minimum strength/length rules and zxcvbn checks).
- **Unencrypted CSV imports left on disk:** Kestrel Vault warns users to delete plaintext CSV exports after importing.

## 4. External Security Audit Scope

For independent verification, the security-critical core is strictly isolated with zero Qt dependencies in:
- `kestrel_vault/crypto.py` (Argon2id, AES-256-GCM, DEK wrapping, recovery HKDF, TOTP)
- `kestrel_vault/storage.py` (atomic I/O, header validation, migration, lockout persistence)
- `kestrel_vault/security.py` (log redaction filter, `ctypes.memset` buffer wiping)
- `kestrel_vault/session.py` (key lifecycle and lock cleanup)
