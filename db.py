"""MySQL connection and transaction helpers for LifeLink."""

from contextlib import contextmanager

import mysql.connector
from flask import current_app


def get_db_connection():
    """Open a MySQL connection using the active Flask application config."""
    options = {
        "host": current_app.config["MYSQL_HOST"],
        "port": current_app.config["MYSQL_PORT"],
        "user": current_app.config["MYSQL_USER"],
        "password": current_app.config["MYSQL_PASSWORD"],
        "database": current_app.config["MYSQL_DATABASE"],
        "connection_timeout": 5,
        "autocommit": False,
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
    }
    ssl_ca = current_app.config.get("MYSQL_SSL_CA")
    if ssl_ca:
        options.update(ssl_ca=ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
    return mysql.connector.connect(**options)


@contextmanager
def transaction():
    """Commit on success; roll back any exception and always close connection."""
    connection = get_db_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_all(sql, params=()):
    connection = get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        connection.close()


def fetch_one(sql, params=()):
    connection = get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        connection.close()
