# LIFELINK

**LIFELINK – Integrated Emergency Response and Blood Resource Management System** is a Flask/MySQL academic demonstration platform for emergency cases, ambulance dispatch, blood-bank inventory, donor coordination and hospital transfers. All built-in example records are synthetic.

## Setup (Windows PowerShell)

### First run on a new computer

Open the LifeLink folder in VS Code, then open **Terminal → New Terminal**. Run:

```powershell
if (!(Test-Path .\venv\Scripts\python.exe)) { py -3.13 -m venv venv }
.\venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

This reuses an existing `venv` instead of replacing it. Set `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME=lifelink`, and a private random `SECRET_KEY`. Do not share or publish `.env`. It is excluded by `.gitignore`.

In MySQL Workbench, make sure MySQL 8 is running and the configured MySQL user can create/use the `lifelink` database. Then, from the project folder, initialize the tables and demo data:

```powershell
.\venv\Scripts\python.exe setup_db.py
```

Setup creates missing tables and applies additive migrations. It preserves existing records and loads synthetic examples only when operational tables are empty. **Do not run the reset script on your current database for a presentation.**

Start LifeLink:

```powershell
.\venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5050/**. The app uses port 5050 by default. Keep the terminal open while presenting. Stop the server with **Ctrl+C**.

### Local demo access

The example `.env` keeps `LOCAL_DEMO_ACCESS=false`, which uses approved staff sign-in. For a local classroom demo only, set `LOCAL_DEMO_ACCESS=true` to open the operational workspaces directly. This allows anyone using the local computer to view and change the local database. The app binds to `127.0.0.1`, and production mode always disables local demo access. Set the value back to `false` for a role-based sign-in demonstration.

### Existing setup

If LifeLink is already installed, keep the existing virtual environment and `.env`. Start MySQL, then run:

```powershell
.\venv\Scripts\python.exe setup_db.py
.\venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5050/**. Staff sign-in is at `/staff/login`; public accounts use `/citizen/register` and `/citizen/login`. First administrator setup is at `/setup` only until an administrator exists. Health checks: `/health` and `/health/db`.

### Demo accounts and sample records

On a fresh empty database, see [demo_credentials.md](docs/demo_credentials.md) for synthetic demo-only admin, staff, and people accounts. Change demo passwords after signing in. When setup detects existing operational records, it skips demo seeding rather than mixing sample records into them. See the [presentation guide](docs/presentation_guide.md) for a short demonstration flow.

### Database reset (destructive)

To deliberately erase and recreate the local `lifelink` database for a fresh academic demo, use this separate command **only after backing up and confirming the database contains no data you need**:

```powershell
.\venv\Scripts\python.exe database\reset_db.py --confirm
```

Reset is blocked when `APP_ENV=production` and refuses any `DB_NAME` other than `lifelink`.

## Publishing and going live

The project uses Flask and MySQL. It is prepared for a GitHub repository, but is not uploaded or deployed. Before publishing, use GitHub Desktop or Git to review the file list and confirm `.env`, virtual environments, database exports, and real personal data are excluded. Start with the [deployment checklist](docs/deployment_checklist.md). Public hosting needs a separate reachable MySQL provider, production secrets, and TLS configuration; local demo access is disabled in production.

## Main workflows

- **Emergency to request:** add a patient, register an emergency and hospital, open its detail page, then use **Create request**. The form preselects the case and receiving hospital. Request quantities are linked to individual product lines.
- **Shortage and donors:** the request view compares required units with eligible local units and shows the shortage. Candidates are filtered by exact recorded group, location and availability. Staff record contact outcomes; the app does not decide donor suitability.
- **Donor portal:** a donor can submit a registration for staff approval, sign in after approval, update availability and respond to approved requests matching their recorded group. A portal response records interest or decline only; staff must coordinate collection and make any inventory allocation.
- **Partial allocation:** reserve eligible units in a transaction; pending quantity is `required - active allocations`. Mark units issued or release reservations. Request status and shortage/pending alerts follow the allocation history.
- **Inventory:** each unit links to a donation, donor, component, recorded blood group and custody hospital. New units start quarantined with clearance pending. Only unexpired units with a latest `inventory_clearance` result entered as `cleared`, and no active allocation/transfer, count as eligible inventory.
- **Ambulance:** register vehicles/drivers, dispatch an available vehicle to a case, mark pickup and arrival. Active dispatch locks the assignment; arrival releases the ambulance and updates case status.
- **Transfer:** request a transfer, approve/reject it, mark it dispatched, then confirm receipt at the destination. Active transfer units are unavailable to allocation; custody changes at receipt.
- **Alerts and reporting:** refresh shortage/expiry alerts or review event alerts. Reports support date filters and applicable hospital filters, CSV download and print view.

## Implemented areas

Authentication, staff registration and administrator approval/role assignment, password-reset request workflow, role permissions, CSRF protection, password hashing/change, patients, emergencies, hospitals, ambulance/driver fleet and dispatches, donor registration/login/approval, donor profiles and contact responses, lookup catalogs, donations and component-level blood units, screening and crossmatch records, emergency-linked requests, shortage calculations, partial allocation/release/issue, transfers and status history, transfusion outcomes, shortage/expiry/low-stock alerts, audit history, database-backed dashboard statistics/charts, and reports. Low-stock notifications use `LOW_STOCK_THRESHOLD` (default 2 eligible units per hospital/group/component). Detailed notes: [project_features.md](docs/project_features.md), [database_design.md](docs/database_design.md), [ER_diagram.md](docs/ER_diagram.md).

The database schema uses MySQL 8.0+ and InnoDB. No SQLite fallback exists. The system records administrative status and test results as entered; it does not automate clinical compatibility, diagnosis or donor eligibility decisions.

## Academic project report

See [PROJECT_REPORT/project_report.md](PROJECT_REPORT/project_report.md) for the academic report, [PROJECT_REPORT/queries.sql](PROJECT_REPORT/queries.sql) for 25 read-only MySQL queries, and [presentation_guide.md](docs/presentation_guide.md) for a short demonstration plan. No live database export or private local data belongs in the submission.

## Verification status

Syntax, Flask routes, template compilation and public HTTP routes can be checked without MySQL. Live CRUD and end-to-end database workflows require a valid `.env`; see [testing.md](docs/testing.md) for the exact status and remaining verification.

## Production deployment (Render web service + managed MySQL)

Production mode fails closed unless a long Flask secret, non-root MySQL credentials and a CA certificate file for verified MySQL TLS are configured. The local development defaults are not accepted in production. Render's Flask guide uses Gunicorn with `gunicorn app:app`; Render environment variables should be configured in its service dashboard rather than committed to the repository ([Flask deploy guide](https://render.com/docs/deploy-flask), [environment variables](https://render.com/docs/configure-environment-variables)).

1. Create a GitHub repository and push this project only after reviewing `git status`. `.env`, `.env.*` (except `.env.example`), private key files and local virtual environments are ignored. Never upload real patient or donor information.
2. Provision a managed MySQL 8 database reachable from the web service. Create database `lifelink`, a runtime user with only the application privileges (`SELECT`, `INSERT`, `UPDATE`, `DELETE`), and a separate migration/setup account with schema privileges. Restrict network access to the app where the provider supports private networking or source IP rules. Download the provider's CA certificate; do not disable TLS verification.
3. From a controlled migration environment, set `APP_ENV=production`, `DB_HOST`, `DB_PORT`, `DB_NAME=lifelink`, `DB_SETUP_USER`, `DB_SETUP_PASSWORD`, `DB_SSL_CA`, `FLASK_SECRET_KEY` (at least 32 random characters), plus `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL` and a one-time `BOOTSTRAP_ADMIN_PASSWORD` (16+ characters). Set `DB_USER` and `DB_PASSWORD` to the runtime account. Run `python setup_db.py`. Production setup inserts only roles, permissions, blood groups/components and the secure bootstrap admin; it does not seed demo patients, donors, emergencies or demo credentials. Remove the bootstrap password and setup-account secrets after successful initialization. For later schema migrations, run the setup command only from the controlled migration environment.
4. In Render, create **New → Web Service** from the pushed repository. Use root directory `.`, runtime **Python**, build command `pip install -r requirements.txt`, and start command `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`. The project includes a `Procfile` and `.python-version` for the same runtime setup.
5. Add the runtime environment variables in Render's Environment settings: `APP_ENV=production`, `FLASK_SECRET_KEY`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME=lifelink`, `DB_SSL_CA` (path to a securely mounted CA file), and `COOKIE_SECURE=true`. Do not configure `FLASK_DEBUG=true`. The setup/bootstrap secrets should not be present in the running web service.
6. Deploy, then verify `/health` returns `200` and `/health/db` returns `200`. Create the first hospital, then use the bootstrap administrator account to register operational records. Set a new administrator password after the first login and retain access to database backups/restore procedures.

Example GitHub publishing commands after creating an empty remote repository:

```powershell
git init
git add .
git status --short
# Verify .env and personal data are not staged.
git commit -m "Prepare LifeLink application"
git branch -M main
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin main
```

These steps prepare a deployment; LifeLink has not been published or deployed. The managed MySQL provider, GitHub remote, secrets, CA certificate and production smoke test still need to be supplied/configured.
