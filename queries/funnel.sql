-- ============================================================================
-- Level funnel health | 10.2 vs 11.0 | matched install window
-- Run from the project root:   duckdb -c ".read queries/funnel.sql"
--
-- Metric choices (they decide whether the break is visible)
--   completion_rate   = completed / started    per attempt success
--   attempts_per_user = started / unique users retry pressure
--   sec_per_attempt   = play time / started    does the level itself change?
-- All three are ratios, so they are robust to the pre aggregated grain.
-- Reach is NOT built on i_level_started (it counts retries and balloons at
-- exactly the hard levels) nor summed i_users alone (a player can recount
-- across cohort days); those raw counts would hide the level 7 churn.
-- ============================================================================
WITH params AS (
    SELECT (MIN(d_install_date) FILTER (WHERE s_app_version = '11.0'))::DATE AS win_start,
           MAX(d_install_date)::DATE - 7                                     AS win_end
    FROM read_csv_auto('data/user_activity.csv')
),
p AS (
    SELECT pr.*
    FROM read_csv_auto('data/progression.csv') pr, params
    WHERE pr.s_app_version IN ('10.2', '11.0')
      AND pr.d_install_date BETWEEN win_start AND win_end
)

SELECT
    i_level,
    ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '10.2')
                / SUM(i_level_started)   FILTER (WHERE s_app_version = '10.2'), 1) AS comp_10_2,
    ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '11.0')
                / SUM(i_level_started)   FILTER (WHERE s_app_version = '11.0'), 1) AS comp_11_0,
    ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '11.0')
                / SUM(i_level_started)   FILTER (WHERE s_app_version = '11.0'), 1)
  - ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '10.2')
                / SUM(i_level_started)   FILTER (WHERE s_app_version = '10.2'), 1) AS delta_pts,
    ROUND(1.0 * SUM(i_level_started) FILTER (WHERE s_app_version = '10.2')
              / SUM(i_users)         FILTER (WHERE s_app_version = '10.2'), 2)     AS attempts_10_2,
    ROUND(1.0 * SUM(i_level_started) FILTER (WHERE s_app_version = '11.0')
              / SUM(i_users)         FILTER (WHERE s_app_version = '11.0'), 2)     AS attempts_11_0,
    ROUND(SUM(f_play_time) FILTER (WHERE s_app_version = '10.2')
        / SUM(i_level_started) FILTER (WHERE s_app_version = '10.2'), 0)           AS sec_per_attempt_10_2,
    ROUND(SUM(f_play_time) FILTER (WHERE s_app_version = '11.0')
        / SUM(i_level_started) FILTER (WHERE s_app_version = '11.0'), 0)           AS sec_per_attempt_11_0,
    SUM(i_level_started) FILTER (WHERE s_app_version = '11.0')                     AS starts_11_0
FROM p
GROUP BY i_level
ORDER BY i_level;

-- Result on the 2026-05-03 extract, the three lines that matter:
--   level 6   94.1 vs 92.3   attempts 1.05 vs 1.07   normal
--   level 7   85.4 vs 39.6   attempts 1.14 vs 1.94   THE BREAK (46 pts, all
--             10 countries, from the first 11.0 cohort; sec/attempt 159 vs
--             162, unchanged, so a tuning change, not a crash)
--   level 8   82.1 vs 74.1   attempts 1.17 vs 1.27   the milder global drift
-- Levels 25..30: under 400 total 11.0 starts, deltas there are noise.
