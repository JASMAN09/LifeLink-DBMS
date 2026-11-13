-- Synthetic LifeLink demonstration data only. Run after schema.sql.
USE lifelink;

INSERT IGNORE INTO `role` (role_id,role_code,name,description) VALUES
 (1,'ADMIN','Administrator','Full project administration'),
 (2,'COORDINATOR','Emergency coordinator','Patient, case, dispatch and request coordination'),
 (3,'INVENTORY','Blood inventory officer','Blood unit, request, donor and transfer coordination'),
 (4,'VIEWER','Read-only viewer','Read-only access to operational pages');
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
 ('contacts.write','Record donor contacts'),('transfers.read','View transfers'),('transfers.write','Manage transfers'),
 ('alerts.read','View alerts'),('alerts.write','Manage alerts'),('hospitals.read','View hospitals'),('hospitals.write','Manage hospitals'),('users.manage','Manage accounts and audit history');

INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r CROSS JOIN permission p WHERE r.role_code='ADMIN';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','ambulances.read','ambulances.write','dispatches.read','dispatches.write','requests.read','requests.write','donors.read','donors.write','contacts.write','hospitals.read','alerts.read','alerts.write') WHERE r.role_code='COORDINATOR';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','blood.read','blood.write','requests.read','requests.write','donors.read','donors.write','contacts.write','transfers.read','transfers.write','alerts.read','alerts.write','hospitals.read') WHERE r.role_code='INVENTORY';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','cases.read','ambulances.read','dispatches.read','blood.read','requests.read','donors.read','transfers.read','alerts.read','hospitals.read') WHERE r.role_code='VIEWER';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','ambulances.read','dispatches.read','dispatches.write','requests.read','requests.write','donors.read','contacts.write','hospitals.read','alerts.read','alerts.write') WHERE r.role_code='EMERGENCY_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','cases.read','ambulances.read','ambulances.write','dispatches.read','dispatches.write','hospitals.read') WHERE r.role_code='AMBULANCE_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','blood.read','blood.write','requests.read','requests.write','donors.read','donors.write','contacts.write','transfers.read','transfers.write','alerts.read','alerts.write','hospitals.read') WHERE r.role_code='BLOOD_BANK_STAFF';
INSERT IGNORE INTO role_permission(role_id,permission_id)
 SELECT r.role_id,p.permission_id FROM `role` r JOIN permission p ON p.permission_code IN
 ('dashboard.read','patients.read','patients.write','cases.read','cases.write','requests.read','requests.write','blood.read','transfers.read','transfers.write','alerts.read','hospitals.read','hospitals.write') WHERE r.role_code='HOSPITAL_STAFF';

INSERT IGNORE INTO hospital(hospital_id,code,name,address_line,city,region,postal_code,phone,email) VALUES
 (1,'LL-DEMO-01','LifeLink Demonstration Hospital A','100 Sample Avenue','Chandigarh','Chandigarh','160001','+91-00000-00001','hospital-a@example.invalid'),
 (2,'LL-DEMO-02','LifeLink Demonstration Hospital B','200 Example Road','Mohali','Punjab','160055','+91-00000-00002','hospital-b@example.invalid');

-- Demo password for account `admin`: DemoLifeLink!2026. Change it after first sign-in.
INSERT IGNORE INTO app_user(user_id,hospital_id,role_id,username,email,password_hash,display_name) VALUES
 (1,1,1,'admin','admin@example.invalid','scrypt:32768:8:1$TjaDmq6VU5zh7SAf$c652fc7e64a56582adc7fe3c96478a37e94a99dbaa16c331214550ee916b46cdba398a3d9cc6d33a39f9870f9546510552fb78b301f855ead04c58437236c81e','Demo Administrator');

-- Synthetic role demonstrations. Password for each: DemoStaff!2026.
INSERT IGNORE INTO app_user(hospital_id,role_id,username,email,password_hash,display_name)
 SELECT 1,role_id,'emergency.demo','emergency@example.invalid','scrypt:32768:8:1$lvfJOTf1x9pDYSXr$812edbd2d3ad2d07b8a20df049afa2c852586de2c1b2e3ea1e52d44dfac27ae0874db5a470c604df0d00c33fd6447c8eef90da506da95ebfe9fbecc580b1d6f9','Demo Emergency Staff' FROM `role` WHERE role_code='EMERGENCY_STAFF';
