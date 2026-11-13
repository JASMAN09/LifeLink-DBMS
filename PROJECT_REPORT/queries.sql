USE lifelink;

-- 1. Hospital directory.
SELECT hospital_id, code, name, city, is_active FROM hospital ORDER BY name;

-- 2. Emergency cases with patient and receiving hospital.
SELECT e.case_ref, p.display_name AS patient, h.name AS hospital, e.case_status, e.priority_label, e.reported_at
FROM emergency_case e JOIN patient p ON p.patient_id=e.patient_id JOIN hospital h ON h.hospital_id=e.receiving_hospital_id
ORDER BY e.reported_at DESC;

-- 3. Emergency totals grouped by status.
SELECT case_status, COUNT(*) AS case_count FROM emergency_case GROUP BY case_status ORDER BY case_status;

-- 4. Patients and their case counts, including patients without cases.
SELECT p.patient_ref, p.display_name, COUNT(e.emergency_case_id) AS case_count
FROM patient p LEFT JOIN emergency_case e ON e.patient_id=p.patient_id GROUP BY p.patient_id ORDER BY p.display_name;

-- 5. Ambulances by hospital and operational state.
SELECT h.name AS hospital, a.operational_status, COUNT(*) AS vehicles
FROM ambulance a JOIN hospital h ON h.hospital_id=a.home_hospital_id GROUP BY h.hospital_id,h.name,a.operational_status ORDER BY h.name,a.operational_status;

-- 6. Dispatch history with case, vehicle and driver.
SELECT e.case_ref, a.vehicle_code, d.display_name AS driver, x.dispatch_status, x.dispatched_at, x.picked_up_at, x.arrived_at
FROM ambulance_dispatch x JOIN emergency_case e ON e.emergency_case_id=x.emergency_case_id JOIN ambulance a ON a.ambulance_id=x.ambulance_id JOIN driver d ON d.driver_id=x.driver_id
ORDER BY x.dispatched_at DESC;

-- 7. Donor directory with recorded blood group.
SELECT d.donor_ref,d.display_name,bg.code AS blood_group,d.city,d.availability_status
FROM donor d JOIN blood_group bg ON bg.blood_group_id=d.blood_group_id WHERE d.is_active=1 ORDER BY bg.code,d.city,d.display_name;

-- 8. Donation counts per donor, including donors with no donations.
SELECT d.donor_ref,d.display_name,COUNT(n.donation_id) AS donation_count
FROM donor d LEFT JOIN donation n ON n.donor_id=d.donor_id GROUP BY d.donor_id ORDER BY donation_count DESC,d.display_name;

-- 9. Physical unit counts by hospital, blood group, component and state.
SELECT h.name AS hospital,bg.code AS blood_group,bc.name AS component,u.inventory_status,COUNT(*) AS units
FROM blood_unit u JOIN hospital h ON h.hospital_id=u.current_hospital_id JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id
GROUP BY h.hospital_id,h.name,bg.blood_group_id,bg.code,bc.component_id,bc.name,u.inventory_status ORDER BY h.name,bg.code,bc.name,u.inventory_status;

-- 10. Eligible inventory (mirrors application rules; no clinical compatibility inference).
SELECT h.name AS hospital,bg.code AS blood_group,bc.name AS component,COUNT(DISTINCT u.blood_unit_id) AS eligible_units
FROM blood_unit u JOIN hospital h ON h.hospital_id=u.current_hospital_id JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id
WHERE u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
AND NOT EXISTS(SELECT 1 FROM blood_allocation a WHERE a.blood_unit_id=u.blood_unit_id AND a.allocation_status IN ('reserved','issued'))
AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit'))
GROUP BY h.hospital_id,h.name,bg.blood_group_id,bg.code,bc.component_id,bc.name ORDER BY h.name,bg.code,bc.name;

-- 11. Units expiring within 30 days (including already expired units for review).
SELECT u.unit_code,h.name AS hospital,bg.code AS blood_group,bc.name AS component,u.expires_on
FROM blood_unit u JOIN hospital h ON h.hospital_id=u.current_hospital_id JOIN blood_group bg ON bg.blood_group_id=u.blood_group_id JOIN blood_component bc ON bc.component_id=u.component_id
WHERE u.expires_on<=UTC_TIMESTAMP()+INTERVAL 30 DAY ORDER BY u.expires_on;

