# LifeLink Verification Record

## Current verification (2026-11-13)

- Existing project suite: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` passed: **33 tests** on 2026-11-13; `python -m py_compile app.py config.py setup_db.py` also passed.
- Regression checks cover local demo access to operational modules while keeping user administration protected, staff access registration with pending approval, citizen username signup and username-or-email sign-in, authentication and password recovery, citizen profile updates and emergency intake, SOS-to-hospital-case linking, donor response matching/recording/alerting, missing-CSRF recovery, emergency/dispatch consistency and valid dispatch creation, blood-request validation, blood inventory behavior, multi-component donations, role-aware navigation, hospital-scoped emergency and inventory lists, and denial of cross-hospital case, hospital, and blood-request access.
- The additive database setup added required citizen usernames and assigned unique `citizen_<id>` usernames to pre-existing people accounts; aggregate checks confirmed all existing accounts have distinct usernames and operational records were preserved. Existing people may continue signing in with their email.
- Local demo access was enabled in the ignored `.env`. Unauthenticated GET checks returned 200 for the dashboard and operational workspaces; `/users` remained protected and redirected to staff sign-in. Production configuration forces this local-only option off.
- Local development now uses a stable secret key across restarts; the placeholder was replaced privately in the ignored `.env`, and a private ignored-instance fallback is used if no usable local key is configured.
- Hospital-assigned staff are scoped in the dashboard, emergency/patient/hospital workflows, ambulance/dispatch operations, blood inventory/screening, blood requests, transfusions, transfers, alerts, reports, and request-linked donor contacts. Donor search and donor activity remain network-wide so staff can locate eligible donors.
- The additive SOS schema and permission update ran successfully against the configured database; setup reported existing operational records and preserved them. Read-only checks confirmed the SOS table, both permissions, and intended emergency response roles.
- Live local checks returned 200 for the public home, citizen registration/login, and donor registration/login; `/citizen/sos` correctly redirects signed-out visitors to sign in; `/health/db` reports connected. A clean restart cleared the stale 500 page on `/citizen/register`.
- The configured `lifelink` database answered a read-only connection check on MySQL 8.0.46 and contained 33 tables.
- Staff admin sign-in and the live dashboard were confirmed in the browser after refreshing the server's saved `.env` configuration.
- Account, ambulance, blood-request, and component-unit regression checks use mocked transactions. Full database-backed create-to-finish workflows, concurrency behavior, and transfers still need an isolated integration run.
- No schema reset or operational-record deletion was performed.

## Latest offline checks (2026-11-12)

- `venv\Scripts\python.exe -m py_compile app.py config.py db.py database.py setup_db.py database\reset_db.py` passed.
- `venv\Scripts\python.exe -m pytest -q` passed: **5 tests**. Coverage: public-page smoke routes, unauthenticated staff-route redirect, Jinja template compilation, registered view mappings, and Flask-WTF CSRF rejection/acceptance.
- Flask imported successfully with 75 routes and 23 templates. All registered endpoints map to a view function.
- Test client: `/`, `/health`, `/login`, and `/donor/login` returned 200; `/health/db` returned 503.
- `database/reset_db.py` without `--confirm` refused to proceed before connecting. The destructive reset was not run.
- Existing `venv` was updated with the requested `pytest` and `Flask-WTF` packages.

## Still not verified end-to-end

Blood inventory, emergency, ambulance, allocation, transfer, donor, report, and account workflows have not all been exercised end-to-end against MySQL. Transaction concurrency and complete role-by-role authorization still need dedicated integration checks. The public citizen portal supports account registration, sign-in, administrator-reviewed password recovery, profile/password management, and SOS reporting with status tracking. Public blood requests are not implemented. Offline or mocked checks do not prove live database workflow behavior.

## To run database integration checks

1. Use the configured local MySQL connection and check `/health/db`.
2. For an existing database, use the additive `venv\Scripts\python.exe setup_db.py` command; it preserves operational records.
3. Run live workflow and concurrency checks against an isolated test database. Do not run `database\reset_db.py --confirm` against a database with records you need; that command drops the configured `lifelink` database.
