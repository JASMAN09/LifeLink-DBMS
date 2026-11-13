"""Fast offline smoke tests; database integration tests require MySQL credentials."""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import app as application


@pytest.fixture()
def client():
    application.config.update(TESTING=True, LOCAL_DEMO_ACCESS=False)
    return application.test_client()


def test_public_pages_render_without_database(client):
    for path in ("/", "/health", "/login", "/staff/login", "/citizen/register", "/citizen/login", "/citizen/forgot-password", "/donor/login"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_management_page_requires_staff_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_local_demo_access_opens_operational_modules_but_not_user_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_all", lambda *args, **kwargs: [])
    application.config["LOCAL_DEMO_ACCESS"] = True
    try:
        modules = ("/dashboard", "/patients", "/emergencies", "/ambulances", "/dispatches", "/blood", "/requests", "/donors", "/hospitals", "/alerts", "/transfers")
        for path in modules:
            response = client.get(path)
            assert response.status_code == 200, path
        assert client.get("/users").status_code == 302
    finally:
        application.config["LOCAL_DEMO_ACCESS"] = False


def test_staff_registration_is_available_and_separate_from_people_account(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert b'name="username"' in response.data
    response = client.get("/citizen/account")
    assert response.status_code == 302
    assert "/citizen/login" in response.headers["Location"]


def test_citizen_signup_validates_password_before_database_write(client):
    page = client.get("/citizen/register")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/citizen/register", data={
        "csrf_token": token,
        "full_name": "Sample Person",
        "username": "sample_person",
        "email": "sample@example.invalid",
        "phone": "5550100",
        "password": "short",
    })
    assert response.status_code == 200
    assert b"at least 8 characters" in response.data


def test_citizen_and_staff_logins_use_separate_portal_sessions(client, monkeypatch):
    executed = []

    class FakeCursor:
        def execute(self, sql, params=()):
            executed.append((sql, params))

    class FakeConnection:
        def cursor(self, *args, **kwargs):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConnection()

    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    citizen = {"citizen_id": 25, "password_hash": generate_password_hash("CitizenPass1"), "is_active": 1, "failed_attempts": 0, "locked_until": None}
    monkeypatch.setattr(app_module, "fetch_one", lambda sql, params=(): citizen if "FROM citizen_account WHERE username" in sql else None)
    page = client.get("/citizen/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/citizen/login", data={"csrf_token": token, "username": "person_one", "password": "CitizenPass1"})
    assert response.status_code == 302
    with client.session_transaction() as state:
        assert state["portal"] == "citizen"
        assert state["citizen_id"] == 25
        assert "user_id" not in state

    staff = {"user_id": 8, "password_hash": generate_password_hash("StaffPass1"), "is_active": 1, "approval_status": "approved", "force_password_change": 0, "failed_attempts": 0, "locked_until": None, "role_code": "ADMIN"}
    staff_lookups = []
    def fetch_staff(sql, params=()):
        if "FROM app_user u JOIN `role`" in sql:
            if "password_hash" in sql:
                staff_lookups.append(params)
            return staff
        return None
    monkeypatch.setattr(app_module, "fetch_one", fetch_staff)
    page = client.get("/staff/login")
    assert b"First-time admin setup" not in page.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/staff/login", data={"csrf_token": token, "username": "admin@example.invalid", "password": "StaffPass1"})
    assert response.status_code == 302
    assert staff_lookups == [("admin@example.invalid", "admin@example.invalid", "admin@example.invalid")]
    with client.session_transaction() as state:
        assert state["portal"] == "staff"
        assert state["user_id"] == 8
        assert "citizen_id" not in state
    assert len(executed) >= 4


def test_citizen_registration_saves_username_and_logs_in(client, monkeypatch):
    statements = []
    class FakeCursor:
        lastrowid = 25
        def execute(self, sql, params=()): statements.append((sql, params))
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    page = client.get("/citizen/register")
    assert b'name="username"' in page.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/citizen/register", data={
        "csrf_token": token, "full_name": "Sample Person", "username": "person_one",
        "email": "person@example.invalid", "phone": "5550100", "password": "CitizenPass1",
    })
    assert response.status_code == 302
    insert = next((params for sql, params in statements if "INSERT INTO citizen_account" in sql), None)
    assert insert and insert[0] == "person_one"
    with client.session_transaction() as state:
        assert state["portal"] == "citizen"
        assert state["citizen_id"] == 25


