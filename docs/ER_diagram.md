# LifeLink ER diagram

```mermaid
erDiagram
  HOSPITAL ||--o{ APP_USER : scopes
  ROLE ||--o{ APP_USER : assigns
  ROLE ||--o{ ROLE_PERMISSION : grants
  PERMISSION ||--o{ ROLE_PERMISSION : includes
  APP_USER ||--o{ AUDIT_LOG : performs
  PATIENT ||--o{ EMERGENCY_CASE : has
  HOSPITAL ||--o{ EMERGENCY_CASE : receives
  APP_USER ||--o{ EMERGENCY_CASE : registers
  EMERGENCY_CASE ||--o{ EMERGENCY_CASE_EVENT : tracks
  APP_USER ||--o{ EMERGENCY_CASE_EVENT : records
  HOSPITAL ||--o{ AMBULANCE : owns
  HOSPITAL ||--o{ DRIVER : employs
  EMERGENCY_CASE ||--o{ AMBULANCE_DISPATCH : dispatches
  AMBULANCE ||--o{ AMBULANCE_DISPATCH : assigned
  DRIVER ||--o{ AMBULANCE_DISPATCH : drives
  HOSPITAL ||--o{ AMBULANCE_DISPATCH : pickup_destination
  BLOOD_GROUP ||--o{ DONOR : records
  DONOR ||--o{ DONATION : gives
  HOSPITAL ||--o{ DONATION : collects
  DONATION ||--o{ BLOOD_UNIT : produces
  BLOOD_GROUP ||--o{ BLOOD_UNIT : classifies
  BLOOD_COMPONENT ||--o{ BLOOD_UNIT : describes
  HOSPITAL ||--o{ BLOOD_UNIT : holds
  BLOOD_UNIT ||--o{ SCREENING_RECORD : screened
  EMERGENCY_CASE ||--o{ BLOOD_REQUEST : requires
  HOSPITAL ||--o{ BLOOD_REQUEST : requests_at
  BLOOD_REQUEST ||--|{ BLOOD_REQUEST_LINE : contains
  BLOOD_GROUP ||--o{ BLOOD_REQUEST_LINE : specifies
  BLOOD_COMPONENT ||--o{ BLOOD_REQUEST_LINE : specifies
  BLOOD_REQUEST_LINE ||--o{ BLOOD_ALLOCATION : fulfilled_by
  BLOOD_UNIT ||--o{ BLOOD_ALLOCATION : allocated
  BLOOD_REQUEST_LINE ||--o{ CROSSMATCH_RECORD : records
  BLOOD_UNIT ||--o{ CROSSMATCH_RECORD : tested
  DONOR ||--o{ DONOR_CONTACT_ATTEMPT : contacted
  BLOOD_REQUEST ||--o{ DONOR_CONTACT_ATTEMPT : coordinates
  BLOOD_REQUEST_LINE o|--o{ DONOR_CONTACT_ATTEMPT : optionally_targets
  HOSPITAL ||--o{ BLOOD_TRANSFER : sends_or_receives
  BLOOD_TRANSFER ||--|{ BLOOD_TRANSFER_UNIT : contains
  BLOOD_UNIT ||--o{ BLOOD_TRANSFER_UNIT : moved
  BLOOD_TRANSFER ||--o{ BLOOD_TRANSFER_STATUS_HISTORY : tracks
  HOSPITAL ||--o{ ALERT : receives
  BLOOD_REQUEST o|--o{ ALERT : concerns
  BLOOD_REQUEST_LINE o|--o{ ALERT : shortage_for
  BLOOD_UNIT o|--o{ ALERT : expiry_for
  BLOOD_REQUEST ||--o{ TRANSFUSION_RECORD : follows
  BLOOD_ALLOCATION ||--o| TRANSFUSION_RECORD : documents
```

The drawing uses plural display names for readability; physical MySQL names are singular or underscore-delimited as in `schema.sql` (for example, `emergency_case`, `blood_unit`, and `app_user`). `BLOOD_TRANSFER` has source and destination hospital foreign keys. `TRANSFUSION_RECORD.allocation_id` is unique so an issued unit can have at most one demonstration outcome record.