INSERT IGNORE INTO app_user(hospital_id,role_id,username,email,password_hash,display_name)
 SELECT 1,role_id,'ambulance.demo','ambulance@example.invalid','scrypt:32768:8:1$lvfJOTf1x9pDYSXr$812edbd2d3ad2d07b8a20df049afa2c852586de2c1b2e3ea1e52d44dfac27ae0874db5a470c604df0d00c33fd6447c8eef90da506da95ebfe9fbecc580b1d6f9','Demo Ambulance Staff' FROM `role` WHERE role_code='AMBULANCE_STAFF';
INSERT IGNORE INTO app_user(hospital_id,role_id,username,email,password_hash,display_name)
 SELECT 1,role_id,'bloodbank.demo','bloodbank@example.invalid','scrypt:32768:8:1$lvfJOTf1x9pDYSXr$812edbd3d2ad2d07b8a20df049afa2c852586de2c1b2e3ea1e52d44dfac27ae0874db5a470c604df0d00c33fd6447c8eef90da506da95ebfe9fbecc580b1d6f9','Demo Blood Bank Staff' FROM `role` WHERE role_code='BLOOD_BANK_STAFF';
INSERT IGNORE INTO app_user(hospital_id,role_id,username,email,password_hash,display_name)
 SELECT 1,role_id,'hospital.demo','hospital@example.invalid','scrypt:32768:8:1$lvfJOTf1x9pDYSXr$812edbd3d2ad2d07b8a20df049afa2c852586de2c1b2e3ea1e52d44dfac27ae0874db5a470c604df0d00c33fd6447c8eef90da506da95ebfe9fbecc580b1d6f9','Demo Hospital Staff' FROM `role` WHERE role_code='HOSPITAL_STAFF';

-- Public accounts are synthetic; password for each is DemoCitizen!2026.
INSERT IGNORE INTO citizen_account(full_name,email,phone,password_hash) VALUES
 ('Demo Citizen One','citizen.one@example.invalid','+91-00000-00101','scrypt:32768:8:1$WxHPwLb0zAsPRsPW$22e527f88aff7b61f31eb9c19616db1f4bc416dac7f9dc885024213afbd3155886bba0fe3e8cfb829515b0fb929968ffdc2e4310fd5bc64d06f169926b6a9326'),
 ('Demo Citizen Two','citizen.two@example.invalid','+91-00000-00102','scrypt:32768:8:1$WxHPwLb0zAsPRsPW$22e527f88aff7b61f31eb9c19616db1f4bc416dac7f9dc885024213afbd3155886bba0fe3e8cfb829515b0fb929968ffdc2e4310fd5bc64d06f169926b6a9326');

INSERT IGNORE INTO blood_group(blood_group_id,code,label) VALUES
 (1,'A+','A positive'),(2,'A-','A negative'),(3,'B+','B positive'),(4,'B-','B negative'),
 (5,'AB+','AB positive'),(6,'AB-','AB negative'),(7,'O+','O positive'),(8,'O-','O negative');
INSERT IGNORE INTO blood_component(component_id,code,name) VALUES
 (1,'RBC','Red blood cells'),(2,'PLASMA','Plasma'),(3,'PLATELETS','Platelets');

INSERT IGNORE INTO patient(patient_id,patient_ref,display_name,date_of_birth,sex_recorded,phone) VALUES
 (1,'PAT-DEMO-001','Demo Patient One','1990-01-01','Not specified',NULL),
 (2,'PAT-DEMO-002','Demo Patient Two','1985-05-15','Not specified',NULL);
INSERT IGNORE INTO emergency_case(emergency_case_id,patient_id,receiving_hospital_id,registered_by_user_id,case_ref,reported_at,case_status,emergency_location,priority_label) VALUES
 (1,1,1,1,'EMG-DEMO-001',UTC_TIMESTAMP()-INTERVAL 2 HOUR,'response_in_progress','Demo Sector 1','urgent'),
 (2,2,2,1,'EMG-DEMO-002',UTC_TIMESTAMP()-INTERVAL 35 MINUTE,'registered','Demo Sector 2','routine');
INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details)
 SELECT 1,1,'registered','Synthetic demonstration emergency case' WHERE NOT EXISTS (SELECT 1 FROM emergency_case_event WHERE emergency_case_id=1 AND event_type='registered');
INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details)
 SELECT 2,1,'registered','Synthetic demonstration emergency case' WHERE NOT EXISTS (SELECT 1 FROM emergency_case_event WHERE emergency_case_id=2 AND event_type='registered');

INSERT IGNORE INTO ambulance(ambulance_id,home_hospital_id,vehicle_code,registration_number,operational_status) VALUES
 (1,1,'AMB-DEMO-01','DEMO-AMB-001','available'),(2,2,'AMB-DEMO-02','DEMO-AMB-002','available');
INSERT IGNORE INTO driver(driver_id,employer_hospital_id,driver_ref,display_name,phone,is_active) VALUES
 (1,1,'DRV-DEMO-01','Demo Driver One',NULL,1),(2,2,'DRV-DEMO-02','Demo Driver Two',NULL,1);

INSERT IGNORE INTO donor(donor_id,blood_group_id,donor_ref,display_name,phone,email,city,region,availability_status) VALUES
 (1,7,'DON-DEMO-001','Demo Donor One',NULL,NULL,'Chandigarh','Chandigarh','available'),
 (2,7,'DON-DEMO-002','Demo Donor Two',NULL,NULL,'Mohali','Punjab','available'),
 (3,1,'DON-DEMO-003','Demo Donor Three',NULL,NULL,'Chandigarh','Chandigarh','unavailable'),
 (4,3,'DON-DEMO-004','Demo Donor Four',NULL,NULL,'Mohali','Punjab','available');
INSERT IGNORE INTO donation(donation_id,donor_id,collection_hospital_id,recorded_by_user_id,donation_ref,collected_at,donation_status) VALUES
 (1,1,1,1,'DNT-DEMO-001',UTC_TIMESTAMP()-INTERVAL 20 DAY,'collected'),
 (2,2,2,1,'DNT-DEMO-002',UTC_TIMESTAMP()-INTERVAL 12 DAY,'collected'),
 (3,4,2,1,'DNT-DEMO-003',UTC_TIMESTAMP()-INTERVAL 9 DAY,'collected');
INSERT IGNORE INTO blood_unit(blood_unit_id,donation_id,component_id,blood_group_id,current_hospital_id,recorded_by_user_id,unit_code,expires_on,inventory_status) VALUES
 (1,1,1,7,1,1,'UNIT-DEMO-001',UTC_TIMESTAMP()+INTERVAL 20 DAY,'available'),
 (2,2,1,7,2,1,'UNIT-DEMO-002',UTC_TIMESTAMP()+INTERVAL 18 DAY,'available'),
 (3,3,1,3,2,1,'UNIT-DEMO-003',UTC_TIMESTAMP()+INTERVAL 2 DAY,'available');
INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes)
 SELECT 1,1,'inventory_clearance',UTC_TIMESTAMP()-INTERVAL 18 DAY,'cleared','Synthetic demonstration result entered as cleared' WHERE NOT EXISTS (SELECT 1 FROM screening_record WHERE blood_unit_id=1 AND test_code='inventory_clearance');
INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes)
 SELECT 2,1,'inventory_clearance',UTC_TIMESTAMP()-INTERVAL 10 DAY,'cleared','Synthetic demonstration result entered as cleared' WHERE NOT EXISTS (SELECT 1 FROM screening_record WHERE blood_unit_id=2 AND test_code='inventory_clearance');
INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes)
 SELECT 3,1,'inventory_clearance',UTC_TIMESTAMP()-INTERVAL 8 DAY,'cleared','Synthetic demonstration result entered as cleared' WHERE NOT EXISTS (SELECT 1 FROM screening_record WHERE blood_unit_id=3 AND test_code='inventory_clearance');

INSERT IGNORE INTO blood_request(blood_request_id,emergency_case_id,requesting_hospital_id,requested_by_user_id,request_ref,requested_at,request_status,notes) VALUES
 (1,1,1,1,'REQ-DEMO-001',UTC_TIMESTAMP()-INTERVAL 90 MINUTE,'open','Synthetic request demonstrates an inventory shortage');
