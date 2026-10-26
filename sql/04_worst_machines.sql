-- Machines with the most downtime and their failure counts
SELECT d.machineID,
       m.model,
       m.age,
       ROUND(SUM(d.breakdown_hours + d.minor_stop_hours), 0)   AS downtime_hours,
       (SELECT COUNT(*) FROM failures f WHERE f.machineID = d.machineID) AS failures,
       ROUND(SUM(d.run_hours) / SUM(d.planned_hours) * SUM(d.output) / SUM(d.ideal_output)
             * SUM(d.good_output) / SUM(d.output), 4)          AS oee
FROM machine_day d
JOIN machines m ON m.machineID = d.machineID
GROUP BY d.machineID, m.model, m.age
ORDER BY downtime_hours DESC
LIMIT 15;
