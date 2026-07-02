-- ============================================================================
-- Robustness pack: the same retention read two other ways.
-- Run from the project root:   duckdb -c ".read queries/retention_robustness.sql"
--
--   naive_censored ..... pools every cohort with no maturity gate. WRONG on
--                        purpose: cohorts installed less than N days before
--                        the extract have no day N row yet but still inflate
--                        the day 0 denominator. This is what a dashboard
--                        without a gate shows, and why 11.0 D7 reads 6.83%.
--   maturity_gate ...... per day gate on the FULL 90 day history: a cohort
--                        counts at day N only if install <= extract - N.
--                        Uses all of 10.2's history (no fixed window). Must
--                        agree with the matched window read, and it does.
-- ============================================================================
WITH params AS (
    SELECT MAX(d_install_date)::DATE AS extract_date
    FROM read_csv_auto('data/user_activity.csv')
),
base AS (
    SELECT a.*, p.extract_date
    FROM read_csv_auto('data/user_activity.csv') a, params p
    WHERE a.s_app_version IN ('10.2', '11.0')
)

SELECT
    'naive_censored (do not ship)' AS read_type,
    s_app_version                  AS version,
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d1,
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 3)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d3,
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 7)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d7
FROM base
GROUP BY 1, 2

UNION ALL

SELECT
    'maturity_gate (full history)' AS read_type,
    s_app_version                  AS version,
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1 AND d_install_date <= extract_date - 1)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= extract_date - 1), 2),
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 3 AND d_install_date <= extract_date - 3)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= extract_date - 3), 2),
    ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 7 AND d_install_date <= extract_date - 7)
                / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= extract_date - 7), 2)
FROM base
GROUP BY 1, 2
ORDER BY read_type, version;

-- Result on the 2026-05-03 extract (D1 / D3 / D7):
--   maturity_gate   10.2  54.99 / 31.74 / 19.49    11.0  50.19 / 24.28 / 12.91
--   naive_censored  10.2  54.57 / 31.06 / 18.55    11.0  46.62 / 19.16 /  6.83
-- The gate agrees with the matched window (retention.sql), so the window is
-- not doing the work. The naive read nearly doubles the apparent D7 damage.
