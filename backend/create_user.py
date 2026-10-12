"""Create or update a user in svg_users.

Usage:
    python backend/create_user.py <username> <password>

Passwords are stored as PBKDF2-SHA256 (200k iterations) with a random salt -
only the hash and salt ever leave this script.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

import boto3

REGION = "ap-south-1"
TABLE = "svg_users"
ITERATIONS = 200_000


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    username, password = sys.argv[1].strip().lower(), sys.argv[2]
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")

    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), ITERATIONS
    ).hex()

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    table.put_item(Item={
        "username": username,
        "salt": salt,
        "password_hash": password_hash,
        "iterations": ITERATIONS,
        "created_at": int(__import__("time").time()),
    })
    print(f"User ready: {username} (table {TABLE})")


if __name__ == "__main__":
    main()
