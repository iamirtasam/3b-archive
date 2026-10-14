"""List every user account in svg_users.

Usage:
    python backend/list_users.py
    python backend/list_users.py --delete <username>   # remove an account

Shows username and creation date. Passwords are salted+PBKDF2-hashed and
are NOT recoverable - reset one with create_user.py instead.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

REGION = json_region = None
_cfg = Path(__file__).resolve().parent.parent / "config.json"
if _cfg.exists():
    import json
    REGION = json.loads(_cfg.read_text())["aws"]["region"]
REGION = REGION or "ap-south-1"
TABLE = "svg_users"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delete", metavar="USERNAME",
                        help="remove this account instead of listing")
    args = parser.parse_args()

    ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

    if args.delete:
        ddb.delete_item(Key={"username": args.delete.strip().lower()})
        print(f"Deleted: {args.delete}")
        return

    users = []
    response = ddb.scan()
    users.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = ddb.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        users.extend(response.get("Items", []))

    print(f"{len(users)} user(s) in {TABLE}:")
    for u in sorted(users, key=lambda x: x["username"]):
        created = u.get("created_at")
        when = (datetime.fromtimestamp(created, tz=timezone.utc)
                .strftime("%Y-%m-%d") if isinstance(created, int) else "?")
        print(f"  - {u['username']}  (created {when})")


if __name__ == "__main__":
    main()
