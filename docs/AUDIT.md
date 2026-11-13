# LifeLink Codebase Audit

**Audit date:** 2026-11-12  
**Scope:** Existing project files before starting the phased architecture in the latest project brief. This is a source inspection plus local no-database checks; it is not a live MySQL or browser workflow audit.

> **Historical snapshot:** This audit predates the 2026-11-13 fixes. The current verification record in [testing.md](testing.md) supersedes its statements about missing tests, database configuration, citizen portal, and hospital scoping. The audit remains useful for structural and feature gaps that are still outstanding.

## Existing application

- Flask app factory and WSGI export are in root `app.py`. It currently declares about 75 routes directly inside `create_app()` rather than registering Blueprints. Route logic, SQL, validation, transactions, and response rendering are mixed in this module.
- `config.py` loads `.env` with python-dotenv and configures the Flask session, MySQL settings, development/production behavior, and production validation.
- `database.py` contains raw MySQL connection helpers and a transaction context manager that commits or rolls back. Queries are primarily issued from `app.py`; no ORM is present.
- `schema.sql`, `seed.sql`, and `seed_reference.sql` are at the repository root. `setup_db.py` creates/migrates schema and loads reference/sample data. It is intentionally additive and does not reset/drop existing tables.
- The interface is in root `templates/`, with shared CSS and JavaScript under `static/`. The existing LifeLink brand and staff workspace should be retained during later refactors.
- There is no `tests/` directory, no `database/` package, and no `auth/` or `modules/` Blueprint packages. `requirements.txt` currently includes Flask, mysql-connector-python, python-dotenv, and Gunicorn, but not Flask-WTF or pytest.
- `.env` is absent. `.env.example` is present. The previously attempted local MySQL connection using `root` without a password failed with access denied.

## Functionality present in the inspected source

The application has staff login/logout, password hashing/change, role-permission checks, CSRF token validation, staff registration/approval, admin user management and reset-request handling. It also has CRUD/list/detail routes for hospitals, patients, emergencies, ambulance fleet/dispatch, donor profiles, blood units/catalog/screening, blood requests, allocations, transfers, alerts, transfusions, notifications, audit history, dashboard statistics, and reports.

The source includes MySQL transactions for important allocation and transfer operations, allocation uniqueness protection in the schema/migration path, emergency-linked blood requests, screening and expiry checks for eligible stock, and partial fulfillment tracking. A basic donor login/approval/response portal was recently added. These are source-level observations; because MySQL credentials are unavailable, none of the database workflows are confirmed live.

## Gaps against the new brief

| Area | Current state | Required direction |
|---|---|---|
| Application structure | One large app module; no Blueprints or module services/queries | Split into the requested packages incrementally while preserving route behavior |
| Staff portal | Staff login is `/login`; public staff self-registration is enabled and requires admin approval | Move under `/staff`, disable public staff registration, provide first-admin one-time setup, enforce role and organization scope |
| Citizen portal | No citizen account model, separate portal session, SOS inbox/tracking, public blood requests, or citizen ownership checks | Add separate `/citizen/*` account and workflow with guest SOS tracking |
| Donor portal | Registration approval and basic availability/request response exist; no complete citizen donor profile/history/consent workflow | Integrate donor access with citizen identities, consent, donation history, and ownership checks |
| Data model | Current schema models unit-level inventory, hospital custody, staff roles/permissions and donor accounts | Brief proposes batch inventory, blood banks, ledger, SOS/citizen entities, lockout fields, and distinct org scoping; this needs a reviewed migration plan, not table duplication |
| Security | CSRF, password hashes, secure cookie options, permissions and audit logging are present | Add portal separation, brute-force lockout, organization-scoped authorization, explicit citizen ownership, and required role checks |
| Database setup | Root-level schema/seed plus additive `setup_db.py` | Brief requests `database/schema.sql`, synthetic seed data meeting specified counts, and explicit destructive `database/reset_db.py` |
| Testing | No pytest suite; only source/docs mention prior local route/template checks | Add isolated `lifelink_test` integration tests; unavailable MySQL currently blocks them |
| Documentation | Existing README and database/design/testing docs | Generate truthful status, schema-derived dictionary and ER documents as the schema changes |

## Risks and compatibility notes

1. The proposed batch inventory model and existing individual-unit model represent stock differently. Replacing or duplicating them in place could corrupt records or break allocations/transfers. The migration needs an explicit decision about preserving current data and mapping existing units to batches.
2. The required `reset_db.py` is intentionally destructive. It should be used only against an explicitly named development/test database and must never run automatically when the web app starts.
3. Existing URLs and templates may be in use. Blueprint migration should preserve route names/URLs until their replacements are verified, or include redirects.
4. A demo seed currently exists, but the exact count/data coverage in the new brief has not been verified against the seed script. Existing operational data is preserved by `setup_db.py` rather than reset.
5. The current machine has no `.env`, and MySQL rejected the starter `root`/no-password connection. Database schema, migration, seed, and transaction behavior cannot be verified until a valid local test database account is configured.

## Phase 0 result

Audit complete. No application or database records were modified as part of the audit.

## Phase 1 foundation progress

- Added `db.py` as the new connection/transaction helper and kept `database.py` as a compatibility re-export; the Flask app now imports `db.py`.
- Moved the existing schema and seed scripts to `database/schema.sql`, `database/seed.sql`, and `database/seed_reference.sql`. Updated additive `setup_db.py` and documentation paths.
- Added `database/reset_db.py`. It requires `--confirm`, refuses production, and refuses a database name other than `lifelink`; it was not run because it would erase data and credentials are unavailable.
- Added `SECRET_KEY` environment-variable support while retaining the earlier `FLASK_SECRET_KEY` name. Added Flask-WTF and pytest to requirements and installed them into the existing `venv`.
- Switched form CSRF validation to Flask-WTF while retaining existing template calls, added separate public/staff base template entry points, and added the generic 500 page handler.
- Added an offline pytest smoke suite. All five current smoke tests pass.
- Updated README with the safe additive setup path and explicitly destructive local reset command.

**Verification:** Python compile, application import, all 75 current route registrations, 21 Jinja templates, and no-database smoke endpoints passed after the changes. `/health/db` remains 503 because MySQL credentials are unavailable. The reset script's refusal without explicit confirmation is safe to test; the reset itself and database setup were not run.

**Still limited:** Phase 1's offline foundation checks pass, but database-dependent acceptance checks are blocked by missing credentials. The existing schema remains unit-level; requested exact seed counts, test-database migration checks, and real database tests remain outstanding. The root monolithic routes have not been split into Blueprints. Phase 2 is not started; the complete Phase 1 gate still needs a configured disposable MySQL test database.
