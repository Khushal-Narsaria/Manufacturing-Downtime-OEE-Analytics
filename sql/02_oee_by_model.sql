-- OEE and downtime by machine model
SELECT m.model,
       COUNT(DISTINCT d.machineID)                                     AS machines,
       ROUND(SUM(d.run_hours) / SUM(d.planned_hours), 4)               AS availability,
       ROUND(SUM(d.output) / SUM(d.ideal_output), 4)                   AS performance,
       ROUND(SUM(d.good_output) / SUM(d.output), 4)                    AS quality,
       ROUND(SUM(d.breakdown_hours + d.minor_stop_hours) * 1.0 / COUNT(DISTINCT d.machineID), 1) AS downtime_hours_per_machine
FROM machine_day d
JOIN machines m ON m.machineID = d.machineID
GROUP BY m.model
ORDER BY downtime_hours_per_machine DESC;
