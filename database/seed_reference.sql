-- Non-personal lookup and access-control rows used for production startup.
USE lifelink;

INSERT IGNORE INTO `role` (role_code,name,description) VALUES
 ('ADMIN','Administrator','Full project administration'),
 ('COORDINATOR','Emergency coordinator','Patient, case, dispatch and request coordination'),
 ('INVENTORY','Blood inventory officer','Blood unit, request, donor and transfer coordination'),
 ('VIEWER','Read-only viewer','Read-only access to operational pages');
INSERT IGNORE INTO `role` (role_code,name,description) VALUES
 ('EMERGENCY_STAFF','Emergency staff','Emergency case and patient coordination'),
 ('AMBULANCE_STAFF','Ambulance staff','Fleet and ambulance dispatch coordination'),
 ('BLOOD_BANK_STAFF','Blood bank staff','Blood inventory, requests, donors and transfers'),
 ('HOSPITAL_STAFF','Hospital staff','Hospital and linked emergency/request coordination');

INSERT IGNORE INTO permission(permission_code,description) VALUES
 ('dashboard.read','View dashboard'),('patients.read','View patients'),('patients.write','Manage patients'),
 ('cases.read','View emergencies'),('cases.write','Manage emergencies'),('ambulances.read','View fleet'),('ambulances.write','Manage fleet'),
 ('dispatches.read','View dispatches'),('dispatches.write','Manage dispatches'),('blood.read','View blood inventory'),('blood.write','Manage blood inventory and tests'),
 ('requests.read','View blood requests'),('requests.write','Manage requests and allocations'),('donors.read','View donor coordination'),('donors.write','Manage donors'),
 ('contacts.write','Record donor contacts'),('citizen_sos.read','Review citizen emergency reports'),('citizen_sos.write','Link or update citizen emergency reports'),('transfers.read','View transfers'),('transfers.write','Manage transfers'),
 ('alerts.read','View alerts'),('alerts.write','Manage alerts'),('hospitals.read','View hospitals'),('hospitals.write','Manage hospitals'),('users.manage','Manage accounts and audit history');

INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r CROSS JOIN permission p WHERE r.role_code='ADMIN';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','ambulances.read','ambulances.write','dispatches.read','dispatches.write','requests.read','requests.write','donors.read','donors.write','contacts.write','citizen_sos.read','citizen_sos.write','hospitals.read','alerts.read','alerts.write') WHERE r.role_code='COORDINATOR';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','blood.read','blood.write','requests.read','requests.write','donors.read','donors.write','contacts.write','transfers.read','transfers.write','alerts.read','alerts.write','hospitals.read') WHERE r.role_code='INVENTORY';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','cases.read','ambulances.read','dispatches.read','blood.read','requests.read','donors.read','transfers.read','alerts.read','hospitals.read') WHERE r.role_code='VIEWER';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','ambulances.read','dispatches.read','dispatches.write','requests.read','requests.write','donors.read','contacts.write','citizen_sos.read','citizen_sos.write','hospitals.read','alerts.read','alerts.write') WHERE r.role_code='EMERGENCY_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','cases.read','ambulances.read','ambulances.write','dispatches.read','dispatches.write','hospitals.read') WHERE r.role_code='AMBULANCE_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','blood.read','blood.write','requests.read','requests.write','donors.read','donors.write','contacts.write','transfers.read','transfers.write','alerts.read','alerts.write','hospitals.read') WHERE r.role_code='BLOOD_BANK_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','requests.read','requests.write','blood.read','transfers.read','transfers.write','citizen_sos.read','citizen_sos.write','alerts.read','hospitals.read','hospitals.write') WHERE r.role_code='HOSPITAL_STAFF';

INSERT IGNORE INTO blood_group(code,label) VALUES
 ('A+','A positive'),('A-','A negative'),('B+','B positive'),('B-','B negative'),
 ('AB+','AB positive'),('AB-','AB negative'),('O+','O positive'),('O-','O negative');
INSERT IGNORE INTO blood_component(code,name) VALUES
 ('RBC','Red blood cells'),('PLASMA','Plasma'),('PLATELETS','Platelets');
