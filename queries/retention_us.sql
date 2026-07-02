-- ============================================================================
-- US focus: the headline retention query with one extra filter.
-- Run from the project root:   duckdb -c ".read queries/retention_us.sql"
-- Answers: is the regression a market effect or the build itself?
-- ============================================================================
WITH params AS (
    SELECT (MIN(d_install_date) FILTER (WHERE s_app_version = '11.0'))::DATE AS win_start,
           MAX(d_install_date)::DATE - 7                                     AS win_end
    FROM read_csv_auto('data/user_activity.csv')
),
base AS (
    SELECT a.*
    FROM read_csv_auto('data/user_activity.csv') a, params p
    WHERE a.s_app_version IN ('10.2', '11.0')
      AND a.d_install_date BETWEEN p.win_start AND p.win_end
      AND a.s_country = 'US'                    -- the only change vs retention.sql
),
cohort_size AS (
    SELECT s_app_version, SUM(i_active_users) AS d0_users
    FROM base WHERE i_cohort_groups = 0 GROUP BY 1
),
actives AS (
    SELECT s_app_version, i_cohort_groups AS cohort_day, SUM(i_active_users) AS active_users
    FROM base GROUP BY 1, 2
)
SELECT
    a.s_app_version                               AS version,
    a.cohort_day,
    c.d0_users                                    AS cohort_size,
    ROUND(100.0 * a.active_users / c.d0_users, 2) AS retention_pct
FROM actives a
JOIN cohort_size c USING (s_app_version)
WHERE a.cohort_day IN (1, 3, 7)
ORDER BY version, cohort_day;

-- Result on the 2026-05-03 extract (D1 / D3 / D7):
--   10.2   55.42 / 31.64 / 19.40    (4,361 day 0 users)
--   11.0   50.73 / 24.50 / 12.93    (2,135 day 0 users)
-- Same shape as worldwide: the regression is the build, not a region.
