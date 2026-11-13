"""Explicitly rebuild the local LifeLink database from schema and demo seed.

This is destructive. It is deliberately separate from app startup and requires
``--confirm``. It refuses production and databases other than ``lifelink``.
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import mysql.connector

from config import Config
from setup_db import execute_script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="confirm all existing lifelink data may be deleted")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("reset requires --confirm because it permanently drops the configured database")
    if Config.APP_ENV == "production":
        parser.error("database reset is disabled when APP_ENV=production")
    if Config.MYSQL_DATABASE != "lifelink":
        parser.error("the current schema targets database 'lifelink'; refusing to reset a different DB_NAME")

    options = {
        "host": Config.MYSQL_HOST,
        "port": Config.MYSQL_PORT,
        "user": Config.MYSQL_SETUP_USER,
        "password": Config.MYSQL_SETUP_PASSWORD,
        "connection_timeout": 5,
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
        "autocommit": True,
    }
    if Config.MYSQL_SSL_CA:
        options.update(ssl_ca=Config.MYSQL_SSL_CA, ssl_verify_cert=True, ssl_verify_identity=True)

    connection = mysql.connector.connect(**options)
    try:
        cursor = connection.cursor()
        cursor.execute("DROP DATABASE IF EXISTS `lifelink`")
        execute_script(cursor, BASE_DIR / "database" / "schema.sql")
        execute_script(cursor, BASE_DIR / "database" / "seed_reference.sql")
        execute_script(cursor, BASE_DIR / "database" / "seed.sql")
        cursor.close()
        print("Database 'lifelink' was rebuilt from the schema and synthetic demonstration seed.")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        Config.validate_production()
        main()
    except mysql.connector.Error as exc:
        print(f"MySQL reset failed: {exc.msg}", file=sys.stderr)
        raise SystemExit(1) from None
