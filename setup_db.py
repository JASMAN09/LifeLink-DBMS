"""Initialize the MySQL schema and synthetic demo records from project SQL files."""

from pathlib import Path
import os

import mysql.connector
from werkzeug.security import generate_password_hash

from config import BASE_DIR, Config


def execute_script(cursor, path):
    sql = Path(path).read_text(encoding="utf-8")
    cursor.execute(sql, map_results=True)
    while True:
        if cursor.with_rows:
            cursor.fetchall()
        if not cursor.nextset():
            break


def ensure_column(cursor, table, column, definition):
    cursor.execute(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (Config.MYSQL_DATABASE, table, column),
    )
    if not cursor.fetchone():
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")


def migrate_existing_schema(cursor):
    """Add the current release's fields without dropping tables or records."""
    for table, column, definition in [
        ("hospital", "hospital_type", "VARCHAR(60) NOT NULL DEFAULT 'General'"),
        ("hospital", "capacity", "INT UNSIGNED NULL"),
        ("hospital", "facilities", "TEXT NULL"),
        ("hospital", "resource_notes", "TEXT NULL"),
        ("app_user", "phone", "VARCHAR(30) NULL"),
        ("app_user", "approval_status", "VARCHAR(20) NOT NULL DEFAULT 'approved'"),
        ("app_user", "force_password_change", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("app_user", "failed_attempts", "SMALLINT UNSIGNED NOT NULL DEFAULT 0"),
        ("app_user", "locked_until", "DATETIME NULL"),
        ("citizen_account", "username", "VARCHAR(80) NULL UNIQUE"),
        ("emergency_case", "emergency_type", "VARCHAR(100) NOT NULL DEFAULT 'Unspecified'"),
        ("emergency_case", "contact_name", "VARCHAR(160) NULL"),
        ("emergency_case", "contact_phone", "VARCHAR(30) NULL"),
        ("blood_request", "urgency", "VARCHAR(24) NOT NULL DEFAULT 'routine'"),
        ("blood_allocation", "active_blood_unit_id", "BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN allocation_status IN ('reserved','issued') THEN blood_unit_id ELSE NULL END) STORED"),
    ]:
        ensure_column(cursor, table, column, definition)
    cursor.execute("UPDATE citizen_account SET username=CONCAT('citizen_',citizen_id) WHERE username IS NULL OR username=''" )
    cursor.execute(
        "SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='citizen_account' AND COLUMN_NAME='username'",
        (Config.MYSQL_DATABASE,),
    )
    username_column = cursor.fetchone()
    if username_column and username_column[0] == "YES":
        cursor.execute("ALTER TABLE citizen_account MODIFY COLUMN username VARCHAR(80) NOT NULL")
    cursor.execute("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='blood_allocation' AND INDEX_NAME='uq_active_blood_unit_allocation'",(Config.MYSQL_DATABASE,))
    if not cursor.fetchone():
        cursor.execute("SELECT active_blood_unit_id,COUNT(*) AS occurrences FROM blood_allocation WHERE active_blood_unit_id IS NOT NULL GROUP BY active_blood_unit_id HAVING COUNT(*)>1 LIMIT 1")
        duplicate=cursor.fetchone()
        if duplicate:
            raise RuntimeError(f"Cannot add allocation uniqueness constraint: blood unit {duplicate[0]} has multiple active allocation records.")
        cursor.execute("ALTER TABLE blood_allocation ADD UNIQUE KEY uq_active_blood_unit_allocation (active_blood_unit_id)")
    cursor.execute(
        """SELECT tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE
           FROM information_schema.TABLE_CONSTRAINTS tc
           JOIN information_schema.CHECK_CONSTRAINTS cc
             ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
           WHERE tc.TABLE_SCHEMA=%s AND tc.TABLE_NAME='alert' AND tc.CONSTRAINT_TYPE='CHECK'""",
        (Config.MYSQL_DATABASE,),
    )
    checks = cursor.fetchall()
    for check in checks:
        if "alert_type" in str(check[1]).lower():
            cursor.execute(f"ALTER TABLE `alert` DROP CHECK `{check[0]}`")
    cursor.execute(
        """SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
           WHERE TABLE_SCHEMA=%s AND TABLE_NAME='alert' AND CONSTRAINT_TYPE='CHECK'
             AND CONSTRAINT_NAME='chk_alert_type'""",
        (Config.MYSQL_DATABASE,),
    )
    if not cursor.fetchone():
        cursor.execute(
            "ALTER TABLE `alert` ADD CONSTRAINT `chk_alert_type` CHECK "
            "(alert_type IN ('shortage','expiry','low_stock','pending_request','emergency','donor_response','transfer'))"
        )
    cursor.execute(
        """SELECT tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc
           JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
           WHERE tc.TABLE_SCHEMA=%s AND tc.TABLE_NAME='blood_request' AND tc.CONSTRAINT_TYPE='CHECK'""",
        (Config.MYSQL_DATABASE,),
    )
    for check in cursor.fetchall():
        if "request_status" in str(check[1]).lower() and "approved" not in str(check[1]).lower():
            cursor.execute(f"ALTER TABLE `blood_request` DROP CHECK `{check[0]}`")
    cursor.execute(
        """SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
           WHERE TABLE_SCHEMA=%s AND TABLE_NAME='blood_request' AND CONSTRAINT_TYPE='CHECK' AND CONSTRAINT_NAME='chk_blood_request_status'""",
        (Config.MYSQL_DATABASE,),
    )
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE `blood_request` ADD CONSTRAINT `chk_blood_request_status` CHECK (request_status IN ('open','approved','rejected','partially_fulfilled','fulfilled','cancelled'))")
    cursor.execute(
        """SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
           WHERE TABLE_SCHEMA=%s AND TABLE_NAME='alert' AND CONSTRAINT_TYPE='CHECK'
             AND CONSTRAINT_NAME='chk_alert_reference'""",
        (Config.MYSQL_DATABASE,),
    )
    if not cursor.fetchone():
        cursor.execute(
            "ALTER TABLE `alert` ADD CONSTRAINT `chk_alert_reference` CHECK "
            "((alert_type='shortage' AND request_line_id IS NOT NULL) OR "
            "(alert_type='expiry' AND blood_unit_id IS NOT NULL) OR "
            "alert_type IN ('low_stock','pending_request','emergency','donor_response','transfer'))"
        )


def has_existing_operational_records(cursor):
    for table in ("hospital", "app_user", "patient", "emergency_case", "ambulance", "driver", "donor", "blood_unit", "blood_request", "blood_transfer"):
        cursor.execute(f"SELECT EXISTS(SELECT 1 FROM `{table}` LIMIT 1)")
        if cursor.fetchone()[0]:
            return True
    return False


def ensure_production_bootstrap_admin(cursor):
    """Create the first production administrator from deployment secrets only."""
    cursor.execute("SELECT COUNT(*) FROM app_user")
    if cursor.fetchone()[0]:
        return
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    display_name = os.getenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "LifeLink Administrator").strip()
    if not username or not email or len(password) < 16:
        raise RuntimeError(
            "The production database has no user. Set BOOTSTRAP_ADMIN_USERNAME, "
            "BOOTSTRAP_ADMIN_EMAIL and a BOOTSTRAP_ADMIN_PASSWORD of at least 16 characters, then rerun setup_db.py."
        )
    cursor.execute("SELECT role_id FROM `role` WHERE role_code='ADMIN'")
    role = cursor.fetchone()
    if not role:
        raise RuntimeError("Production reference seed is missing the ADMIN role.")
    cursor.execute(
        "INSERT INTO app_user(role_id,username,email,password_hash,display_name) VALUES(%s,%s,%s,%s,%s)",
        (role[0], username, email, generate_password_hash(password), display_name or "LifeLink Administrator"),
    )
    print(f"Created initial production administrator '{username}' from environment configuration.")


