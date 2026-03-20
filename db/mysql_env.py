"""MySQL connection settings from environment (Docker / production friendly)."""

from __future__ import annotations

import os


def mysql_connect_kwargs() -> dict:
    return {
        "user": os.environ.get("MYSQL_USER", "energy_user"),
        "password": os.environ.get("MYSQL_PASSWORD", "REDACTED"),
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("MYSQL_PORT", "3307")),
        "database": os.environ.get("MYSQL_DATABASE", "unified"),
    }
