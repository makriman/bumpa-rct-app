from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.crypto import FieldCipher, FieldCipherError
from app.db.session import SessionLocal
from app.services.field_key_rotation import rotate_provider_credentials

APPLY_CONFIRMATION = "ROTATE FIELD ENCRYPTION KEYS"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and re-encrypt durable provider credentials with the current key."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the rotation. Without this flag, the command performs a dry run.",
    )
    parser.add_argument(
        "--confirm",
        help=f"Required with --apply; must equal {APPLY_CONFIRMATION!r}.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    args = _parser().parse_args(argv)
    if args.apply and args.confirm != APPLY_CONFIRMATION:
        error_output.write(json.dumps({"error": "apply_confirmation_required"}) + "\n")
        return 2
    if not args.apply and args.confirm is not None:
        error_output.write(json.dumps({"error": "confirmation_without_apply"}) + "\n")
        return 2
    try:
        settings = get_settings()
        cipher = FieldCipher.from_settings(settings)
        with SessionLocal() as db:
            result = rotate_provider_credentials(db, cipher, apply=args.apply)
    except (FieldCipherError, OSError, SQLAlchemyError, ValidationError, ValueError):
        error_output.write(json.dumps({"error": "field_key_rotation_failed"}) + "\n")
        return 1
    output.write(json.dumps(result.public_dict(), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