-- 12. Required, actively allocated and pending units per request line.
SELECT r.request_ref,e.case_ref,l.line_number,bg.code AS blood_group,bc.name AS component,l.required_units,
SUM(CASE WHEN a.allocation_status IN ('reserved','issued') THEN 1 ELSE 0 END) AS allocated_units,
GREATEST(l.required_units-SUM(CASE WHEN a.allocation_status IN ('reserved','issued') THEN 1 ELSE 0 END),0) AS pending_units
FROM blood_request_line l JOIN blood_request r ON r.blood_request_id=l.blood_request_id JOIN emergency_case e ON e.emergency_case_id=r.emergency_case_id JOIN blood_group bg ON bg.blood_group_id=l.blood_group_id JOIN blood_component bc ON bc.component_id=l.component_id
LEFT JOIN blood_allocation a ON a.request_line_id=l.request_line_id GROUP BY l.request_line_id,r.request_ref,e.case_ref,l.line_number,bg.code,bc.name,l.required_units,r.requested_at ORDER BY r.requested_at DESC,l.line_number;

-- 13. Pending request lines and their shortage after local eligible inventory.
SELECT r.request_ref,e.case_ref,h.name AS hospital,bg.code AS blood_group,bc.name AS component,
GREATEST(l.required_units-COUNT(DISTINCT CASE WHEN a.allocation_status IN ('reserved','issued') THEN a.allocation_id END),0) AS pending_units,
COUNT(DISTINCT CASE WHEN u.inventory_status='available' AND u.expires_on>UTC_TIMESTAMP()
AND (SELECT s.result_status FROM screening_record s WHERE s.blood_unit_id=u.blood_unit_id AND s.test_code='inventory_clearance' ORDER BY s.tested_at DESC,s.screening_id DESC LIMIT 1)='cleared'
AND NOT EXISTS(SELECT 1 FROM blood_allocation ax WHERE ax.blood_unit_id=u.blood_unit_id AND ax.allocation_status IN ('reserved','issued'))
AND NOT EXISTS(SELECT 1 FROM blood_transfer_unit tu JOIN blood_transfer t ON t.transfer_id=tu.transfer_id WHERE tu.blood_unit_id=u.blood_unit_id AND t.transfer_status IN ('requested','approved','in_transit')) THEN u.blood_unit_id END) AS eligible_units
FROM blood_request_line l JOIN blood_request r ON r.blood_request_id=l.blood_request_id JOIN emergency_case e ON e.emergency_case_id=r.emergency_case_id JOIN hospital h ON h.hospital_id=r.requesting_hospital_id JOIN blood_group bg ON bg.blood_group_id=l.blood_group_id JOIN blood_component bc ON bc.component_id=l.component_id
LEFT JOIN blood_allocation a ON a.request_line_id=l.request_line_id LEFT JOIN blood_unit u ON u.current_hospital_id=r.requesting_hospital_id AND u.blood_group_id=l.blood_group_id AND u.component_id=l.component_id
GROUP BY l.request_line_id,r.request_ref,e.case_ref,h.name,bg.code,bc.name,l.required_units,r.requested_at HAVING pending_units>eligible_units ORDER BY r.requested_at;

