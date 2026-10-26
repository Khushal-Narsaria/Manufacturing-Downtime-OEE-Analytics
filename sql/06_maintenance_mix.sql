-- Planned replacements vs repairs after a failure, per component
SELECT comp                                          AS component,
       SUM(CASE WHEN after_failure = 0 THEN 1 ELSE 0 END) AS planned_replacements,
       SUM(after_failure)                            AS repairs_after_failure,
       ROUND(100.0 * SUM(after_failure) / COUNT(*), 1) AS reactive_share_pct
FROM maintenance
WHERE datetime >= '2015-01-01'
GROUP BY comp
ORDER BY reactive_share_pct DESC;
