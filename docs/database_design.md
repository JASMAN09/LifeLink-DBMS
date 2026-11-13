# LifeLink database design

**Status:** Implemented in `database/schema.sql`; setup applies additive migrations in `setup_db.py`. Database tables are MySQL 8.0+ InnoDB. No records are deleted by setup. Synthetic seed data is loaded only when the operational tables are empty.

## Model and keys

| Area | Tables and key relationships |
|---|---|
| Access control | `app_user` belongs to `role`; `role_permission` joins `role` to `permission`. `audit_log` records actor, action, target, timestamp, and JSON context. |
| Hospitals and emergencies | `patient` 1:M `emergency_case`; each case references one patient and one receiving `hospital`; `emergency_case_event` is an append-only timeline. Cases store emergency type, priority, reported location, caller contact and status. |
| Ambulance | `hospital` 1:M `ambulance` and `driver`; each `ambulance_dispatch` references one case, ambulance, driver, pickup and destination hospital. Dispatch state changes are transactional and release the ambulance on arrival/cancellation. |
| Donors and inventory | `donor` 1:M `donation`; a donation 1:M `blood_unit`; each unit references a blood group, component and current custody hospital. `screening_record` and `crossmatch_record` retain successive entered test results. |
| Requests | Each `blood_request` references one emergency and one requesting hospital. `blood_request_line` is a request-to-product detail table. `blood_allocation` links individual units to a line and preserves reservation, issue and release history. `transfusion_record` tracks outcomes against an issued allocation. |
| Donor coordination | `donor_contact_attempt` links a donor to a request and optionally one request line; each row records method, outcome, operator, time and notes. |
| Transfers | `blood_transfer` references source and destination hospitals. `blood_transfer_unit` is the many-to-many junction between transfers and units. `blood_transfer_status_history` stores every milestone. |
| Alerts | `alert` references a hospital and optionally a request, line or unit. It records type, severity, message, resolution state and resolver. |

Primary keys are auto-incrementing `BIGINT UNSIGNED` IDs except `role_permission` and `blood_transfer_unit`, which use composite keys. Business references (case, request, donor, donation, ambulance and transfer references) are unique. Foreign keys use restrictive deletes for retained operational history; unreferenced people records may be removed through guarded application actions, and master lookups/hospitals are deactivated instead of deleted.

## Relationships and normalization

- **1:M:** hospital-to-cases, hospital-to-units, patient-to-cases, donor-to-donations, donation-to-units, emergency-to-requests, request-to-lines, line-to-allocations, unit-to-screening records, and transfer-to-status events.
- **M:N:** roles and permissions through `role_permission`; transfers and individual units through `blood_transfer_unit`. Requests and product needs use `blood_request_line`, enabling multiple products per request.
- **1NF:** each field stores one value; repeated products, test results, contact attempts, transfer units and status events occupy separate rows.
- **2NF:** junction rows use all key columns; request lines and transfer items describe their whole relationship, not just part of a composite key.
- **3NF:** descriptive hospital, patient, donor, group, component, role and permission data is stored once and referenced by FK. Pending quantities, shortages and eligible inventory are calculated from request lines, allocations and unit state rather than maintained as editable duplicate columns.

## Inventory and fulfillment rules

A unit is eligible only when its inventory status is `available`, expiry is in the future, its latest `inventory_clearance` result is `cleared`, it has no active (`reserved` or `issued`) allocation, and it is not part of an active (`requested`, `approved`, `in_transit`) transfer. Counts use distinct individual unit IDs. `in_transfer`, expired, reserved, issued, quarantined and discarded units do not count as available. This is administrative matching by exact recorded group and component; the app does not infer clinical compatibility.

For each request line:

```text
allocated = count(active allocation rows: reserved or issued)
pending   = max(required_units - allocated, 0)
shortage  = max(pending - eligible_local_units, 0)
```

Allocation locks the request line and candidate unit rows in one MySQL transaction, verifies the requested amount against pending and eligible inventory, inserts allocation history and marks units reserved. A failed validation rolls back all changes. Issue changes the unit to `issued`; release restores it to `available` only if unexpired and the latest clearance is still `cleared`, otherwise it remains quarantined. Transfer creation also locks units, marks them unavailable through the active transfer relationship, and updates unit custody only when receipt is confirmed.

## Constraints and security

- Positive request quantities and valid workflow values are checked in SQL and again by Flask validation.
- Unique unit codes, account names/emails and business references prevent duplicate identifiers.
- FK restrictions protect request, case, donation, allocation, test, transfer and audit history.
- All user-provided SQL values are bound parameters. Dynamic report columns are selected only from a server-side allowlist.
- Passwords use Werkzeug password hashes; permissions are loaded from role assignments. Every POST requires a session CSRF token.
- `setup_db.py` creates missing tables and adds only known columns/check constraints; it does not drop tables or seed over a database with operational records.

See [ER_diagram.md](ER_diagram.md) for the Mermaid diagram and [project_features.md](project_features.md) for route/workflow notes.
