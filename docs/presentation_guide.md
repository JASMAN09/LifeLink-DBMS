# LifeLink school presentation guide

## Project in one minute

LifeLink is an academic Flask and MySQL prototype for coordinating emergency cases, ambulance dispatch, hospital resources, blood inventory, donor responses, and inter-hospital transfers. Its database links those records so that a request can be traced to a case, hospital, product, individual blood units, and follow-up events. Seeded example records are fictional. LifeLink does not make diagnoses or clinical eligibility decisions.

## Suggested 6-minute presentation

1. **Problem and goal — 45 seconds.** Explain why separate emergency, ambulance, hospital, and blood lists make coordination hard. State that the prototype links these records in one relational database.
2. **Architecture — 60 seconds.** Show Flask routes and templates, the MySQL schema, and the ER diagram. Point out primary keys, foreign keys, junction tables, status history, and the `blood_request_line` relationship.
3. **Emergency workflow — 90 seconds.** Open **Emergencies**, select a case, and show its linked patient, receiving hospital, current status, and event timeline. Explain that each case has a unique reference.
4. **Resource coordination — 90 seconds.** Show **Ambulance dispatch** and an available vehicle, then show a blood request and its product lines. Explain that eligibility is calculated from unit status, screening, expiry, allocations, and transfers.
5. **Database design — 75 seconds.** Open `PROJECT_REPORT/queries.sql` or MySQL Workbench. Explain one-to-many relationships, normalized tables, constraints, and transaction/row-lock handling for allocations and transfers.
6. **Limitations and close — 40 seconds.** State that this is a simulated academic prototype. It does not contact real emergency services, decide medical compatibility, send email resets, or replace hospital systems. Production deployment and full end-to-end workflow verification remain separate work.

## Before presenting

- Start MySQL and confirm `http://127.0.0.1:5050/health/db` reports a connected database.
- Start the app from the project folder using `.\venv\Scripts\python.exe app.py`, then open `http://127.0.0.1:5050/`.
- Keep the terminal open while the app is running. Press **Ctrl+C** when finished.
- Review the records you plan to show. Use only synthetic information; the current local database may contain records created during testing. Do not display or upload real patient, donor, phone, email, or password data.
- Do not run `database/reset_db.py --confirm` on the current database. Setup preserves existing records and skips demo seeding when operational records exist.
- If using staff sign-in, use an approved staff account. The demo usernames and passwords in [demo_credentials.md](demo_credentials.md) work only when a fresh empty database was seeded; they are not guaranteed to exist in an already populated database.
- `LOCAL_DEMO_ACCESS=true` allows anyone using the local computer to change records without staff sign-in. For a role/permission demonstration, set it to `false`, restart the app, and use an approved account.

## Database-management talking points

- **Normalization:** patients, hospitals, emergencies, requests, products, donors, and units are stored in separate related tables instead of repeating descriptive data in one large table.
- **Integrity:** foreign keys, unique references, status checks, and positive quantity validation guard relationships and values.
- **Transactions:** allocations and transfers use transactional updates and row locks so an individual unit cannot be assigned twice at the same time.
- **Derived values:** pending quantities and shortages are calculated from request lines, active allocations, and eligible inventory instead of saved as duplicate editable totals.
- **Auditability:** case events, transfer milestones, notifications, and audit records preserve a history of important changes.

## Likely questions

**Why MySQL?** It demonstrates relational constraints, joins, transactions, and multi-table workflow relationships required by the DBMS project.

**What does an available blood unit mean in the application?** It is an administrative inventory rule: the unit is marked available, unexpired, has a latest clearance record entered as cleared, has no active allocation, and is not in an active transfer. This does not establish clinical compatibility.

**Is it ready for real hospital use?** No. It is a student prototype using simulated data. A live service would need a managed MySQL database, protected production secrets, verified TLS, backups, operational monitoring, and formal clinical/security review.

## Submission files

- `PROJECT_REPORT/project_report.md` — report and relational design explanation.
- `PROJECT_REPORT/queries.sql` — 25 read-only MySQL queries.
- `docs/ER_diagram.md` — Mermaid ER diagram.
- `docs/database_design.md` — relationships, normalization, and transaction rules.
- `docs/testing.md` — what has and has not been verified.