def test_staff_registration_creates_pending_username_account(client, monkeypatch):
    statements = []
    class FakeCursor:
        lastrowid = 44
        def execute(self, sql, params=()): statements.append((sql, params))
        def fetchone(self): return (7,) if "role_code='VIEWER'" in statements[-1][0] else None
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    page = client.get("/register")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/register", data={
        "csrf_token": token, "display_name": "Sample Staff", "username": "sample_staff",
        "email": "staff@example.invalid", "phone": "5550100", "password": "StaffPassword123",
    })
    assert response.status_code == 302
    insert = next((params for sql, params in statements if "INSERT INTO app_user" in sql), None)
    assert insert and insert[1] == "sample_staff"
    assert "0,'pending'" in next(sql for sql, _ in statements if "INSERT INTO app_user" in sql)


def test_templates_compile():
    for name in application.jinja_env.list_templates():
        application.jinja_env.get_template(name)


def test_every_registered_route_has_a_view():
    assert all(rule.endpoint in application.view_functions for rule in application.url_map.iter_rules())


def test_posts_require_flask_wtf_csrf_token(client):
    assert client.post("/logout").status_code == 400
    page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302


def test_missing_csrf_session_shows_a_safe_reload_page(client):
    page=client.get("/citizen/register")
    token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
    with client.session_transaction() as state:
        state.clear()
    response=client.post("/citizen/register",data={"csrf_token":token})
    assert response.status_code==400
    assert b"Refresh this form" in response.data and b"This submission was not saved" in response.data

def test_password_recovery_stays_in_the_requested_portal(client, monkeypatch):
    import mysql.connector

    for path, destination in (("/citizen/forgot-password", "/citizen/login"), ("/staff/forgot-password", "/staff/login")):
        page = client.get(path)
        assert page.status_code == 200
        assert f'href="{destination}"'.encode() in page.data
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        monkeypatch.setattr(app_module, "fetch_one", lambda *args, **kwargs: (_ for _ in ()).throw(mysql.connector.Error("offline")))
        response = client.post(path, data={"csrf_token": token, "email": "person@example.invalid"})
        assert response.status_code == 503
        assert f'href="{destination}"'.encode() in response.data


