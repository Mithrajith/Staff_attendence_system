"""Bootstrap CLI. Usage: python -m api.cli create-admin --email you@example.com --name "Your Name"

The password is read from ADMIN_PASSWORD or prompted for; it is never accepted as a CLI argument.
"""

import argparse
import getpass
import os
import sys

from api.core.security import hash_password
from database.models import Department, Role, User
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


def seed_departments(items: list[str]) -> int:
    """Idempotent upsert of CODE=NAME pairs (matched by code)."""
    db = next(get_db())
    try:
        for item in items:
            code, sep, name = item.partition("=")
            code, name = code.strip(), name.strip()
            if not sep or not code or not name:
                print(f"Bad entry {item!r}; expected CODE=NAME", file=sys.stderr)
                return 1
            dept = db.query(Department).filter(Department.code == code).first()
            if dept:
                dept.name = name
            else:
                db.add(Department(code=code, name=name))
            db.commit()
            print(f"{code}: {name}")
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="api.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    d = sub.add_parser("seed-departments", help='e.g. seed-departments 247=AIML "002=CSE(CY)"')
    d.add_argument("items", nargs="+", metavar="CODE=NAME")
    args = parser.parse_args()
    if args.cmd == "seed-departments":
        return seed_departments(args.items)
    return create_admin(args.email, args.name)


if __name__ == "__main__":
    sys.exit(main())