INSERT IGNORE INTO blood_request_line(request_line_id,blood_request_id,component_id,blood_group_id,line_number,required_units) VALUES
 (1,1,1,7,1,3);

INSERT IGNORE INTO donor_contact_attempt(contact_attempt_id,donor_id,blood_request_id,request_line_id,contacted_by_user_id,contact_method,outcome,response_notes) VALUES
 (1,1,1,1,1,'phone','follow_up','Synthetic contact history example');

INSERT IGNORE INTO alert(alert_id,hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message,alert_status) VALUES
 (1,1,1,1,1,'shortage','high','Synthetic demonstration shortage alert','open');


-- Additional deterministic synthetic demonstration records (IDs 3+ avoid the starter examples).
INSERT IGNORE INTO hospital(hospital_id,code,name,address_line,city,region,postal_code,phone,email,hospital_type,capacity) VALUES
 (3,'LL-DEMO-03','LifeLink Demonstration Hospital C','300 Fictional Boulevard','Panchkula','Haryana','134109','+91-00000-00003','hospital-c@example.invalid','District hospital',180),
 (1,'LL-DEMO-01','LifeLink Demonstration Hospital A','100 Sample Avenue','Chandigarh','Chandigarh','160001','+91-00000-00001','hospital-a@example.invalid','Teaching hospital',420),
 (2,'LL-DEMO-02','LifeLink Demonstration Hospital B','200 Example Road','Mohali','Punjab','160055','+91-00000-00002','hospital-b@example.invalid','General hospital',260);
INSERT IGNORE INTO blood_component(component_id,code,name) VALUES
 (4,'WHOLE','Whole blood'),(5,'PRBC','Packed red blood cells');
INSERT IGNORE INTO patient(patient_id,patient_ref,display_name,date_of_birth,sex_recorded,phone) VALUES
 (3,'PAT-DEMO-003','Demo Patient Three','1978-08-20','Not specified',NULL),
 (4,'PAT-DEMO-004','Demo Patient Four','2001-03-14','Not specified',NULL),
 (5,'PAT-DEMO-005','Demo Patient Five','1964-12-02','Not specified',NULL);
INSERT IGNORE INTO emergency_case(emergency_case_id,patient_id,receiving_hospital_id,registered_by_user_id,case_ref,reported_at,case_status,emergency_location,priority_label,emergency_type) VALUES
 (3,3,3,1,'EMG-DEMO-003',UTC_TIMESTAMP()-INTERVAL 1 DAY,'completed','Demo Sector 3','routine','Transport coordination'),
 (4,4,1,1,'EMG-DEMO-004',UTC_TIMESTAMP()-INTERVAL 3 HOUR,'response_in_progress','Demo Sector 4','critical','Road incident'),
 (5,5,2,1,'EMG-DEMO-005',UTC_TIMESTAMP()-INTERVAL 45 MINUTE,'registered','Demo Sector 5','urgent','Emergency referral');
INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details)
 SELECT 3,1,'registered','Synthetic demonstration case' WHERE NOT EXISTS (SELECT 1 FROM emergency_case_event WHERE emergency_case_id=3 AND event_type='registered');
INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details)
 SELECT 4,1,'registered','Synthetic demonstration case' WHERE NOT EXISTS (SELECT 1 FROM emergency_case_event WHERE emergency_case_id=4 AND event_type='registered');
INSERT INTO emergency_case_event(emergency_case_id,recorded_by_user_id,event_type,details)
 SELECT 5,1,'registered','Synthetic demonstration case' WHERE NOT EXISTS (SELECT 1 FROM emergency_case_event WHERE emergency_case_id=5 AND event_type='registered');
INSERT IGNORE INTO ambulance(ambulance_id,home_hospital_id,vehicle_code,registration_number,operational_status) VALUES
 (3,1,'AMB-DEMO-03','DEMO-AMB-003','available'),(4,2,'AMB-DEMO-04','DEMO-AMB-004','available'),(5,3,'AMB-DEMO-05','DEMO-AMB-005','maintenance');
INSERT IGNORE INTO driver(driver_id,employer_hospital_id,driver_ref,display_name,phone,is_active) VALUES
 (3,1,'DRV-DEMO-03','Demo Driver Three',NULL,1),(4,2,'DRV-DEMO-04','Demo Driver Four',NULL,1),(5,3,'DRV-DEMO-05','Demo Driver Five',NULL,1);
