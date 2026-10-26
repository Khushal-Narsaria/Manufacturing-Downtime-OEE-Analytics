-- Monthly OEE = availability x performance x quality
SELECT substr(date, 1, 7)                                   AS month,
       ROUND(SUM(run_hours) / SUM(planned_hours), 4)        AS availability,
       ROUND(SUM(output) / SUM(ideal_output), 4)            AS performance,
       ROUND(SUM(good_output) / SUM(output), 4)             AS quality,
       ROUND(SUM(run_hours) / SUM(planned_hours) * SUM(output) / SUM(ideal_output)
             * SUM(good_output) / SUM(output), 4)           AS oee,
       ROUND(SUM(breakdown_hours + minor_stop_hours), 0)    AS downtime_hours,
       ROUND(SUM(good_output), 0)                           AS good_units
FROM machine_day
GROUP BY month
ORDER BY month;
