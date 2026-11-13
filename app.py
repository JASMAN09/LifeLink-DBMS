"""LifeLink web application: emergency response and blood resource coordination."""

from __future__ import annotations

import json
import csv
import io
import secrets
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from functools import wraps

import mysql.connector
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from mysql.connector import Error as MySQLError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from db import fetch_all, fetch_one, get_db_connection, transaction


def create_app():
    Config.validate_production()
    app = Flask(__name__)
    app.config.from_object(Config)
    CSRFProtect(app)
    if Config.APP_ENV == "production":
        # Trust exactly one reverse proxy hop (for example, the hosting provider's edge).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if Config.APP_ENV == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.endpoint in {"issue_password_reset", "issue_citizen_password_reset"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response

    @app.context_processor
    def inject_globals():
        return {"csrf_token": generate_csrf, "current_user": getattr(g, "user", None), "donor_signed_in": bool(session.get("donor_account_id")), "can": can,
                "current_citizen": getattr(g, "citizen", None),
                "unread_notification_count": getattr(g, "unread_notification_count", 0)}

    def can(permission):
        return bool(getattr(g, "user", None) and permission in getattr(g, "permissions", set()))

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        g.citizen = None
        g.permissions = set()
        g.unread_notification_count = 0
        user_id = session.get("user_id")
        if user_id:
            if session.get("portal", "staff") != "staff":
                session.clear()
                user_id = None
        if user_id:
            try:
                g.user = fetch_one(
                    "SELECT u.user_id,u.username,u.display_name,u.hospital_id,r.role_code,r.name AS role_name "
                    "FROM app_user u JOIN `role` r ON r.role_id=u.role_id WHERE u.user_id=%s AND u.is_active=1 AND u.approval_status='approved'",
                    (user_id,),
                )
                if g.user:
                    g.permissions = {row["permission_code"] for row in fetch_all(
                        "SELECT p.permission_code FROM role_permission rp JOIN permission p ON p.permission_id=rp.permission_id JOIN app_user u ON u.role_id=rp.role_id WHERE u.user_id=%s",
                        (user_id,),
                    )}
                    unread = fetch_one("SELECT COUNT(*) AS total FROM notification WHERE user_id=%s AND read_at IS NULL", (user_id,))
                    g.unread_notification_count = unread["total"] if unread else 0
                else:
                    session.clear()
            except MySQLError:
                session.clear()
                g.user = None
                g.permissions = set()
        citizen_id = session.get("citizen_id") if session.get("portal") == "citizen" else None
        if citizen_id:
            try:
                g.citizen = fetch_one("SELECT citizen_id,full_name,email,phone,force_password_change FROM citizen_account WHERE citizen_id=%s AND is_active=1", (citizen_id,))
                if not g.citizen:
                    session.clear()
            except MySQLError:
                session.clear()
                g.citizen = None
        if session.get("force_password_change") and request.endpoint not in {"account", "logout", "static", "health", "database_health"}:
            flash("Set a new password before continuing.", "info")
            return redirect(url_for("account"))
        if g.citizen and g.citizen["force_password_change"] and request.endpoint not in {"citizen_account", "citizen_logout", "static", "health"}:
            flash("Set a new password before continuing.", "info")
            return redirect(url_for("citizen_account"))

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                flash("Sign in to continue.", "info")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def donor_login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("portal") != "donor" or not session.get("donor_account_id"):
                flash("Sign in to your donor account to continue.", "info")
                return redirect(url_for("donor_login"))
            donor = fetch_one("SELECT d.donor_id,d.donor_ref,d.display_name,d.availability_status FROM donor_account a JOIN donor d ON d.donor_id=a.donor_id WHERE a.donor_account_id=%s AND a.is_active=1 AND d.is_active=1", (session["donor_account_id"],))
            if not donor:
                session.pop("donor_account_id", None)
                return redirect(url_for("donor_login"))
            g.donor = donor
            return view(*args, **kwargs)
        return wrapped

    def citizen_login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.citizen:
                flash("Sign in to your LifeLink public account to continue.", "info")
                return redirect(url_for("citizen_login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def permission_required(permission):
        def decorate(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                local_demo_permissions = {
                    "dashboard.read", "patients.read", "patients.write", "cases.read", "cases.write",
                    "ambulances.read", "ambulances.write", "dispatches.read", "dispatches.write",
                    "blood.read", "blood.write", "requests.read", "requests.write", "donors.read",
                    "donors.write", "contacts.write", "transfers.read", "transfers.write",
                    "alerts.read", "alerts.write", "hospitals.read", "hospitals.write",
                }
                if not g.user and app.config.get("LOCAL_DEMO_ACCESS") and permission in local_demo_permissions:
                    g.user = {"user_id": None, "username": "local-demo", "display_name": "Local demo workspace",
                              "hospital_id": None, "role_code": "LOCAL_DEMO", "role_name": "Local demo access"}
                    g.permissions = local_demo_permissions
                if not g.user:
                    flash("Sign in to continue.", "info")
                    return redirect(url_for("login", next=request.path))
                if not can(permission):
                    abort(403)
                return view(*args, **kwargs)
            return wrapped
        return decorate

    def assigned_hospital_id():
        """Return the staff member's operational scope; administrators remain network-wide."""
        if not g.user or g.user.get("role_code") == "ADMIN":
            return None
        return g.user.get("hospital_id")

    def require_hospital_access(hospital_id):
        scope = assigned_hospital_id()
        if scope is not None and (hospital_id is None or int(hospital_id) != int(scope)):
            abort(404)

    def require_related_hospital_access(*hospital_ids):
        scope = assigned_hospital_id()
        if scope is not None and all(value is None or int(value) != int(scope) for value in hospital_ids):
            abort(404)

    def patient_scope_condition(alias="p"):
        scope = assigned_hospital_id()
        if scope is None:
            return "1=1", ()
        return (f"(EXISTS(SELECT 1 FROM emergency_case scoped_case WHERE scoped_case.patient_id={alias}.patient_id AND scoped_case.receiving_hospital_id=%s) "
                f"OR EXISTS(SELECT 1 FROM audit_log scoped_audit JOIN app_user scoped_actor ON scoped_actor.user_id=scoped_audit.actor_user_id "
                f"WHERE scoped_audit.entity_type='patient' AND scoped_audit.action='create' AND scoped_audit.entity_id=CAST({alias}.patient_id AS CHAR) AND scoped_actor.hospital_id=%s))", (scope, scope))

    def require_patient_access(patient_id):
        scope = assigned_hospital_id()
        if scope is None:
            return
        condition, params = patient_scope_condition("p")
        patient = fetch_one(f"SELECT p.patient_id FROM patient p WHERE p.patient_id=%s AND {condition}", (patient_id, *params))
        if not patient:
            abort(404)

    def require_blood_request_access(request_id):
        scope = assigned_hospital_id()
        if scope is not None and not fetch_one(
            "SELECT blood_request_id FROM blood_request WHERE blood_request_id=%s AND requesting_hospital_id=%s",
            (request_id, scope),
        ):
            abort(404)

    def require_blood_unit_access(unit_id):
        scope = assigned_hospital_id()
        if scope is not None and not fetch_one(
            "SELECT blood_unit_id FROM blood_unit WHERE blood_unit_id=%s AND current_hospital_id=%s",
            (unit_id, scope),
        ):
            abort(404)

    def add_audit(cursor, action, entity_type, entity_id, details=None):
        cursor.execute(
            "INSERT INTO audit_log (actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES (%s,%s,%s,%s,%s,%s)",
            (g.user["user_id"] if g.user else None, action, entity_type, str(entity_id),
             json.dumps(details, ensure_ascii=False) if details else None, request.remote_addr),
        )

    def create_notifications(cursor, permission_codes, notification_type, message, entity_type=None, entity_id=None, exclude_user_id=None):
        permission_codes = tuple(permission_codes)
        if not permission_codes:
            return
        markers = ",".join(["%s"] * len(permission_codes))
        scope=assigned_hospital_id()
        scope_clause=" AND (u.hospital_id=%s OR u.role_id IN (SELECT role_id FROM `role` WHERE role_code='ADMIN'))" if scope is not None else ""
        cursor.execute(
            f"SELECT DISTINCT u.user_id FROM app_user u JOIN role_permission rp ON rp.role_id=u.role_id "
            f"JOIN permission p ON p.permission_id=rp.permission_id WHERE p.permission_code IN ({markers}) "
            "AND u.is_active=1 AND u.approval_status='approved'"+scope_clause,
            permission_codes+((scope,) if scope is not None else ()),
        )
        for (recipient_id,) in cursor.fetchall():
            if exclude_user_id is not None and recipient_id == exclude_user_id:
                continue
            cursor.execute(
                "INSERT INTO notification(user_id,notification_type,message,entity_type,entity_id) VALUES(%s,%s,%s,%s,%s)",
                (recipient_id, notification_type, message, entity_type, str(entity_id) if entity_id is not None else None),
            )

    def create_user_notification(cursor, user_id, notification_type, message, entity_type=None, entity_id=None):
        cursor.execute(
            "INSERT INTO notification(user_id,notification_type,message,entity_type,entity_id) VALUES(%s,%s,%s,%s,%s)",
            (user_id, notification_type, message, entity_type, str(entity_id) if entity_id is not None else None),
        )

    def sync_request_alerts(cursor, request_id, actor_id):
        cursor.execute("SELECT requesting_hospital_id,request_status,urgency FROM blood_request WHERE blood_request_id=%s",(request_id,))
        request_row=cursor.fetchone()
        if not request_row: return
        if request_row["request_status"] in {"cancelled","rejected"}:
            cursor.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE blood_request_id=%s AND alert_type IN ('shortage','pending_request') AND alert_status IN ('open','acknowledged')",(actor_id,request_id))
            return
        cursor.execute("SELECT l.request_line_id,l.required_units,l.component_id,l.blood_group_id,(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')) AS allocated FROM blood_request_line l WHERE l.blood_request_id=%s",(request_id,))
        lines=cursor.fetchall(); pending_total=0
        for line in lines:
            pending=max(0,line["required_units"]-line["allocated"]); pending_total+=pending
            cursor.execute("""SELECT COUNT(*) AS eligible FROM blood_unit u WHERE u.current_hospital_id=%s AND u.component_id=%s AND u.blood_group_id=%s AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
                AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
                AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
                AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))""",(request_row["requesting_hospital_id"],line["component_id"],line["blood_group_id"]))
            shortage=max(0,pending-cursor.fetchone()["eligible"])
            cursor.execute("SELECT alert_id FROM alert WHERE alert_type='shortage' AND request_line_id=%s AND alert_status IN ('open','acknowledged') LIMIT 1",(line["request_line_id"],)); existing=cursor.fetchone()
            if shortage and not existing:
                cursor.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,%s,'shortage',%s,%s)",(request_row["requesting_hospital_id"],request_id,line["request_line_id"],actor_id,"high" if request_row["urgency"] in {"urgent","critical"} else "medium",f"Shortage of {shortage} eligible unit(s)"))
                create_notifications(cursor,("blood.read","requests.read"),"shortage",f"Blood request {request_id} has a shortage of {shortage} eligible unit(s).","blood_request",request_id,actor_id)
            elif shortage:
                cursor.execute("UPDATE alert SET severity=%s,message=%s WHERE alert_id=%s",("high" if request_row["urgency"] in {"urgent","critical"} else "medium",f"Shortage of {shortage} eligible unit(s)",existing["alert_id"]))
            elif not shortage:
                cursor.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE alert_type='shortage' AND request_line_id=%s AND alert_status IN ('open','acknowledged')",(actor_id,line["request_line_id"]))
        if pending_total and request_row["request_status"] not in {"cancelled","rejected"}:
            cursor.execute("SELECT alert_id FROM alert WHERE alert_type='pending_request' AND blood_request_id=%s AND alert_status IN ('open','acknowledged') LIMIT 1",(request_id,))
            pending_alert=cursor.fetchone()
            if not pending_alert:
                cursor.execute("INSERT INTO alert(hospital_id,blood_request_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,'pending_request',%s,%s)",(request_row["requesting_hospital_id"],request_id,actor_id,"high" if request_row["urgency"]=="critical" else "medium",f"Request {request_id} has {pending_total} pending unit(s)"))
            else:
                cursor.execute("UPDATE alert SET message=%s,severity=%s WHERE alert_id=%s",(f"Request {request_id} has {pending_total} pending unit(s)","high" if request_row["urgency"]=="critical" else "medium",pending_alert["alert_id"]))
        else:
            cursor.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE alert_type='pending_request' AND blood_request_id=%s AND alert_status IN ('open','acknowledged')",(actor_id,request_id))

    def make_ref(prefix):
        return f"{prefix}-{datetime.now(timezone.utc).replace(tzinfo=None):%y%m%d}-{secrets.token_hex(3).upper()}"

    def required_text(name, label, max_length=255):
        value = request.form.get(name, "").strip()
        if not value:
            raise ValueError(f"{label} is required.")
        if len(value) > max_length:
            raise ValueError(f"{label} is too long.")
        return value

    def positive_int(name, label):
        try:
            value = int(request.form.get(name, ""))
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a whole number.")
        if value < 1:
            raise ValueError(f"{label} must be greater than zero.")
        return value

    def mysql_datetime(value, label):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("T", " "))
        except ValueError:
            raise ValueError(f"{label} must be a valid date and time.")

    def run_form(action, success_message, redirect_endpoint, **redirect_values):
        try:
            action()
            flash(success_message, "success")
        except ValueError as exc:
            flash(str(exc), "error")
        except mysql.connector.IntegrityError as exc:
            app.logger.info("LifeLink rejected conflicting data: %s", exc)
            flash("That record conflicts with existing data or a required relationship.", "error")
        except MySQLError:
            app.logger.exception("LifeLink database operation failed")
            flash("The database operation failed. Check the MySQL service, schema, and required fields.", "error")
        return redirect(url_for(redirect_endpoint, **redirect_values))

    def render_records(title, subtitle, endpoint, columns, rows, fields=None, action_url=None, permission=None, empty=None, filters=None, filter_groups=None, filter_components=None, filter_hospitals=None):
        rows = list(rows)
        query = request.args.get("q", "").strip()
        if query:
            query_lower = query.casefold()
            rows = [row for row in rows if query_lower in " ".join(str(value or "") for value in row.values()).casefold()]
        total = len(rows)
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        try:
            per_page = min(100, max(10, int(request.args.get("per_page", "20"))))
        except ValueError:
            per_page = 20
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = rows[(page - 1) * per_page:page * per_page]
        args = request.args.to_dict(flat=True)
        args.pop("page", None)
        pagination = {
            "total": total, "page": page, "pages": pages, "per_page": per_page,
            "query": query,
            "urls": [url_for(request.endpoint, **args, page=number) for number in range(1, pages + 1)],
        }
        return render_template("records.html", title=title, subtitle=subtitle, endpoint=endpoint,
                               columns=columns, rows=rows, fields=fields or [], action_url=action_url,
                               permission=permission, empty=empty or "No records to show yet.",
                               filters=filters or {}, filter_groups=filter_groups or [],
                               filter_components=filter_components or [],filter_hospitals=filter_hospitals or [],pagination=pagination)

    def render_edit(title, subtitle, action_url, fields):
        return render_template("edit_record.html", title=title, subtitle=subtitle,
                               action_url=action_url, fields=fields)

    def as_form_value(value):
        if value is None:
            return ""
        return value.strftime("%Y-%m-%dT%H:%M") if isinstance(value, datetime) else str(value)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok", service="LifeLink")

    @app.get("/health/db")
    def database_health():
        connection = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return jsonify(status="ok", database="connected")
        except MySQLError:
            return jsonify(status="error", database="unavailable"), 503
        finally:
            if connection is not None:
                connection.close()

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Accept staff access requests; an administrator must approve and assign a role."""
        if request.method == "POST":
            display_name = request.form.get("display_name", "").strip()
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            if not display_name or len(display_name) > 160:
                flash("Enter your name.", "error")
            elif len(username) < 3 or len(username) > 80 or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
                flash("Username must be 3–80 characters and use only letters, numbers, dots, dashes or underscores.", "error")
            elif not email or len(email) > 254 or "@" not in email or any(ch.isspace() for ch in email):
                flash("Enter a valid email address.", "error")
            elif len(phone) > 30:
                flash("Phone must be 30 characters or fewer.", "error")
            elif len(password) < 12 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
                flash("Password must be at least 12 characters and include a letter and a number.", "error")
            else:
                try:
                    with transaction() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT role_id FROM `role` WHERE role_code='VIEWER' LIMIT 1")
                        viewer_role = cur.fetchone()
                        if not viewer_role:
                            raise ValueError("Staff access is not ready. Ask an administrator to initialize LifeLink.")
                        cur.execute("INSERT INTO app_user(role_id,username,email,password_hash,display_name,phone,is_active,approval_status) VALUES(%s,%s,%s,%s,%s,%s,0,'pending')", (viewer_role[0], username, email, generate_password_hash(password), display_name, phone or None))
                        user_id = cur.lastrowid
                        cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'staff_registration','app_user',%s,'{}',%s)", (str(user_id), request.remote_addr))
                    flash("Staff access requested. An administrator must approve your account and assign your role before sign-in.", "success")
                    return redirect(url_for("login"))
                except mysql.connector.IntegrityError:
                    flash("That username or email is already in use. Try signing in or choose another.", "error")
                except ValueError as exc:
                    flash(str(exc), "error")
                except MySQLError:
                    app.logger.exception("Staff registration failed")
                    flash("LifeLink could not save the request. Check the database and try again.", "error")
                    return render_template("register.html"), 503
        return render_template("register.html")

    @app.route("/staff/forgot-password", methods=["GET", "POST"], endpoint="staff_forgot_password")
    @app.route("/citizen/forgot-password", methods=["GET", "POST"], endpoint="citizen_forgot_password")
    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            try:
                if email and len(email) <= 254:
                    user = fetch_one("SELECT user_id FROM app_user WHERE email=%s AND is_active=1 AND approval_status='approved'", (email,))
                    citizen = fetch_one("SELECT citizen_id FROM citizen_account WHERE email=%s AND is_active=1", (email,))
                    if user:
                        with transaction() as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT reset_request_id FROM password_reset_request WHERE user_id=%s AND request_status='pending' LIMIT 1", (user["user_id"],))
                            if not cur.fetchone():
                                cur.execute("INSERT INTO password_reset_request(user_id) VALUES(%s)", (user["user_id"],))
                                create_notifications(cur, ("users.manage",), "password_reset", "A staff member requested a password reset.", "app_user", user["user_id"])
                    if citizen:
                        with transaction() as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT reset_request_id FROM citizen_password_reset_request WHERE citizen_id=%s AND request_status='pending' LIMIT 1", (citizen["citizen_id"],))
                            if not cur.fetchone():
                                cur.execute("INSERT INTO citizen_password_reset_request(citizen_id) VALUES(%s)", (citizen["citizen_id"],))
                                create_notifications(cur, ("users.manage",), "citizen_password_reset", "A public account requested a password reset.", "citizen_account", citizen["citizen_id"])
                flash("If the account is eligible, a reset request has been sent to an administrator for review.", "success")
            except MySQLError:
                app.logger.exception("Password reset request failed")
                flash("LifeLink could not record the request. Check the database connection and try again.", "error")
                return render_template("forgot_password.html", recovery_portal="citizen" if request.path.startswith("/citizen/") else "staff"), 503
            return redirect(url_for("citizen_login" if request.path.startswith("/citizen/") else "login"))
        return render_template("forgot_password.html", recovery_portal="citizen" if request.path.startswith("/citizen/") else "staff")

    @app.route("/citizen/register", methods=["GET", "POST"])
    def citizen_register():
        if g.citizen:
            return redirect(url_for("citizen_account"))
        if request.method == "POST":
            name = request.form.get("full_name", "").strip()
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            if not name or len(name) > 160:
                flash("Enter your name.", "error")
            elif len(username) < 3 or len(username) > 80 or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
                flash("Username must be 3–80 characters and use only letters, numbers, dots, dashes or underscores.", "error")
            elif not email or len(email) > 254 or "@" not in email or any(ch.isspace() for ch in email):
                flash("Enter a valid email address.", "error")
            elif not phone or len(phone) > 30:
                flash("Enter a valid phone number.", "error")
            elif len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
                flash("Password must be at least 8 characters and include a letter and a number.", "error")
            else:
                try:
                    with transaction() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO citizen_account(username,full_name,email,phone,password_hash) VALUES(%s,%s,%s,%s,%s)", (username, name, email, phone, generate_password_hash(password)))
                        citizen_id = cur.lastrowid
                        cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'create','citizen_account',%s,'{\"source\":\"public_registration\"}',%s)", (str(citizen_id), request.remote_addr))
                    session.clear()
                    session["portal"] = "citizen"
                    session["citizen_id"] = citizen_id
                    flash("Your public account is ready.", "success")
                    return redirect(url_for("citizen_account"))
                except mysql.connector.IntegrityError:
                    flash("That username or email is already in use. Choose another one.", "error")
                except MySQLError:
                    app.logger.exception("Citizen registration failed")
                    flash("LifeLink could not create the account. Check the database and try again.", "error")
                    return render_template("citizen_register.html"), 503
        return render_template("citizen_register.html")

    @app.route("/citizen/login", methods=["GET", "POST"])
    def citizen_login():
        if g.citizen:
            return redirect(url_for("citizen_account"))
        if request.method == "POST":
            login_name = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            try:
                citizen = fetch_one("SELECT citizen_id,password_hash,is_active,failed_attempts,locked_until FROM citizen_account WHERE username=%s OR email=%s LIMIT 1", (login_name, login_name.lower()))
                if citizen and citizen["locked_until"] and citizen["locked_until"] > datetime.now(timezone.utc).replace(tzinfo=None):
                    flash("This account is temporarily locked. Try again after 10 minutes or request help.", "error")
                elif not citizen or not citizen["is_active"] or not check_password_hash(citizen["password_hash"], password):
                    if citizen and citizen["is_active"]:
                        with transaction() as conn:
                            cur = conn.cursor()
                            cur.execute("UPDATE citizen_account SET locked_until=IF(failed_attempts>=4,UTC_TIMESTAMP()+INTERVAL 10 MINUTE,locked_until),failed_attempts=failed_attempts+1 WHERE citizen_id=%s", (citizen["citizen_id"],))
                    flash("Username or password was not recognized.", "error")
                else:
                    session.clear()
                    session["portal"] = "citizen"
                    session["citizen_id"] = citizen["citizen_id"]
                    with transaction() as conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE citizen_account SET failed_attempts=0,locked_until=NULL,last_login_at=UTC_TIMESTAMP() WHERE citizen_id=%s", (citizen["citizen_id"],))
                        cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'login','citizen_account',%s,'{}',%s)", (str(citizen["citizen_id"]), request.remote_addr))
                    target = request.args.get("next", "")
                    safe_target = target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc
                    return redirect(target if safe_target else url_for("citizen_account"))
            except MySQLError:
                app.logger.exception("Citizen sign-in failed")
                flash("LifeLink cannot reach the account service. Try again later.", "error")
                return render_template("citizen_login.html"), 503
        return render_template("citizen_login.html")

    @app.get("/citizen/account")
    @citizen_login_required
    def citizen_account():
        sos_requests=fetch_all("SELECT s.sos_request_id,s.request_ref,s.emergency_type,s.emergency_location,s.request_status,s.priority_label,s.reported_at,h.name AS hospital,c.case_ref FROM citizen_sos_request s LEFT JOIN hospital h ON h.hospital_id=s.preferred_hospital_id LEFT JOIN emergency_case c ON c.emergency_case_id=s.linked_emergency_case_id WHERE s.citizen_id=%s ORDER BY s.reported_at DESC",(g.citizen["citizen_id"],))
        return render_template("citizen_account.html", citizen=g.citizen,sos_requests=sos_requests)

    @app.route("/citizen/sos",methods=["GET","POST"])
    @citizen_login_required
    def citizen_sos():
        if request.method=="POST":
            emergency_type=request.form.get("emergency_type","").strip()
            location=request.form.get("emergency_location","").strip()
            location_notes=request.form.get("location_notes","").strip()
            priority=request.form.get("priority_label","urgent")
            try:
                if not emergency_type or len(emergency_type)>100: raise ValueError("Describe the emergency type in 100 characters or fewer.")
                if not location or len(location)>255: raise ValueError("Enter a location or address in 255 characters or fewer.")
                if len(location_notes)>500: raise ValueError("Location details must be 500 characters or fewer.")
                if priority not in {"urgent","critical"}: raise ValueError("Choose urgent or critical priority.")
                preferred=request.form.get("preferred_hospital_id",type=int)
                if preferred and not fetch_one("SELECT hospital_id FROM hospital WHERE hospital_id=%s AND is_active=1",(preferred,)):
                    raise ValueError("Choose an active hospital or leave it blank for network triage.")
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("INSERT INTO citizen_sos_request(citizen_id,preferred_hospital_id,request_ref,emergency_type,emergency_location,location_notes,contact_name,contact_phone,priority_label) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(g.citizen["citizen_id"],preferred,make_ref("SOS"),emergency_type,location,location_notes or None,g.citizen["full_name"],g.citizen["phone"],priority))
                    sos_id=cur.lastrowid
                    cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'create','citizen_sos_request',%s,%s,%s)",(str(sos_id),json.dumps({"priority":priority}),request.remote_addr))
                flash("Your emergency report was submitted to the LifeLink coordination team. For immediate danger, call your local emergency number now.","success")
                return redirect(url_for("citizen_account"))
            except ValueError as exc: flash(str(exc),"error")
            except MySQLError:
                app.logger.exception("Citizen SOS submission failed")
                flash("LifeLink could not save this report. Please contact emergency services directly if help is needed now.","error")
        hospitals_list=fetch_all("SELECT hospital_id AS value,name AS label,city FROM hospital WHERE is_active=1 ORDER BY name")
        return render_template("citizen_sos.html",hospitals=hospitals_list)

    @app.get("/citizen-sos")
    @permission_required("citizen_sos.read")
    def citizen_sos_queue():
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT s.sos_request_id,s.request_ref,s.citizen_id,s.preferred_hospital_id,s.linked_emergency_case_id,s.emergency_type,s.emergency_location,s.location_notes,s.contact_name,s.contact_phone,s.request_status,s.priority_label,s.reported_at,c.case_ref,h.name AS hospital FROM citizen_sos_request s LEFT JOIN emergency_case c ON c.emergency_case_id=s.linked_emergency_case_id LEFT JOIN hospital h ON h.hospital_id=s.preferred_hospital_id WHERE (%s IS NULL OR s.preferred_hospital_id=%s) ORDER BY FIELD(s.request_status,'submitted','triaged','linked','closed','rejected'),s.reported_at",(scope,scope))
        hospitals_list=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND (%s IS NULL OR hospital_id=%s) ORDER BY name",(scope,scope))
        return render_template("citizen_sos_queue.html",rows=rows,hospitals=hospitals_list,scope=scope)

    @app.post("/citizen-sos/<int:sos_id>/link")
    @permission_required("citizen_sos.write")
    def link_citizen_sos(sos_id):
        try:
            hospital_id=int(request.form.get("hospital_id",""))
            require_hospital_access(hospital_id)
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT s.*,a.full_name,a.phone FROM citizen_sos_request s JOIN citizen_account a ON a.citizen_id=s.citizen_id WHERE s.sos_request_id=%s FOR UPDATE",(sos_id,))
                sos=cur.fetchone()
                if not sos: abort(404)
                if sos["preferred_hospital_id"] and sos["preferred_hospital_id"]!=hospital_id:
                    raise ValueError("This report was directed to a different hospital.")
                if sos["request_status"] not in {"submitted","triaged"} or sos["linked_emergency_case_id"]:
                    raise ValueError("This report has already been linked or closed.")
                cur.execute("SELECT hospital_id FROM hospital WHERE hospital_id=%s AND is_active=1",(hospital_id,))
                if not cur.fetchone(): raise ValueError("Choose an active receiving hospital.")
                cur.execute("INSERT INTO patient(patient_ref,display_name,phone) VALUES(%s,%s,%s)",(make_ref("PAT"),sos["full_name"],sos["phone"]))
                patient_id=cur.lastrowid
                add_audit(cur,"create","patient",patient_id,{"source":"citizen_sos","request_ref":sos["request_ref"]})
                cur.execute("INSERT INTO emergency_case(patient_id,receiving_hospital_id,registered_by_user_id,case_ref,case_status,emergency_location,location_notes,emergency_type,contact_name,contact_phone,priority_label) VALUES(%s,%s,%s,%s,'registered',%s,%s,%s,%s,%s,%s)",(patient_id,hospital_id,g.user["user_id"],make_ref("EMR"),sos["emergency_location"],sos["location_notes"],sos["emergency_type"],sos["contact_name"],sos["contact_phone"],sos["priority_label"]))
                case_id=cur.lastrowid
                cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,'citizen_sos_linked',%s)",(case_id,g.user["user_id"],f"Created from public emergency report {sos['request_ref']}"))
                cur.execute("UPDATE citizen_sos_request SET preferred_hospital_id=%s,linked_emergency_case_id=%s,request_status='linked' WHERE sos_request_id=%s",(hospital_id,case_id,sos_id))
                cur.execute("INSERT INTO alert(hospital_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,'emergency',%s,%s)",(hospital_id,g.user["user_id"],"high" if sos["priority_label"]=="critical" else "medium",f"Citizen report {sos['request_ref']} was linked to emergency case {case_id}."))
                create_notifications(cur,("cases.read",),"emergency",f"Citizen report {sos['request_ref']} was linked to a new emergency case.","emergency_case",case_id,g.user["user_id"])
                add_audit(cur,"link","citizen_sos_request",sos_id,{"emergency_case_id":case_id,"hospital_id":hospital_id})
            flash("Emergency case created and linked to the citizen report.","success")
        except (ValueError,KeyError) as exc: flash(str(exc) or "Select a valid hospital.","error")
        except MySQLError:
            app.logger.exception("Citizen SOS case creation failed")
            flash("The emergency case could not be created; the report remains in the queue.","error")
        return redirect(url_for("citizen_sos_queue"))

    @app.post("/citizen-sos/<int:sos_id>/status")
    @permission_required("citizen_sos.write")
    def citizen_sos_status(sos_id):
        new_status=request.form.get("status")
        if new_status not in {"triaged","rejected","closed"}: abort(400)
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT sos_request_id,preferred_hospital_id,linked_emergency_case_id,request_status FROM citizen_sos_request WHERE sos_request_id=%s FOR UPDATE",(sos_id,))
                report=cur.fetchone()
                if not report: abort(404)
                hospital_id=report["preferred_hospital_id"] or request.form.get("hospital_id",type=int)
                if not hospital_id: raise ValueError("Choose the hospital responsible for this report.")
                require_hospital_access(hospital_id)
                if new_status=="triaged" and report["request_status"] in {"submitted","triaged"} and not report["linked_emergency_case_id"]:
                    cur.execute("UPDATE citizen_sos_request SET preferred_hospital_id=%s,request_status='triaged' WHERE sos_request_id=%s",(hospital_id,sos_id))
                elif new_status=="rejected" and report["request_status"] in {"submitted","triaged"} and not report["linked_emergency_case_id"]:
                    cur.execute("UPDATE citizen_sos_request SET preferred_hospital_id=%s,request_status='rejected' WHERE sos_request_id=%s",(hospital_id,sos_id))
                elif new_status=="closed" and report["request_status"]=="linked":
                    cur.execute("UPDATE citizen_sos_request SET request_status='closed' WHERE sos_request_id=%s",(sos_id,))
                else: raise ValueError("That status change is not available for this report.")
                add_audit(cur,"status_change","citizen_sos_request",sos_id,{"status":new_status})
            flash(f"Citizen report marked {new_status}.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Citizen SOS status update failed")
            flash("The report status could not be updated.","error")
        return redirect(url_for("citizen_sos_queue"))

    @app.post("/citizen/account/profile")
    @citizen_login_required
    def citizen_update_profile():
        def update():
            name = required_text("full_name", "Full name", 160)
            phone = required_text("phone", "Phone number", 30)
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE citizen_account SET full_name=%s,phone=%s WHERE citizen_id=%s", (name, phone, g.citizen["citizen_id"]))
                cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'update','citizen_account',%s,'{}',%s)", (str(g.citizen["citizen_id"]), request.remote_addr))
        return run_form(update, "Your profile was updated.", "citizen_account")

    @app.post("/citizen/account/password")
    @citizen_login_required
    def citizen_change_password():
        old_password = request.form.get("current_password", "")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        account = fetch_one("SELECT password_hash FROM citizen_account WHERE citizen_id=%s", (g.citizen["citizen_id"],))
        if not account or (not g.citizen["force_password_change"] and not check_password_hash(account["password_hash"], old_password)):
            flash("Current password was not recognized.", "error")
        elif len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
            flash("New password must be at least 8 characters and include a letter and a number.", "error")
        elif password != confirm_password:
            flash("The new passwords do not match.", "error")
        else:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE citizen_account SET password_hash=%s,force_password_change=0 WHERE citizen_id=%s", (generate_password_hash(password), g.citizen["citizen_id"]))
                cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'password_change','citizen_account',%s,'{}',%s)", (str(g.citizen["citizen_id"]), request.remote_addr))
            flash("Password updated.", "success")
        return redirect(url_for("citizen_account"))

    @app.post("/citizen/logout")
    @citizen_login_required
    def citizen_logout():
        citizen_id = session.get("citizen_id")
        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(NULL,'logout','citizen_account',%s,'{}',%s)", (str(citizen_id), request.remote_addr))
        except MySQLError:
            app.logger.exception("Could not record citizen logout audit event")
        session.clear()
        flash("You have signed out.", "success")
        return redirect(url_for("index"))

    @app.route("/setup", methods=["GET", "POST"])
    def first_admin_setup():
        try:
            admin = fetch_one("SELECT u.user_id FROM app_user u JOIN `role` r ON r.role_id=u.role_id WHERE r.role_code='ADMIN' LIMIT 1")
        except MySQLError:
            flash("MySQL is unavailable. Configure the database before creating the first administrator.", "error")
            return render_template("setup_admin.html"), 503
        if admin:
            abort(404)
        if request.method == "POST":
            name = request.form.get("display_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not name or len(name) > 160 or not email or len(email) > 254 or "@" not in email or not username or len(username) > 80:
                flash("Enter a name, valid email, and username.", "error")
            elif len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
                flash("Password must be at least 8 characters and include a letter and a number.", "error")
            else:
                try:
                    with transaction() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT GET_LOCK('lifelink_initial_admin',10)")
                        if cur.fetchone()[0] != 1:
                            raise ValueError("Another first-admin setup is in progress. Try again.")
                        cur.execute("SELECT u.user_id FROM app_user u JOIN `role` r ON r.role_id=u.role_id WHERE r.role_code='ADMIN' LIMIT 1")
                        if cur.fetchone():
                            abort(404)
                        cur.execute("SELECT role_id FROM `role` WHERE role_code='ADMIN'")
                        role = cur.fetchone()
                        if not role:
                            raise ValueError("Role data is not initialized. Run setup_db.py first.")
                        cur.execute("INSERT INTO app_user(role_id,username,email,password_hash,display_name,is_active,approval_status) VALUES(%s,%s,%s,%s,%s,1,'approved')", (role[0], username, email, generate_password_hash(password), name))
                        user_id = cur.lastrowid
                        cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(%s,'create','app_user',%s,'{\"role\":\"ADMIN\",\"source\":\"first_admin_setup\"}',%s)", (user_id, str(user_id), request.remote_addr))
                        cur.execute("SELECT RELEASE_LOCK('lifelink_initial_admin')")
                    flash("Administrator account created. Sign in to continue.", "success")
                    return redirect(url_for("login"))
                except ValueError as exc:
                    flash(str(exc), "error")
                except MySQLError:
                    app.logger.exception("First administrator setup failed")
                    flash("Administrator setup could not be saved. Check the username and email, then retry.", "error")
        return render_template("setup_admin.html")

    @app.route("/login", methods=["GET", "POST"])
    @app.route("/staff/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        first_admin_setup_available = False
        if request.method == "GET":
            try:
                first_admin_setup_available = not fetch_one("SELECT u.user_id FROM app_user u JOIN `role` r ON r.role_id=u.role_id WHERE r.role_code='ADMIN' LIMIT 1")
            except MySQLError:
                pass
        if request.method == "POST":
            login_identifier = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            try:
                user = fetch_one("SELECT u.user_id,u.password_hash,u.is_active,u.approval_status,u.force_password_change,u.failed_attempts,u.locked_until,r.role_code FROM app_user u JOIN `role` r ON r.role_id=u.role_id WHERE u.username=%s OR u.email=%s ORDER BY (u.username=%s) DESC LIMIT 1", (login_identifier, login_identifier.lower(), login_identifier))
            except MySQLError:
                flash("LifeLink cannot reach MySQL. Configure .env and initialize the database first.", "error")
                return render_template("login.html"), 503
            if user and user["locked_until"] and user["locked_until"] > datetime.now(timezone.utc).replace(tzinfo=None):
                flash("This account is temporarily locked. Try again after the lock expires or contact an administrator.", "error")
            elif user and user["role_code"] not in {"ADMIN", "EMERGENCY_STAFF", "AMBULANCE_STAFF", "BLOOD_BANK_STAFF", "HOSPITAL_STAFF", "COORDINATOR", "INVENTORY", "VIEWER"}:
                flash("Username or password was not recognized.", "error")
            elif user and user["approval_status"] == "pending":
                flash("This staff registration is waiting for administrator approval.", "info")
            elif user and (not user["is_active"] or user["approval_status"] != "approved"):
                flash("This account is not active. Contact an administrator.", "error")
            elif not user or not check_password_hash(user["password_hash"], password):
                if user and user["is_active"] and user["approval_status"] == "approved":
                    with transaction() as connection:
                        cursor = connection.cursor()
                        cursor.execute("UPDATE app_user SET locked_until=IF(failed_attempts>=4,UTC_TIMESTAMP()+INTERVAL 10 MINUTE,locked_until),failed_attempts=failed_attempts+1 WHERE user_id=%s", (user["user_id"],))
                flash("Username or password was not recognized.", "error")
            else:
                session.clear()
                session["user_id"] = user["user_id"]
                session["portal"] = "staff"
                session["force_password_change"] = bool(user["force_password_change"])
                with transaction() as connection:
                    cursor = connection.cursor()
                    cursor.execute("UPDATE app_user SET failed_attempts=0,locked_until=NULL,last_login_at=UTC_TIMESTAMP() WHERE user_id=%s", (user["user_id"],))
                    cursor.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(%s,'login','app_user',%s,'{}',%s)", (user["user_id"], str(user["user_id"]), request.remote_addr))
                target=request.args.get("next","")
                safe_target=target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc
                return redirect(target if safe_target else url_for("dashboard"))
        return render_template("login.html", first_admin_setup_available=first_admin_setup_available)

    @app.post("/logout")
    @app.post("/staff/logout")
    @login_required
    def logout():
        user_id = session.get("user_id")
        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details,source_ip) VALUES(%s,'logout','app_user',%s,'{}',%s)", (user_id, str(user_id), request.remote_addr))
        except MySQLError:
            app.logger.exception("Could not record staff logout audit event")
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @permission_required("dashboard.read")
    def dashboard():
        scope=assigned_hospital_id()
        stats = fetch_one("""SELECT
          (SELECT COUNT(*) FROM emergency_case) AS total_cases,
          (SELECT COUNT(*) FROM emergency_case WHERE case_status NOT IN ('completed','cancelled')) AS active_cases,
          (SELECT COUNT(*) FROM emergency_case WHERE DATE(reported_at)=UTC_DATE()) AS cases_today,
          (SELECT COUNT(*) FROM blood_request WHERE request_status IN ('open','approved','partially_fulfilled')) AS open_requests,
          (SELECT COUNT(*) FROM ambulance) AS total_ambulances,
          (SELECT COUNT(*) FROM hospital WHERE is_active=1) AS hospitals_count,
          (SELECT COUNT(*) FROM blood_unit) AS total_units,
          (SELECT COUNT(*) FROM blood_request WHERE request_status='fulfilled') AS completed_requests,
          (SELECT COUNT(*) FROM alert WHERE alert_type='low_stock' AND alert_status IN ('open','acknowledged')) AS low_stock_alerts,
          (SELECT COUNT(*) FROM blood_unit u WHERE u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
             AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
             AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
             AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))) AS available_units,
          (SELECT COUNT(*) FROM ambulance WHERE operational_status='available') AS ambulances_ready,
          (SELECT COUNT(*) FROM donor WHERE is_active=1 AND availability_status='available') AS donors_available,
          (SELECT COUNT(*) FROM donor WHERE is_active=1) AS donors_total,
          (SELECT COUNT(*) FROM blood_transfer WHERE transfer_status IN ('requested','approved','in_transit')) AS pending_transfers,
          (SELECT COUNT(*) FROM alert WHERE alert_status='open') AS recorded_alerts""")
        if scope is not None:
            stats.update(fetch_one("""SELECT
              (SELECT COUNT(*) FROM emergency_case WHERE receiving_hospital_id=%s) AS total_cases,
              (SELECT COUNT(*) FROM emergency_case WHERE receiving_hospital_id=%s AND case_status NOT IN ('completed','cancelled')) AS active_cases,
              (SELECT COUNT(*) FROM emergency_case WHERE receiving_hospital_id=%s AND DATE(reported_at)=UTC_DATE()) AS cases_today,
              (SELECT COUNT(*) FROM blood_request WHERE requesting_hospital_id=%s AND request_status IN ('open','approved','partially_fulfilled')) AS open_requests,
              (SELECT COUNT(*) FROM ambulance WHERE home_hospital_id=%s) AS total_ambulances,
              1 AS hospitals_count,
              (SELECT COUNT(*) FROM blood_unit WHERE current_hospital_id=%s) AS total_units,
              (SELECT COUNT(*) FROM blood_request WHERE requesting_hospital_id=%s AND request_status='fulfilled') AS completed_requests,
              (SELECT COUNT(*) FROM alert WHERE hospital_id=%s AND alert_type='low_stock' AND alert_status IN ('open','acknowledged')) AS low_stock_alerts,
              (SELECT COUNT(*) FROM blood_unit u WHERE u.current_hospital_id=%s AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared' AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued')) AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))) AS available_units,
              (SELECT COUNT(*) FROM ambulance WHERE home_hospital_id=%s AND operational_status='available') AS ambulances_ready,
              (SELECT COUNT(*) FROM blood_transfer WHERE (from_hospital_id=%s OR to_hospital_id=%s) AND transfer_status IN ('requested','approved','in_transit')) AS pending_transfers,
              (SELECT COUNT(*) FROM alert WHERE hospital_id=%s AND alert_status='open') AS recorded_alerts""",(scope,scope,scope,scope,scope,scope,scope,scope,scope,scope,scope,scope,scope)))
        lines = fetch_all("""SELECT l.request_line_id,l.required_units,b.blood_request_id,b.request_ref,b.request_status,h.name AS hospital,
          c.case_ref,bh.code AS blood_group,bc.name AS component,b.urgency,
          (SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')) AS allocated_units,
          (SELECT COUNT(DISTINCT u.blood_unit_id) FROM blood_unit u
           WHERE u.current_hospital_id=b.requesting_hospital_id AND u.component_id=l.component_id AND u.blood_group_id=l.blood_group_id
             AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
             AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
             AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
             AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))) AS eligible_units
          FROM blood_request_line l JOIN blood_request b ON b.blood_request_id=l.blood_request_id
          JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN hospital h ON h.hospital_id=b.requesting_hospital_id
          JOIN blood_group bh ON bh.blood_group_id=l.blood_group_id JOIN blood_component bc ON bc.component_id=l.component_id
          WHERE b.request_status NOT IN ('cancelled','rejected') AND (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY b.requested_at DESC LIMIT 8""",(scope,scope))
        for line in lines:
            line["pending_units"] = max(0, line["required_units"] - line["allocated_units"])
            line["shortage"] = max(0, line["pending_units"] - line["eligible_units"])
        critical=fetch_one("""SELECT COUNT(*) AS total FROM blood_request_line l JOIN blood_request b ON b.blood_request_id=l.blood_request_id
          WHERE b.request_status IN ('open','approved','partially_fulfilled') AND b.urgency IN ('urgent','critical') AND (%s IS NULL OR b.requesting_hospital_id=%s)
          AND GREATEST(l.required_units-(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')),0)>
          (SELECT COUNT(DISTINCT u.blood_unit_id) FROM blood_unit u WHERE u.current_hospital_id=b.requesting_hospital_id AND u.component_id=l.component_id AND u.blood_group_id=l.blood_group_id AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
            AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
            AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
            AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit')))""",(scope,scope))
        stats["critical_shortages"]=critical["total"]
        emergencies = fetch_all("""SELECT c.emergency_case_id,c.case_ref,c.case_status,c.reported_at,p.display_name,h.name AS hospital
            FROM emergency_case c JOIN patient p ON p.patient_id=c.patient_id JOIN hospital h ON h.hospital_id=c.receiving_hospital_id
            WHERE (%s IS NULL OR c.receiving_hospital_id=%s) ORDER BY c.reported_at DESC LIMIT 6""",(scope,scope))
        alerts = fetch_all("SELECT * FROM alert WHERE alert_status IN ('open','acknowledged') AND (%s IS NULL OR hospital_id=%s) ORDER BY created_at DESC LIMIT 5",(scope,scope))
        recent_requests=fetch_all("SELECT b.blood_request_id,b.request_ref,b.request_status,b.urgency,b.requested_at,c.case_ref,h.name AS hospital FROM blood_request b JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN hospital h ON h.hospital_id=b.requesting_hospital_id WHERE (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY b.requested_at DESC LIMIT 5",(scope,scope))
        donor_activity=fetch_all("SELECT d.display_name,d.donor_ref,bg.code AS blood_group,d.city,(SELECT MAX(dn.collected_at) FROM donation dn WHERE dn.donor_id=d.donor_id) AS last_donation FROM donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id ORDER BY d.created_at DESC LIMIT 5")
        expiring_units=fetch_all("SELECT u.unit_code,bg.code AS blood_group,h.name AS hospital,u.expires_on FROM blood_unit u JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN hospital h ON h.hospital_id=u.current_hospital_id WHERE u.expires_on>UTC_TIMESTAMP() AND u.expires_on<=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 7 DAY) AND u.inventory_status NOT IN ('issued','discarded') AND (%s IS NULL OR u.current_hospital_id=%s) ORDER BY u.expires_on LIMIT 5",(scope,scope))
        group_inventory=fetch_all("SELECT bg.code AS blood_group,COUNT(u.blood_unit_id) AS unit_count FROM blood_group bg LEFT JOIN blood_unit u ON u.blood_group_id=bg.blood_group_id AND (%s IS NULL OR u.current_hospital_id=%s) AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared' AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued')) AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit')) GROUP BY bg.blood_group_id ORDER BY bg.blood_group_id",(scope,scope))
        case_status_summary=fetch_all("SELECT case_status,COUNT(*) AS total FROM emergency_case WHERE (%s IS NULL OR receiving_hospital_id=%s) GROUP BY case_status ORDER BY case_status",(scope,scope))
        request_status_summary=fetch_all("SELECT request_status,COUNT(*) AS total FROM blood_request WHERE (%s IS NULL OR requesting_hospital_id=%s) GROUP BY request_status ORDER BY request_status",(scope,scope))
        donation_month_summary=fetch_all("SELECT DATE_FORMAT(MIN(collected_at),'%b %Y') AS month_label,COUNT(*) AS total FROM donation WHERE donation_status='collected' AND collected_at>=DATE_SUB(DATE_FORMAT(UTC_DATE(),'%Y-%m-01'),INTERVAL 5 MONTH) AND (%s IS NULL OR collection_hospital_id=%s) GROUP BY YEAR(collected_at),MONTH(collected_at) ORDER BY YEAR(collected_at),MONTH(collected_at)",(scope,scope))
        group_max=max((int(row['unit_count']) for row in group_inventory),default=0)
        case_max=max((int(row['total']) for row in case_status_summary),default=0)
        request_max=max((int(row['total']) for row in request_status_summary),default=0)
        donation_max=max((int(row['total']) for row in donation_month_summary),default=0)
        return render_template("dashboard.html", stats=stats, lines=lines, emergencies=emergencies, alerts=alerts,
                               recent_requests=recent_requests, donor_activity=donor_activity,
                               expiring_units=expiring_units, group_inventory=group_inventory,
                               case_status_summary=case_status_summary,request_status_summary=request_status_summary,
                               donation_month_summary=donation_month_summary,group_max=group_max,case_max=case_max,
                               request_max=request_max,donation_max=donation_max)

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            current_password=request.form.get("current_password",""); new_password=request.form.get("new_password","")
            if len(new_password)<12:
                flash("New password must be at least 12 characters.","error")
            else:
                try:
                    user=fetch_one("SELECT password_hash FROM app_user WHERE user_id=%s",(g.user["user_id"],))
                    if not user or not check_password_hash(user["password_hash"],current_password):
                        flash("Current password was not recognized.","error")
                    else:
                        with transaction() as conn:
                            cur=conn.cursor(); cur.execute("UPDATE app_user SET password_hash=%s,force_password_change=0 WHERE user_id=%s",(generate_password_hash(new_password),g.user["user_id"]))
                            add_audit(cur,"password_change","app_user",g.user["user_id"])
                        session.pop("force_password_change", None)
                        flash("Password updated.","success")
                except MySQLError:
                    app.logger.exception("Password update failed"); flash("Could not update the password.","error")
        return render_template("account.html")

    @app.route("/hospitals", methods=["GET", "POST"])
    @permission_required("hospitals.read")
    def hospitals():
        if request.method == "POST":
            if not can("hospitals.write"):
                abort(403)
            if assigned_hospital_id() is not None:
                abort(403)
            def create():
                code, name = required_text("code", "Code", 30).upper(), required_text("name", "Hospital name", 160)
                address, city = required_text("address_line", "Address", 255), required_text("city", "City", 100)
                capacity=int(request.form["capacity"]) if request.form.get("capacity") else None
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO hospital(code,name,address_line,city,region,phone,email,hospital_type,capacity,facilities,resource_notes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                (code,name,address,city,request.form.get("region") or None,request.form.get("phone") or None,request.form.get("email") or None,request.form.get("hospital_type") or "General",capacity,request.form.get("facilities") or None,request.form.get("resource_notes") or None))
                    add_audit(cur,"create","hospital",cur.lastrowid,{"code":code})
            return run_form(create,"Hospital added.","hospitals")
        scope = assigned_hospital_id()
        rows = fetch_all("SELECT hospital_id,code,name,city,region,phone,email,hospital_type,capacity,facilities,resource_notes,is_active FROM hospital WHERE hospital_id=%s ORDER BY name" if scope is not None else "SELECT hospital_id,code,name,city,region,phone,email,hospital_type,capacity,facilities,resource_notes,is_active FROM hospital ORDER BY name", (scope,) if scope is not None else ())
        fields = [f("code","Hospital code",required=True),f("name","Hospital name",required=True),f("hospital_type","Hospital type",required=True),f("capacity","Capacity (administrative)",type="number",min=1),f("address_line","Street address",required=True),f("city","City",required=True),f("region","Region"),f("phone","Phone"),f("email","Email",type="email"),f("facilities","Facilities / services"),f("resource_notes","Administrative resource notes")]
        return render_records("Hospital network","Manage hospitals, contact information, facilities and administrative resource notes.","hospitals",[c("code","Code"),c("name","Hospital"),c("hospital_type","Type"),c("capacity","Capacity"),c("city","City"),c("region","Region"),c("phone","Phone"),c("facilities","Facilities"),c("resource_notes","Resource notes"),c("is_active","Active")],rows,fields,"hospitals","hospitals.write")

    @app.get("/hospitals/<int:hospital_id>")
    @permission_required("hospitals.read")
    def hospital_detail(hospital_id):
        hospital=fetch_one("SELECT * FROM hospital WHERE hospital_id=%s",(hospital_id,))
        if not hospital: abort(404)
        require_hospital_access(hospital_id)
        stats=fetch_one("""SELECT (SELECT COUNT(*) FROM emergency_case WHERE receiving_hospital_id=%s) AS emergencies,
            (SELECT COUNT(*) FROM blood_unit WHERE current_hospital_id=%s) AS blood_units,
            (SELECT COUNT(*) FROM blood_transfer WHERE (from_hospital_id=%s OR to_hospital_id=%s) AND transfer_status IN ('requested','approved','in_transit')) AS active_transfers""",(hospital_id,hospital_id,hospital_id,hospital_id))
        inventory=fetch_all("SELECT bg.code AS blood_group,bc.name AS component,COUNT(*) AS unit_count FROM blood_unit u JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id WHERE u.current_hospital_id=%s GROUP BY bg.blood_group_id,bc.component_id ORDER BY bg.code,bc.name",(hospital_id,))
        cases=fetch_all("SELECT c.emergency_case_id,c.case_ref,c.emergency_type,c.priority_label,c.case_status,c.reported_at,p.display_name FROM emergency_case c JOIN patient p ON p.patient_id=c.patient_id WHERE c.receiving_hospital_id=%s ORDER BY c.reported_at DESC LIMIT 10",(hospital_id,))
        return render_template("hospital_detail.html",hospital=hospital,stats=stats,inventory=inventory,cases=cases)

    @app.route("/hospitals/<int:hospital_id>/edit", methods=["GET","POST"])
    @permission_required("hospitals.write")
    def edit_hospital(hospital_id):
        hospital=fetch_one("SELECT * FROM hospital WHERE hospital_id=%s",(hospital_id,))
        if not hospital: abort(404)
        require_hospital_access(hospital_id)
        if request.method=="POST":
            def update():
                values=(required_text("code","Code",30).upper(),required_text("name","Hospital name",160),required_text("hospital_type","Hospital type",60),int(request.form["capacity"]) if request.form.get("capacity") else None,required_text("address_line","Address",255),required_text("city","City",100),request.form.get("region") or None,request.form.get("phone") or None,request.form.get("email") or None,request.form.get("facilities") or None,request.form.get("resource_notes") or None,hospital_id)
                with transaction() as conn:
                    cur=conn.cursor(); cur.execute("UPDATE hospital SET code=%s,name=%s,hospital_type=%s,capacity=%s,address_line=%s,city=%s,region=%s,phone=%s,email=%s,facilities=%s,resource_notes=%s WHERE hospital_id=%s",values)
                    add_audit(cur,"update","hospital",hospital_id,{"code":values[0]})
            return run_form(update,"Hospital details updated.","hospitals")
        fields=[f("code","Hospital code",required=True),f("name","Hospital name",required=True),f("hospital_type","Hospital type",required=True),f("capacity","Capacity (administrative)",type="number",min=1),f("address_line","Street address",required=True),f("city","City",required=True),f("region","Region"),f("phone","Phone"),f("email","Email",type="email"),f("facilities","Facilities / services"),f("resource_notes","Administrative resource notes")]
        for field in fields: field["value"]=as_form_value(hospital.get(field["name"]))
        return render_edit("Edit hospital",hospital["name"],"edit_hospital",fields)

    @app.post("/hospitals/<int:hospital_id>/active")
    @permission_required("hospitals.write")
    def hospital_active(hospital_id):
        require_hospital_access(hospital_id)
        new_value=request.form.get("active")=="1"
        with transaction() as conn:
            cur=conn.cursor(); cur.execute("UPDATE hospital SET is_active=%s WHERE hospital_id=%s",(new_value,hospital_id))
            if cur.rowcount!=1: abort(404)
            add_audit(cur,"status_change","hospital",hospital_id,{"is_active":new_value})
        flash("Hospital activation updated.","success")
        return redirect(url_for("hospitals"))

    @app.route("/patients", methods=["GET", "POST"])
    @permission_required("patients.read")
    def patients():
        if request.method == "POST":
            if not can("patients.write"):
                abort(403)
            def create():
                name = required_text("display_name","Display name",160)
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO patient(patient_ref,display_name,date_of_birth,sex_recorded,phone) VALUES(%s,%s,%s,%s,%s)",
                                (make_ref("PAT"),name,request.form.get("date_of_birth") or None,request.form.get("sex_recorded") or None,request.form.get("phone") or None))
                    add_audit(cur,"create","patient",cur.lastrowid)
            return run_form(create,"Patient record added.","patients")
        condition, params = patient_scope_condition("p")
        rows = fetch_all(f"SELECT p.patient_id,p.patient_ref,p.display_name,p.date_of_birth,p.sex_recorded,p.phone,p.created_at FROM patient p WHERE {condition} ORDER BY p.created_at DESC", params)
        fields = [f("display_name","Display name",required=True),f("date_of_birth","Date of birth",type="date"),f("sex_recorded","Sex recorded"),f("phone","Phone")]
        return render_records("Patients","Synthetic or institution-approved patient records.","patients",[c("patient_ref","Reference"),c("display_name","Name"),c("date_of_birth","Date of birth"),c("sex_recorded","Sex"),c("phone","Phone"),c("created_at","Created")],rows,fields,"patients","patients.write")

    @app.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
    @permission_required("patients.write")
    def edit_patient(patient_id):
        patient=fetch_one("SELECT * FROM patient WHERE patient_id=%s",(patient_id,))
        if not patient: abort(404)
        require_patient_access(patient_id)
        if request.method=="POST":
            def update():
                name=required_text("display_name","Display name",160)
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("UPDATE patient SET display_name=%s,date_of_birth=%s,sex_recorded=%s,phone=%s WHERE patient_id=%s",(name,request.form.get("date_of_birth") or None,request.form.get("sex_recorded") or None,request.form.get("phone") or None,patient_id))
                    add_audit(cur,"update","patient",patient_id)
            return run_form(update,"Patient details updated.","patients")
        fields=[f("display_name","Display name",required=True),f("date_of_birth","Date of birth",type="date"),f("sex_recorded","Sex recorded"),f("phone","Phone")]
        for field in fields: field["value"]=as_form_value(patient.get(field["name"]))
        return render_edit("Edit patient",patient["patient_ref"],"edit_patient",fields)

    @app.post("/patients/<int:patient_id>/delete")
    @permission_required("patients.write")
    def delete_patient(patient_id):
        require_patient_access(patient_id)
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT patient_id,patient_ref FROM patient WHERE patient_id=%s FOR UPDATE",(patient_id,)); row=cur.fetchone()
                if not row: raise ValueError("Patient record not found.")
                cur.execute("SELECT emergency_case_id FROM emergency_case WHERE patient_id=%s LIMIT 1",(patient_id,))
                if cur.fetchone(): raise ValueError("This patient has emergency history and cannot be deleted.")
                add_audit(cur,"delete","patient",patient_id,{"patient_ref":row["patient_ref"]})
                cur.execute("DELETE FROM patient WHERE patient_id=%s",(patient_id,))
            flash("Unlinked patient record deleted.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Patient deletion failed"); flash("Patient record could not be deleted.","error")
        return redirect(url_for("patients"))

    @app.route("/emergencies", methods=["GET", "POST"])
    @permission_required("cases.read")
    def emergencies():
        if request.method == "POST":
            if not can("cases.write"):
                abort(403)
            def create():
                patient_id,hospital_id=int(request.form["patient_id"]),int(request.form["receiving_hospital_id"])
                location=required_text("emergency_location","Emergency location")
                status=request.form.get("case_status","registered")
                if status not in {"registered","response_in_progress","at_hospital"}:
                    raise ValueError("Choose a valid starting case status.")
                require_hospital_access(hospital_id)
                require_patient_access(patient_id)
                case_ref=make_ref("EMG")
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("INSERT INTO emergency_case(patient_id,receiving_hospital_id,registered_by_user_id,case_ref,case_status,emergency_location,location_notes,emergency_type,contact_name,contact_phone,priority_label) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                (patient_id,hospital_id,g.user["user_id"],case_ref,status,location,request.form.get("location_notes") or None,request.form.get("emergency_type") or "Unspecified",request.form.get("contact_name") or None,request.form.get("contact_phone") or None,request.form.get("priority_label") or None))
                    case_id=cur.lastrowid
                    cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,'registered',%s)",(case_id,g.user["user_id"],"Emergency case registered"))
                    if request.form.get("priority_label")=="critical":
                        cur.execute("INSERT INTO alert(hospital_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,'emergency','high',%s)",(hospital_id,g.user["user_id"],f"Critical emergency {case_ref} registered"))
                    add_audit(cur,"create","emergency_case",case_id)
                    create_notifications(cur,("cases.read",),"emergency",f"Emergency {case_ref} registered with {request.form.get('priority_label','routine')} priority.","emergency_case",case_id,g.user["user_id"])
            return run_form(create,"Emergency case registered.","emergencies")
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT c.emergency_case_id,c.case_ref,p.display_name AS patient,h.name AS hospital,c.emergency_type,c.emergency_location,c.priority_label,c.case_status,c.reported_at FROM emergency_case c JOIN patient p ON p.patient_id=c.patient_id JOIN hospital h ON h.hospital_id=c.receiving_hospital_id WHERE c.receiving_hospital_id=%s ORDER BY c.reported_at DESC" if scope is not None else "SELECT c.emergency_case_id,c.case_ref,p.display_name AS patient,h.name AS hospital,c.emergency_type,c.emergency_location,c.priority_label,c.case_status,c.reported_at FROM emergency_case c JOIN patient p ON p.patient_id=c.patient_id JOIN hospital h ON h.hospital_id=c.receiving_hospital_id ORDER BY c.reported_at DESC", (scope,) if scope is not None else ())
        patient_condition, patient_params=patient_scope_condition("p")
        patients_list=fetch_all(f"SELECT p.patient_id AS value,CONCAT(p.patient_ref,' · ',p.display_name) AS label FROM patient p WHERE {patient_condition} ORDER BY p.display_name",patient_params)
        hospital_list=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND hospital_id=%s ORDER BY name" if scope is not None else "SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name", (scope,) if scope is not None else ())
        fields=[f("patient_id","Patient",type="select",options=patients_list,required=True),f("receiving_hospital_id","Receiving hospital",type="select",options=hospital_list,required=True),f("emergency_type","Emergency type",required=True),f("emergency_location","Emergency location",required=True),f("location_notes","Location notes"),f("contact_name","Caller / contact name"),f("contact_phone","Contact phone"),f("priority_label","Severity",type="select",options=opts(("routine","Routine"),("urgent","Urgent"),("critical","Critical")),required=True),f("case_status","Initial status",type="select",options=opts(("registered","Registered"),("response_in_progress","Response in progress"),("at_hospital","At hospital")),required=True)]
        return render_records("Emergency management","Register cases and follow response progress.","emergencies",[c("case_ref","Case"),c("patient","Patient"),c("hospital","Receiving hospital"),c("emergency_type","Type"),c("priority_label","Severity"),c("emergency_location","Location"),c("case_status","Status"),c("reported_at","Reported")],rows,fields,"emergencies","cases.write")

    @app.get("/emergencies/<int:case_id>")
    @permission_required("cases.read")
    def emergency_detail(case_id):
        case=fetch_one("SELECT c.*,p.display_name AS patient,h.name AS hospital FROM emergency_case c JOIN patient p ON p.patient_id=c.patient_id JOIN hospital h ON h.hospital_id=c.receiving_hospital_id WHERE c.emergency_case_id=%s",(case_id,))
        if not case: abort(404)
        require_hospital_access(case["receiving_hospital_id"])
        events=fetch_all("SELECT event_type,event_at,details FROM emergency_case_event WHERE emergency_case_id=%s ORDER BY event_at DESC",(case_id,))
        requests=fetch_all("SELECT blood_request_id,request_ref,request_status,requested_at FROM blood_request WHERE emergency_case_id=%s ORDER BY requested_at DESC",(case_id,))
        return render_template("case_detail.html",case=case,events=events,requests=requests)

    @app.route("/emergencies/<int:case_id>/edit", methods=["GET","POST"])
    @permission_required("cases.write")
    def edit_emergency(case_id):
        case=fetch_one("SELECT * FROM emergency_case WHERE emergency_case_id=%s",(case_id,))
        if not case: abort(404)
        require_hospital_access(case["receiving_hospital_id"])
        if request.method=="POST":
            def update():
                patient_id=int(request.form["patient_id"]); hospital_id=int(request.form["receiving_hospital_id"])
                require_hospital_access(hospital_id)
                require_patient_access(patient_id)
                location=required_text("emergency_location","Emergency location")
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("SELECT receiving_hospital_id FROM emergency_case WHERE emergency_case_id=%s FOR UPDATE",(case_id,))
                    current=cur.fetchone()
                    if not current: raise ValueError("Emergency case not found.")
                    if hospital_id != current[0]:
                        cur.execute("SELECT blood_request_id FROM blood_request WHERE emergency_case_id=%s LIMIT 1",(case_id,))
                        if cur.fetchone(): raise ValueError("A case with linked blood requests cannot be moved to another hospital.")
                    cur.execute("UPDATE emergency_case SET patient_id=%s,receiving_hospital_id=%s,emergency_type=%s,emergency_location=%s,location_notes=%s,contact_name=%s,contact_phone=%s,priority_label=%s WHERE emergency_case_id=%s",(patient_id,hospital_id,request.form.get("emergency_type") or "Unspecified",location,request.form.get("location_notes") or None,request.form.get("contact_name") or None,request.form.get("contact_phone") or None,request.form.get("priority_label") or None,case_id))
                    cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,'details_updated','Emergency case details updated')",(case_id,g.user["user_id"]))
                    add_audit(cur,"update","emergency_case",case_id)
            return run_form(update,"Emergency details updated.","emergency_detail",case_id=case_id)
        patient_condition, patient_params=patient_scope_condition("p")
        patient_options=fetch_all(f"SELECT p.patient_id AS value,CONCAT(p.patient_ref,' · ',p.display_name) AS label FROM patient p WHERE {patient_condition} ORDER BY p.display_name",patient_params)
        scope=assigned_hospital_id()
        hospital_options=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND hospital_id=%s ORDER BY name" if scope is not None else "SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name", (scope,) if scope is not None else ())
        fields=[f("patient_id","Patient",type="select",options=patient_options,required=True),f("receiving_hospital_id","Receiving hospital",type="select",options=hospital_options,required=True),f("emergency_type","Emergency type",required=True),f("emergency_location","Emergency location",required=True),f("location_notes","Location notes"),f("contact_name","Caller / contact name"),f("contact_phone","Contact phone"),f("priority_label","Severity",type="select",options=opts(("routine","Routine"),("urgent","Urgent"),("critical","Critical")),required=True)]
        values={"patient_id":case["patient_id"],"receiving_hospital_id":case["receiving_hospital_id"],"emergency_type":case["emergency_type"],"emergency_location":case["emergency_location"],"location_notes":case["location_notes"],"contact_name":case["contact_name"],"contact_phone":case["contact_phone"],"priority_label":case["priority_label"]}
        for field in fields: field["value"]=as_form_value(values.get(field["name"]))
        return render_edit("Edit emergency case",case["case_ref"],"edit_emergency",fields)

    @app.post("/emergencies/<int:case_id>/status")
    @permission_required("cases.write")
    def emergency_status(case_id):
        new_status=request.form.get("case_status")
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT case_status,receiving_hospital_id FROM emergency_case WHERE emergency_case_id=%s FOR UPDATE",(case_id,)); row=cur.fetchone()
                if not row: raise ValueError("Emergency case not found.")
                require_hospital_access(row["receiving_hospital_id"])
                transitions={"registered":{"response_in_progress","cancelled"},"response_in_progress":{"at_hospital","cancelled"},"at_hospital":{"completed","cancelled"}}
                if new_status not in transitions.get(row["case_status"],set()): raise ValueError("That case status transition is not allowed.")
                if new_status=="cancelled":
                    cur.execute("SELECT dispatch_id FROM ambulance_dispatch WHERE emergency_case_id=%s AND dispatch_status IN ('dispatched','picked_up') FOR UPDATE",(case_id,))
                    if cur.fetchone(): raise ValueError("Cancel active ambulance dispatches before cancelling this emergency case.")
                closed="UTC_TIMESTAMP()" if new_status in {"completed","cancelled"} else "NULL"
                cur.execute(f"UPDATE emergency_case SET case_status=%s,closed_at={closed} WHERE emergency_case_id=%s",(new_status,case_id))
                cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,%s,%s)",(case_id,g.user["user_id"],"status_"+new_status,"Emergency case status updated"))
                add_audit(cur,"status_change","emergency_case",case_id,{"status":new_status})
            flash("Emergency case status updated.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Emergency status update failed"); flash("Could not update case status.","error")
        return redirect(url_for("emergency_detail",case_id=case_id))

    @app.route("/ambulances", methods=["GET","POST"])
    @permission_required("ambulances.read")
    def ambulances():
        if request.method=="POST":
            if not can("ambulances.write"): abort(403)
            def create():
                hospital_id=int(request.form["home_hospital_id"])
                require_hospital_access(hospital_id)
                with transaction() as conn:
                    cur=conn.cursor()
                    if request.form.get("record_type")=="driver":
                        name=required_text("display_name","Driver name",160)
                        cur.execute("INSERT INTO driver(employer_hospital_id,driver_ref,display_name,phone) VALUES(%s,%s,%s,%s)",(hospital_id,make_ref("DRV"),name,request.form.get("phone") or None))
                        add_audit(cur,"create","driver",cur.lastrowid)
                    else:
                        code=required_text("vehicle_code","Vehicle code",30).upper(); reg=required_text("registration_number","Registration number",30).upper()
                        cur.execute("INSERT INTO ambulance(home_hospital_id,vehicle_code,registration_number) VALUES(%s,%s,%s)",(hospital_id,code,reg))
                        add_audit(cur,"create","ambulance",cur.lastrowid)
            return run_form(create,"Fleet record added.","ambulances")
        scope=assigned_hospital_id()
        fleet_filter=" WHERE a.home_hospital_id=%s" if scope is not None else ""
        driver_filter=" WHERE d.employer_hospital_id=%s" if scope is not None else ""
        fleet_sql="SELECT a.ambulance_id AS record_id,a.vehicle_code AS reference,'ambulance' AS record_type,h.name AS hospital,a.operational_status AS status,a.registration_number AS details FROM ambulance a JOIN hospital h ON h.hospital_id=a.home_hospital_id"+fleet_filter+" UNION ALL SELECT d.driver_id,d.driver_ref,'driver',h.name,IF(d.is_active,'active','inactive'),COALESCE(d.phone,'') FROM driver d JOIN hospital h ON h.hospital_id=d.employer_hospital_id"+driver_filter+" ORDER BY record_type,reference"
        fleet_params=(scope,scope) if scope is not None else ()
        rows=fetch_all(fleet_sql,fleet_params)
        hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND hospital_id=%s ORDER BY name" if scope is not None else "SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name", (scope,) if scope is not None else ())
        fields=[f("record_type","Record type",type="select",options=opts(("ambulance","Ambulance"),("driver","Driver")),required=True),f("home_hospital_id","Hospital",type="select",options=hs,required=True),f("vehicle_code","Vehicle code"),f("registration_number","Registration number"),f("display_name","Driver name"),f("phone","Phone")]
        return render_records("Ambulance & driver management","Maintain response vehicles and driver records.","ambulances",[c("record_type","Type"),c("reference","Reference"),c("hospital","Hospital"),c("status","Status"),c("details","Details")],rows,fields,"ambulances","ambulances.write")

    @app.route("/fleet/<record_type>/<int:record_id>/edit",methods=["GET","POST"])
    @permission_required("ambulances.write")
    def edit_fleet_record(record_type,record_id):
        if record_type not in {"ambulance","driver"}: abort(404)
        table="ambulance" if record_type=="ambulance" else "driver"
        pk="ambulance_id" if record_type=="ambulance" else "driver_id"
        record=fetch_one(f"SELECT * FROM {table} WHERE {pk}=%s",(record_id,))
        if not record: abort(404)
        existing_hospital_id=record["home_hospital_id"] if record_type=="ambulance" else record["employer_hospital_id"]
        require_hospital_access(existing_hospital_id)
        if request.method=="POST":
            def update():
                hospital_key="home_hospital_id" if record_type=="ambulance" else "employer_hospital_id"
                hospital_id=int(request.form[hospital_key])
                require_hospital_access(hospital_id)
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    if record_type=="ambulance":
                        status=request.form.get("operational_status")
                        if status not in {"available","maintenance","inactive","dispatched"}: raise ValueError("Choose a valid ambulance state.")
                        cur.execute("SELECT dispatch_id FROM ambulance_dispatch WHERE ambulance_id=%s AND dispatch_status IN ('dispatched','picked_up') FOR UPDATE",(record_id,))
                        active_dispatch=cur.fetchone()
                        if active_dispatch and status!="dispatched": raise ValueError("An active ambulance must remain dispatched until its dispatch is completed.")
                        if not active_dispatch and status=="dispatched": raise ValueError("An ambulance can be marked dispatched only by creating a dispatch.")
                        cur.execute("UPDATE ambulance SET home_hospital_id=%s,vehicle_code=%s,registration_number=%s,operational_status=%s WHERE ambulance_id=%s",(hospital_id,required_text("vehicle_code","Vehicle code",30).upper(),required_text("registration_number","Registration number",30).upper(),status,record_id))
                    else:
                        cur.execute("SELECT dispatch_id FROM ambulance_dispatch WHERE driver_id=%s AND dispatch_status IN ('dispatched','picked_up') FOR UPDATE",(record_id,))
                        active_dispatch=cur.fetchone()
                        if active_dispatch and (request.form.get("is_active")!="1" or hospital_id!=record["employer_hospital_id"]): raise ValueError("A driver assigned to an active dispatch cannot be deactivated or moved.")
                        cur.execute("UPDATE driver SET employer_hospital_id=%s,display_name=%s,phone=%s,is_active=%s WHERE driver_id=%s",(hospital_id,required_text("display_name","Driver name",160),request.form.get("phone") or None,request.form.get("is_active")=="1",record_id))
                    add_audit(cur,"update",record_type,record_id)
            return run_form(update,"Fleet record updated.","ambulances")
        scope=assigned_hospital_id()
        hospital_options=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND hospital_id=%s ORDER BY name" if scope is not None else "SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name", (scope,) if scope is not None else ())
        if record_type=="ambulance":
            fields=[f("home_hospital_id","Home hospital",type="select",options=hospital_options,required=True),f("vehicle_code","Vehicle code",required=True),f("registration_number","Registration number",required=True),f("operational_status","Status",type="select",options=opts(("available","Available"),("maintenance","Maintenance"),("inactive","Inactive"),("dispatched","Dispatched")),required=True)]
            values={"home_hospital_id":record["home_hospital_id"],"vehicle_code":record["vehicle_code"],"registration_number":record["registration_number"],"operational_status":record["operational_status"]}
            label=record["vehicle_code"]
        else:
            fields=[f("employer_hospital_id","Employer hospital",type="select",options=hospital_options,required=True),f("display_name","Driver name",required=True),f("phone","Phone"),f("is_active","Active",type="select",options=opts(("1","Active"),("0","Inactive")),required=True)]
            fields[0]["name"]="employer_hospital_id"
            values={"employer_hospital_id":record["employer_hospital_id"],"display_name":record["display_name"],"phone":record["phone"],"is_active":"1" if record["is_active"] else "0"}
            label=record["driver_ref"]
        for field in fields: field["value"]=as_form_value(values.get(field["name"]))
        return render_edit("Edit ambulance" if record_type=="ambulance" else "Edit driver",label,"edit_fleet_record",fields)

    @app.route("/dispatches", methods=["GET","POST"])
    @permission_required("dispatches.read")
    def dispatches():
        if request.method=="POST":
            if not can("dispatches.write"): abort(403)
            def create():
                case_id=int(request.form["emergency_case_id"]); ambulance_id=int(request.form["ambulance_id"]); driver_id=int(request.form["driver_id"]); destination_id=int(request.form["destination_hospital_id"])
                pickup=required_text("pickup_location","Pickup location"); destination=required_text("destination_location","Destination location")
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT case_status,receiving_hospital_id FROM emergency_case WHERE emergency_case_id=%s FOR UPDATE",(case_id,)); emergency=cur.fetchone()
                    if not emergency or emergency["case_status"] in {"completed","cancelled"}: raise ValueError("A closed emergency case cannot receive a new ambulance dispatch.")
                    require_hospital_access(emergency["receiving_hospital_id"])
                    cur.execute("SELECT * FROM ambulance WHERE ambulance_id=%s FOR UPDATE",(ambulance_id,)); ambulance=cur.fetchone()
                    if not ambulance or ambulance["operational_status"]!="available": raise ValueError("That ambulance is not available.")
                    require_hospital_access(ambulance["home_hospital_id"])
                    cur.execute("SELECT driver_id,employer_hospital_id FROM driver WHERE driver_id=%s AND is_active=1 FOR UPDATE",(driver_id,)); driver=cur.fetchone()
                    if not driver: raise ValueError("Select an active driver.")
                    require_hospital_access(driver["employer_hospital_id"])
                    cur.execute("SELECT dispatch_id FROM ambulance_dispatch WHERE driver_id=%s AND dispatch_status IN ('dispatched','picked_up') FOR UPDATE",(driver_id,))
                    if cur.fetchone(): raise ValueError("That driver already has an active dispatch.")
                    cur.execute("SELECT dispatch_id FROM ambulance_dispatch WHERE ambulance_id=%s AND dispatch_status IN ('dispatched','picked_up') FOR UPDATE",(ambulance_id,))
                    if cur.fetchone(): raise ValueError("That ambulance already has an active dispatch.")
                    cur.execute("INSERT INTO ambulance_dispatch(emergency_case_id,ambulance_id,driver_id,pickup_hospital_id,destination_hospital_id,dispatched_by_user_id,pickup_location,destination_location) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(case_id,ambulance_id,driver_id,ambulance["home_hospital_id"],destination_id,g.user["user_id"],pickup,destination))
                    dispatch_id=cur.lastrowid
                    cur.execute("UPDATE ambulance SET operational_status='dispatched' WHERE ambulance_id=%s",(ambulance_id,))
                    cur.execute("UPDATE emergency_case SET case_status='response_in_progress' WHERE emergency_case_id=%s AND case_status='registered'",(case_id,))
                    cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,'ambulance_dispatched',%s)",(case_id,g.user["user_id"],f"Dispatch {dispatch_id} started"))
                    add_audit(cur,"create","ambulance_dispatch",dispatch_id)
                    create_notifications(cur,("dispatches.read",),"dispatch",f"Ambulance dispatch {dispatch_id} assigned to emergency {case_id}.","ambulance_dispatch",dispatch_id,g.user["user_id"])
            return run_form(create,"Ambulance dispatched.","dispatches")
        if request.args.get("action") and request.method=="GET":
            abort(405)
        scope=assigned_hospital_id()
        dispatch_filter=" WHERE d.pickup_hospital_id=%s OR d.destination_hospital_id=%s" if scope is not None else ""
        rows=fetch_all("SELECT d.dispatch_id,c.case_ref,a.vehicle_code,dr.display_name AS driver,d.pickup_location,h.name AS destination,d.dispatch_status,d.dispatched_at FROM ambulance_dispatch d JOIN emergency_case c ON c.emergency_case_id=d.emergency_case_id JOIN ambulance a ON a.ambulance_id=d.ambulance_id JOIN driver dr ON dr.driver_id=d.driver_id JOIN hospital h ON h.hospital_id=d.destination_hospital_id"+dispatch_filter+" ORDER BY d.dispatched_at DESC", (scope,scope) if scope is not None else ())
        case_filter=" AND receiving_hospital_id=%s" if scope is not None else ""
        cases=fetch_all("SELECT emergency_case_id AS value,CONCAT(case_ref,' · ',emergency_location) AS label FROM emergency_case WHERE case_status NOT IN ('completed','cancelled')"+case_filter+" ORDER BY reported_at DESC", (scope,) if scope is not None else ())
        vehicles=fetch_all("SELECT ambulance_id AS value,CONCAT(vehicle_code,' · ',registration_number) AS label FROM ambulance WHERE operational_status='available'"+(" AND home_hospital_id=%s" if scope is not None else "")+" ORDER BY vehicle_code", (scope,) if scope is not None else ())
        drivers=fetch_all("SELECT driver_id AS value,display_name AS label FROM driver WHERE is_active=1"+(" AND employer_hospital_id=%s" if scope is not None else "")+" ORDER BY display_name", (scope,) if scope is not None else ())
        hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        fields=[f("emergency_case_id","Emergency case",type="select",options=cases,required=True),f("ambulance_id","Available ambulance",type="select",options=vehicles,required=True),f("driver_id","Driver",type="select",options=drivers,required=True),f("pickup_location","Pickup location",required=True),f("destination_hospital_id","Destination hospital",type="select",options=hs,required=True),f("destination_location","Destination location",required=True)]
        return render_records("Ambulance dispatches","Dispatch an available vehicle and record arrival milestones.","dispatches",[c("case_ref","Case"),c("vehicle_code","Ambulance"),c("driver","Driver"),c("pickup_location","Pickup"),c("destination","Destination"),c("dispatch_status","Status"),c("dispatched_at","Dispatched")],rows,fields,"dispatches","dispatches.write")

    @app.get("/dispatches/<int:dispatch_id>")
    @permission_required("dispatches.read")
    def dispatch_detail(dispatch_id):
        dispatch=fetch_one("SELECT d.*,c.case_ref,a.vehicle_code,a.registration_number,dr.driver_ref,dr.display_name AS driver,h.name AS destination FROM ambulance_dispatch d JOIN emergency_case c ON c.emergency_case_id=d.emergency_case_id JOIN ambulance a ON a.ambulance_id=d.ambulance_id JOIN driver dr ON dr.driver_id=d.driver_id JOIN hospital h ON h.hospital_id=d.destination_hospital_id WHERE d.dispatch_id=%s",(dispatch_id,))
        if not dispatch: abort(404)
        require_related_hospital_access(dispatch["pickup_hospital_id"],dispatch["destination_hospital_id"])
        return render_template("dispatch_detail.html",dispatch=dispatch)

    @app.post("/dispatches/<int:dispatch_id>/status")
    @permission_required("dispatches.write")
    def dispatch_status(dispatch_id):
        new_status=request.form.get("status")
        def update():
            transitions={"dispatched":{"picked_up","cancelled"},"picked_up":{"arrived","cancelled"}}
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT emergency_case_id,pickup_hospital_id,destination_hospital_id FROM ambulance_dispatch WHERE dispatch_id=%s",(dispatch_id,)); dispatch_ref=cur.fetchone()
                if not dispatch_ref: raise ValueError("Dispatch not found.")
                require_related_hospital_access(dispatch_ref["pickup_hospital_id"],dispatch_ref["destination_hospital_id"])
                cur.execute("SELECT case_status,receiving_hospital_id FROM emergency_case WHERE emergency_case_id=%s FOR UPDATE",(dispatch_ref["emergency_case_id"],))
                emergency=cur.fetchone()
                if not emergency: raise ValueError("Emergency case not found.")
                require_related_hospital_access(emergency["receiving_hospital_id"],dispatch_ref["pickup_hospital_id"],dispatch_ref["destination_hospital_id"])
                cur.execute("SELECT d.*,a.operational_status FROM ambulance_dispatch d JOIN ambulance a ON a.ambulance_id=d.ambulance_id WHERE dispatch_id=%s FOR UPDATE",(dispatch_id,)); row=cur.fetchone()
                if not row: raise ValueError("Dispatch not found.")
                if new_status not in transitions.get(row["dispatch_status"],set()): raise ValueError("That dispatch status change is not allowed.")
                stamp="picked_up_at" if new_status=="picked_up" else "arrived_at" if new_status=="arrived" else None
                if stamp: cur.execute(f"UPDATE ambulance_dispatch SET dispatch_status=%s,{stamp}=UTC_TIMESTAMP() WHERE dispatch_id=%s",(new_status,dispatch_id))
                else: cur.execute("UPDATE ambulance_dispatch SET dispatch_status=%s WHERE dispatch_id=%s",(new_status,dispatch_id))
                if new_status in {"arrived","cancelled"}:
                    cur.execute("UPDATE ambulance SET operational_status='available' WHERE ambulance_id=%s",(row["ambulance_id"],))
                if new_status=="arrived":
                    cur.execute("UPDATE emergency_case SET case_status='at_hospital' WHERE emergency_case_id=%s",(row["emergency_case_id"],))
                elif new_status=="cancelled":
                    cur.execute("SELECT COUNT(*) AS active_dispatches FROM ambulance_dispatch WHERE emergency_case_id=%s AND dispatch_status IN ('dispatched','picked_up')",(row["emergency_case_id"],))
                    if cur.fetchone()["active_dispatches"] == 0:
                        cur.execute("UPDATE emergency_case SET case_status='registered' WHERE emergency_case_id=%s AND case_status='response_in_progress'",(row["emergency_case_id"],))
                cur.execute("INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details) VALUES(%s,%s,%s,%s)",(row["emergency_case_id"],g.user["user_id"],"dispatch_"+new_status,f"Dispatch {dispatch_id}"))
                add_audit(cur,"status_change","ambulance_dispatch",dispatch_id,{"status":new_status})
        return run_form(update,"Dispatch status updated.","dispatches")

    @app.route("/donor/register", methods=["GET", "POST"])
    def donor_register():
        if request.method == "POST":
            try:
                username = required_text("username", "Username", 80)
                password = required_text("password", "Password", 200)
                if len(password) < 12:
                    raise ValueError("Password must contain at least 12 characters.")
                name = required_text("display_name", "Name", 160)
                city = required_text("city", "City", 100)
                group_id = int(request.form.get("blood_group_id", ""))
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO donor(blood_group_id,donor_ref,display_name,phone,email,city,region,availability_status,is_active) VALUES(%s,%s,%s,%s,%s,%s,%s,'unavailable',0)", (group_id, make_ref("DON"), name, request.form.get("phone") or None, request.form.get("email") or None, city, request.form.get("region") or None))
                    donor_id = cur.lastrowid
                    cur.execute("INSERT INTO donor_account(donor_id,username,password_hash,is_active) VALUES(%s,%s,%s,0)", (donor_id, username, generate_password_hash(password)))
                    create_notifications(cur, ("donors.write",), "donor_registration", f"Donor account registration from {name} is awaiting review.", "donor", donor_id)
                flash("Registration received. Staff approval is required before your account can sign in.", "success")
                return redirect(url_for("donor_login"))
            except ValueError as exc:
                flash(str(exc), "error")
            except MySQLError:
                app.logger.exception("Donor registration failed")
                flash("Registration could not be saved. Check the username and required information.", "error")
        try:
            groups = fetch_all("SELECT blood_group_id AS value, CONCAT(code,' — ',label) AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        except MySQLError:
            app.logger.exception("Donor registration form could not load blood groups")
            flash("The MySQL database is unavailable. Donor registration is temporarily unavailable.", "error")
            return render_template("donor_register.html", groups=[]), 503
        return render_template("donor_register.html", groups=groups)

    @app.route("/donor/login", methods=["GET", "POST"])
    def donor_login():
        if request.method == "POST":
            account = fetch_one("SELECT donor_account_id,password_hash,is_active FROM donor_account WHERE username=%s", (request.form.get("username", "").strip(),))
            if account and check_password_hash(account["password_hash"], request.form.get("password", "")):
                if not account["is_active"]:
                    flash("This donor account is awaiting staff approval.", "info")
                else:
                    session.clear()
                    session["portal"] = "donor"
                    session["donor_account_id"] = account["donor_account_id"]
                    flash("Signed in to the donor portal.", "success")
                    return redirect(url_for("donor_portal"))
            else:
                flash("Username or password is incorrect.", "error")
        return render_template("donor_login.html")

    @app.post("/donor/logout")
    def donor_logout():
        session.pop("donor_account_id", None)
        flash("You have signed out.", "success")
        return redirect(url_for("donor_login"))

    @app.get("/donor/portal")
    @donor_login_required
    def donor_portal():
        donor = g.donor
        requests = fetch_all("SELECT DISTINCT b.blood_request_id,b.request_ref,b.urgency,h.name AS hospital,h.city,br.code AS blood_group,(SELECT SUM(GREATEST(0,l.required_units-(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')))) FROM blood_request_line l WHERE l.blood_request_id=b.blood_request_id AND l.blood_group_id=br.blood_group_id) AS pending_units FROM blood_request b JOIN hospital h ON h.hospital_id=b.requesting_hospital_id JOIN blood_request_line l ON l.blood_request_id=b.blood_request_id JOIN blood_group br ON br.blood_group_id=l.blood_group_id JOIN donor d ON d.blood_group_id=br.blood_group_id WHERE d.donor_id=%s AND b.request_status IN ('approved','partially_fulfilled') AND d.availability_status='available' AND NOT EXISTS(SELECT 1 FROM donor_contact_attempt ca WHERE ca.donor_id=d.donor_id AND ca.blood_request_id=b.blood_request_id) ORDER BY b.created_at DESC", (donor["donor_id"],))
        return render_template("donor_portal.html", donor=donor, requests=requests)

    @app.post("/donor/availability")
    @donor_login_required
    def donor_portal_availability():
        status = request.form.get("availability_status")
        if status not in {"available", "unavailable", "do_not_contact"}:
            abort(400)
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE donor SET availability_status=%s,last_availability_update=UTC_TIMESTAMP() WHERE donor_id=%s", (status, g.donor["donor_id"]))
        flash("Availability updated.", "success")
        return redirect(url_for("donor_portal"))

    @app.post("/donor/requests/<int:request_id>/respond")
    @donor_login_required
    def donor_respond(request_id):
        outcome = request.form.get("outcome")
        if outcome not in {"interested", "declined"}:
            abort(400)
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("""SELECT l.request_line_id,b.requesting_hospital_id,b.request_ref,b.requested_by_user_id FROM blood_request b
                JOIN blood_request_line l ON l.blood_request_id=b.blood_request_id
                JOIN donor d ON d.blood_group_id=l.blood_group_id
                WHERE b.blood_request_id=%s AND b.request_status IN ('approved','partially_fulfilled')
                  AND d.donor_id=%s AND d.is_active=1 AND d.availability_status='available'
                  AND GREATEST(l.required_units-(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')),0)>0
                AND NOT EXISTS(SELECT 1 FROM donor_contact_attempt ca WHERE ca.donor_id=d.donor_id AND ca.blood_request_id=b.blood_request_id)
                ORDER BY l.line_number LIMIT 1 FOR UPDATE""",(request_id,g.donor["donor_id"]))
            match=cur.fetchone()
            if not match: abort(404)
            cur.execute("INSERT INTO donor_contact_attempt(donor_id,blood_request_id,request_line_id,contact_method,outcome,response_notes) VALUES(%s,%s,%s,'other',%s,'Donor portal response')", (g.donor["donor_id"], request_id, match["request_line_id"], outcome))
            cur.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,alert_type,severity,message) VALUES(%s,%s,%s,'donor_response','medium',%s)",(match["requesting_hospital_id"],request_id,match["request_line_id"],f"Donor response ({outcome}) for {match['request_ref']}"))
            cur.execute("""SELECT DISTINCT u.user_id FROM app_user u JOIN role_permission rp ON rp.role_id=u.role_id
                JOIN permission p ON p.permission_id=rp.permission_id WHERE u.hospital_id=%s AND u.is_active=1
                AND u.approval_status='approved' AND p.permission_code IN ('requests.read','contacts.write')""",(match["requesting_hospital_id"],))
            for (recipient_id,) in cur.fetchall():
                create_user_notification(cur,recipient_id,"donor_response",f"A donor {outcome} your request {match['request_ref']}.","blood_request",request_id)
            add_audit(cur,"donor_response","blood_request",request_id,{"donor_id":g.donor["donor_id"],"outcome":outcome})
        flash("Your response was recorded.", "success")
        return redirect(url_for("donor_portal"))

    @app.post("/donors/<int:donor_id>/approve")
    @permission_required("donors.write")
    def approve_donor_account(donor_id):
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE donor SET is_active=1,availability_status='unavailable',last_availability_update=UTC_TIMESTAMP() WHERE donor_id=%s AND EXISTS(SELECT 1 FROM donor_account WHERE donor_id=%s)", (donor_id, donor_id))
            cur.execute("UPDATE donor_account SET is_active=1 WHERE donor_id=%s", (donor_id,))
            if cur.rowcount != 1:
                raise ValueError("Pending donor account not found.")
        flash("Donor account approved. The donor can now sign in and set availability.", "success")
        return redirect(url_for("donors"))

    @app.route("/donors", methods=["GET","POST"])
    @permission_required("donors.read")
    def donors():
        if request.method=="POST":
            if not can("donors.write"): abort(403)
            def create():
                name=required_text("display_name","Donor display name",160); group_id=int(request.form["blood_group_id"]); city=required_text("city","City",100)
                availability=request.form.get("availability_status","available")
                if availability not in {"available","unavailable","do_not_contact"}: raise ValueError("Choose a valid availability status.")
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("INSERT INTO donor(blood_group_id,donor_ref,display_name,phone,email,city,region,postal_code,availability_status,last_availability_update) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())",(group_id,make_ref("DON"),name,request.form.get("phone") or None,request.form.get("email") or None,city,request.form.get("region") or None,request.form.get("postal_code") or None,availability))
                    add_audit(cur,"create","donor",cur.lastrowid)
            return run_form(create,"Donor profile added.","donors")
        group=request.args.get("blood_group",""); city=request.args.get("city","").strip(); availability=request.args.get("availability","")
        clauses=["(d.is_active=1 OR da.donor_account_id IS NOT NULL)"]; params=[]
        if group: clauses.append("bg.code=%s"); params.append(group)
        if city: clauses.append("d.city LIKE %s"); params.append(f"%{city}%")
        if availability in {"available","unavailable","do_not_contact"}: clauses.append("d.availability_status=%s"); params.append(availability)
        rows=fetch_all("SELECT d.donor_id,d.donor_ref,d.display_name,bg.code AS blood_group,d.city,d.region,d.phone,d.availability_status,d.last_availability_update,d.is_active,da.is_active AS account_active,da.donor_account_id FROM donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id LEFT JOIN donor_account da ON da.donor_id=d.donor_id WHERE "+" AND ".join(clauses)+" ORDER BY d.display_name",tuple(params))
        groups=fetch_all("SELECT blood_group_id AS value,code AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        fields=[f("blood_group_id","Recorded blood group",type="select",options=groups,required=True),f("display_name","Synthetic display name",required=True),f("phone","Phone"),f("email","Email",type="email"),f("city","City",required=True),f("region","Region"),f("postal_code","Postal code"),f("availability_status","Availability",type="select",options=opts(("available","Available"),("unavailable","Unavailable"),("do_not_contact","Do not contact")),required=True)]
        return render_records("Donor coordination","Search exact recorded blood group, location and availability. Candidate listing is not clinical eligibility.","donors",[c("donor_ref","Reference"),c("display_name","Donor"),c("blood_group","Group"),c("city","City"),c("phone","Phone"),c("availability_status","Availability"),c("last_availability_update","Updated")],rows,fields,"donors","donors.write",filters={"blood_group":group,"city":city,"availability":availability},filter_groups=[g["label"] for g in fetch_all("SELECT code AS label FROM blood_group ORDER BY code")])

    @app.get("/donors/<int:donor_id>")
    @permission_required("donors.read")
    def donor_detail(donor_id):
        donor=fetch_one("SELECT d.*,bg.code AS blood_group,(SELECT MAX(collected_at) FROM donation WHERE donor_id=d.donor_id) AS last_donation FROM donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id WHERE d.donor_id=%s",(donor_id,))
        if not donor: abort(404)
        donations=fetch_all("SELECT n.donation_ref,n.collected_at,n.donation_status,h.name AS hospital,COUNT(u.blood_unit_id) AS units FROM donation n JOIN hospital h ON h.hospital_id=n.collection_hospital_id LEFT JOIN blood_unit u ON u.donation_id=n.donation_id WHERE n.donor_id=%s GROUP BY n.donation_id ORDER BY n.collected_at DESC",(donor_id,))
        contacts=fetch_all("SELECT ca.attempted_at,ca.contact_method,ca.outcome,ca.response_notes,b.request_ref,au.display_name AS staff FROM donor_contact_attempt ca JOIN blood_request b ON b.blood_request_id=ca.blood_request_id LEFT JOIN app_user au ON au.user_id=ca.contacted_by_user_id WHERE ca.donor_id=%s ORDER BY ca.attempted_at DESC",(donor_id,))
        return render_template("donor_detail.html",donor=donor,donations=donations,contacts=contacts)

    @app.route("/donors/<int:donor_id>/edit",methods=["GET","POST"])
    @permission_required("donors.write")
    def edit_donor(donor_id):
        donor=fetch_one("SELECT * FROM donor WHERE donor_id=%s",(donor_id,))
        if not donor: abort(404)
        if request.method=="POST":
            def update():
                group_id=int(request.form["blood_group_id"])
                availability=request.form.get("availability_status")
                if availability not in {"available","unavailable","do_not_contact"}:
                    raise ValueError("Choose a valid donor availability status.")
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    if group_id!=donor["blood_group_id"]:
                        cur.execute("SELECT donation_id FROM donation WHERE donor_id=%s LIMIT 1",(donor_id,))
                        if cur.fetchone(): raise ValueError("Blood group cannot be changed after a donation has been recorded.")
                    cur.execute("UPDATE donor SET blood_group_id=%s,display_name=%s,phone=%s,email=%s,city=%s,region=%s,postal_code=%s,availability_status=%s,last_availability_update=UTC_TIMESTAMP() WHERE donor_id=%s",(group_id,required_text("display_name","Donor display name",160),request.form.get("phone") or None,request.form.get("email") or None,required_text("city","City",100),request.form.get("region") or None,request.form.get("postal_code") or None,availability,donor_id))
                    add_audit(cur,"update","donor",donor_id)
            return run_form(update,"Donor profile updated.","donors")
        groups=fetch_all("SELECT blood_group_id AS value,code AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        fields=[f("blood_group_id","Recorded blood group",type="select",options=groups,required=True),f("display_name","Display name",required=True),f("phone","Phone"),f("email","Email",type="email"),f("city","City",required=True),f("region","Region"),f("postal_code","Postal code"),f("availability_status","Availability",type="select",options=opts(("available","Available"),("unavailable","Unavailable"),("do_not_contact","Do not contact")),required=True)]
        for field in fields: field["value"]=as_form_value(donor.get(field["name"]))
        return render_edit("Edit donor",donor["donor_ref"],"edit_donor",fields)

    @app.post("/donors/<int:donor_id>/delete")
    @permission_required("donors.write")
    def delete_donor(donor_id):
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT donor_ref FROM donor WHERE donor_id=%s FOR UPDATE",(donor_id,)); donor=cur.fetchone()
                if not donor: raise ValueError("Donor not found.")
                cur.execute("SELECT contact_attempt_id FROM donor_contact_attempt WHERE donor_id=%s LIMIT 1",(donor_id,)); attempted=cur.fetchone()
                if attempted: raise ValueError("Donor contact history exists; use Do not contact to preserve history.")
                cur.execute("SELECT donation_id FROM donation WHERE donor_id=%s LIMIT 1",(donor_id,))
                if cur.fetchone(): raise ValueError("Donation history exists; this donor cannot be deleted.")
                add_audit(cur,"delete","donor",donor_id,{"donor_ref":donor["donor_ref"]})
                cur.execute("DELETE FROM donor WHERE donor_id=%s",(donor_id,))
            flash("Unlinked donor profile deleted.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Donor deletion failed"); flash("Donor cannot be deleted while it is referenced by history.","error")
        return redirect(url_for("donors"))

    @app.post("/donors/<int:donor_id>/availability")
    @permission_required("donors.write")
    def donor_availability(donor_id):
        status=request.form.get("availability_status")
        if status not in {"available","unavailable","do_not_contact"}: abort(400)
        try:
            with transaction() as conn:
                cur=conn.cursor()
                cur.execute("UPDATE donor SET availability_status=%s,last_availability_update=UTC_TIMESTAMP() WHERE donor_id=%s AND is_active=1",(status,donor_id))
                if cur.rowcount!=1: raise ValueError("Active donor not found.")
                add_audit(cur,"availability_change","donor",donor_id,{"status":status})
            flash("Donor availability updated.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Donor availability update failed"); flash("Could not update donor availability.","error")
        return redirect(url_for("donors"))

    @app.route("/donor-contacts", methods=["GET","POST"])
    @permission_required("donors.read")
    def donor_contacts():
        if request.method=="POST":
            if not can("contacts.write"): abort(403)
            def create():
                donor_id=int(request.form["donor_id"]); request_id=int(request.form["blood_request_id"])
                require_blood_request_access(request_id)
                method=request.form.get("contact_method"); outcome=request.form.get("outcome")
                if method not in {"phone","message","email","other"} or outcome not in {"no_answer","reached","interested","declined","follow_up"}: raise ValueError("Choose a valid contact method and response.")
                with transaction() as conn:
                    cur=conn.cursor()
                    line_id=int(request.form["request_line_id"]) if request.form.get("request_line_id") else None
                    if line_id:
                        cur.execute("SELECT request_line_id FROM blood_request_line WHERE request_line_id=%s AND blood_request_id=%s",(line_id,request_id))
                        if not cur.fetchone(): raise ValueError("The selected line does not belong to that request.")
                    cur.execute("INSERT INTO donor_contact_attempt(donor_id,blood_request_id,request_line_id,contacted_by_user_id,contact_method,outcome,response_notes) VALUES(%s,%s,%s,%s,%s,%s,%s)",(donor_id,request_id,line_id,g.user["user_id"],method,outcome,request.form.get("response_notes") or None))
                    contact_id=cur.lastrowid
                    add_audit(cur,"create","donor_contact_attempt",contact_id)
                    if outcome in {"interested","follow_up"}:
                        create_notifications(cur,("requests.read",),"donor_response",f"A donor response ({outcome.replace('_',' ')}) was recorded for request {request_id}.","blood_request",request_id,g.user["user_id"])
                    if outcome=="follow_up":
                        cur.execute("SELECT requesting_hospital_id,request_ref FROM blood_request WHERE blood_request_id=%s",(request_id,)); req=cur.fetchone()
                        if req:
                            cur.execute("INSERT INTO alert(hospital_id,blood_request_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,'donor_response','medium',%s)",(req[0],request_id,g.user["user_id"],f"Donor contact for {req[1]} needs follow-up"))
                            create_notifications(cur,("requests.read",),"donor_response",f"Donor contact for {req[1]} needs follow-up.","blood_request",request_id,g.user["user_id"])
            return run_form(create,"Donor contact attempt recorded.","donor_contacts")
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT d.display_name AS donor,b.request_ref,bh.code AS blood_group,ca.contact_method,ca.outcome,ca.attempted_at,ca.response_notes FROM donor_contact_attempt ca JOIN donor d ON d.donor_id=ca.donor_id JOIN blood_request b ON b.blood_request_id=ca.blood_request_id JOIN blood_group bh ON bh.blood_group_id=d.blood_group_id WHERE (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY ca.attempted_at DESC",(scope,scope))
        donors_list=fetch_all("SELECT donor_id AS value,CONCAT(donor_ref,' · ',display_name) AS label FROM donor WHERE is_active=1 ORDER BY display_name")
        reqs=fetch_all("SELECT blood_request_id AS value,CONCAT(request_ref,' · ',request_status) AS label FROM blood_request WHERE request_status NOT IN ('cancelled','rejected') AND (%s IS NULL OR requesting_hospital_id=%s) ORDER BY requested_at DESC",(scope,scope))
        lines=fetch_all("SELECT l.request_line_id AS value,CONCAT(b.request_ref,' / ',bg.code,' ',bc.name) AS label FROM blood_request_line l JOIN blood_request b ON b.blood_request_id=l.blood_request_id JOIN blood_group bg ON bg.blood_group_id=l.blood_group_id JOIN blood_component bc ON bc.component_id=l.component_id WHERE (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY b.requested_at DESC",(scope,scope))
        fields=[f("donor_id","Donor",type="select",options=donors_list,required=True),f("blood_request_id","Blood request",type="select",options=reqs,required=True),f("request_line_id","Request line (optional)",type="select",options=[{"value":"","label":"No specific line"}]+lines),f("contact_method","Contact method",type="select",options=opts(("phone","Phone"),("message","Message"),("email","Email"),("other","Other")),required=True),f("outcome","Response",type="select",options=opts(("no_answer","No answer"),("reached","Reached"),("interested","Interested"),("declined","Declined"),("follow_up","Follow up")),required=True),f("response_notes","Notes")]
        for index,arg in ((0,"donor_id"),(1,"blood_request_id")):
            selected=request.args.get(arg)
            options=donors_list if index==0 else reqs
            if selected and any(str(option["value"])==selected for option in options): fields[index]["value"]=selected
        selected_line=request.args.get("request_line_id")
        if selected_line and any(str(option["value"])==selected_line for option in lines): fields[2]["value"]=selected_line
        return render_records("Donor contact history","Record contact attempts and responses linked to a request.","donor_contacts",[c("donor","Donor"),c("request_ref","Request"),c("blood_group","Recorded group"),c("contact_method","Method"),c("outcome","Response"),c("attempted_at","Attempted"),c("response_notes","Notes")],rows,fields,"donor_contacts","contacts.write")

    @app.route("/blood", methods=["GET","POST"])
    @permission_required("blood.read")
    def blood():
        if request.method=="POST":
            if not can("blood.write"): abort(403)
            def create():
                donor_id=int(request.form["donor_id"]); hospital_id=int(request.form["hospital_id"]); group_id=int(request.form["blood_group_id"]); component_id=int(request.form["component_id"])
                require_hospital_access(hospital_id)
                expiry=mysql_datetime(request.form.get("expires_on"),"Expiry date")
                if not expiry or expiry<=datetime.now(timezone.utc).replace(tzinfo=None): raise ValueError("Enter a future expiry date.")
                unit_code=required_text("unit_code","Unit code",50)
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("SELECT blood_group_id FROM donor WHERE donor_id=%s AND is_active=1",(donor_id,))
                    donor=cur.fetchone()
                    if not donor: raise ValueError("Select an active donor.")
                    if donor[0]!=group_id: raise ValueError("Blood unit group must match the donor's recorded blood group.")
                    cur.execute("INSERT INTO donation(donor_id,collection_hospital_id,recorded_by_user_id,donation_ref,collected_at) VALUES(%s,%s,%s,%s,UTC_TIMESTAMP())",(donor_id,hospital_id,g.user["user_id"],make_ref("DNT")))
                    donation_id=cur.lastrowid
                    cur.execute("INSERT INTO blood_unit(donation_id,component_id,blood_group_id,current_hospital_id,recorded_by_user_id,unit_code,expires_on,inventory_status) VALUES(%s,%s,%s,%s,%s,%s,%s,'quarantined')",(donation_id,component_id,group_id,hospital_id,g.user["user_id"],unit_code,expiry))
                    unit_id=cur.lastrowid
                    cur.execute("INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes) VALUES(%s,%s,'inventory_clearance',UTC_TIMESTAMP(),'pending','Initial clearance result pending')",(unit_id,g.user["user_id"]))
                    add_audit(cur,"create","blood_unit",unit_id,{"unit_code":unit_code})
            return run_form(create,"Donation and individual blood unit recorded. Clearance is pending until a screening result is entered.","blood")
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT u.blood_unit_id,u.donation_id,u.component_id,u.current_hospital_id AS hospital_id,u.unit_code,bg.code AS blood_group,bc.name AS component,h.name AS hospital,d.donation_ref,dn.display_name AS donor,u.expires_on,CASE WHEN EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit')) THEN 'in_transfer' WHEN u.expires_on<=UTC_TIMESTAMP() AND u.inventory_status NOT IN ('issued','discarded') THEN 'expired' ELSE u.inventory_status END AS inventory_status,COALESCE((SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1),'not recorded') AS screening FROM blood_unit u JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id JOIN hospital h ON h.hospital_id=u.current_hospital_id JOIN donation d ON d.donation_id=u.donation_id JOIN donor dn ON dn.donor_id=d.donor_id WHERE (%s IS NULL OR u.current_hospital_id=%s) ORDER BY u.created_at DESC",(scope,scope))
        donors_list=fetch_all("SELECT donor_id AS value,CONCAT(donor_ref,' · ',display_name) AS label FROM donor WHERE is_active=1 ORDER BY display_name")
        hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        groups=fetch_all("SELECT blood_group_id AS value,code AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        components=fetch_all("SELECT component_id AS value,name AS label FROM blood_component WHERE is_active=1 ORDER BY name")
        group_filter=request.args.get("blood_group",""); component_filter=request.args.get("component_id",""); hospital_filter=request.args.get("hospital_id",""); state_filter=request.args.get("status","")
        if group_filter: rows=[row for row in rows if row["blood_group"]==group_filter]
        if component_filter: rows=[row for row in rows if str(row.get("component_id",""))==component_filter]
        if hospital_filter: rows=[row for row in rows if str(row.get("hospital_id",""))==hospital_filter]
        if state_filter: rows=[row for row in rows if row["inventory_status"]==state_filter]
        rows=[dict(row,component_id=next((item["value"] for item in components if item["label"]==row["component"]),None),hospital_id=next((item["value"] for item in hs if item["label"]==row["hospital"]),None)) for row in rows]
        fields=[f("donor_id","Donor",type="select",options=donors_list,required=True),f("hospital_id","Collection hospital",type="select",options=hs,required=True),f("blood_group_id","Recorded blood group",type="select",options=groups,required=True),f("component_id","Component",type="select",options=components,required=True),f("unit_code","Unique unit code",required=True),f("expires_on","Expiry date and time",type="datetime-local",required=True)]
        return render_records("Blood bank","Track donations and blood components by individual unit.","blood",[c("unit_code","Unit"),c("blood_group","Group"),c("component","Component"),c("hospital","Current hospital"),c("donor","Donation from"),c("expires_on","Expires"),c("screening","Screening"),c("inventory_status","Inventory status")],rows,fields,"blood","blood.write",filters={"inventory":True,"blood_group":group_filter,"component_id":component_filter,"hospital_id":hospital_filter,"status":state_filter},filter_groups=[row["label"] for row in groups],filter_components=components,filter_hospitals=hs)

    @app.route("/donations/<int:donation_id>/components", methods=["GET", "POST"])
    @permission_required("blood.write")
    def donation_components(donation_id):
        if request.method == "POST":
            def create_component():
                component_id = int(request.form["component_id"])
                unit_code = required_text("unit_code", "Unit code", 50)
                expiry = mysql_datetime(request.form.get("expires_on"), "Expiry date")
                if not expiry or expiry <= datetime.now(timezone.utc).replace(tzinfo=None):
                    raise ValueError("Enter a future expiry date and time.")
                with transaction() as conn:
                    cur = conn.cursor(dictionary=True)
                    cur.execute("SELECT d.donation_id,d.collection_hospital_id,dn.blood_group_id FROM donation d JOIN donor dn ON dn.donor_id=d.donor_id WHERE d.donation_id=%s FOR UPDATE", (donation_id,))
                    donation = cur.fetchone()
                    if not donation:
                        raise ValueError("Donation record not found.")
                    cur.execute("SELECT component_id FROM blood_component WHERE component_id=%s AND is_active=1", (component_id,))
                    if not cur.fetchone():
                        raise ValueError("Choose an active blood component.")
                    cur.execute("INSERT INTO blood_unit(donation_id,component_id,blood_group_id,current_hospital_id,recorded_by_user_id,unit_code,expires_on,inventory_status) VALUES(%s,%s,%s,%s,%s,%s,%s,'quarantined')", (donation_id,component_id,donation["blood_group_id"],donation["collection_hospital_id"],g.user["user_id"],unit_code,expiry))
                    unit_id = cur.lastrowid
                    cur.execute("INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes) VALUES(%s,%s,'inventory_clearance',UTC_TIMESTAMP(),'pending','Initial clearance result pending')", (unit_id,g.user["user_id"]))
                    add_audit(cur,"create","blood_unit",unit_id,{"unit_code":unit_code,"donation_id":donation_id})
            return run_form(create_component,"Component unit added to the donation. Clearance is pending until screening is recorded.","donation_components",donation_id=donation_id)
        donation = fetch_one("SELECT d.donation_id,d.donation_ref,d.collected_at,dn.donor_ref,dn.display_name,bg.code AS blood_group FROM donation d JOIN donor dn ON dn.donor_id=d.donor_id JOIN blood_group bg ON bg.blood_group_id=dn.blood_group_id WHERE d.donation_id=%s", (donation_id,))
        if not donation:
            abort(404)
        units = fetch_all("SELECT u.unit_code,bc.name AS component,u.expires_on,u.inventory_status FROM blood_unit u JOIN blood_component bc ON bc.component_id=u.component_id WHERE u.donation_id=%s ORDER BY u.created_at,u.blood_unit_id", (donation_id,))
        components = fetch_all("SELECT component_id AS value,name AS label FROM blood_component WHERE is_active=1 ORDER BY name")
        return render_template("donation_components.html", donation=donation, units=units, components=components)

    @app.route("/blood/<int:unit_id>/edit",methods=["GET","POST"])
    @permission_required("blood.write")
    def edit_blood_unit(unit_id):
        unit=fetch_one("SELECT u.*,d.collected_at FROM blood_unit u JOIN donation d ON d.donation_id=u.donation_id WHERE u.blood_unit_id=%s",(unit_id,))
        if not unit: abort(404)
        require_hospital_access(unit["current_hospital_id"])
        if request.method=="POST":
            def update():
                expiry=mysql_datetime(request.form.get("expires_on"),"Expiry date")
                if not expiry or expiry<=unit["collected_at"]: raise ValueError("Expiry must be later than the collection timestamp.")
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT blood_unit_id FROM blood_unit WHERE blood_unit_id=%s FOR UPDATE",(unit_id,))
                    if not cur.fetchone(): raise ValueError("Blood unit not found.")
                    cur.execute("SELECT allocation_id FROM blood_allocation WHERE blood_unit_id=%s AND allocation_status IN ('reserved','issued')",(unit_id,))
                    if cur.fetchone(): raise ValueError("Expiry cannot be edited while the unit is allocated.")
                    cur.execute("SELECT transfer_id FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=%s AND t.transfer_status IN ('requested','approved','in_transit')",(unit_id,))
                    if cur.fetchone(): raise ValueError("Expiry cannot be edited while the unit is transferring.")
                    cur.execute("UPDATE blood_unit SET expires_on=%s WHERE blood_unit_id=%s",(expiry,unit_id))
                    add_audit(cur,"update","blood_unit",unit_id,{"unit_code":unit["unit_code"],"expires_on":str(expiry)})
            return run_form(update,"Blood unit expiry updated.","blood")
        fields=[f("expires_on","Expiry date and time",type="datetime-local",required=True)]
        fields[0]["value"]=as_form_value(unit["expires_on"])
        return render_edit("Edit blood unit expiry",unit["unit_code"],"edit_blood_unit",fields)

    @app.post("/blood/<int:unit_id>/discard")
    @permission_required("blood.write")
    def discard_blood_unit(unit_id):
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT unit_code,inventory_status FROM blood_unit WHERE blood_unit_id=%s FOR UPDATE",(unit_id,)); unit=cur.fetchone()
                if not unit: raise ValueError("Blood unit not found.")
                if unit["inventory_status"] in {"issued","discarded"}: raise ValueError("Issued or already discarded units cannot be discarded again.")
                cur.execute("SELECT allocation_id FROM blood_allocation WHERE blood_unit_id=%s AND allocation_status IN ('reserved','issued')",(unit_id,))
                if cur.fetchone(): raise ValueError("Release the active reservation before discarding this unit.")
                cur.execute("SELECT transfer_id FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=%s AND t.transfer_status IN ('requested','approved','in_transit')",(unit_id,))
                if cur.fetchone(): raise ValueError("A transferring unit cannot be discarded until the transfer is resolved.")
                cur.execute("UPDATE blood_unit SET inventory_status='discarded' WHERE blood_unit_id=%s",(unit_id,))
                add_audit(cur,"discard","blood_unit",unit_id,{"unit_code":unit["unit_code"],"reason":request.form.get("reason") or "Discarded by inventory operator"})
            flash("Blood unit marked discarded and excluded from availability.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Discard unit failed"); flash("The unit could not be discarded.","error")
        return redirect(url_for("blood"))

    @app.route("/blood-catalog",methods=["GET","POST"])
    @permission_required("blood.read")
    def blood_catalog():
        if request.method=="POST":
            if not can("blood.write"): abort(403)
            def create():
                kind=request.form.get("kind"); code=required_text("code","Code",24).upper(); name=required_text("name","Name",80)
                if kind not in {"group","component"}: raise ValueError("Choose a valid catalog type.")
                table="blood_group" if kind=="group" else "blood_component"
                second="label" if kind=="group" else "name"
                with transaction() as conn:
                    cur=conn.cursor(); cur.execute(f"INSERT INTO {table}(code,{second}) VALUES(%s,%s)",(code,name))
                    add_audit(cur,"create",table,cur.lastrowid,{"code":code})
            return run_form(create,"Blood catalog item added.","blood_catalog")
        groups=fetch_all("SELECT blood_group_id AS item_id,'group' AS kind,code,label AS name,is_active FROM blood_group")
        components=fetch_all("SELECT component_id AS item_id,'component' AS kind,code,name,is_active FROM blood_component")
        rows=groups+components; rows.sort(key=lambda row:(row["kind"],row["code"]))
        fields=[f("kind","Catalog type",type="select",options=opts(("group","Blood group"),("component","Component")),required=True),f("code","Code",required=True),f("name","Display name",required=True)]
        return render_records("Blood groups & components","Maintain lookup values used by requests, donors and unit records.","blood_catalog",[c("kind","Type"),c("code","Code"),c("name","Name"),c("is_active","Active")],rows,fields,"blood_catalog","blood.write")

    @app.route("/blood-catalog/<kind>/<int:item_id>/edit",methods=["GET","POST"])
    @permission_required("blood.write")
    def edit_catalog(kind,item_id):
        if kind not in {"group","component"}: abort(404)
        table="blood_group" if kind=="group" else "blood_component"; pk="blood_group_id" if kind=="group" else "component_id"; second="label" if kind=="group" else "name"
        row=fetch_one(f"SELECT * FROM {table} WHERE {pk}=%s",(item_id,))
        if not row: abort(404)
        if request.method=="POST":
            def update():
                code=required_text("code","Code",24).upper(); name=required_text("name","Name",80)
                with transaction() as conn:
                    cur=conn.cursor(); cur.execute(f"UPDATE {table} SET code=%s,{second}=%s WHERE {pk}=%s",(code,name,item_id)); add_audit(cur,"update",table,item_id,{"code":code})
            return run_form(update,"Catalog item updated.","blood_catalog")
        fields=[f("code","Code",required=True),f("name","Display name",required=True)]; fields[0]["value"]=row["code"]; fields[1]["value"]=row[second]
        return render_edit("Edit catalog item",row["code"],"edit_catalog",fields)

    @app.post("/blood-catalog/<kind>/<int:item_id>/active")
    @permission_required("blood.write")
    def catalog_active(kind,item_id):
        if kind not in {"group","component"}: abort(404)
        table="blood_group" if kind=="group" else "blood_component"; pk="blood_group_id" if kind=="group" else "component_id"
        active=request.form.get("active")=="1"
        with transaction() as conn:
            cur=conn.cursor(); cur.execute(f"UPDATE {table} SET is_active=%s WHERE {pk}=%s",(active,item_id))
            if cur.rowcount!=1: abort(404)
            add_audit(cur,"status_change",table,item_id,{"is_active":active})
        flash("Catalog availability updated.","success")
        return redirect(url_for("blood_catalog"))

    @app.route("/screenings", methods=["GET","POST"])
    @permission_required("blood.read")
    def screenings():
        if request.method=="POST":
            if not can("blood.write"): abort(403)
            def create():
                unit_id=int(request.form["blood_unit_id"]); test_code=required_text("test_code","Test code",40); result=request.form.get("result_status")
                if result not in {"pending","recorded","cleared","not_cleared","inconclusive"}: raise ValueError("Choose a valid recorded test result.")
                tested_at=mysql_datetime(request.form.get("tested_at"),"Test date") or datetime.now(timezone.utc).replace(tzinfo=None)
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT inventory_status,current_hospital_id FROM blood_unit WHERE blood_unit_id=%s FOR UPDATE",(unit_id,))
                    unit=cur.fetchone()
                    if not unit: raise ValueError("Select a valid blood unit.")
                    require_hospital_access(unit["current_hospital_id"])
                    cur.execute("SELECT allocation_id FROM blood_allocation WHERE blood_unit_id=%s AND allocation_status IN ('reserved','issued')",(unit_id,))
                    if cur.fetchone(): raise ValueError("Screening history cannot be changed while the unit has an active allocation.")
                    cur.execute("SELECT transfer_id FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=%s AND t.transfer_status IN ('requested','approved','in_transit')",(unit_id,))
                    if cur.fetchone(): raise ValueError("Screening history cannot be changed while the unit is transferring.")
                    cur.execute("INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,result_reference,notes) VALUES(%s,%s,%s,%s,%s,%s,%s)",(unit_id,g.user["user_id"],test_code,tested_at,result,request.form.get("result_reference") or None,request.form.get("notes") or None))
                    if test_code=="inventory_clearance":
                        target_status="available" if result=="cleared" else "quarantined"
                        cur.execute("UPDATE blood_unit SET inventory_status=IF(%s='available' AND expires_on>UTC_TIMESTAMP(),'available','quarantined') WHERE blood_unit_id=%s",(target_status,unit_id))
                    add_audit(cur,"create","screening_record",cur.lastrowid,{"test_code":test_code,"result_status":result})
            return run_form(create,"Screening result recorded.","screenings")
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT u.unit_code,s.test_code,s.tested_at,s.result_status,s.result_reference,s.notes,au.display_name AS recorded_by FROM screening_record s JOIN blood_unit u ON u.blood_unit_id=s.blood_unit_id LEFT JOIN app_user au ON au.user_id=s.recorded_by_user_id WHERE (%s IS NULL OR u.current_hospital_id=%s) ORDER BY s.tested_at DESC",(scope,scope))
        units=fetch_all("SELECT blood_unit_id AS value,unit_code AS label FROM blood_unit WHERE (%s IS NULL OR current_hospital_id=%s) ORDER BY unit_code",(scope,scope))
        fields=[f("blood_unit_id","Blood unit",type="select",options=units,required=True),f("test_code","Test code (use inventory_clearance for inventory state)",required=True),f("tested_at","Tested at",type="datetime-local"),f("result_status","Recorded result",type="select",options=opts(("pending","Pending"),("recorded","Recorded"),("cleared","Cleared as entered"),("not_cleared","Not cleared as entered"),("inconclusive","Inconclusive")),required=True),f("result_reference","Result reference"),f("notes","Notes")]
        return render_records("Blood screening","Record test results; the app does not interpret clinical compatibility.","screenings",[c("unit_code","Unit"),c("test_code","Test"),c("tested_at","Tested"),c("result_status","Result"),c("result_reference","Reference"),c("notes","Notes"),c("recorded_by","Recorded by")],rows,fields,"screenings","blood.write")

    @app.route("/requests", methods=["GET","POST"])
    @permission_required("requests.read")
    def blood_requests():
        if request.method=="POST":
            if not can("requests.write"): abort(403)
            def create():
                case_id=int(request.form["emergency_case_id"]); hospital_id=int(request.form["requesting_hospital_id"])
                require_hospital_access(hospital_id)
                group_id=int(request.form["blood_group_id"]); component_id=int(request.form["component_id"]); units=positive_int("required_units","Required units")
                urgency=request.form.get("urgency","routine")
                if urgency not in {"routine","urgent","critical"}: raise ValueError("Choose a valid request urgency.")
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("SELECT receiving_hospital_id,case_status FROM emergency_case WHERE emergency_case_id=%s FOR UPDATE",(case_id,)); case=cur.fetchone()
                    if not case: raise ValueError("Select a valid emergency case.")
                    if case[1] in {"completed","cancelled"}: raise ValueError("A closed emergency case cannot receive a new blood request.")
                    if case[0] != hospital_id: raise ValueError("The requesting hospital must match the emergency case's receiving hospital.")
                    cur.execute("INSERT INTO blood_request(emergency_case_id,requesting_hospital_id,requested_by_user_id,request_ref,urgency,notes) VALUES(%s,%s,%s,%s,%s,%s)",(case_id,hospital_id,g.user["user_id"],make_ref("REQ"),urgency,request.form.get("notes") or None))
                    request_id=cur.lastrowid
                    cur.execute("INSERT INTO blood_request_line(blood_request_id,component_id,blood_group_id,line_number,required_units) VALUES(%s,%s,%s,1,%s)",(request_id,component_id,group_id,units))
                    line_id=cur.lastrowid
                    cur.execute("""SELECT COUNT(*) FROM blood_unit u WHERE u.current_hospital_id=%s AND u.component_id=%s AND u.blood_group_id=%s AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
                        AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
                        AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
                        AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))""",(hospital_id,component_id,group_id))
                    eligible=cur.fetchone()[0]
                    cur.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,%s,'pending_request',%s,%s)",(hospital_id,request_id,line_id,g.user["user_id"],"high" if urgency=="critical" else "medium",f"Request {request_id} has {units} pending unit(s)"))
                    if eligible<units:
                        cur.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,%s,'shortage',%s,%s)",(hospital_id,request_id,line_id,g.user["user_id"],"high" if urgency in {"urgent","critical"} else "medium",f"Shortage of {units-eligible} eligible unit(s)"))
                    add_audit(cur,"create","blood_request",request_id,{"required_units":units,"eligible_units":eligible,"shortage":max(units-eligible,0)})
                    create_notifications(cur,("requests.read",),"blood_request",f"Blood request {request_id} was created for emergency {case_id}.","blood_request",request_id,g.user["user_id"])
                    if eligible<units:
                        create_notifications(cur,("blood.read",),"shortage",f"Blood request {request_id} has an initial shortage of {units-eligible} eligible unit(s).","blood_request",request_id,g.user["user_id"])
            return run_form(create,"Emergency-linked blood request created.","blood_requests")
        rows=fetch_all("""SELECT b.blood_request_id,b.request_ref,c.case_ref,h.name AS hospital,b.request_status,b.urgency,b.requested_at,
            (SELECT COALESCE(SUM(l.required_units),0) FROM blood_request_line l WHERE l.blood_request_id=b.blood_request_id) AS required_units,
            (SELECT COUNT(*) FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id WHERE l.blood_request_id=b.blood_request_id AND a.allocation_status IN ('reserved','issued')) AS allocated_units
            FROM blood_request b JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN hospital h ON h.hospital_id=b.requesting_hospital_id
            WHERE (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY b.requested_at DESC""",(assigned_hospital_id(),assigned_hospital_id()))
        for row in rows: row["pending_units"]=max(0,row["required_units"]-row["allocated_units"])
        cases=fetch_all("SELECT emergency_case_id AS value,CONCAT(case_ref,' · ',emergency_location) AS label FROM emergency_case WHERE case_status NOT IN ('completed','cancelled') AND (%s IS NULL OR receiving_hospital_id=%s) ORDER BY reported_at DESC",(assigned_hospital_id(),assigned_hospital_id()))
        hospitals_list=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND (%s IS NULL OR hospital_id=%s) ORDER BY name",(assigned_hospital_id(),assigned_hospital_id()))
        groups=fetch_all("SELECT blood_group_id AS value,code AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        components=fetch_all("SELECT component_id AS value,name AS label FROM blood_component WHERE is_active=1 ORDER BY name")
        fields=[f("emergency_case_id","Emergency case (required)",type="select",options=cases,required=True),f("requesting_hospital_id","Requesting hospital",type="select",options=hospitals_list,required=True),f("blood_group_id","Exact recorded group",type="select",options=groups,required=True),f("component_id","Component",type="select",options=components,required=True),f("required_units","Required units",type="number",min=1,required=True),f("urgency","Request urgency",type="select",options=opts(("routine","Routine"),("urgent","Urgent"),("critical","Critical")),required=True),f("notes","Request notes")]
        preselected_case=request.args.get("case_id",type=int)
        if preselected_case and any(int(option["value"])==preselected_case for option in cases):
            fields[0]["value"]=str(preselected_case)
            linked=fetch_one("SELECT receiving_hospital_id FROM emergency_case WHERE emergency_case_id=%s",(preselected_case,))
            if linked: fields[1]["value"]=str(linked["receiving_hospital_id"])
        return render_records("Blood requests","Every request is linked to an emergency case. Group/component matching is exact and administrative.","blood_requests",[c("request_ref","Request"),c("case_ref","Emergency case"),c("hospital","Hospital"),c("urgency","Urgency"),c("request_status","Status"),c("required_units","Required"),c("allocated_units","Allocated"),c("pending_units","Pending"),c("requested_at","Requested")],rows,fields,"blood_requests","requests.write")

    @app.post("/requests/<int:request_id>/decision")
    @permission_required("requests.write")
    def request_decision(request_id):
        require_blood_request_access(request_id)
        decision=request.form.get("decision")
        if decision not in {"approved","rejected"}: abort(400)
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT * FROM blood_request WHERE blood_request_id=%s FOR UPDATE",(request_id,)); request_row=cur.fetchone()
                if not request_row or request_row["request_status"]!="open": raise ValueError("Only a request awaiting review can be approved or rejected.")
                cur.execute("SELECT COUNT(*) AS active_units FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id WHERE l.blood_request_id=%s AND a.allocation_status IN ('reserved','issued')",(request_id,))
                if cur.fetchone()["active_units"]: raise ValueError("A request with active allocations cannot be rejected.")
                cur.execute("UPDATE blood_request SET request_status=%s WHERE blood_request_id=%s",(decision,request_id))
                if decision=="approved":
                    sync_request_alerts(cur,request_id,g.user["user_id"])
                else:
                    sync_request_alerts(cur,request_id,g.user["user_id"])
                if request_row["requested_by_user_id"]:
                    create_user_notification(cur,request_row["requested_by_user_id"],"blood_request",f"Your blood request {request_row['request_ref']} was {decision}.","blood_request",request_id)
                create_notifications(cur,("requests.read",),"blood_request",f"Blood request {request_row['request_ref']} was {decision}.","blood_request",request_id,g.user["user_id"])
                add_audit(cur,"request_decision","blood_request",request_id,{"decision":decision})
            flash(f"Blood request {decision}.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Blood request decision failed"); flash("The request decision could not be saved.","error")
        return redirect(url_for("request_detail",request_id=request_id))

    @app.get("/requests/<int:request_id>")
    @permission_required("requests.read")
    def request_detail(request_id):
        require_blood_request_access(request_id)
        request_row=fetch_one("SELECT b.*,c.case_ref,h.name AS hospital,h.city AS hospital_city FROM blood_request b JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN hospital h ON h.hospital_id=b.requesting_hospital_id WHERE b.blood_request_id=%s",(request_id,))
        if not request_row: abort(404)
        lines=fetch_all("""SELECT l.*,bg.code AS blood_group,bc.name AS component,
            (SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')) AS allocated_units,
            (SELECT COUNT(DISTINCT u.blood_unit_id) FROM blood_unit u WHERE u.current_hospital_id=%s AND u.component_id=l.component_id AND u.blood_group_id=l.blood_group_id AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared' AND NOT EXISTS(SELECT 1 FROM blood_allocation aa WHERE aa.blood_unit_id=u.blood_unit_id AND aa.allocation_status IN ('reserved','issued')) AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))) AS eligible_units
            FROM blood_request_line l JOIN blood_group bg ON bg.blood_group_id=l.blood_group_id JOIN blood_component bc ON bc.component_id=l.component_id WHERE l.blood_request_id=%s ORDER BY l.line_number""",(request_row["requesting_hospital_id"],request_id))
        for line in lines:
            line["pending_units"]=max(0,line["required_units"]-line["allocated_units"])
            line["shortage"]=max(0,line["pending_units"]-line["eligible_units"])
            line["allocations"]=fetch_all("SELECT a.allocation_id,a.blood_unit_id,a.allocation_status,a.allocated_at,u.unit_code,t.transfusion_id FROM blood_allocation a JOIN blood_unit u ON u.blood_unit_id=a.blood_unit_id LEFT JOIN transfusion_record t ON t.allocation_id=a.allocation_id WHERE a.request_line_id=%s ORDER BY a.allocated_at DESC",(line["request_line_id"],))
            line["active_allocations"]=[allocation for allocation in line["allocations"] if allocation["allocation_status"] in {"reserved","issued"}]
            line["crossmatches"]=fetch_all("SELECT x.tested_at,x.test_status,x.result_reference,x.notes,u.unit_code,au.display_name AS recorded_by FROM crossmatch_record x JOIN blood_unit u ON u.blood_unit_id=x.blood_unit_id LEFT JOIN app_user au ON au.user_id=x.recorded_by_user_id WHERE x.request_line_id=%s ORDER BY x.tested_at DESC",(line["request_line_id"],))
        donors=fetch_all("SELECT DISTINCT d.donor_id,d.donor_ref,d.display_name,d.city,d.phone,bg.code AS blood_group FROM donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id JOIN blood_request_line l ON l.blood_request_id=%s AND l.blood_group_id=d.blood_group_id WHERE d.is_active=1 AND d.availability_status='available' ORDER BY (d.city=%s) DESC,d.city,d.display_name",(request_id,request_row["hospital_city"]))
        donor_responses=fetch_all("SELECT d.display_name,d.donor_ref,ca.outcome,ca.attempted_at,ca.response_notes,l.line_number FROM donor_contact_attempt ca JOIN donor d ON d.donor_id=ca.donor_id LEFT JOIN blood_request_line l ON l.request_line_id=ca.request_line_id WHERE ca.blood_request_id=%s ORDER BY ca.attempted_at DESC",(request_id,))
        return render_template("request_detail.html",blood_request=request_row,lines=lines,donors=donors,donor_responses=donor_responses)

    @app.route("/requests/<int:request_id>/edit",methods=["GET","POST"])
    @permission_required("requests.write")
    def edit_blood_request(request_id):
        require_blood_request_access(request_id)
        blood_request=fetch_one("SELECT * FROM blood_request WHERE blood_request_id=%s",(request_id,))
        if not blood_request: abort(404)
        request_lines=fetch_all("SELECT * FROM blood_request_line WHERE blood_request_id=%s ORDER BY line_number",(request_id,))
        if len(request_lines)!=1:
            flash("This editor supports a single requested product line. Multi-line requests remain viewable and allocatable by line.","error")
            return redirect(url_for("request_detail",request_id=request_id))
        line=request_lines[0]
        if request.method=="POST":
            def update():
                required=positive_int("required_units","Required units"); component=int(request.form["component_id"]); group=int(request.form["blood_group_id"]); hospital_id=int(request.form["requesting_hospital_id"])
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT request_status FROM blood_request WHERE blood_request_id=%s FOR UPDATE",(request_id,)); current=cur.fetchone()
                    if not current or current["request_status"] in {"cancelled","rejected"}: raise ValueError("Cancelled, rejected or missing requests cannot be edited.")
                    cur.execute("SELECT COUNT(*) AS allocated FROM blood_allocation WHERE request_line_id=%s AND allocation_status IN ('reserved','issued')",(line["request_line_id"],)); allocated=cur.fetchone()["allocated"]
                    if required<allocated: raise ValueError(f"Required units cannot be reduced below the {allocated} already allocated.")
                    if allocated and (component!=line["component_id"] or group!=line["blood_group_id"]): raise ValueError("Blood group or component cannot change while units are allocated.")
                    urgency=request.form.get("urgency","routine")
                    if urgency not in {"routine","urgent","critical"}: raise ValueError("Choose a valid request urgency.")
                    cur.execute("UPDATE blood_request SET requesting_hospital_id=%s,urgency=%s,notes=%s WHERE blood_request_id=%s",(hospital_id,urgency,request.form.get("notes") or None,request_id))
                    cur.execute("UPDATE blood_request_line SET component_id=%s,blood_group_id=%s,required_units=%s WHERE request_line_id=%s",(component,group,required,line["request_line_id"]))
                    sync_request_alerts(cur,request_id,g.user["user_id"])
                    add_audit(cur,"update","blood_request",request_id,{"required_units":required})
            return run_form(update,"Blood request updated.","request_detail",request_id=request_id)
        hospitals_list=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        groups=fetch_all("SELECT blood_group_id AS value,code AS label FROM blood_group WHERE is_active=1 ORDER BY code")
        components=fetch_all("SELECT component_id AS value,name AS label FROM blood_component WHERE is_active=1 ORDER BY name")
        fields=[f("requesting_hospital_id","Requesting hospital",type="select",options=hospitals_list,required=True),f("blood_group_id","Exact recorded group",type="select",options=groups,required=True),f("component_id","Component",type="select",options=components,required=True),f("required_units","Required units",type="number",min=1,required=True),f("urgency","Request urgency",type="select",options=opts(("routine","Routine"),("urgent","Urgent"),("critical","Critical")),required=True),f("notes","Notes")]
        values={"requesting_hospital_id":blood_request["requesting_hospital_id"],"blood_group_id":line["blood_group_id"],"component_id":line["component_id"],"required_units":line["required_units"],"urgency":blood_request["urgency"],"notes":blood_request["notes"]}
        for field in fields: field["value"]=as_form_value(values.get(field["name"]))
        return render_edit("Edit blood request",blood_request["request_ref"],"edit_blood_request",fields)

    @app.post("/requests/<int:request_id>/cancel")
    @permission_required("requests.write")
    def cancel_blood_request(request_id):
        require_blood_request_access(request_id)
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT request_status FROM blood_request WHERE blood_request_id=%s FOR UPDATE",(request_id,)); req=cur.fetchone()
                if not req: raise ValueError("Blood request not found.")
                if req["request_status"] in {"cancelled","rejected"}: raise ValueError("This request is already closed.")
                cur.execute("SELECT a.allocation_id,a.blood_unit_id,a.allocation_status FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id WHERE l.blood_request_id=%s AND a.allocation_status IN ('reserved','issued') FOR UPDATE",(request_id,)); allocations=cur.fetchall()
                if any(a["allocation_status"]=="issued" for a in allocations): raise ValueError("A request with issued units cannot be cancelled. Contact the inventory administrator.")
                for allocation in allocations:
                    cur.execute("UPDATE blood_allocation SET allocation_status='released',released_at=UTC_TIMESTAMP(),release_reason='Request cancelled' WHERE allocation_id=%s",(allocation["allocation_id"],))
                    cur.execute("UPDATE blood_unit SET inventory_status=IF(expires_on>UTC_TIMESTAMP() AND (SELECT result_status FROM screening_record WHERE blood_unit_id=%s AND test_code='inventory_clearance' ORDER BY tested_at DESC,screening_id DESC LIMIT 1)='cleared','available','quarantined') WHERE blood_unit_id=%s",(allocation["blood_unit_id"],allocation["blood_unit_id"]))
                cur.execute("UPDATE blood_request SET request_status='cancelled' WHERE blood_request_id=%s",(request_id,))
                sync_request_alerts(cur,request_id,g.user["user_id"])
                add_audit(cur,"cancel","blood_request",request_id)
            flash("Blood request cancelled; unissued reservations were safely released.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Request cancellation failed"); flash("Request cancellation failed safely.","error")
        return redirect(url_for("blood_requests"))

    @app.post("/requests/<int:request_id>/crossmatch")
    @permission_required("requests.write")
    def crossmatch(request_id):
        require_blood_request_access(request_id)
        try:
            line_id=int(request.form["request_line_id"]); unit_id=int(request.form["blood_unit_id"]); status=request.form.get("test_status")
            if status not in {"pending","recorded","compatible_as_entered","incompatible_as_entered","inconclusive"}:
                raise ValueError("Choose a valid recorded result.")
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT request_line_id FROM blood_request_line WHERE request_line_id=%s AND blood_request_id=%s FOR UPDATE",(line_id,request_id))
                if not cur.fetchone(): raise ValueError("Request line does not belong to this request.")
                cur.execute("SELECT blood_unit_id FROM blood_allocation WHERE request_line_id=%s AND blood_unit_id=%s AND allocation_status IN ('reserved','issued') FOR UPDATE",(line_id,unit_id))
                if not cur.fetchone(): raise ValueError("Select a unit actively allocated to this request line.")
                cur.execute("INSERT INTO crossmatch_record(request_line_id,blood_unit_id,recorded_by_user_id,tested_at,test_status,result_reference,notes) VALUES(%s,%s,%s,UTC_TIMESTAMP(),%s,%s,%s)",(line_id,unit_id,g.user["user_id"],status,request.form.get("result_reference") or None,request.form.get("notes") or None))
                add_audit(cur,"create","crossmatch_record",cur.lastrowid,{"status":status})
            flash("Crossmatch result recorded as entered.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Crossmatch record failed"); flash("Could not save the crossmatch record.","error")
        return redirect(url_for("request_detail",request_id=request_id))

    @app.post("/requests/<int:request_id>/allocate")
    @permission_required("requests.write")
    def allocate(request_id):
        require_blood_request_access(request_id)
        try: line_id=int(request.form["request_line_id"]); count=positive_int("units","Units to allocate")
        except (ValueError,KeyError) as exc:
            flash(str(exc) or "Invalid allocation quantity.","error"); return redirect(url_for("request_detail",request_id=request_id))
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT l.*,b.requesting_hospital_id,b.request_status FROM blood_request_line l JOIN blood_request b ON b.blood_request_id=l.blood_request_id WHERE l.request_line_id=%s AND l.blood_request_id=%s FOR UPDATE",(line_id,request_id)); line=cur.fetchone()
                if not line or line["request_status"] not in {"approved","partially_fulfilled"}: raise ValueError("A request must be approved before units can be allocated.")
                cur.execute("SELECT COUNT(*) AS n FROM blood_allocation WHERE request_line_id=%s AND allocation_status IN ('reserved','issued')",(line_id,)); allocated=cur.fetchone()["n"]
                pending=max(0,line["required_units"]-allocated)
                if count>pending: raise ValueError(f"Only {pending} unit(s) remain pending on this line.")
                cur.execute("""SELECT u.blood_unit_id,u.unit_code FROM blood_unit u
                    WHERE u.current_hospital_id=%s AND u.component_id=%s AND u.blood_group_id=%s AND u.inventory_status='available'
                    AND u.expires_on>UTC_TIMESTAMP()
                    AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
                    AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
                    AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))
                    ORDER BY u.expires_on,u.blood_unit_id LIMIT %s FOR UPDATE""",(line["requesting_hospital_id"],line["component_id"],line["blood_group_id"],count)); units=cur.fetchall()
                if len(units)<count: raise ValueError(f"Only {len(units)} eligible unit(s) are currently available; allocation was not partially changed. Reduce the quantity and retry.")
                for unit in units:
                    cur.execute("INSERT INTO blood_allocation(request_line_id,blood_unit_id,allocated_by_user_id,allocation_status) VALUES(%s,%s,%s,'reserved')",(line_id,unit["blood_unit_id"],g.user["user_id"]))
                    cur.execute("UPDATE blood_unit SET inventory_status='reserved' WHERE blood_unit_id=%s AND inventory_status='available'",(unit["blood_unit_id"],))
                    if cur.rowcount!=1: raise ValueError("Inventory changed during allocation; nothing was committed.")
                cur.execute("SELECT COUNT(*) AS line_count,SUM(required_units) AS required_total,SUM((SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued'))) AS allocated_total FROM blood_request_line l WHERE l.blood_request_id=%s",(request_id,)); totals=cur.fetchone()
                overall="fulfilled" if totals["line_count"] and totals["allocated_total"]>=totals["required_total"] else "partially_fulfilled" if totals["allocated_total"] else "open"
                cur.execute("UPDATE blood_request SET request_status=%s WHERE blood_request_id=%s AND request_status NOT IN ('cancelled','rejected')",(overall,request_id))
                sync_request_alerts(cur,request_id,g.user["user_id"])
                add_audit(cur,"allocate","blood_request",request_id,{"request_line_id":line_id,"units":count,"unit_codes":[u["unit_code"] for u in units]})
            flash(f"Reserved {count} unit(s).", "success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Blood allocation failed")
            flash("Allocation failed safely and was rolled back.","error")
        return redirect(url_for("request_detail",request_id=request_id))

    @app.post("/allocations/<int:allocation_id>/status")
    @permission_required("requests.write")
    def allocation_status(allocation_id):
        status=request.form.get("status")
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT a.*,u.inventory_status,r.blood_request_id FROM blood_allocation a JOIN blood_unit u ON u.blood_unit_id=a.blood_unit_id JOIN blood_request_line l ON l.request_line_id=a.request_line_id JOIN blood_request r ON r.blood_request_id=l.blood_request_id WHERE a.allocation_id=%s FOR UPDATE",(allocation_id,)); row=cur.fetchone()
                if not row: raise ValueError("Allocation not found.")
                if row["allocation_status"]!="reserved" or status not in {"issued","released"}: raise ValueError("Only a reserved unit can be issued or released.")
                if status=="issued":
                    cur.execute("UPDATE blood_allocation SET allocation_status='issued',issued_at=UTC_TIMESTAMP() WHERE allocation_id=%s",(allocation_id,)); cur.execute("UPDATE blood_unit SET inventory_status='issued' WHERE blood_unit_id=%s",(row["blood_unit_id"],))
                else:
                    cur.execute("UPDATE blood_allocation SET allocation_status='released',released_at=UTC_TIMESTAMP(),release_reason=%s WHERE allocation_id=%s",(request.form.get("release_reason") or "Released by inventory user",allocation_id)); cur.execute("UPDATE blood_unit SET inventory_status=IF(expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=%s AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared','available','quarantined') WHERE blood_unit_id=%s",(row["blood_unit_id"],row["blood_unit_id"]))
                cur.execute("SELECT COUNT(*) AS line_count,SUM(required_units) AS required_total,SUM((SELECT COUNT(*) FROM blood_allocation aa WHERE aa.request_line_id=l.request_line_id AND aa.allocation_status IN ('reserved','issued'))) AS allocated_total FROM blood_request_line l WHERE l.blood_request_id=%s",(row["blood_request_id"],)); totals=cur.fetchone()
                overall="fulfilled" if totals["line_count"] and totals["allocated_total"]>=totals["required_total"] else "partially_fulfilled" if totals["allocated_total"] else "open"
                cur.execute("UPDATE blood_request SET request_status=%s WHERE blood_request_id=%s AND request_status NOT IN ('cancelled','rejected')",(overall,row["blood_request_id"]))
                sync_request_alerts(cur,row["blood_request_id"],g.user["user_id"])
                add_audit(cur,"status_change","blood_allocation",allocation_id,{"status":status})
            flash("Allocation updated.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Allocation status update failed"); flash("Could not update allocation.","error")
        return redirect(url_for("request_detail",request_id=row["blood_request_id"]))

    @app.route("/transfers", methods=["GET","POST"])
    @permission_required("transfers.read")
    def transfers():
        if request.method=="POST":
            if not can("transfers.write"): abort(403)
            try:
                from_id,to_id=int(request.form["from_hospital_id"]),int(request.form["to_hospital_id"])
                require_hospital_access(from_id)
                unit_ids=sorted({int(x) for x in request.form.getlist("blood_unit_ids")})
                if from_id==to_id or not unit_ids: raise ValueError("Choose different hospitals and at least one blood unit.")
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT hospital_id FROM hospital WHERE hospital_id IN (%s,%s) FOR UPDATE",(from_id,to_id))
                    if len(cur.fetchall())!=2: raise ValueError("Both hospitals must exist.")
                    cur.execute("SELECT blood_unit_id,unit_code,inventory_status,current_hospital_id,expires_on FROM blood_unit WHERE blood_unit_id IN ("+",".join(["%s"]*len(unit_ids))+") ORDER BY blood_unit_id FOR UPDATE",tuple(unit_ids)); units=cur.fetchall()
                    if len(units)!=len(unit_ids): raise ValueError("One or more selected units no longer exist.")
                    for unit in units:
                        if unit["inventory_status"]!="available": raise ValueError(f"{unit['unit_code']} is not available for transfer.")
                        if unit["current_hospital_id"]!=from_id: raise ValueError(f"{unit['unit_code']} is not at the source hospital.")
                        if unit["expires_on"]<=datetime.now(timezone.utc).replace(tzinfo=None): raise ValueError(f"{unit['unit_code']} has expired.")
                        cur.execute("SELECT result_status FROM screening_record WHERE blood_unit_id=%s AND test_code='inventory_clearance' ORDER BY tested_at DESC,screening_id DESC LIMIT 1",(unit["blood_unit_id"]))
                        screening=cur.fetchone()
                        if not screening or screening["result_status"]!="cleared": raise ValueError(f"{unit['unit_code']} has no current cleared inventory screening record.")
                        cur.execute("SELECT allocation_id FROM blood_allocation WHERE blood_unit_id=%s AND allocation_status IN ('reserved','issued')",(unit["blood_unit_id"],))
                        if cur.fetchone(): raise ValueError(f"{unit['unit_code']} has an active allocation.")
                        cur.execute("SELECT transfer_id FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=%s AND t.transfer_status IN ('requested','approved','in_transit')",(unit["blood_unit_id"]))
                        if cur.fetchone(): raise ValueError(f"{unit['unit_code']} is already in an active transfer.")
                    cur.execute("INSERT INTO blood_transfer(from_hospital_id,to_hospital_id,initiated_by_user_id,transfer_ref,transfer_status,notes) VALUES(%s,%s,%s,%s,'requested',%s)",(from_id,to_id,g.user["user_id"],make_ref("TRF"),request.form.get("notes") or None)); transfer_id=cur.lastrowid
                    cur.execute("INSERT INTO blood_transfer_status_history(transfer_id,changed_by_user_id,status,notes) VALUES(%s,%s,'requested','Transfer request created')",(transfer_id,g.user["user_id"]))
                    for unit in units:
                        cur.execute("INSERT INTO blood_transfer_unit(transfer_id,blood_unit_id) VALUES(%s,%s)",(transfer_id,unit["blood_unit_id"]))
                    cur.execute("INSERT INTO alert(hospital_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,'transfer','medium',%s)",(to_id,g.user["user_id"],f"Transfer {transfer_id} awaits approval"))
                    add_audit(cur,"create","blood_transfer",transfer_id,{"unit_count":len(units)})
                    create_notifications(cur,("transfers.read",),"transfer",f"Blood transfer {transfer_id} is awaiting approval.","blood_transfer",transfer_id,g.user["user_id"])
                flash("Transfer request created. Selected units are reserved from allocation until the transfer is resolved.","success")
            except ValueError as exc: flash(str(exc),"error")
            except MySQLError:
                app.logger.exception("Transfer creation failed"); flash("Transfer failed safely and was rolled back.","error")
            return redirect(url_for("transfers"))
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT t.transfer_id,t.transfer_ref,src.name AS source,dst.name AS destination,t.transfer_status,t.requested_at,t.dispatched_at,t.received_at,(SELECT COUNT(*) FROM blood_transfer_unit tu WHERE tu.transfer_id=t.transfer_id) AS unit_count FROM blood_transfer t JOIN hospital src ON src.hospital_id=t.from_hospital_id JOIN hospital dst ON dst.hospital_id=t.to_hospital_id WHERE (%s IS NULL OR t.from_hospital_id=%s OR t.to_hospital_id=%s) ORDER BY t.requested_at DESC",(scope,scope,scope))
        hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND (%s IS NULL OR hospital_id=%s) ORDER BY name",(scope,scope))
        destination_hospitals=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        units=fetch_all("SELECT blood_unit_id AS value,CONCAT(unit_code,' · ',bg.code,' · ',bc.name,' · ',h.name) AS label FROM blood_unit u JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id JOIN hospital h ON h.hospital_id=u.current_hospital_id WHERE u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared' AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued')) AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit')) ORDER BY u.unit_code")
        fields=[f("from_hospital_id","Source hospital",type="select",options=hs,required=True),f("to_hospital_id","Destination hospital",type="select",options=destination_hospitals,required=True),f("blood_unit_ids","Available units (multi-select)",type="select-multiple",options=units,required=True),f("notes","Transfer notes")]
        return render_records("Inter-hospital transfers","Dispatch and receive individually tracked blood units.","transfers",[c("transfer_ref","Transfer"),c("source","From"),c("destination","To"),c("unit_count","Units"),c("transfer_status","Status"),c("requested_at","Requested"),c("received_at","Received")],rows,fields,"transfers","transfers.write")

    @app.post("/transfers/<int:transfer_id>/status")
    @permission_required("transfers.write")
    def transfer_status(transfer_id):
        requested=request.form.get("status")
        transitions={"requested":{"approved","cancelled"},"approved":{"in_transit","cancelled"}}
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT * FROM blood_transfer WHERE transfer_id=%s FOR UPDATE",(transfer_id,)); transfer=cur.fetchone()
                if not transfer: raise ValueError("Transfer not found.")
                require_related_hospital_access(transfer["from_hospital_id"],transfer["to_hospital_id"])
                if requested not in transitions.get(transfer["transfer_status"],set()): raise ValueError("That transfer status change is not allowed.")
                scope=assigned_hospital_id()
                if scope is not None and requested=="approved" and int(scope)!=int(transfer["to_hospital_id"]):
                    raise ValueError("Only the destination hospital can approve a transfer.")
                if scope is not None and requested=="in_transit" and int(scope)!=int(transfer["from_hospital_id"]):
                    raise ValueError("Only the source hospital can dispatch a transfer.")
                if scope is not None and requested=="cancelled" and int(scope)!=int(transfer["from_hospital_id"]):
                    raise ValueError("Only the source hospital can cancel this transfer before dispatch.")
                dispatched=requested=="in_transit"
                cur.execute("UPDATE blood_transfer SET transfer_status=%s,dispatched_at=IF(%s,UTC_TIMESTAMP(),dispatched_at) WHERE transfer_id=%s",(requested,dispatched,transfer_id))
                cur.execute("INSERT INTO blood_transfer_status_history(transfer_id,changed_by_user_id,status,notes) VALUES(%s,%s,%s,%s)",(transfer_id,g.user["user_id"],requested,"Transfer status updated"))
                cur.execute("INSERT INTO alert(hospital_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,'transfer','medium',%s)",(transfer["to_hospital_id"],g.user["user_id"],f"Transfer {transfer['transfer_ref']} {requested.replace('_',' ')}"))
                add_audit(cur,"status_change","blood_transfer",transfer_id,{"status":requested})
                create_notifications(cur,("transfers.read",),"transfer",f"Transfer {transfer['transfer_ref']} is now {requested.replace('_',' ')}.","blood_transfer",transfer_id,g.user["user_id"])
            flash("Transfer status updated.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Transfer status update failed"); flash("Could not update transfer status.","error")
        return redirect(url_for("transfer_detail",transfer_id=transfer_id))

    @app.get("/transfers/<int:transfer_id>")
    @permission_required("transfers.read")
    def transfer_detail(transfer_id):
        transfer=fetch_one("SELECT t.*,src.name AS source,dst.name AS destination FROM blood_transfer t JOIN hospital src ON src.hospital_id=t.from_hospital_id JOIN hospital dst ON dst.hospital_id=t.to_hospital_id WHERE t.transfer_id=%s",(transfer_id,))
        if not transfer: abort(404)
        require_related_hospital_access(transfer["from_hospital_id"],transfer["to_hospital_id"])
        units=fetch_all("SELECT u.unit_code,bg.code AS blood_group,bc.name AS component,u.expires_on,CASE WHEN t.transfer_status IN ('requested','approved','in_transit') THEN 'in_transfer' WHEN u.expires_on<=UTC_TIMESTAMP() AND u.inventory_status NOT IN ('issued','discarded') THEN 'expired' ELSE u.inventory_status END AS inventory_status FROM blood_transfer_unit tu JOIN blood_unit u ON u.blood_unit_id=tu.blood_unit_id JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.transfer_id=%s ORDER BY u.unit_code",(transfer_id,))
        history=fetch_all("SELECT h.status,h.changed_at,h.notes,u.display_name AS changed_by FROM blood_transfer_status_history h LEFT JOIN app_user u ON u.user_id=h.changed_by_user_id WHERE h.transfer_id=%s ORDER BY h.changed_at DESC",(transfer_id,))
        return render_template("transfer_detail.html",transfer=transfer,units=units,history=history)

    @app.post("/transfers/<int:transfer_id>/receive")
    @permission_required("transfers.write")
    def receive_transfer(transfer_id):
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT * FROM blood_transfer WHERE transfer_id=%s FOR UPDATE",(transfer_id,)); transfer=cur.fetchone()
                if not transfer or transfer["transfer_status"]!="in_transit": raise ValueError("This transfer is not in transit.")
                require_hospital_access(transfer["to_hospital_id"])
                cur.execute("SELECT u.blood_unit_id,u.inventory_status,u.expires_on FROM blood_transfer_unit tu JOIN blood_unit u ON u.blood_unit_id=tu.blood_unit_id WHERE tu.transfer_id=%s ORDER BY u.blood_unit_id FOR UPDATE",(transfer_id,)); units=cur.fetchall()
                if not units: raise ValueError("Transfer contains no units.")
                for unit in units:
                    if unit["inventory_status"]!="available": raise ValueError("A transfer unit has an inconsistent inventory state.")
                    cur.execute("SELECT result_status FROM screening_record WHERE blood_unit_id=%s AND test_code='inventory_clearance' ORDER BY tested_at DESC,screening_id DESC LIMIT 1",(unit["blood_unit_id"],)); screening=cur.fetchone()
                    status="available" if unit["expires_on"]>datetime.now(timezone.utc).replace(tzinfo=None) and screening and screening["result_status"]=="cleared" else "quarantined"
                    cur.execute("UPDATE blood_unit SET current_hospital_id=%s,inventory_status=%s WHERE blood_unit_id=%s",(transfer["to_hospital_id"],status,unit["blood_unit_id"]))
                cur.execute("UPDATE blood_transfer SET transfer_status='received',received_at=UTC_TIMESTAMP() WHERE transfer_id=%s",(transfer_id,))
                cur.execute("INSERT INTO blood_transfer_status_history(transfer_id,changed_by_user_id,status,notes) VALUES(%s,%s,'received','Destination receipt confirmed')",(transfer_id,g.user["user_id"]))
                add_audit(cur,"receive","blood_transfer",transfer_id,{"unit_count":len(units)})
            flash("Transfer received and unit locations updated.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Transfer receipt failed"); flash("Transfer receipt failed and was rolled back.","error")
        return redirect(url_for("transfers"))

    @app.route("/alerts", methods=["GET","POST"])
    @permission_required("alerts.read")
    def alerts():
        if request.method=="POST":
            if not can("alerts.write"): abort(403)
            if assigned_hospital_id() is not None: abort(403)
            created=0
            try:
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("""SELECT l.request_line_id,b.requesting_hospital_id,b.blood_request_id,l.required_units,
                        GREATEST(l.required_units-(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')),0) AS pending,
                        (SELECT COUNT(DISTINCT u.blood_unit_id) FROM blood_unit u WHERE u.current_hospital_id=b.requesting_hospital_id AND u.component_id=l.component_id AND u.blood_group_id=l.blood_group_id AND u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP() AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared' AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued')) AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))) AS eligible
                        FROM blood_request_line l JOIN blood_request b ON b.blood_request_id=l.blood_request_id WHERE b.request_status NOT IN ('cancelled','rejected')""")
                    lines=cur.fetchall()
                    for line in lines:
                        if line["pending"]>line["eligible"]:
                            cur.execute("SELECT alert_id FROM alert WHERE alert_type='shortage' AND request_line_id=%s AND alert_status IN ('open','acknowledged')",(line["request_line_id"],))
                            if not cur.fetchone():
                                cur.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,%s,'shortage','high',%s)",(line["requesting_hospital_id"],line["blood_request_id"],line["request_line_id"],g.user["user_id"],f"Shortage of {line['pending']-line['eligible']} eligible unit(s)")); created+=1
                                create_notifications(cur,("blood.read",),"shortage",f"Request {line['blood_request_id']} has a shortage of {line['pending']-line['eligible']} eligible unit(s).","blood_request",line["blood_request_id"],g.user["user_id"])
                        else:
                            cur.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE alert_type='shortage' AND request_line_id=%s AND alert_status IN ('open','acknowledged')",(g.user["user_id"],line["request_line_id"]))
                        if line["pending"]>0:
                            cur.execute("SELECT alert_id FROM alert WHERE alert_type='pending_request' AND blood_request_id=%s AND alert_status IN ('open','acknowledged') LIMIT 1",(line["blood_request_id"],))
                            if not cur.fetchone():
                                cur.execute("INSERT INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,%s,'pending_request','medium',%s)",(line["requesting_hospital_id"],line["blood_request_id"],line["request_line_id"],g.user["user_id"],f"Request has {line['pending']} pending unit(s)")); created+=1
                        else:
                            cur.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE alert_type='pending_request' AND blood_request_id=%s AND alert_status IN ('open','acknowledged')",(g.user["user_id"],line["blood_request_id"]))
                    cur.execute("SELECT u.blood_unit_id,u.current_hospital_id,u.unit_code,u.expires_on FROM blood_unit u WHERE u.inventory_status NOT IN ('issued','discarded') AND u.expires_on<=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 7 DAY)")
                    for unit in cur.fetchall():
                        cur.execute("SELECT alert_id FROM alert WHERE alert_type='expiry' AND blood_unit_id=%s AND alert_status IN ('open','acknowledged')",(unit["blood_unit_id"],))
                        if not cur.fetchone():
                            expired=unit["expires_on"]<=datetime.now(timezone.utc).replace(tzinfo=None)
                            message=f"Unit {unit['unit_code']} has expired" if expired else f"Unit {unit['unit_code']} expires within 7 days"
                            cur.execute("INSERT INTO alert(hospital_id,blood_unit_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,%s,'expiry',%s,%s)",(unit["current_hospital_id"],unit["blood_unit_id"],g.user["user_id"],"high" if expired else "medium",message)); created+=1
                            create_notifications(cur,("blood.read",),"expiry",message,"blood_unit",unit["blood_unit_id"],g.user["user_id"])
                    cur.execute("""SELECT h.hospital_id,bg.code AS blood_group,bc.name AS component,
                        COUNT(DISTINCT CASE WHEN u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
                          AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
                          AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
                          AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))
                          THEN u.blood_unit_id END) AS eligible_units
                        FROM blood_unit u JOIN hospital h ON h.hospital_id=u.current_hospital_id
                        JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id
                        JOIN blood_component bc ON bc.component_id=u.component_id
                        GROUP BY h.hospital_id,bg.blood_group_id,bc.component_id""")
                    for stock in cur.fetchall():
                        prefix=f"Low eligible stock for {stock['blood_group']} {stock['component']}:"
                        cur.execute("SELECT alert_id FROM alert WHERE hospital_id=%s AND alert_type='low_stock' AND alert_status IN ('open','acknowledged') AND message LIKE %s LIMIT 1",(stock["hospital_id"],prefix+"%"))
                        existing=cur.fetchone()
                        if stock["eligible_units"] <= Config.LOW_STOCK_THRESHOLD:
                            message=f"{prefix} {stock['eligible_units']} unit(s) (threshold: {Config.LOW_STOCK_THRESHOLD})."
                            if existing:
                                cur.execute("UPDATE alert SET message=%s WHERE alert_id=%s",(message,existing["alert_id"]))
                            else:
                                cur.execute("INSERT INTO alert(hospital_id,created_by_user_id,alert_type,severity,message) VALUES(%s,%s,'low_stock','medium',%s)",(stock["hospital_id"],g.user["user_id"],message)); created+=1
                                create_notifications(cur,("blood.read",),"low_stock",message,"hospital",stock["hospital_id"],g.user["user_id"])
                        elif existing:
                            cur.execute("UPDATE alert SET alert_status='resolved',resolved_at=UTC_TIMESTAMP(),resolved_by_user_id=%s WHERE hospital_id=%s AND alert_type='low_stock' AND alert_status IN ('open','acknowledged') AND message LIKE %s",(g.user["user_id"],stock["hospital_id"],prefix+"%"))
                    add_audit(cur,"refresh","alert","generated",{"created":created})
                flash(f"Alert refresh complete: {created} new alert(s) recorded.","success")
            except MySQLError:
                app.logger.exception("Alert refresh failed"); flash("Could not refresh alerts.","error")
            return redirect(url_for("alerts"))
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT a.alert_id,a.alert_type,a.severity,a.message,h.name AS hospital,a.alert_status,a.created_at,a.resolved_at FROM alert a JOIN hospital h ON h.hospital_id=a.hospital_id WHERE (%s IS NULL OR a.hospital_id=%s) ORDER BY FIELD(a.alert_status,'open','acknowledged','resolved','dismissed'),a.created_at DESC",(scope,scope))
        return render_records("Alerts & tracking","Refresh calculated shortage and near-expiry alerts; acknowledge or resolve recorded alerts.","alerts",[c("alert_type","Type"),c("severity","Priority"),c("message","Alert"),c("hospital","Hospital"),c("alert_status","Status"),c("created_at","Created"),c("resolved_at","Resolved")],rows,[],None,"alerts.write",filters={"refresh":True})

    @app.post("/alerts/<int:alert_id>/status")
    @permission_required("alerts.write")
    def alert_status(alert_id):
        status=request.form.get("status")
        if status not in {"acknowledged","resolved","dismissed"}: abort(400)
        scope=assigned_hospital_id()
        if scope is not None and not fetch_one("SELECT alert_id FROM alert WHERE alert_id=%s AND hospital_id=%s",(alert_id,scope)): abort(404)
        with transaction() as conn:
            cur=conn.cursor()
            cur.execute("UPDATE alert SET alert_status=%s,resolved_at=IF(%s IN ('resolved','dismissed'),UTC_TIMESTAMP(),NULL),resolved_by_user_id=%s WHERE alert_id=%s AND alert_status IN ('open','acknowledged')",(status,status,g.user["user_id"],alert_id))
            add_audit(cur,"status_change","alert",alert_id,{"status":status})
        flash("Alert updated.","success")
        return redirect(url_for("alerts"))

    REPORTS={
        "emergencies":("Emergency cases","c.case_ref,p.display_name AS patient,h.name AS hospital,c.emergency_type,c.priority_label AS severity,c.case_status,c.reported_at","emergency_case c JOIN patient p ON p.patient_id=c.patient_id JOIN hospital h ON h.hospital_id=c.receiving_hospital_id","c.reported_at","c.receiving_hospital_id"),
        "inventory":("Blood inventory","u.unit_code,bg.code AS blood_group,bc.name AS component,h.name AS hospital,u.inventory_status,u.expires_on","blood_unit u JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id JOIN hospital h ON h.hospital_id=u.current_hospital_id","u.created_at","u.current_hospital_id"),
        "requests":("Blood requests","b.request_ref,c.case_ref,h.name AS hospital,b.urgency,b.request_status,b.requested_at,(SELECT COALESCE(SUM(l.required_units),0) FROM blood_request_line l WHERE l.blood_request_id=b.blood_request_id) AS required_units,(SELECT COUNT(*) FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id WHERE l.blood_request_id=b.blood_request_id AND a.allocation_status IN ('reserved','issued')) AS allocated_units","blood_request b JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN hospital h ON h.hospital_id=b.requesting_hospital_id","b.requested_at","b.requesting_hospital_id"),
        "donors":("Donors","d.donor_ref,d.display_name,bg.code AS blood_group,d.city,d.region,d.availability_status,(SELECT MAX(dn.collected_at) FROM donation dn WHERE dn.donor_id=d.donor_id) AS last_donation","donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id","d.created_at",None),
        "hospitals":("Hospitals","h.code,h.name,h.hospital_type,h.capacity,h.city,h.region,h.phone,h.is_active","hospital h","h.created_at",None),
        "ambulances":("Ambulance fleet","a.vehicle_code,a.registration_number,h.name AS hospital,a.operational_status","ambulance a JOIN hospital h ON h.hospital_id=a.home_hospital_id","a.created_at","a.home_hospital_id"),
        "transfers":("Blood transfers","t.transfer_ref,src.name AS source,dst.name AS destination,t.transfer_status,t.requested_at,t.dispatched_at,t.received_at,(SELECT COUNT(*) FROM blood_transfer_unit tu WHERE tu.transfer_id=t.transfer_id) AS unit_count","blood_transfer t JOIN hospital src ON src.hospital_id=t.from_hospital_id JOIN hospital dst ON dst.hospital_id=t.to_hospital_id","t.requested_at",None),
    }
    REPORT_HEADERS={
        "emergencies":["case_ref","patient","hospital","emergency_type","severity","case_status","reported_at"],
        "inventory":["unit_code","blood_group","component","hospital","inventory_status","expires_on"],
        "requests":["request_ref","case_ref","hospital","urgency","request_status","requested_at","required_units","allocated_units"],
        "donors":["donor_ref","display_name","blood_group","city","region","availability_status","last_donation"],
        "hospitals":["code","name","hospital_type","capacity","city","region","phone","is_active"],
        "ambulances":["vehicle_code","registration_number","hospital","operational_status"],
        "transfers":["transfer_ref","source","destination","transfer_status","requested_at","dispatched_at","received_at","unit_count"],
    }

    def report_data():
        kind=request.args.get("type","emergencies")
        if kind not in REPORTS: abort(400,"Choose a supported report type.")
        title,select_sql,from_sql,date_column,hospital_column=REPORTS[kind]
        clauses=[]; params=[]
        for arg,operator in (("date_from",">="),("date_to","<=")):
            value=request.args.get(arg," ").strip()
            if value:
                try: datetime.strptime(value,"%Y-%m-%d")
                except ValueError: abort(400,f"{arg.replace('_',' ').title()} must be a valid date.")
                clauses.append(f"DATE({date_column}) {operator} %s"); params.append(value)
        hospital_id=request.args.get("hospital_id",type=int)
        scope=assigned_hospital_id()
        if scope is not None:
            if kind=="transfers":
                clauses.append("(t.from_hospital_id=%s OR t.to_hospital_id=%s)"); params.extend((scope,scope))
            elif kind=="donors":
                clauses.append("EXISTS(SELECT 1 FROM donation rd WHERE rd.donor_id=d.donor_id AND rd.collection_hospital_id=%s)"); params.append(scope)
            elif kind=="hospitals":
                clauses.append("h.hospital_id=%s"); params.append(scope)
            elif hospital_column:
                clauses.append(f"{hospital_column}=%s"); params.append(scope)
        elif hospital_id and hospital_column:
            clauses.append(f"{hospital_column}=%s"); params.append(hospital_id)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        rows=fetch_all(f"SELECT {select_sql} FROM {from_sql}{where} ORDER BY {date_column} DESC LIMIT 2000",tuple(params))
        return kind,title,rows

    @app.get("/reports")
    @permission_required("dashboard.read")
    def reports():
        kind,title,rows=report_data()
        hospitals_list=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 AND (%s IS NULL OR hospital_id=%s) ORDER BY name",(assigned_hospital_id(),assigned_hospital_id()))
        status_counts={}
        key=next((name for name in ("case_status","request_status","inventory_status","availability_status","operational_status","transfer_status","is_active") if rows and name in rows[0]),None)
        if key:
            for row in rows: status_counts[str(row.get(key) or "Unknown")]=status_counts.get(str(row.get(key) or "Unknown"),0)+1
        return render_template("reports.html",reports=REPORTS,selected_type=kind,title=title,rows=rows,
                               hospitals=hospitals_list,status_counts=status_counts,hospital_filter=REPORTS[kind][4] is not None)

    @app.get("/reports.csv")
    @permission_required("dashboard.read")
    def export_report():
        kind,title,rows=report_data()
        output=io.StringIO(newline="")
        writer=csv.writer(output)
        headers=list(rows[0].keys()) if rows else REPORT_HEADERS[kind]
        writer.writerow(headers)
        for row in rows:
            values=[]
            for header in headers:
                value=row.get(header)
                if isinstance(value,str) and value[:1] in {"=","+","-","@","\t","\r"}: value="'"+value
                values.append(value)
            writer.writerow(values)
        response=app.response_class(output.getvalue(),mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"]=f"attachment; filename=lifelink_{kind}_report.csv"
        return response

    @app.route("/transfusions",methods=["GET","POST"])
    @permission_required("requests.read")
    def transfusions():
        if request.method=="POST":
            if not can("requests.write"): abort(403)
            try:
                allocation_id=int(request.form["allocation_id"])
                started_at=mysql_datetime(request.form.get("started_at"),"Start time") or datetime.now(timezone.utc).replace(tzinfo=None)
                with transaction() as conn:
                    cur=conn.cursor(dictionary=True)
                    cur.execute("SELECT a.allocation_id,a.allocation_status,r.blood_request_id FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id JOIN blood_request r ON r.blood_request_id=l.blood_request_id WHERE a.allocation_id=%s FOR UPDATE",(allocation_id,))
                    allocation=cur.fetchone()
                    if not allocation or allocation["allocation_status"]!="issued": raise ValueError("A transfusion record can only reference a unit marked issued.")
                    require_blood_request_access(allocation["blood_request_id"])
                    cur.execute("SELECT transfusion_id FROM transfusion_record WHERE allocation_id=%s",(allocation_id,))
                    if cur.fetchone(): raise ValueError("This issued unit already has a transfusion record.")
                    cur.execute("INSERT INTO transfusion_record(blood_request_id,allocation_id,recorded_by_user_id,started_at,status,notes) VALUES(%s,%s,%s,%s,'started',%s)",(allocation["blood_request_id"],allocation_id,g.user["user_id"],started_at,request.form.get("notes") or None))
                    add_audit(cur,"create","transfusion_record",cur.lastrowid,{"allocation_id":allocation_id})
                flash("Transfusion record started.","success")
            except (ValueError,KeyError) as exc: flash(str(exc) or "Choose a valid issued unit.","error")
            except MySQLError:
                app.logger.exception("Transfusion record creation failed"); flash("Could not create transfusion record.","error")
            return redirect(url_for("transfusions"))
        scope=assigned_hospital_id()
        rows=fetch_all("SELECT t.transfusion_id,t.status,t.started_at,t.completed_at,t.notes,b.request_ref,c.case_ref,u.display_name AS recorded_by,bu.unit_code FROM transfusion_record t JOIN blood_request b ON b.blood_request_id=t.blood_request_id JOIN emergency_case c ON c.emergency_case_id=b.emergency_case_id JOIN blood_allocation a ON a.allocation_id=t.allocation_id JOIN blood_unit bu ON bu.blood_unit_id=a.blood_unit_id LEFT JOIN app_user u ON u.user_id=t.recorded_by_user_id WHERE (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY t.started_at DESC",(scope,scope))
        allocations=fetch_all("SELECT a.allocation_id AS value,CONCAT(b.request_ref,' · ',u.unit_code) AS label FROM blood_allocation a JOIN blood_request_line l ON l.request_line_id=a.request_line_id JOIN blood_request b ON b.blood_request_id=l.blood_request_id JOIN blood_unit u ON u.blood_unit_id=a.blood_unit_id LEFT JOIN transfusion_record t ON t.allocation_id=a.allocation_id WHERE a.allocation_status='issued' AND t.transfusion_id IS NULL AND (%s IS NULL OR b.requesting_hospital_id=%s) ORDER BY a.issued_at DESC",(scope,scope))
        fields=[f("allocation_id","Issued blood unit",type="select",options=allocations,required=True),f("started_at","Start time",type="datetime-local"),f("notes","Notes")]
        selected=request.args.get("allocation_id")
        if selected and any(str(row["value"])==selected for row in allocations): fields[0]["value"]=selected
        return render_records("Transfusion records","Document administration outcomes for units already marked issued.","transfusions",[c("transfusion_id","Record ID"),c("request_ref","Request"),c("case_ref","Emergency"),c("unit_code","Unit"),c("status","Status"),c("started_at","Started"),c("completed_at","Completed"),c("recorded_by","Recorded by"),c("notes","Notes")],rows,fields,"transfusions","requests.write")

    @app.post("/transfusions/<int:transfusion_id>/status")
    @permission_required("requests.write")
    def transfusion_status(transfusion_id):
        status=request.form.get("status")
        if status not in {"completed","stopped"}: abort(400)
        with transaction() as conn:
            cur=conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM transfusion_record WHERE transfusion_id=%s FOR UPDATE",(transfusion_id,)); record=cur.fetchone()
            if not record: abort(404)
            require_blood_request_access(record["blood_request_id"])
            if record["status"]!="started":
                flash("Only a started transfusion can be closed.","error")
            else:
                cur.execute("UPDATE transfusion_record SET status=%s,completed_at=UTC_TIMESTAMP() WHERE transfusion_id=%s",(status,transfusion_id))
                add_audit(cur,"status_change","transfusion_record",transfusion_id,{"status":status})
                flash("Transfusion outcome recorded.","success")
        return redirect(url_for("transfusions"))

    @app.route("/users", methods=["GET","POST"])
    @permission_required("users.manage")
    def users():
        if request.method=="POST":
            try:
                username=required_text("username","Username",80); display=required_text("display_name","Display name",160); email=required_text("email","Email",254); password=request.form.get("password","")
                if len(password)<8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password): raise ValueError("Password must be at least 8 characters and include a letter and a number.")
                role_id=int(request.form["role_id"]); hospital_id=int(request.form["hospital_id"]) if request.form.get("hospital_id") else None
                with transaction() as conn:
                    cur=conn.cursor()
                    cur.execute("INSERT INTO app_user(hospital_id,role_id,username,email,password_hash,display_name,phone,approval_status) VALUES(%s,%s,%s,%s,%s,%s,%s,'approved')",(hospital_id,role_id,username,email,generate_password_hash(password),display,request.form.get("phone") or None))
                    add_audit(cur,"create","app_user",cur.lastrowid,{"username":username})
                flash("User account created.","success")
            except ValueError as exc: flash(str(exc),"error")
            except MySQLError:
                app.logger.exception("User creation failed"); flash("User could not be created. Check for duplicate username/email.","error")
            return redirect(url_for("users"))
        rows=fetch_all("SELECT u.user_id,u.username,u.display_name,u.email,u.phone,r.name AS role,r.role_id,u.approval_status,COALESCE(h.name,'All hospitals') AS hospital,u.is_active,u.last_login_at FROM app_user u JOIN `role` r ON r.role_id=u.role_id LEFT JOIN hospital h ON h.hospital_id=u.hospital_id ORDER BY FIELD(u.approval_status,'pending','approved','rejected'),u.display_name")
        roles=fetch_all("SELECT role_id AS value,name AS label FROM `role` ORDER BY role_id")
        hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        fields=[f("username","Username",required=True),f("display_name","Display name",required=True),f("email","Email",type="email",required=True),f("phone","Phone"),f("password","Temporary password (12+ characters)",type="password",required=True),f("role_id","Role",type="select",options=roles,required=True),f("hospital_id","Hospital scope",type="select",options=[{"value":"","label":"All hospitals"}]+hs)]
        return render_records("User access","Approve staff registrations, assign operational roles, and manage staff accounts.","users",[c("username","Username"),c("display_name","Name"),c("email","Email"),c("phone","Phone"),c("role","Role"),c("hospital","Hospital scope"),c("approval_status","Approval"),c("is_active","Active"),c("last_login_at","Last login")],rows,fields,"users","users.manage",filters={"approval_roles":roles})

    @app.post("/users/<int:user_id>/approval")
    @permission_required("users.manage")
    def approve_user(user_id):
        decision=request.form.get("decision")
        if decision not in {"approved","rejected","pending"}: abort(400)
        try:
            role_id=int(request.form.get("role_id", "0"))
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT user_id,username,approval_status FROM app_user WHERE user_id=%s FOR UPDATE",(user_id,)); target=cur.fetchone()
                if not target or target["approval_status"]!="pending": raise ValueError("This registration is no longer pending review.")
                if decision in {"approved","pending"}:
                    cur.execute("SELECT role_id FROM `role` WHERE role_id=%s",(role_id,))
                    if not cur.fetchone(): raise ValueError("Select a valid staff role.")
                elif decision=="rejected":
                    cur.execute("SELECT role_id FROM `role` WHERE role_code='VIEWER'"); role=cur.fetchone(); role_id=role["role_id"]
                cur.execute("UPDATE app_user SET role_id=%s,approval_status=%s,is_active=%s WHERE user_id=%s",(role_id,decision,decision=="approved",user_id))
                message="Your LifeLink staff registration was approved." if decision=="approved" else "Your LifeLink staff registration was rejected." if decision=="rejected" else "Your LifeLink registration is pending administrator review again."
                create_user_notification(cur,user_id,"staff_access",message,"app_user",user_id)
                add_audit(cur,"registration_review","app_user",user_id,{"decision":decision,"role_id":role_id})
            flash("Staff registration returned to pending review." if decision=="pending" else f"Staff registration {decision}.","success")
        except ValueError as exc: flash(str(exc),"error")
        except MySQLError:
            app.logger.exception("Staff approval failed"); flash("Could not review this staff registration.","error")
        return redirect(url_for("users"))

    @app.get("/password-resets")
    @permission_required("users.manage")
    def password_resets():
        rows=fetch_all("SELECT r.reset_request_id,r.request_status,r.requested_at,u.username,u.display_name,u.email FROM password_reset_request r JOIN app_user u ON u.user_id=r.user_id ORDER BY FIELD(r.request_status,'pending','issued','closed'),r.requested_at DESC")
        return render_records("Password reset requests","Review staff reset requests. Temporary credentials are generated only when an administrator issues a reset.","password_resets",[c("username","Username"),c("display_name","Staff member"),c("email","Email"),c("request_status","Status"),c("requested_at","Requested")],rows)

    @app.post("/password-resets/<int:reset_request_id>/issue")
    @permission_required("users.manage")
    def issue_password_reset(reset_request_id):
        temporary_password=secrets.token_urlsafe(18)
        try:
            with transaction() as conn:
                cur=conn.cursor(dictionary=True)
                cur.execute("SELECT r.reset_request_id,r.user_id,r.request_status,u.username FROM password_reset_request r JOIN app_user u ON u.user_id=r.user_id WHERE r.reset_request_id=%s FOR UPDATE",(reset_request_id,)); reset=cur.fetchone()
                if not reset or reset["request_status"]!="pending": raise ValueError("This password reset request is no longer pending.")
                cur.execute("UPDATE app_user SET password_hash=%s,force_password_change=1,failed_attempts=0,locked_until=NULL WHERE user_id=%s",(generate_password_hash(temporary_password),reset["user_id"]))
                cur.execute("UPDATE password_reset_request SET request_status='issued',processed_at=UTC_TIMESTAMP(),processed_by_user_id=%s WHERE reset_request_id=%s",(g.user["user_id"],reset_request_id))
                create_user_notification(cur,reset["user_id"],"password_reset","An administrator issued a temporary password. Sign in and change it immediately.","app_user",reset["user_id"])
                add_audit(cur,"password_reset_issue","app_user",reset["user_id"],{"request_id":reset_request_id})
        except ValueError as exc:
            flash(str(exc),"error"); return redirect(url_for("password_resets"))
        except MySQLError:
            app.logger.exception("Password reset issue failed"); flash("Could not issue the password reset.","error"); return redirect(url_for("password_resets"))
        return render_template("reset_password_issued.html",temporary_password=temporary_password)

    @app.post("/password-resets/<int:reset_request_id>/close")
    @permission_required("users.manage")
    def close_password_reset(reset_request_id):
        with transaction() as conn:
            cur=conn.cursor(); cur.execute("UPDATE password_reset_request SET request_status='closed',processed_at=UTC_TIMESTAMP(),processed_by_user_id=%s WHERE reset_request_id=%s AND request_status='pending'",(g.user["user_id"],reset_request_id))
            if cur.rowcount!=1: flash("Reset request was not pending.","error")
            else: add_audit(cur,"password_reset_close","password_reset_request",reset_request_id); flash("Reset request closed.","success")
        return redirect(url_for("password_resets"))

    @app.get("/citizen-password-resets")
    @permission_required("users.manage")
    def citizen_password_resets():
        rows = fetch_all("SELECT r.reset_request_id,c.full_name,c.email,c.phone,r.request_status,r.requested_at FROM citizen_password_reset_request r JOIN citizen_account c ON c.citizen_id=r.citizen_id ORDER BY FIELD(r.request_status,'pending','issued','closed'),r.requested_at DESC")
        return render_template("citizen_password_resets.html", rows=rows)

    @app.post("/citizen-password-resets/<int:reset_request_id>/issue")
    @permission_required("users.manage")
    def issue_citizen_password_reset(reset_request_id):
        temporary_password = secrets.token_urlsafe(15)
        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                cur.execute("SELECT reset_request_id,citizen_id,request_status FROM citizen_password_reset_request WHERE reset_request_id=%s FOR UPDATE", (reset_request_id,))
                reset = cur.fetchone()
                if not reset or reset["request_status"] != "pending":
                    raise ValueError("This public-account reset request is no longer pending.")
                cur.execute("UPDATE citizen_account SET password_hash=%s,force_password_change=1,failed_attempts=0,locked_until=NULL WHERE citizen_id=%s", (generate_password_hash(temporary_password), reset["citizen_id"]))
                cur.execute("UPDATE citizen_password_reset_request SET request_status='issued',processed_at=UTC_TIMESTAMP(),processed_by_user_id=%s WHERE reset_request_id=%s", (g.user["user_id"], reset_request_id))
                add_audit(cur, "password_reset_issue", "citizen_account", reset["citizen_id"], {"request_id": reset_request_id})
            response = render_template("reset_password_issued.html", temporary_password=temporary_password, account_type="public", return_endpoint="citizen_password_resets")
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            return response
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("citizen_password_resets"))
        except MySQLError:
            app.logger.exception("Could not issue public-account password reset")
            flash("The reset could not be issued.", "error")
            return redirect(url_for("citizen_password_resets"))

    @app.route("/users/<int:user_id>/edit",methods=["GET","POST"])
    @permission_required("users.manage")
    def edit_user(user_id):
        user=fetch_one("SELECT * FROM app_user WHERE user_id=%s",(user_id,))
        if not user: abort(404)
        if request.method=="POST":
            def update():
                role_id=int(request.form["role_id"]); hospital_id=int(request.form["hospital_id"]) if request.form.get("hospital_id") else None
                with transaction() as conn:
                    cur=conn.cursor(); cur.execute("UPDATE app_user SET display_name=%s,email=%s,phone=%s,role_id=%s,hospital_id=%s WHERE user_id=%s",(required_text("display_name","Display name",160),required_text("email","Email",254),request.form.get("phone") or None,role_id,hospital_id,user_id))
                    add_audit(cur,"update","app_user",user_id,{"username":user["username"]})
            return run_form(update,"User account updated.","users")
        roles=fetch_all("SELECT role_id AS value,name AS label FROM `role` ORDER BY role_id"); hs=fetch_all("SELECT hospital_id AS value,name AS label FROM hospital WHERE is_active=1 ORDER BY name")
        fields=[f("display_name","Display name",required=True),f("email","Email",type="email",required=True),f("phone","Phone"),f("role_id","Role",type="select",options=roles,required=True),f("hospital_id","Hospital association",type="select",options=[{"value":"","label":"All hospitals"}]+hs)]
        values={"display_name":user["display_name"],"email":user["email"],"phone":user.get("phone"),"role_id":user["role_id"],"hospital_id":user["hospital_id"]}
        for field in fields: field["value"]=as_form_value(values.get(field["name"]))
        return render_edit("Edit user",user["username"],"edit_user",fields)

    @app.post("/users/<int:user_id>/active")
    @permission_required("users.manage")
    def user_active(user_id):
        active=request.form.get("active")=="1"
        if user_id==g.user["user_id"] and not active:
            flash("You cannot deactivate your own account.","error"); return redirect(url_for("users"))
        with transaction() as conn:
            cur=conn.cursor(); cur.execute("UPDATE app_user SET is_active=%s WHERE user_id=%s",(active,user_id))
            if cur.rowcount!=1: abort(404)
            add_audit(cur,"status_change","app_user",user_id,{"is_active":active})
        flash("User account status updated.","success")
        return redirect(url_for("users"))

    @app.get("/notifications")
    @login_required
    def notifications():
        rows=fetch_all("SELECT notification_id,notification_type,message,entity_type,entity_id,created_at,read_at FROM notification WHERE user_id=%s ORDER BY read_at IS NULL DESC,created_at DESC",(g.user["user_id"],))
        return render_records("Notifications","In-app updates for emergencies, dispatches, requests, transfers and account actions.","notifications",[c("notification_type","Type"),c("message","Notification"),c("entity_type","Related record"),c("created_at","Received"),c("read_at","Read")],rows)

    @app.post("/notifications/<int:notification_id>/read")
    @login_required
    def notification_read(notification_id):
        with transaction() as conn:
            cur=conn.cursor(); cur.execute("UPDATE notification SET read_at=UTC_TIMESTAMP() WHERE notification_id=%s AND user_id=%s AND read_at IS NULL",(notification_id,g.user["user_id"]))
        return redirect(url_for("notifications"))

    @app.post("/notifications/read-all")
    @login_required
    def notifications_read_all():
        with transaction() as conn:
            cur=conn.cursor(); cur.execute("UPDATE notification SET read_at=UTC_TIMESTAMP() WHERE user_id=%s AND read_at IS NULL",(g.user["user_id"],))
            add_audit(cur,"read_all","notification",g.user["user_id"],{"count":cur.rowcount})
        flash("Notifications marked as read.","success")
        return redirect(url_for("notifications"))

    @app.get("/audit")
    @permission_required("users.manage")
    def audit():
        rows=fetch_all("SELECT a.occurred_at,u.username,a.action,a.entity_type,a.entity_id,a.details,a.source_ip FROM audit_log a LEFT JOIN app_user u ON u.user_id=a.actor_user_id ORDER BY a.occurred_at DESC LIMIT 250")
        return render_records("Audit history","Recent important application operations.","audit",[c("occurred_at","Time"),c("username","User"),c("action","Action"),c("entity_type","Record type"),c("entity_id","Record ID"),c("details","Details"),c("source_ip","Source")],rows)

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html",code=403,message="Your account does not have permission to open this page."),403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html",code=404,message="This LifeLink record or page could not be found."),404

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html",code=400,message=getattr(error,"description","The request could not be processed.")),400

    @app.errorhandler(CSRFError)
    def csrf_failure(_error):
        referrer=urlparse(request.referrer or "")
        retry_url=(referrer.path+(("?"+referrer.query) if referrer.query else "")) if referrer.netloc==request.host and referrer.scheme in {"http","https"} else url_for("index")
        return render_template("csrf_error.html",retry_url=retry_url),400

    @app.errorhandler(500)
    def internal_error(error):
        original = getattr(error, "original_exception", None)
        if original:
            app.logger.error("Unhandled LifeLink request error", exc_info=(type(original), original, original.__traceback__))
        return render_template("error.html", code=500, message="LifeLink could not complete this request. Please retry or contact an administrator."), 500

    return app


def c(key, label):
    return {"key": key, "label": label}


def f(name, label, type="text", required=False, options=None, min=None):
    return {"name": name, "label": label, "type": type, "required": required, "options": options or [], "min": min}


def opts(*pairs):
    return [{"value": value, "label": label} for value,label in pairs]


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=app.config["PORT"], debug=app.config["DEBUG"])
