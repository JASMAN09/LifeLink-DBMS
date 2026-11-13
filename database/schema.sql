-- LifeLink database schema. MySQL 8.0+, InnoDB, utf8mb4.
CREATE DATABASE IF NOT EXISTS lifelink CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE lifelink;

CREATE TABLE IF NOT EXISTS hospital (
 hospital_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(30) NOT NULL UNIQUE, name VARCHAR(160) NOT NULL,
 address_line VARCHAR(255) NOT NULL, city VARCHAR(100) NOT NULL,
 region VARCHAR(100), postal_code VARCHAR(20), phone VARCHAR(30), email VARCHAR(254),
 hospital_type VARCHAR(60) NOT NULL DEFAULT 'General', capacity INT UNSIGNED,
 facilities TEXT, resource_notes TEXT,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `role` (
 role_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 role_code VARCHAR(40) NOT NULL UNIQUE, name VARCHAR(80) NOT NULL UNIQUE,
 description VARCHAR(255)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS permission (
 permission_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 permission_code VARCHAR(60) NOT NULL UNIQUE, description VARCHAR(255) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS role_permission (
 role_id SMALLINT UNSIGNED NOT NULL, permission_id SMALLINT UNSIGNED NOT NULL,
 PRIMARY KEY (role_id, permission_id),
 FOREIGN KEY (role_id) REFERENCES `role`(role_id) ON DELETE RESTRICT,
 FOREIGN KEY (permission_id) REFERENCES permission(permission_id) ON DELETE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS app_user (
 user_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 hospital_id BIGINT UNSIGNED, role_id SMALLINT UNSIGNED NOT NULL,
 username VARCHAR(80) NOT NULL UNIQUE, email VARCHAR(254) NOT NULL UNIQUE,
 password_hash VARCHAR(255) NOT NULL, display_name VARCHAR(160) NOT NULL, phone VARCHAR(30),
 approval_status VARCHAR(20) NOT NULL DEFAULT 'approved', force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
 is_active BOOLEAN NOT NULL DEFAULT TRUE, failed_attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
 locked_until DATETIME, last_login_at DATETIME,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (role_id) REFERENCES `role`(role_id) ON DELETE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS citizen_account (
 citizen_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 username VARCHAR(80) NOT NULL UNIQUE, full_name VARCHAR(160) NOT NULL, email VARCHAR(254) NOT NULL UNIQUE,
 phone VARCHAR(30) NOT NULL, password_hash VARCHAR(255) NOT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE, failed_attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
 locked_until DATETIME, force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
 last_login_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 INDEX idx_citizen_phone (phone)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS password_reset_request (
 reset_request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL, request_status VARCHAR(20) NOT NULL DEFAULT 'pending',
 requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 processed_at DATETIME, processed_by_user_id BIGINT UNSIGNED,
 FOREIGN KEY (user_id) REFERENCES app_user(user_id) ON DELETE RESTRICT,
 FOREIGN KEY (processed_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (request_status IN ('pending','issued','closed')),
 INDEX idx_reset_status_time (request_status,requested_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS citizen_password_reset_request (
 reset_request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 citizen_id BIGINT UNSIGNED NOT NULL, request_status VARCHAR(20) NOT NULL DEFAULT 'pending',
 requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 processed_at DATETIME, processed_by_user_id BIGINT UNSIGNED,
 FOREIGN KEY (citizen_id) REFERENCES citizen_account(citizen_id) ON DELETE RESTRICT,
 FOREIGN KEY (processed_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (request_status IN ('pending','issued','closed')),
 INDEX idx_citizen_reset_status_time (request_status,requested_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS patient (
 patient_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 patient_ref VARCHAR(40) NOT NULL UNIQUE, display_name VARCHAR(160) NOT NULL,
 date_of_birth DATE, sex_recorded VARCHAR(30), phone VARCHAR(30),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS emergency_case (
 emergency_case_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 patient_id BIGINT UNSIGNED NOT NULL, receiving_hospital_id BIGINT UNSIGNED NOT NULL,
 registered_by_user_id BIGINT UNSIGNED, case_ref VARCHAR(40) NOT NULL UNIQUE,
 reported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 case_status VARCHAR(24) NOT NULL DEFAULT 'registered', emergency_location VARCHAR(255) NOT NULL,
 location_notes VARCHAR(500), emergency_type VARCHAR(100) NOT NULL DEFAULT 'Unspecified',
 contact_name VARCHAR(160), contact_phone VARCHAR(30), priority_label VARCHAR(24), closed_at DATETIME,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE RESTRICT,
 FOREIGN KEY (receiving_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (registered_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (closed_at IS NULL OR closed_at >= reported_at),
 CHECK (case_status IN ('registered','response_in_progress','at_hospital','completed','cancelled')),
 INDEX idx_case_status_time (case_status, reported_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS emergency_case_event (
 case_event_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 emergency_case_id BIGINT UNSIGNED NOT NULL, recorded_by_user_id BIGINT UNSIGNED,
 event_type VARCHAR(40) NOT NULL, event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 details VARCHAR(1000), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (emergency_case_id) REFERENCES emergency_case(emergency_case_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 INDEX idx_case_event (emergency_case_id, event_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS citizen_sos_request (
 sos_request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 citizen_id BIGINT UNSIGNED NOT NULL, preferred_hospital_id BIGINT UNSIGNED NULL,
 linked_emergency_case_id BIGINT UNSIGNED NULL, request_ref VARCHAR(40) NOT NULL UNIQUE,
 emergency_type VARCHAR(100) NOT NULL, emergency_location VARCHAR(255) NOT NULL,
 location_notes VARCHAR(500), contact_name VARCHAR(160) NOT NULL, contact_phone VARCHAR(30) NOT NULL,
 request_status VARCHAR(24) NOT NULL DEFAULT 'submitted', priority_label VARCHAR(24) NOT NULL DEFAULT 'urgent',
 reported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (citizen_id) REFERENCES citizen_account(citizen_id) ON DELETE RESTRICT,
 FOREIGN KEY (preferred_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (linked_emergency_case_id) REFERENCES emergency_case(emergency_case_id) ON DELETE RESTRICT,
 CHECK (request_status IN ('submitted','triaged','linked','closed','rejected')),
 CHECK (priority_label IN ('routine','urgent','critical')),
 INDEX idx_sos_citizen_time (citizen_id,reported_at),
 INDEX idx_sos_status_time (request_status,reported_at),
 INDEX idx_sos_hospital_status (preferred_hospital_id,request_status)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ambulance (
 ambulance_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 home_hospital_id BIGINT UNSIGNED NOT NULL,
 vehicle_code VARCHAR(30) NOT NULL UNIQUE, registration_number VARCHAR(30) NOT NULL UNIQUE,
 operational_status VARCHAR(20) NOT NULL DEFAULT 'available',
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (home_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 CHECK (operational_status IN ('available','dispatched','maintenance','inactive'))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS driver (
 driver_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 employer_hospital_id BIGINT UNSIGNED NOT NULL, driver_ref VARCHAR(40) NOT NULL UNIQUE,
 display_name VARCHAR(160) NOT NULL, phone VARCHAR(30), is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (employer_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ambulance_dispatch (
 dispatch_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 emergency_case_id BIGINT UNSIGNED NOT NULL, ambulance_id BIGINT UNSIGNED NOT NULL,
 driver_id BIGINT UNSIGNED NOT NULL, pickup_hospital_id BIGINT UNSIGNED,
 destination_hospital_id BIGINT UNSIGNED NOT NULL, dispatched_by_user_id BIGINT UNSIGNED,
 pickup_location VARCHAR(255) NOT NULL, destination_location VARCHAR(255) NOT NULL,
 dispatched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, picked_up_at DATETIME,
 arrived_at DATETIME, dispatch_status VARCHAR(24) NOT NULL DEFAULT 'dispatched',
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (emergency_case_id) REFERENCES emergency_case(emergency_case_id) ON DELETE RESTRICT,
 FOREIGN KEY (ambulance_id) REFERENCES ambulance(ambulance_id) ON DELETE RESTRICT,
 FOREIGN KEY (driver_id) REFERENCES driver(driver_id) ON DELETE RESTRICT,
 FOREIGN KEY (pickup_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (destination_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (dispatched_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (dispatch_status IN ('dispatched','picked_up','arrived','cancelled')),
 INDEX idx_dispatch_ambulance_status (ambulance_id, dispatch_status),
 INDEX idx_dispatch_case_time (emergency_case_id, dispatched_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_group (
 blood_group_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(8) NOT NULL UNIQUE, label VARCHAR(40) NOT NULL UNIQUE,
 is_active BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_component (
 component_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(24) NOT NULL UNIQUE, name VARCHAR(80) NOT NULL UNIQUE,
 is_active BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS donor (
 donor_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 blood_group_id SMALLINT UNSIGNED NOT NULL, donor_ref VARCHAR(40) NOT NULL UNIQUE,
 display_name VARCHAR(160) NOT NULL, phone VARCHAR(30), email VARCHAR(254),
 city VARCHAR(100) NOT NULL, region VARCHAR(100), postal_code VARCHAR(20),
 availability_status VARCHAR(20) NOT NULL DEFAULT 'available',
 last_availability_update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (blood_group_id) REFERENCES blood_group(blood_group_id) ON DELETE RESTRICT,
 CHECK (availability_status IN ('available','unavailable','do_not_contact')),
 INDEX idx_donor_search (blood_group_id, availability_status, is_active, city)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS donor_account (
 donor_account_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 donor_id BIGINT UNSIGNED NOT NULL UNIQUE, username VARCHAR(80) NOT NULL UNIQUE,
 password_hash VARCHAR(255) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT FALSE,
 last_login_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (donor_id) REFERENCES donor(donor_id) ON DELETE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS donation (
 donation_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 donor_id BIGINT UNSIGNED NOT NULL, collection_hospital_id BIGINT UNSIGNED NOT NULL,
 recorded_by_user_id BIGINT UNSIGNED, donation_ref VARCHAR(40) NOT NULL UNIQUE,
 collected_at DATETIME NOT NULL, donation_status VARCHAR(20) NOT NULL DEFAULT 'collected',
 notes VARCHAR(500), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (donor_id) REFERENCES donor(donor_id) ON DELETE RESTRICT,
 FOREIGN KEY (collection_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (donation_status IN ('collected','cancelled'))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_unit (
 blood_unit_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 donation_id BIGINT UNSIGNED NOT NULL, component_id SMALLINT UNSIGNED NOT NULL,
 blood_group_id SMALLINT UNSIGNED NOT NULL, current_hospital_id BIGINT UNSIGNED NOT NULL,
 recorded_by_user_id BIGINT UNSIGNED, unit_code VARCHAR(50) NOT NULL UNIQUE,
 expires_on DATETIME NOT NULL, inventory_status VARCHAR(20) NOT NULL DEFAULT 'available',
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (donation_id) REFERENCES donation(donation_id) ON DELETE RESTRICT,
 FOREIGN KEY (component_id) REFERENCES blood_component(component_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_group_id) REFERENCES blood_group(blood_group_id) ON DELETE RESTRICT,
 FOREIGN KEY (current_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (inventory_status IN ('available','reserved','issued','quarantined','discarded')),
 INDEX idx_unit_inventory (current_hospital_id, component_id, blood_group_id, inventory_status, expires_on)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS screening_record (
 screening_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 blood_unit_id BIGINT UNSIGNED NOT NULL, recorded_by_user_id BIGINT UNSIGNED,
 test_code VARCHAR(40) NOT NULL, tested_at DATETIME NOT NULL,
 result_status VARCHAR(24) NOT NULL, result_reference VARCHAR(100), notes VARCHAR(500),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (blood_unit_id) REFERENCES blood_unit(blood_unit_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (result_status IN ('pending','recorded','cleared','not_cleared','inconclusive')),
 INDEX idx_screening_latest (blood_unit_id, tested_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_request (
 blood_request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 emergency_case_id BIGINT UNSIGNED NOT NULL, requesting_hospital_id BIGINT UNSIGNED NOT NULL,
 requested_by_user_id BIGINT UNSIGNED, request_ref VARCHAR(40) NOT NULL UNIQUE,
 requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 request_status VARCHAR(24) NOT NULL DEFAULT 'open', urgency VARCHAR(24) NOT NULL DEFAULT 'routine', notes VARCHAR(1000),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (emergency_case_id) REFERENCES emergency_case(emergency_case_id) ON DELETE RESTRICT,
 FOREIGN KEY (requesting_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (requested_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (request_status IN ('open','approved','rejected','partially_fulfilled','fulfilled','cancelled')),
 INDEX idx_request_case_time (emergency_case_id, requested_at),
 INDEX idx_request_hospital_status (requesting_hospital_id, request_status)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_request_line (
 request_line_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 blood_request_id BIGINT UNSIGNED NOT NULL, component_id SMALLINT UNSIGNED NOT NULL,
 blood_group_id SMALLINT UNSIGNED NOT NULL, line_number SMALLINT UNSIGNED NOT NULL,
 required_units INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (blood_request_id) REFERENCES blood_request(blood_request_id) ON DELETE RESTRICT,
 FOREIGN KEY (component_id) REFERENCES blood_component(component_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_group_id) REFERENCES blood_group(blood_group_id) ON DELETE RESTRICT,
 UNIQUE KEY uq_request_line_number (blood_request_id, line_number),
 UNIQUE KEY uq_request_line_spec (blood_request_id, component_id, blood_group_id),
 CHECK (required_units > 0)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_allocation (
 allocation_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 request_line_id BIGINT UNSIGNED NOT NULL, blood_unit_id BIGINT UNSIGNED NOT NULL,
 allocated_by_user_id BIGINT UNSIGNED, allocation_status VARCHAR(20) NOT NULL DEFAULT 'reserved',
 allocated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, issued_at DATETIME,
 released_at DATETIME, release_reason VARCHAR(300),
 active_blood_unit_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN allocation_status IN ('reserved','issued') THEN blood_unit_id ELSE NULL END) STORED,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (request_line_id) REFERENCES blood_request_line(request_line_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_unit_id) REFERENCES blood_unit(blood_unit_id) ON DELETE RESTRICT,
 FOREIGN KEY (allocated_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (allocation_status IN ('reserved','issued','released')),
 INDEX idx_allocation_line_status (request_line_id, allocation_status),
 INDEX idx_allocation_unit_status (blood_unit_id, allocation_status),
 UNIQUE KEY uq_active_blood_unit_allocation (active_blood_unit_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS crossmatch_record (
 crossmatch_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 request_line_id BIGINT UNSIGNED NOT NULL, blood_unit_id BIGINT UNSIGNED NOT NULL,
 recorded_by_user_id BIGINT UNSIGNED, tested_at DATETIME NOT NULL,
 test_status VARCHAR(24) NOT NULL, result_reference VARCHAR(100), notes VARCHAR(500),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (request_line_id) REFERENCES blood_request_line(request_line_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_unit_id) REFERENCES blood_unit(blood_unit_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (test_status IN ('pending','recorded','compatible_as_entered','incompatible_as_entered','inconclusive')),
 INDEX idx_crossmatch_line_unit (request_line_id, blood_unit_id, tested_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS donor_contact_attempt (
 contact_attempt_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 donor_id BIGINT UNSIGNED NOT NULL, blood_request_id BIGINT UNSIGNED NOT NULL,
 request_line_id BIGINT UNSIGNED, contacted_by_user_id BIGINT UNSIGNED,
 attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, contact_method VARCHAR(20) NOT NULL,
 outcome VARCHAR(24) NOT NULL, response_notes VARCHAR(500),
 FOREIGN KEY (donor_id) REFERENCES donor(donor_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_request_id) REFERENCES blood_request(blood_request_id) ON DELETE RESTRICT,
 FOREIGN KEY (request_line_id) REFERENCES blood_request_line(request_line_id) ON DELETE RESTRICT,
 FOREIGN KEY (contacted_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (contact_method IN ('phone','message','email','other')),
 CHECK (outcome IN ('no_answer','reached','interested','declined','follow_up')),
 INDEX idx_contact_request_time (blood_request_id, attempted_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_transfer (
 transfer_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 from_hospital_id BIGINT UNSIGNED NOT NULL, to_hospital_id BIGINT UNSIGNED NOT NULL,
 initiated_by_user_id BIGINT UNSIGNED, transfer_ref VARCHAR(40) NOT NULL UNIQUE,
 requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, dispatched_at DATETIME,
 received_at DATETIME, transfer_status VARCHAR(20) NOT NULL DEFAULT 'requested', notes VARCHAR(500),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY (from_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (to_hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (initiated_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (from_hospital_id <> to_hospital_id),
 CHECK (transfer_status IN ('requested','approved','in_transit','received','cancelled')),
 INDEX idx_transfer_from_status (from_hospital_id, transfer_status),
 INDEX idx_transfer_to_status (to_hospital_id, transfer_status)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_transfer_unit (
 transfer_id BIGINT UNSIGNED NOT NULL, blood_unit_id BIGINT UNSIGNED NOT NULL,
 added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (transfer_id, blood_unit_id),
 FOREIGN KEY (transfer_id) REFERENCES blood_transfer(transfer_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_unit_id) REFERENCES blood_unit(blood_unit_id) ON DELETE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS blood_transfer_status_history (
 transfer_status_event_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 transfer_id BIGINT UNSIGNED NOT NULL, changed_by_user_id BIGINT UNSIGNED,
 status VARCHAR(20) NOT NULL, changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 notes VARCHAR(500),
 FOREIGN KEY (transfer_id) REFERENCES blood_transfer(transfer_id) ON DELETE RESTRICT,
 FOREIGN KEY (changed_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 INDEX idx_transfer_history (transfer_id, changed_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS alert (
 alert_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 hospital_id BIGINT UNSIGNED NOT NULL, blood_request_id BIGINT UNSIGNED,
 request_line_id BIGINT UNSIGNED, blood_unit_id BIGINT UNSIGNED,
 created_by_user_id BIGINT UNSIGNED, resolved_by_user_id BIGINT UNSIGNED,
 alert_type VARCHAR(20) NOT NULL, severity VARCHAR(16) NOT NULL,
 message VARCHAR(500) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 alert_status VARCHAR(16) NOT NULL DEFAULT 'open', resolved_at DATETIME,
 FOREIGN KEY (hospital_id) REFERENCES hospital(hospital_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_request_id) REFERENCES blood_request(blood_request_id) ON DELETE RESTRICT,
 FOREIGN KEY (request_line_id) REFERENCES blood_request_line(request_line_id) ON DELETE RESTRICT,
 FOREIGN KEY (blood_unit_id) REFERENCES blood_unit(blood_unit_id) ON DELETE RESTRICT,
 FOREIGN KEY (created_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 FOREIGN KEY (resolved_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 CHECK (alert_type IN ('shortage','expiry','low_stock','pending_request','emergency','donor_response','transfer')),
 CHECK (alert_status IN ('open','acknowledged','resolved','dismissed')),
 CHECK ((alert_type='shortage' AND request_line_id IS NOT NULL) OR (alert_type='expiry' AND blood_unit_id IS NOT NULL) OR alert_type IN ('low_stock','pending_request','emergency','donor_response','transfer'))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS audit_log (
 audit_log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 actor_user_id BIGINT UNSIGNED, action VARCHAR(40) NOT NULL,
 entity_type VARCHAR(60) NOT NULL, entity_id VARCHAR(80) NOT NULL,
 occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 details JSON, source_ip VARCHAR(45),
 FOREIGN KEY (actor_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 INDEX idx_audit_entity (entity_type, entity_id, occurred_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS notification (
 notification_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL, notification_type VARCHAR(40) NOT NULL,
 message VARCHAR(500) NOT NULL, entity_type VARCHAR(60), entity_id VARCHAR(80),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, read_at DATETIME,
 FOREIGN KEY (user_id) REFERENCES app_user(user_id) ON DELETE RESTRICT,
 INDEX idx_notification_user_unread (user_id,read_at,created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS transfusion_record (
 transfusion_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 blood_request_id BIGINT UNSIGNED NOT NULL, allocation_id BIGINT UNSIGNED NOT NULL,
 recorded_by_user_id BIGINT UNSIGNED, started_at DATETIME NOT NULL,
 completed_at DATETIME, status VARCHAR(24) NOT NULL DEFAULT 'started', notes VARCHAR(500),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (blood_request_id) REFERENCES blood_request(blood_request_id) ON DELETE RESTRICT,
 FOREIGN KEY (allocation_id) REFERENCES blood_allocation(allocation_id) ON DELETE RESTRICT,
 FOREIGN KEY (recorded_by_user_id) REFERENCES app_user(user_id) ON DELETE SET NULL,
 UNIQUE KEY uq_transfusion_allocation (allocation_id),
 CHECK (status IN ('started','completed','stopped')),
 CHECK (completed_at IS NULL OR completed_at >= started_at),
 INDEX idx_transfusion_request_time (blood_request_id, started_at)
) ENGINE=InnoDB;
