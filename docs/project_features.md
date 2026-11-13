# Implemented project features

## Application pages

- Public overview; separate staff, citizen and donor sign-in; citizen account recovery, contact profile editing and password changes.
- Authenticated citizen emergency intake: priority, location, contact details and preferred hospital are saved as a private report; citizens can track status from their account. Authorized response staff can triage, reject, close or convert a report into an emergency case linked to a hospital. The form clearly says to call local emergency services for immediate help.
- Dashboard with live emergency, ambulance, hospital, blood, request, donor, transfer and alert counts; current request shortage calculations, recent cases/requests/donor activity, inventory by group, near-expiry units, case/request status and donation trend charts.
- Patients, hospitals, emergencies, ambulance/driver fleet and dispatches with create, edit, view, guarded delete/deactivation, search and status workflow. Cancelling the last active dispatch returns an in-progress case to registered; parallel active dispatches keep it in progress. Active dispatches prevent case cancellation, and closed cases cannot receive stale new dispatches.
- Blood group/component catalog; donations can record multiple individually tracked component units, each held for screening; screening and crossmatch history; unit expiry/discard controls.
- Emergency-linked requests are restricted to an active case and its receiving hospital; exact group/component inventory calculation, shortage alerts, donor candidates/contact outcomes, donor-portal replies routed to the requesting hospital, partial reservations, issue/release, and transfusion history.
- Inter-hospital transfer request, approval/rejection, dispatch, receipt and append-only milestone history.
- Database-backed shortage, expiry, configurable low-stock (`LOW_STOCK_THRESHOLD`, default 2 eligible units), pending request, emergency, donor response and transfer alerts with acknowledgement/resolution.
- Role based user management, password change, audit history and date/hospital-filtered reports with CSV export and print view.

## Demo safety

`database/seed.sql` contains synthetic records only. The system records entered test/crossmatch results; it does not decide medical compatibility or donor suitability. Blood group and component matching are exact recorded-value filters. Hospital capacity is an administrative display value.

Production `setup_db.py` uses `database/seed_reference.sql` and creates the first administrator only from supplied environment credentials; it does not load synthetic operational records or use the development demo password.

## Workflow

1. Register/select a patient and an active hospital; add an emergency case.
2. From that case, create a request (the case and its hospital are preselected), choose recorded group/component/urgency and units.
3. Review eligible local inventory, shortage and candidate donors. Record contact outcomes as staff-entered history.
4. Allocate only the eligible count. Reservations update pending quantities; issue/release each reservation. A completed request is derived from its allocation rows.
5. Record screening before a unit can count as available; transfer units through request, approval, dispatch and receipt.
6. Review alerts, dashboard and reports; every supported report can be filtered and exported to CSV.
