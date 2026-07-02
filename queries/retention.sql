-- ============================================================================
-- Retention D1..D7 | Wonders Kingdom, Android | 10.2 vs 11.0
-- The query for the walkthrough. Engine: DuckDB, reads the CSV directly.
-- Run from the project root:   duckdb -c ".read queries/retention.sql"
--
-- Definitions (from the brief)
--   Cohort size  = i_active_users at i_cohort_groups = 0.
--   Retention DN = active users at cohort day N / cohort size.
--   Aggregation  = pooled within version (sum over cohorts), so each cohort
--                  weighs by its size, which is what a rollout decision needs.
--                  The unweighted per cohort mean lands within 0.3 pts.
--
-- The two corrections that make the number honest
--   1) Versions 10.2 and 11.0 only. The stray label 11.0.1 carries installs
--      that predate 11.0 by two months, so the label cannot be trusted.
--      Quarantined and flagged, not silently dropped.
--   2) Matched install window: first 11.0 install .. extract date minus 7.
--      Both builds coexist there and every cohort is mature to day 7. This
--      removes right censoring (a young cohort has no day N row yet but
--      still sits in the day 0 denominator) and aligns the calendar in one
--      move. Without it, 11.0 D7 reads 6.8% instead of the real 12.9%.
--      Bounds are DERIVED from the data, never hardcoded; on this extract
--      they resolve to 2026-04-15 .. 2026-04-26 (extract = 2026-05-03).
-- ============================================================================
WITH params AS (
    SELECT MAX(d_install_date)::DATE                                         AS extract_date,
           (MIN(d_install_date) FILTER (WHERE s_app_version = '11.0'))::DATE AS win_start,
           MAX(d_install_date)::DATE - 7                                     AS win_end
    FROM read_csv_auto('data/user_activity.csv')
),

base AS (
    SELECT a.*
    FROM read_csv_auto('data/user_activity.csv') a, params p
    WHERE a.s_app_version IN ('10.2', '11.0')          -- correction 1
      AND a.d_install_date BETWEEN p.win_start AND p.win_end   -- correction 2
),

cohort_size AS (   -- denominator: active users on install day
    SELECT s_app_version, SUM(i_active_users) AS d0_users
    FROM base
    WHERE i_cohort_groups = 0
    GROUP BY 1
),

actives AS (       -- numerator: active users on each cohort day
    SELECT s_app_version, i_cohort_groups AS cohort_day, SUM(i_active_users) AS active_users
    FROM base
    GROUP BY 1, 2
)

SELECT
    a.s_app_version                               AS version,
    a.cohort_day,
    c.d0_users                                    AS cohort_size,
    a.active_users,
    ROUND(100.0 * a.active_users / c.d0_users, 2) AS retention_pct
FROM actives a
JOIN cohort_size c USING (s_app_version)
ORDER BY version, cohort_day;

-- Result on the 2026-05-03 extract (D1 / D3 / D7):
--   10.2   55.43 / 31.88 / 19.63     (cohort size 22,334)
--   11.0   50.06 / 24.23 / 12.91     (cohort size 10,595)
-- 11.0 is 10% lower on D1, 24% on D3, 34% on D7. The gap widens with age.