def test_citizen_can_update_contact_details(client, monkeypatch):
    citizen = {"citizen_id": 25, "full_name": "Sample Person", "email": "person@example.invalid", "phone": "5550100", "force_password_change": 0}
    statements = []

    class FakeCursor:
        def execute(self, sql, params=()):
            statements.append((sql, params))

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConnection()

    monkeypatch.setattr(app_module, "fetch_one", lambda *args, **kwargs: citizen)
    monkeypatch.setattr(app_module, "fetch_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    with client.session_transaction() as state:
        state["portal"] = "citizen"
        state["citizen_id"] = citizen["citizen_id"]

    page = client.get("/citizen/account")
    assert page.status_code == 200
    assert b'value="person@example.invalid"' in page.data and b'readonly' in page.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/citizen/account/profile", data={
        "csrf_token": token,
        "full_name": "Updated Person",
        "phone": "5550200",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/citizen/account")
    assert any("UPDATE citizen_account SET full_name=%s,phone=%s" in sql for sql, _ in statements)

@pytest.mark.parametrize("remaining_dispatches, should_reset_case", [(0, True), (1, False)])
def test_cancelling_dispatch_reopens_case_only_when_no_other_ambulance_is_active(client, monkeypatch, remaining_dispatches, should_reset_case):
    statements = []
    admin = {"user_id": 8, "username": "admin", "display_name": "Admin", "hospital_id": None, "role_code": "ADMIN", "role_name": "Administrator"}
    dispatch = {"dispatch_id": 3, "ambulance_id": 4, "emergency_case_id": 5, "dispatch_status": "dispatched", "pickup_hospital_id": 1, "destination_hospital_id": 2}

    class FakeCursor:
        def execute(self, sql, params=()):
            statements.append(sql)
        def fetchone(self):
            if "SELECT emergency_case_id,pickup_hospital_id,destination_hospital_id" in statements[-1]:
                return {"emergency_case_id": 5, "pickup_hospital_id": 1, "destination_hospital_id": 2}
            if "SELECT case_status,receiving_hospital_id" in statements[-1]:
                return {"case_status": "response_in_progress", "receiving_hospital_id": 1}
            if "SELECT d.*,a.operational_status" in statements[-1]:
                return dispatch
            if "SELECT COUNT(*) AS active_dispatches" in statements[-1]:
                return {"active_dispatches": remaining_dispatches}
            return None

    class FakeConnection:
        def cursor(self, *args, **kwargs):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConnection()

    def fake_fetch_one(sql, params=()):
        if "FROM app_user u JOIN `role`" in sql:
            return admin
        if "FROM notification" in sql:
            return {"total": 0}
        return None

    monkeypatch.setattr(app_module, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(app_module, "fetch_all", lambda *args, **kwargs: [{"permission_code": "dispatches.write"}])
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    login_page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    with client.session_transaction() as state:
        state["portal"] = "staff"
        state["user_id"] = admin["user_id"]
    response = client.post("/dispatches/3/status", data={"csrf_token": token, "status": "cancelled"})
    assert response.status_code == 302
    reset_case_updates = [sql for sql in statements if "SET case_status='registered'" in sql]
    assert bool(reset_case_updates) is should_reset_case

def _mock_staff_session(client, monkeypatch, permission, hospital_id=None, role_code="ADMIN"):
    admin = {"user_id": 8, "username": "admin", "display_name": "Test Staff", "hospital_id": hospital_id, "role_code": role_code, "role_name": role_code.title()}
    def fake_fetch_one(sql, params=()):
        if "FROM app_user u JOIN `role`" in sql:
            return admin
        if "FROM notification" in sql:
            return {"total": 0}
        return None
    monkeypatch.setattr(app_module, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(app_module, "fetch_all", lambda *args, **kwargs: [{"permission_code": permission}, {"permission_code": "dispatches.read"}, {"permission_code": "cases.read"}, {"permission_code": "requests.read"}])
    page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    with client.session_transaction() as state:
        state["portal"] = "staff"
        state["user_id"] = admin["user_id"]
    return token


def test_emergency_cannot_be_cancelled_while_dispatch_is_active(client, monkeypatch):
    statements = []
    class FakeCursor:
        def execute(self, sql, params=()): statements.append(sql)
        def fetchone(self):
            if "SELECT case_status FROM emergency_case" in statements[-1]:
                return {"case_status": "response_in_progress"}
            if "SELECT dispatch_id FROM ambulance_dispatch" in statements[-1]:
                return {"dispatch_id": 3}
            return None
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    token = _mock_staff_session(client, monkeypatch, "cases.write")
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    response = client.post("/emergencies/5/status", data={"csrf_token": token, "case_status": "cancelled"})
    assert response.status_code == 302
    assert not any("UPDATE emergency_case SET case_status" in sql for sql in statements)


def test_closed_emergency_cannot_receive_a_new_dispatch(client, monkeypatch):
    statements = []
    class FakeCursor:
        def execute(self, sql, params=()): statements.append(sql)
        def fetchone(self): return {"case_status": "completed"}
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    token = _mock_staff_session(client, monkeypatch, "dispatches.write")
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    response = client.post("/dispatches", data={
        "csrf_token": token, "emergency_case_id": "5", "ambulance_id": "4", "driver_id": "6",
        "destination_hospital_id": "2", "pickup_location": "Origin", "destination_location": "Hospital",
    })
    assert response.status_code == 302
    assert not any("SELECT * FROM ambulance WHERE" in sql for sql in statements)


def test_available_ambulance_can_be_dispatched_to_open_emergency(client, monkeypatch):
    statements = []
    class FakeCursor:
        lastrowid = 31
        def execute(self, sql, params=()): statements.append((sql, params))
        def fetchone(self):
            sql = statements[-1][0]
            if "SELECT case_status,receiving_hospital_id FROM emergency_case" in sql:
                return {"case_status": "registered", "receiving_hospital_id": 2}
            if "SELECT * FROM ambulance WHERE" in sql:
                return {"operational_status": "available", "home_hospital_id": 2}
            if "SELECT driver_id,employer_hospital_id FROM driver" in sql:
                return {"driver_id": 6, "employer_hospital_id": 2}
            return None
        def fetchall(self): return []
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    token = _mock_staff_session(client, monkeypatch, "dispatches.write")
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    response = client.post("/dispatches", data={
        "csrf_token": token, "emergency_case_id": "5", "ambulance_id": "4", "driver_id": "6",
        "destination_hospital_id": "2", "pickup_location": "Origin", "destination_location": "Hospital",
    })
    assert response.status_code == 302
    assert any("INSERT INTO ambulance_dispatch" in sql for sql, _ in statements)
    assert any("UPDATE ambulance SET operational_status='dispatched'" in sql for sql, _ in statements)

@pytest.mark.parametrize("case,request_hospital", [
    ((3, "registered"), "2"),
    ((3, "completed"), "3"),
])
def test_blood_request_requires_open_case_and_receiving_hospital(client, monkeypatch, case, request_hospital):
    statements = []
    class FakeCursor:
        def execute(self, sql, params=()): statements.append(sql)
        def fetchone(self): return case
    class FakeConnection:
        def cursor(self, *args, **kwargs): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    token = _mock_staff_session(client, monkeypatch, "requests.write")
    monkeypatch.setattr(app_module, "transaction", fake_transaction)
    response = client.post("/requests", data={
        "csrf_token": token, "emergency_case_id": "5", "requesting_hospital_id": request_hospital,
        "blood_group_id": "1", "component_id": "1", "required_units": "2", "urgency": "urgent",
    })
    assert response.status_code == 302
    assert not any("INSERT INTO blood_request(" in sql for sql in statements)

def test_operational_templates_have_clean_text_encoding():
    for name in ("case_detail.html", "dashboard.html", "hospital_detail.html", "request_detail.html", "transfer_detail.html"):
        content = (Path(application.root_path) / "templates" / name).read_text(encoding="utf-8")
        assert not any(marker in content for marker in ("Ã", "Â", "â", "ï¼", "ðŸ", "�")), name



def test_blood_donation_adds_component_unit_and_pending_screening(client, monkeypatch):
    statements = []
    donation = {"donation_id": 12, "collection_hospital_id": 3, "blood_group_id": 2}
    class Cursor:
        lastrowid = 71
        def execute(self, sql, params=()): statements.append((sql, params))
        def fetchone(self):
            sql = statements[-1][0]
            if "FROM donation d JOIN donor dn" in sql: return donation
            if "FROM blood_component WHERE component_id" in sql: return {"component_id": 4}
            return None
    class Connection:
        def cursor(self, *args, **kwargs): return Cursor()
    from contextlib import contextmanager
    @contextmanager
    def transaction(): yield Connection()
    token = _mock_staff_session(client, monkeypatch, "blood.write")
    monkeypatch.setattr(app_module, "transaction", transaction)
    response = client.post("/donations/12/components", data={"csrf_token": token, "component_id": "4", "unit_code": "UNIT-MULTI-02", "expires_on": "2099-01-01T12:00"})
    assert response.status_code == 302
    unit = next(params for sql, params in statements if "INSERT INTO blood_unit" in sql)
    assert unit[:6] == (12, 4, 2, 3, 8, "UNIT-MULTI-02")
    assert any("INSERT INTO screening_record" in sql for sql, _ in statements)


def test_donation_components_screen_lists_existing_units(client, monkeypatch):
    token = _mock_staff_session(client, monkeypatch, "blood.write")
    donation = {"donation_id": 12, "donation_ref": "DNT-12", "collected_at": "2026-11-01", "donor_ref": "DON-12", "display_name": "Sample Donor", "blood_group": "O+"}
    admin = {"user_id": 8, "username": "admin", "display_name": "Admin", "hospital_id": None, "role_code": "ADMIN", "role_name": "Administrator"}
    def fetch_one(sql, params=()):
        if "FROM app_user u JOIN `role`" in sql: return admin
        if "FROM notification" in sql: return {"total": 0}
        return donation
    monkeypatch.setattr(app_module, "fetch_one", fetch_one)
    def fetch_all(sql, params=()):
        if "permission_code" in sql: return [{"permission_code": "blood.write"}]
        if "FROM blood_unit u JOIN blood_component" in sql:
            return [{"unit_code": "UNIT-01", "component": "Red Cells", "expires_on": "2026-12-01", "inventory_status": "quarantined"}]
        return [{"value": 4, "label": "Plasma"}]
    monkeypatch.setattr(app_module, "fetch_all", fetch_all)
    with client.session_transaction() as state:
        state["portal"] = "staff"
        state["user_id"] = 8
    response = client.get("/donations/12/components")
    assert response.status_code == 200
    assert b"UNIT-01" in response.data and b"Plasma" in response.data
    assert b"New units start in quarantine" in response.data


def test_staff_navigation_shows_only_permitted_workspaces():
    from flask import g, render_template
    with application.test_request_context("/dashboard"):
        g.user = {"user_id": 9, "display_name": "Viewer"}
        g.permissions = {"dashboard.read", "cases.read"}
        g.unread_notification_count = 0
        limited = render_template("base.html") + render_template("_staff_sidebar.html")
        assert "Emergencies" in limited and "Dashboard" in limited
        assert "Blood bank" not in limited and "Transfusions" not in limited and "User access" not in limited
        g.permissions = {"dashboard.read", "cases.read", "patients.read", "dispatches.read", "requests.read", "blood.read", "donors.read", "transfers.read", "alerts.read", "hospitals.read", "users.manage"}
        admin = render_template("base.html") + render_template("_staff_sidebar.html")
        for label in ("Dashboard", "Emergencies", "Patients", "Ambulance dispatch", "Blood requests", "Blood inventory", "Donors", "Transfers", "Alerts", "Hospitals", "User access"):
            assert label in admin

def test_hospital_scoped_staff_sees_only_assigned_emergency_cases(client, monkeypatch):
    token = _mock_staff_session(client, monkeypatch, "cases.read", hospital_id=4, role_code="HOSPITAL_STAFF")
    del token
    seen = []
    def fetch_all(sql, params=()):
        seen.append((sql, params))
        if "permission_code" in sql:
            return [{"permission_code": "cases.read"}, {"permission_code": "cases.write"}]
        return []
    monkeypatch.setattr(app_module, "fetch_all", fetch_all)
    response = client.get("/emergencies")
    assert response.status_code == 200
    case_query = next((sql, params) for sql, params in seen if "FROM emergency_case c JOIN patient" in sql)
    assert "WHERE c.receiving_hospital_id=%s" in case_query[0]
    assert case_query[1] == (4,)


def test_hospital_scoped_staff_cannot_create_case_for_another_hospital(client, monkeypatch):
    token = _mock_staff_session(client, monkeypatch, "cases.write", hospital_id=4, role_code="HOSPITAL_STAFF")
    response = client.post("/emergencies", data={
        "csrf_token": token, "patient_id": "2", "receiving_hospital_id": "3",
        "emergency_location": "Demo location", "emergency_type": "Test", "priority_label": "routine",
        "case_status": "registered",
    })
    assert response.status_code == 404


def test_hospital_scoped_staff_cannot_open_another_hospital_profile(client, monkeypatch):
    _mock_staff_session(client, monkeypatch, "hospitals.read", hospital_id=4, role_code="HOSPITAL_STAFF")
    staff = {"user_id": 8, "username": "admin", "display_name": "Test Staff", "hospital_id": 4, "role_code": "HOSPITAL_STAFF", "role_name": "Hospital Staff"}
    def fetch_one(sql, params=()):
        if "FROM app_user u JOIN `role`" in sql: return staff
        if "FROM notification" in sql: return {"total": 0}
        return {"hospital_id": 3, "name": "Other Hospital"}
    monkeypatch.setattr(app_module, "fetch_one", fetch_one)
    assert client.get("/hospitals/3").status_code == 404


def test_hospital_scoped_blood_inventory_query_is_limited_to_assigned_hospital(client, monkeypatch):
    _mock_staff_session(client, monkeypatch, "blood.read", hospital_id=4, role_code="HOSPITAL_STAFF")
    seen=[]
    def fetch_all(sql, params=()):
        seen.append((sql,params))
        if "permission_code" in sql: return [{"permission_code":"blood.read"}]
        return []
    monkeypatch.setattr(app_module,"fetch_all",fetch_all)
    assert client.get("/blood").status_code==200
    query=next((sql,params) for sql,params in seen if "FROM blood_unit u JOIN blood_group" in sql)
    assert "u.current_hospital_id=%s" in query[0]
    assert query[1]==(4,4)


def test_hospital_scoped_staff_cannot_open_another_hospital_blood_request(client, monkeypatch):
    _mock_staff_session(client, monkeypatch, "requests.read", hospital_id=4, role_code="HOSPITAL_STAFF")
    assert client.get("/requests/77").status_code==404


def test_citizen_sos_submission_saves_an_owned_report(client, monkeypatch):
    citizen={"citizen_id":25,"full_name":"Sample Person","email":"person@example.invalid","phone":"5550100","force_password_change":0}
    statements=[]
    class FakeCursor:
        lastrowid=91
        def execute(self,sql,params=()): statements.append((sql,params))
    class FakeConnection:
        def cursor(self): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    monkeypatch.setattr(app_module,"fetch_one",lambda sql,params=(): citizen if "FROM citizen_account" in sql else {"hospital_id":4})
    monkeypatch.setattr(app_module,"fetch_all",lambda *args,**kwargs: [])
    monkeypatch.setattr(app_module,"transaction",fake_transaction)
    with client.session_transaction() as state:
        state["portal"]="citizen"; state["citizen_id"]=25
    page=client.get("/citizen/sos")
    csrf=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
    response=client.post("/citizen/sos",data={"csrf_token":csrf,"emergency_type":"Road incident","emergency_location":"Main Street","priority_label":"urgent"})
    assert response.status_code==302
    saved=next(params for sql,params in statements if "INSERT INTO citizen_sos_request" in sql)
    assert saved[0]==25 and saved[3:5]==("Road incident","Main Street")


def test_staff_links_citizen_sos_to_hospital_emergency_case(client,monkeypatch):
    token=_mock_staff_session(client,monkeypatch,"citizen_sos.write",hospital_id=4,role_code="HOSPITAL_STAFF")
    statements=[]
    sos={"sos_request_id":91,"request_ref":"SOS-91","citizen_id":25,"preferred_hospital_id":4,"linked_emergency_case_id":None,"request_status":"submitted","priority_label":"urgent","emergency_location":"Main Street","location_notes":None,"emergency_type":"Road incident","contact_name":"Sample Person","contact_phone":"5550100","full_name":"Sample Person","phone":"5550100"}
    class FakeCursor:
        lastrowid=120
        def __init__(self): self.sql=""
        def execute(self,sql,params=()): self.sql=sql; statements.append((sql,params))
        def fetchone(self):
            if "FROM citizen_sos_request s JOIN citizen_account" in self.sql: return sos
            if "FROM hospital WHERE" in self.sql: return {"hospital_id":4}
            return None
        def fetchall(self): return []
    class FakeConnection:
        def cursor(self,dictionary=False): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    real_fetch_one=app_module.fetch_one
    def fetch_one(sql,params=()):
        if "FROM app_user u JOIN `role`" in sql: return {"user_id":8,"display_name":"Staff","hospital_id":4,"role_code":"HOSPITAL_STAFF","role_name":"Hospital Staff"}
        if "FROM notification" in sql: return {"total":0}
        return real_fetch_one(sql,params)
    monkeypatch.setattr(app_module,"fetch_one",fetch_one)
    monkeypatch.setattr(app_module,"fetch_all",lambda sql,*args,**kwargs: [{"permission_code":"citizen_sos.write"}] if "permission_code" in sql else [])
    monkeypatch.setattr(app_module,"transaction",fake_transaction)
    response=client.post("/citizen-sos/91/link",data={"csrf_token":token,"hospital_id":"4"})
    assert response.status_code==302
    assert any("INSERT INTO patient" in sql for sql,_ in statements)
    assert any("INSERT INTO emergency_case" in sql for sql,_ in statements)
    assert any("UPDATE citizen_sos_request" in sql for sql,_ in statements)


def test_donor_response_is_matched_alerted_and_saved_to_request_line(client,monkeypatch):
    page=client.get("/donor/login")
    csrf=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
    donor={"donor_id":31,"donor_ref":"DON-31","display_name":"Sample Donor","availability_status":"available"}
    monkeypatch.setattr(app_module,"fetch_one",lambda sql,params=(): donor if "FROM donor_account a JOIN donor" in sql else None)
    statements=[]
    class FakeCursor:
        def __init__(self): self.sql=""
        def execute(self,sql,params=()): self.sql=sql; statements.append((sql,params))
        def fetchone(self): return {"request_line_id":12,"requesting_hospital_id":4,"request_ref":"REQ-88","requested_by_user_id":9} if "SELECT l.request_line_id,b.requesting_hospital_id" in self.sql else None
        def fetchall(self): return []
    class FakeConnection:
        def cursor(self,dictionary=False): return FakeCursor()
    @contextmanager
    def fake_transaction(): yield FakeConnection()
    monkeypatch.setattr(app_module,"transaction",fake_transaction)
    with client.session_transaction() as state:
        state["portal"]="donor"; state["donor_account_id"]=5
    response=client.post("/donor/requests/88/respond",data={"csrf_token":csrf,"outcome":"interested"})
    assert response.status_code==302
    match_sql=next(sql for sql,_ in statements if "SELECT l.request_line_id,b.requesting_hospital_id" in sql)
    assert "d.blood_group_id=l.blood_group_id" in match_sql and "NOT EXISTS" in match_sql
    assert any("INSERT INTO donor_contact_attempt" in sql for sql,_ in statements)
    assert any("INSERT INTO alert" in sql for sql,_ in statements)

