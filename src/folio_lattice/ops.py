"""Operator-facing migration, backup, and recovery commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .backup import BackupError, create_backup, migration_check, restore_backup, verify_backup
from .service import FolioLattice, utc_now


def _path(value: str | None, env_name: str, default: str) -> Path:
    return Path(value or os.environ.get(env_name, default))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m folio_lattice.ops")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="check repeatable forward migration state")
    migrate_subcommands = migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_check = migrate_subcommands.add_parser("check")
    migrate_check.add_argument("--db")
    migrate_check.add_argument("--blobs")

    backup = commands.add_parser("backup")
    backup_subcommands = backup.add_subparsers(dest="backup_command", required=True)
    create = backup_subcommands.add_parser("create")
    create.add_argument("--output", required=True)
    create.add_argument("--db")
    create.add_argument("--blobs")
    verify = backup_subcommands.add_parser("verify")
    verify.add_argument("--input", required=True)

    restore = commands.add_parser("dr")
    restore_subcommands = restore.add_subparsers(dest="dr_command", required=True)
    dr_restore = restore_subcommands.add_parser("restore")
    dr_restore.add_argument("--input", required=True)
    dr_restore.add_argument("--db")
    dr_restore.add_argument("--blobs")

    audit = commands.add_parser("audit")
    audit_subcommands = audit.add_subparsers(dest="audit_command", required=True)
    purge = audit_subcommands.add_parser("purge", help="purge expired, non-held audit events")
    purge.add_argument("--before")
    purge.add_argument("--db")
    purge.add_argument("--blobs")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "migrate":
            result = migration_check(
                _path(args.db, "FOLIO_DB_PATH", ".data/folio.db"),
                _path(args.blobs, "FOLIO_BLOB_ROOT", ".data/blobs"),
            )
        elif args.command == "backup" and args.backup_command == "create":
            result = create_backup(
                _path(args.db, "FOLIO_DB_PATH", ".data/folio.db"),
                _path(args.blobs, "FOLIO_BLOB_ROOT", ".data/blobs"),
                args.output,
            )
        elif args.command == "backup" and args.backup_command == "verify":
            result = verify_backup(args.input)
        elif args.command == "dr" and args.dr_command == "restore":
            result = restore_backup(
                args.input,
                _path(args.db, "FOLIO_DB_PATH", ".data/recovered/folio.db"),
                _path(args.blobs, "FOLIO_BLOB_ROOT", ".data/recovered/blobs"),
            )
        elif args.command == "audit" and args.audit_command == "purge":
            service = FolioLattice(
                _path(args.db, "FOLIO_DB_PATH", ".data/folio.db"),
                _path(args.blobs, "FOLIO_BLOB_ROOT", ".data/blobs"),
            )
            cutoff = args.before or utc_now()
            result = {
                "status": "purged",
                "before": cutoff,
                "tenants": service.purge_audit_events(now=cutoff),
            }
        else:
            raise BackupError("unsupported operations command")
    except BackupError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
