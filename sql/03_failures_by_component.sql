-- Breakdowns by component, with the share of the year's failures
SELECT failure                                   AS component,
       COUNT(*)                                  AS failures,
       COUNT(DISTINCT machineID)                 AS machines_affected,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM failures), 1) AS share_pct
FROM failures
GROUP BY failure
ORDER BY failures DESC;