INSERT IGNORE INTO donor(donor_id,blood_group_id,donor_ref,display_name,phone,email,city,region,availability_status) VALUES
 (5,2,'DON-DEMO-005','Demo Donor Five',NULL,NULL,'Chandigarh','Chandigarh','available'),
 (6,4,'DON-DEMO-006','Demo Donor Six',NULL,NULL,'Mohali','Punjab','available'),
 (7,5,'DON-DEMO-007','Demo Donor Seven',NULL,NULL,'Panchkula','Haryana','unavailable'),
 (8,6,'DON-DEMO-008','Demo Donor Eight',NULL,NULL,'Chandigarh','Chandigarh','available'),
 (9,8,'DON-DEMO-009','Demo Donor Nine',NULL,NULL,'Mohali','Punjab','available'),
 (10,1,'DON-DEMO-010','Demo Donor Ten',NULL,NULL,'Panchkula','Haryana','available'),
 (11,3,'DON-DEMO-011','Demo Donor Eleven',NULL,NULL,'Chandigarh','Chandigarh','do_not_contact'),
 (12,7,'DON-DEMO-012','Demo Donor Twelve',NULL,NULL,'Panchkula','Haryana','available');
INSERT IGNORE INTO donation(donation_id,donor_id,collection_hospital_id,recorded_by_user_id,donation_ref,collected_at,donation_status) VALUES
 (4,5,1,1,'DNT-DEMO-004',UTC_TIMESTAMP()-INTERVAL 30 DAY,'collected'),
 (5,6,2,1,'DNT-DEMO-005',UTC_TIMESTAMP()-INTERVAL 14 DAY,'collected'),
 (6,7,3,1,'DNT-DEMO-006',UTC_TIMESTAMP()-INTERVAL 8 DAY,'collected'),
 (7,8,1,1,'DNT-DEMO-007',UTC_TIMESTAMP()-INTERVAL 5 DAY,'collected'),
 (8,9,2,1,'DNT-DEMO-008',UTC_TIMESTAMP()-INTERVAL 3 DAY,'collected'),
 (9,10,3,1,'DNT-DEMO-009',UTC_TIMESTAMP()-INTERVAL 2 DAY,'collected'),
 (10,11,1,1,'DNT-DEMO-010',UTC_TIMESTAMP()-INTERVAL 1 DAY,'collected');
INSERT IGNORE INTO blood_unit(blood_unit_id,donation_id,component_id,blood_group_id,current_hospital_id,recorded_by_user_id,unit_code,expires_on,inventory_status) VALUES
 (4,4,5,2,1,1,'UNIT-DEMO-004',UTC_TIMESTAMP()+INTERVAL 15 DAY,'available'),
 (5,5,2,4,2,1,'UNIT-DEMO-005',UTC_TIMESTAMP()+INTERVAL 12 DAY,'available'),
 (6,6,3,5,3,1,'UNIT-DEMO-006',UTC_TIMESTAMP()+INTERVAL 9 DAY,'available'),
 (7,7,2,6,1,1,'UNIT-DEMO-007',UTC_TIMESTAMP()+INTERVAL 3 DAY,'available'),
 (8,8,3,8,2,1,'UNIT-DEMO-008',UTC_TIMESTAMP()+INTERVAL 1 DAY,'available'),
 (9,9,4,1,3,1,'UNIT-DEMO-009',UTC_TIMESTAMP()+INTERVAL 25 DAY,'available'),
 (10,10,5,3,1,1,'UNIT-DEMO-010',UTC_TIMESTAMP()+INTERVAL 18 DAY,'available');
INSERT INTO screening_record(blood_unit_id,recorded_by_user_id,test_code,tested_at,result_status,notes)
 SELECT ids.blood_unit_id,1,'inventory_clearance',UTC_TIMESTAMP()-INTERVAL 1 DAY,'cleared','Synthetic demonstration result recorded as cleared'
 FROM (SELECT 4 AS blood_unit_id UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10) ids
 WHERE NOT EXISTS (SELECT 1 FROM screening_record s WHERE s.blood_unit_id=ids.blood_unit_id AND s.test_code='inventory_clearance');

