# Demonstration credentials

The fresh empty database seed creates the following demo-only accounts:

- Username: `admin`
- Password: `DemoLifeLink!2026`

The following staff accounts use the shared demo password `DemoStaff!2026`:

- `emergency.demo` — Emergency Staff
- `ambulance.demo` — Ambulance Staff
- `bloodbank.demo` — Blood Bank Staff
- `hospital.demo` — Hospital Staff

People accounts use `citizen.one@example.invalid` and `citizen.two@example.invalid` with password `DemoCitizen!2026` at `/citizen/login`.

Change the password from **Account** after logging in. These credentials are for synthetic local demonstration only. Seeded emails use the reserved `.invalid` domain; all seeded people, patient, and donor identities are fictional.

`setup_db.py` skips the demo seed if it detects operational records, to avoid mixing or overwriting an existing database. In that case, configure an existing administrator account or initialize a separate empty `lifelink` database for the demo. Never put production or real patient credentials in the project files.
