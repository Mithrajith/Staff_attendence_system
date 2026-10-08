"""Bootstrap CLI. Usage: python -m api.cli create-admin --email you@example.com --name "Your Name"

The password is read from ADMIN_PASSWORD or prompted for; it is never accepted as a CLI argument.
"""

import argparse
import getpass
import os
import sys

from api.core.security import hash_password
from database.models import Role, User
from database.session import get_db


def create_admin(email: str, name: str) -> int:
    password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    if not 10 <= len(password) <= 128:
        print("Password must be 10-128 characters", file=sys.stderr)
        return 1
    db = next(get_db())
    try:
        if db.query(User).filter(User.email == email.lower()).first():
            print("A user with this email already exists", file=sys.stderr)
            return 1
        db.add(User(email=email.lower(), full_name=name, role=Role.admin, password_hash=hash_password(password)))
        db.commit()
    finally:
        db.close()
    print(f"Admin {email.lower()} created")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="api.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    args = parser.parse_args()
    return create_admin(args.email, args.name)


if __name__ == "__main__":
    sys.exit(main())
