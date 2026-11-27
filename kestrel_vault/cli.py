"""Command-line backup, restore, and verification utilities. No Qt imports."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .crypto import VaultError, derive_raw_key
from .storage import VaultFile, atomic_write, set_data_dir, validate_backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kestrel-vault",
        description="Kestrel Vault encrypted local password manager.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Use a custom directory for vault.json and settings (portable/sandboxed mode).",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Store vault data in a 'KestrelVaultData' folder next to the executable.",
    )
    sub = parser.add_subparsers(dest="command")

    p_bak = sub.add_parser("backup", help="Export a validated encrypted backup of the vault.")
    p_bak.add_argument("dest", type=Path, help="Destination .json backup file.")
    p_bak.add_argument("--verify", action="store_true", help="Prompt for master password to verify decryption before exporting.")

    p_res = sub.add_parser("restore", help="Validate and restore a vault backup.")
    p_res.add_argument("src", type=Path, help="Source .json backup file.")
    p_res.add_argument("--force", action="store_true", help="Skip interactive confirmation prompt.")

    p_ver = sub.add_parser("verify", help="Verify vault structure and optionally test master password decryption.")
    p_ver.add_argument("--path", type=Path, default=None, help="Path to a vault file (defaults to active vault.json).")
    p_ver.add_argument("--decrypt", action="store_true", help="Prompt for master password and verify decryption.")
    return parser


def _resolve_store(args: argparse.Namespace) -> VaultFile:
    if args.data_dir is not None:
        set_data_dir(args.data_dir)
    elif args.portable:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        set_data_dir(base / "KestrelVaultData")
    return VaultFile()


def _cmd_backup(args: argparse.Namespace, store: VaultFile) -> int:
    if not store.path.is_file():
        print(f"No vault found at {store.path}", file=sys.stderr)
        return 1
    try:
        data = store.read()
        validate_backup(data)
        if args.verify:
            header = store.load_header()
            pw = getpass.getpass("Master password: ")
            raw = derive_raw_key(pw, header.salt, header.kdf)
            entries, _dek, _mig = store.open_with_key(header, raw)
            print(f"Verified decryption ({len(entries)} entries).")
        store.export_to(args.dest)
    except (VaultError, ValueError, OSError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Encrypted backup written to {args.dest}")
    return 0


def _cmd_restore(args: argparse.Namespace, store: VaultFile) -> int:
    src: Path = args.src
    if not src.is_file():
        print(f"Backup file not found: {src}", file=sys.stderr)
        return 1
    try:
        raw_text = src.read_text(encoding="utf-8")
        data = validate_backup(json.loads(raw_text))
    except (OSError, ValueError) as exc:
        print(f"Invalid backup: {exc}", file=sys.stderr)
        return 1
    if not args.force and store.path.is_file():
        ans = input(f"Replace {store.path} with {src}? A snapshot will be saved first [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1
    try:
        snap = store.snapshot_before_import()
        atomic_write(store.path, json.dumps(data, indent=1))
    except OSError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    if snap:
        print(f"Previous vault snapshot saved to {snap}")
    print(f"Restored vault to {store.path}")
    return 0


def _cmd_verify(args: argparse.Namespace, store: VaultFile) -> int:
    target = args.path
    if target is not None:
        if not target.is_file():
            print(f"Vault file not found: {target}", file=sys.stderr)
            return 1
        store = VaultFile(target.parent)
        store.path = target
    elif not store.path.is_file():
        print(f"No vault found at {store.path}", file=sys.stderr)
        return 1
    try:
        header = store.load_header()
        validate_backup(header.data)
        if args.decrypt:
            pw = getpass.getpass("Master password: ")
            raw = derive_raw_key(pw, header.salt, header.kdf)
            entries, _dek, migrated = store.open_with_key(header, raw)
            note = " (upgrade pending on GUI unlock)" if migrated else ""
            print(f"OK: schema v{header.schema}, {header.cipher}, {len(entries)} entries{note}")
        else:
            print(f"OK: schema v{header.schema}, {header.cipher}, KDF m={header.kdf['m']} t={header.kdf['t']} p={header.kdf['p']}")
    except (VaultError, ValueError, OSError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


def run_cli(argv: list[str] | None = None) -> tuple[bool, int]:
    """Handle CLI flags/subcommands. Returns (handled, exit_code)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    store = _resolve_store(args)
    if args.command == "backup":
        return True, _cmd_backup(args, store)
    if args.command == "restore":
        return True, _cmd_restore(args, store)
    if args.command == "verify":
        return True, _cmd_verify(args, store)
    return False, 0
