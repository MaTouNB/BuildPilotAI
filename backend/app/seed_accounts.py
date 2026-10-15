"""Explicit local development accounts; no passwords in source, arguments or stdout."""

import argparse
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import HASHER
from app.config import ROOT
from app.db import make_engine
from app.models import Tenant, TenantMember, UserAccount


def main():
    parser = argparse.ArgumentParser(
        description="Create local test accounts with a private credential file"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "data/local/m05-accounts.json")
    args = parser.parse_args()
    path = args.output.resolve()
    # Keep generated credentials in the deliberately ignored local folder only.
    local_folder = ROOT / "data/local"
    if local_folder.is_symlink():
        parser.exit(2, "Local credentials folder must not be a symlink.\n")
    folder = local_folder.resolve()
    if path.parent != folder or path.is_symlink() or path.exists():
        parser.exit(2, "Output must be a new non-symlink file directly in data/local.\n")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = make_engine()
    created = False
    credentials = []
    try:
        with Session(engine) as db, db.begin():
            existing = db.scalar(
                select(UserAccount.id).where(
                    UserAccount.username.in_(["dev-alice", "dev-bob", "dev-carol"])
                )
            )
            if existing:
                parser.exit(2, "Development accounts already exist; not resetting passwords.\n")
            a, b = (
                Tenant(id=uuid4(), name="Development A", status="active"),
                Tenant(id=uuid4(), name="Development B", status="active"),
            )
            db.add_all([a, b])
            db.flush()
            for name, tenants in [("dev-alice", [a, b]), ("dev-bob", [a]), ("dev-carol", [b])]:
                password = secrets.token_urlsafe(24)
                user = UserAccount(
                    id=uuid4(), username=name, password_hash=HASHER.hash(password), status="active"
                )
                db.add(user)
                db.flush()
                for tenant in tenants:
                    db.add(
                        TenantMember(
                            tenant_id=tenant.id, user_id=user.id, role="member", status="active"
                        )
                    )
                credentials.append(
                    {
                        "username": name,
                        "password": password,
                        "tenants": [tenant.name for tenant in tenants],
                    }
                )
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"accounts": credentials}, stream, indent=2)
                stream.write("\n")
        print(f"Created 3 local development accounts. Private credentials: {path}")
        print("Passwords were not printed. Existing Demo builds were not moved.")
    except Exception:
        # A file may have been written before a failed DB commit; remove this owned output.
        if created:
            path.unlink(missing_ok=True)
        parser.exit(
            2,
            "Account initialization failed; verify database and migrations. "
            "No credentials displayed.\n",
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
