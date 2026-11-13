# Repository and live deployment checklist

LifeLink is a local academic prototype. This checklist prepares a safe handoff; it does not publish or deploy the app.

## Publish the existing folder

1. Open the existing `LifeLink` folder in GitHub Desktop or VS Code. The local Git repository is initialized in this folder; no files have been committed or uploaded.
2. Review the changed file list before staging. Confirm `.env`, `venv/`, `.venv/`, `instance/`, database exports, and real personal data are not included. `.gitignore` excludes local secrets, virtual environments, generated keys, and local database files.
3. Commit the reviewed project files with a clear message. Create or select the destination repository in your Git provider, then publish the local `main` branch.
4. Never use Git's “add everything” workflow without checking the staged list. Do not put `.env` values, real records, screenshots with private data, or database dumps in a public repository.

## Prepare production configuration

- Keep `LOCAL_DEMO_ACCESS` disabled. Production mode disables it regardless of the local `.env` setting.
- Provision an external MySQL 8 database and a least-privilege runtime user. Keep a separate setup/migration user for `setup_db.py`.
- Configure `APP_ENV=production`, `FLASK_SECRET_KEY` (at least 32 random characters), `DB_HOST`, `DB_PORT`, `DB_NAME=lifelink`, `DB_USER`, `DB_PASSWORD`, `DB_SSL_CA`, and `COOKIE_SECURE=true` in the hosting provider's secret settings.
- Mount the database provider's CA certificate as a private secret file and set `DB_SSL_CA` to that file path. Do not disable TLS verification.
- For first initialization only, provide `DB_SETUP_USER`, `DB_SETUP_PASSWORD`, and a one-time `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_PASSWORD` (at least 16 characters). Remove setup and bootstrap secrets from the running web service after initialization.
- Back up the database before migrations and before live use. Do not run the reset script against a production database.

## Render settings already represented by the project

- Root directory: `.`
- Runtime: Python; `.python-version` selects Python `3.13.5`.
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
- Health checks: `/health` and `/health/db`.

Render's current Flask deployment guide documents the Python web service build/start settings: [Deploy a Flask App](https://render.com/docs/deploy-flask). Python version selection is documented in [Setting Your Python Version](https://render.com/docs/python-version); environment variables and secret files are covered in [Environment Variables and Secrets](https://render.com/docs/configure-environment-variables).

## Go-live verification

- Confirm `/health` returns success and `/health/db` can connect to production MySQL.
- Sign in with the bootstrap administrator and change its password.
- Verify each role can open only its permitted pages; confirm unauthenticated requests are blocked. Production must never use local demo access.
- Walk through an emergency, dispatch, blood request, allocation, and transfer using test records. Confirm audit/event history and backups.
- Review logs and error handling, then monitor the deployment after the first release.

## Not completed here

No GitHub repository or hosting account was supplied, no files were pushed, and no production MySQL provider or secrets were configured. The current app has not been deployed to a public URL. The local MySQL connection and automated tests do not prove full production readiness.
