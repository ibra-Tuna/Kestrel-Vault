# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

- **Maintainer:** `<YOUR NAME>`
- **Preferred contact:** `<your.email@example.com>`
  (or DM `@<YOUR-USER>` on GitHub)

You should get an acknowledgement within **72 hours**, and a fix or a
mitigation plan within **14 days** for confirmed issues.

If you cannot reach the maintainer, open a **private security advisory** on
GitHub: *Repository → Security → Advisories → Report a vulnerability*.

## Supported versions

| Version | Supported |
| :--- | :--- |
| 3.x | ✅ Yes |
| 2.x (upstream BNDGG-Vault) | ❌ No |

## What this project protects

- **Confidentiality at rest** of the vault file
  (Fernet: AES-128-CBC + HMAC-SHA256).
- **Password-derived keys** via Argon2id (`t=3`, `m=64 MiB`, `p=4`).
- **Integrity** of the vault file — tampering is detected by the HMAC
  before any plaintext is produced.
- **Clipboard hygiene** — secrets are cleared after a delay and marked as
  sensitive where the OS supports it.

## Known limitations

- The vault is **decrypted in memory** while unlocked. Anyone with debugger
  access to the process can read secrets. Use auto-lock.
- The app has **not been independently audited**. Treat it as a personal
  tool, not as a replacement for a professionally audited password manager.
- **CSV imports are plaintext**; the source file is not encrypted.
- Clipboard *manager hints* are advisory. Some clipboard tools ignore them.

## In scope

- Anything that lets an attacker read the vault without the master password.
- Anything that leaks plaintext secrets outside the locked vault.
- Weaknesses in the KDF parameters, Fernet usage, or TOTP handling.
- Path traversal or arbitrary write in the vault import/export flows.

## Out of scope

- Attacks that require a compromised OS or an attacker already running as
  your user.
- Brute-forcing the master password — that's the point of the password.
- Social engineering.