-- A partial shortage example, an allocated partial request, and a completed request.
INSERT IGNORE INTO blood_request(blood_request_id,emergency_case_id,requesting_hospital_id,requested_by_user_id,request_ref,requested_at,request_status,urgency,notes) VALUES
 (2,2,2,1,'REQ-DEMO-002',UTC_TIMESTAMP()-INTERVAL 3 HOUR,'partially_fulfilled','urgent','Synthetic partially fulfilled request'),
 (3,3,3,1,'REQ-DEMO-003',UTC_TIMESTAMP()-INTERVAL 1 DAY,'fulfilled','routine','Synthetic completed request'),
 (4,4,1,1,'REQ-DEMO-004',UTC_TIMESTAMP()-INTERVAL 20 MINUTE,'open','critical','Synthetic shortage demonstration');
INSERT IGNORE INTO blood_request_line(request_line_id,blood_request_id,component_id,blood_group_id,line_number,required_units) VALUES
 (2,2,1,7,1,2),(3,3,3,5,1,1),(4,4,5,1,1,4);
INSERT IGNORE INTO blood_allocation(allocation_id,request_line_id,blood_unit_id,allocated_by_user_id,allocation_status,issued_at) VALUES
 (1,1,1,1,'reserved',NULL),(2,2,2,1,'reserved',NULL),(3,3,6,1,'issued',UTC_TIMESTAMP()-INTERVAL 1 DAY);
UPDATE blood_unit SET inventory_status='reserved' WHERE blood_unit_id IN (1,2) AND EXISTS (SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=blood_unit.blood_unit_id AND a.allocation_status='reserved');
UPDATE blood_unit SET inventory_status='issued' WHERE blood_unit_id=6 AND EXISTS (SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=blood_unit.blood_unit_id AND a.allocation_status='issued');
INSERT IGNORE INTO blood_transfer(transfer_id,from_hospital_id,to_hospital_id,initiated_by_user_id,transfer_ref,requested_at,dispatched_at,received_at,transfer_status,notes) VALUES
 (1,1,3,1,'TRF-DEMO-001',UTC_TIMESTAMP()-INTERVAL 2 DAY,UTC_TIMESTAMP()-INTERVAL 2 DAY,UTC_TIMESTAMP()-INTERVAL 1 DAY,'received','Synthetic completed transfer'),
 (2,2,1,1,'TRF-DEMO-002',UTC_TIMESTAMP()-INTERVAL 15 MINUTE,NULL,NULL,'requested','Synthetic transfer awaiting approval');
INSERT IGNORE INTO blood_transfer_unit(transfer_id,blood_unit_id) VALUES (1,4),(2,8);
UPDATE blood_unit SET current_hospital_id=3 WHERE blood_unit_id=4 AND EXISTS (SELECT 1 FROM blood_transfer t WHERE t.transfer_id=1 AND t.transfer_status='received');
INSERT INTO blood_transfer_status_history(transfer_id,changed_by_user_id,status,notes)
 SELECT 1,1,'received','Synthetic completed demonstration transfer' WHERE NOT EXISTS (SELECT 1 FROM blood_transfer_status_history WHERE transfer_id=1 AND status='received');
INSERT IGNORE INTO alert(hospital_id,blood_request_id,request_line_id,created_by_user_id,alert_type,severity,message) VALUES
 (1,4,4,1,'shortage','high','Synthetic urgent request shortage demonstration'),
 (1,2,NULL,1,'pending_request','medium','Synthetic partially fulfilled request has pending units'),
 (1,NULL,NULL,1,'transfer','medium','Synthetic transfer awaits approval');
UPDATE blood_request SET request_status='partially_fulfilled' WHERE blood_request_id=1 AND EXISTS (SELECT 1 FROM blood_allocation WHERE request_line_id=1 AND allocation_status IN ('reserved','issued'));
INSERT IGNORE INTO transfusion_record(transfusion_id,blood_request_id,allocation_id,recorded_by_user_id,started_at,completed_at,status,notes) VALUES
 (1,3,3,1,UTC_TIMESTAMP()-INTERVAL 23 HOUR,UTC_TIMESTAMP()-INTERVAL 22 HOUR,'completed','Synthetic completed transfusion demonstration');
