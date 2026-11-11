"""MySQL connection settings from environment (Docker / production friendly)."""

from __future__ import annotations

import os
import re

# Telemetry tables are named "{TABLE_PREFIX}_power_YYMMDD" and "{TABLE_PREFIX}_energy_YYMMDD".
TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "site").strip()
if not re.fullmatch(r"[A-Za-z0-9_]+", TABLE_PREFIX):
    raise ValueError("TABLE_PREFIX may only contain letters, digits and underscores.")


def mysql_connect_kwargs() -> dict:
    password = os.environ.get("MYSQL_PASSWORD")
    if password is None:
        raise RuntimeError("MYSQL_PASSWORD is not set. Copy .env.example to .env and fill it in.")
    return {
        "user": os.environ.get("MYSQL_USER", "energy_user"),
        "password": password,
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "database": os.environ.get("MYSQL_DATABASE", "unified"),
    }