def main():
    connection = None
    try:
        Config.validate_production()
        connection_options = dict(
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_SETUP_USER,
            password=Config.MYSQL_SETUP_PASSWORD,
            connection_timeout=5,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            autocommit=True,
        )
        if Config.MYSQL_SSL_CA:
            connection_options.update(ssl_ca=Config.MYSQL_SSL_CA, ssl_verify_cert=True, ssl_verify_identity=True)
        connection = mysql.connector.connect(**connection_options)
        cursor = connection.cursor()
        execute_script(cursor, BASE_DIR / "database" / "schema.sql")
        migrate_existing_schema(cursor)
        execute_script(cursor, BASE_DIR / "database" / "seed_reference.sql")
        if Config.APP_ENV == "production":
            had_existing_records = has_existing_operational_records(cursor)
            ensure_production_bootstrap_admin(cursor)
            if had_existing_records:
                print("Existing operational records detected; preserved them and skipped synthetic demo records.")
            else:
                print("Production schema initialized without synthetic patient, donor or emergency records.")
        elif has_existing_operational_records(cursor):
            print("Existing operational records detected; preserved them and skipped synthetic seed data.")
        else:
            execute_script(cursor, BASE_DIR / "database" / "seed.sql")
        cursor.close()
        print(f"LifeLink schema and synthetic demo records are ready in database '{Config.MYSQL_DATABASE}'.")
    except mysql.connector.Error as exc:
        print("Database setup failed. Check that MySQL is running and .env has a MySQL account allowed to create and use the configured database.")
        print(f"MySQL reported: {exc.msg}")
        raise SystemExit(1) from None
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