-- 14. Request-level progress totals.
SELECT r.request_ref,r.request_status,SUM(l.required_units) AS required_units,
SUM((SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued'))) AS allocated_units,
SUM(GREATEST(l.required_units-(SELECT COUNT(*) FROM blood_allocation a WHERE a.request_line_id=l.request_line_id AND a.allocation_status IN ('reserved','issued')),0)) AS pending_units
FROM blood_request r JOIN blood_request_line l ON l.blood_request_id=r.blood_request_id GROUP BY r.blood_request_id ORDER BY r.requested_at DESC;

-- 15. Donor contact outcomes.
SELECT d.donor_ref,d.display_name,r.request_ref,c.contact_method,c.outcome,c.attempted_at,c.response_notes
FROM donor_contact_attempt c JOIN donor d ON d.donor_id=c.donor_id JOIN blood_request r ON r.blood_request_id=c.blood_request_id ORDER BY c.attempted_at DESC;

-- 16. Screening history per individual unit.
SELECT u.unit_code,s.test_code,s.result_status,s.tested_at,s.result_reference
FROM screening_record s JOIN blood_unit u ON u.blood_unit_id=s.blood_unit_id ORDER BY u.unit_code,s.tested_at DESC;

-- 17. Crossmatch entries as recorded by authorized staff.
SELECT r.request_ref,l.line_number,u.unit_code,x.test_status,x.tested_at,x.result_reference
FROM crossmatch_record x JOIN blood_request_line l ON l.request_line_id=x.request_line_id JOIN blood_request r ON r.blood_request_id=l.blood_request_id JOIN blood_unit u ON u.blood_unit_id=x.blood_unit_id ORDER BY x.tested_at DESC;

-- 18. Transfer manifests and number of included individual units.
SELECT t.transfer_ref,src.name AS source_hospital,dst.name AS destination_hospital,t.transfer_status,COUNT(tu.blood_unit_id) AS unit_count
FROM blood_transfer t JOIN hospital src ON src.hospital_id=t.from_hospital_id JOIN hospital dst ON dst.hospital_id=t.to_hospital_id LEFT JOIN blood_transfer_unit tu ON tu.transfer_id=t.transfer_id
GROUP BY t.transfer_id,t.transfer_ref,src.name,dst.name,t.transfer_status,t.requested_at ORDER BY t.requested_at DESC;

-- 19. Transfer status history.
SELECT t.transfer_ref,h.status,h.changed_at,h.notes FROM blood_transfer_status_history h JOIN blood_transfer t ON t.transfer_id=h.transfer_id ORDER BY h.changed_at;

-- 20. Open or acknowledged alerts with optional request/unit context.
SELECT a.alert_type,a.severity,a.message,a.created_at,h.name AS hospital,r.request_ref,u.unit_code
FROM alert a JOIN hospital h ON h.hospital_id=a.hospital_id LEFT JOIN blood_request r ON r.blood_request_id=a.blood_request_id LEFT JOIN blood_unit u ON u.blood_unit_id=a.blood_unit_id
WHERE a.alert_status IN ('open','acknowledged') ORDER BY a.created_at DESC;

-- 21. Donation totals by month in the current UTC calendar year.
SELECT DATE_FORMAT(collected_at,'%Y-%m') AS collection_month,COUNT(*) AS donations
FROM donation WHERE donation_status='collected' AND collected_at>=MAKEDATE(YEAR(UTC_DATE()),1) GROUP BY DATE_FORMAT(collected_at,'%Y-%m') ORDER BY collection_month;

-- 22. Emergency case counts by recorded location.
SELECT emergency_location,COUNT(*) AS case_count FROM emergency_case GROUP BY emergency_location ORDER BY case_count DESC,emergency_location;

-- 23. Latest 100 audit events.
SELECT a.occurred_at,u.username,a.action,a.entity_type,a.entity_id,a.details
FROM audit_log a LEFT JOIN app_user u ON u.user_id=a.actor_user_id ORDER BY a.occurred_at DESC LIMIT 100;

-- 24. Transfusion outcomes linked to request and allocation.
SELECT r.request_ref,a.allocation_id,t.started_at,t.completed_at,t.status,t.notes
FROM transfusion_record t JOIN blood_request r ON r.blood_request_id=t.blood_request_id JOIN blood_allocation a ON a.allocation_id=t.allocation_id ORDER BY t.started_at DESC;

-- 25. Requests with no active allocated units.
SELECT r.request_ref,r.request_status,r.requested_at,e.case_ref
FROM blood_request r JOIN emergency_case e ON e.emergency_case_id=r.emergency_case_id
WHERE NOT EXISTS(SELECT 1 FROM blood_request_line l JOIN blood_allocation a ON a.request_line_id=l.request_line_id WHERE l.blood_request_id=r.blood_request_id AND a.allocation_status IN ('reserved','issued'))
ORDER BY r.requested_at DESC;
