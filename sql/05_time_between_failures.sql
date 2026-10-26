-- Hours between consecutive failures of the same machine (window function)
WITH ordered AS (
    SELECT machineID, failure, datetime,
           LAG(datetime) OVER (PARTITION BY machineID ORDER BY datetime) AS previous_failure
    FROM failures
)
SELECT failure AS component,
       COUNT(*)                                                                  AS gaps,
       ROUND(AVG((julianday(datetime) - julianday(previous_failure)) * 24), 0)  AS avg_hours_between_failures,
       ROUND(MIN((julianday(datetime) - julianday(previous_failure)) * 24), 0)  AS min_hours
FROM ordered
WHERE previous_failure IS NOT NULL
GROUP BY failure
ORDER BY avg_hours_between_failures;
