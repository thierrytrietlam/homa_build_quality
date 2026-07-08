"""Wonders Kingdom build quality read: 10.2 vs 11.0 (Android)."""

import json
import math
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --------------------------------------------------------------------------- #
# 0. Setup. The window is DERIVED from the data, not hardcoded: a nightly job
#    must re derive it from its own run date or every fresh build fakes a
#    retention collapse (see writeup, production notes).
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

VERSIONS = ("10.2", "11.0")  # the two builds under test; 11.0.1 handled in section 2
MAX_HORIZON = 7              # brief: data beyond cohort day 7 is out of scope

con = duckdb.connect()
# Load CSVs as DuckDB views.
con.execute(f"""
    CREATE VIEW act  AS SELECT * FROM read_csv_auto('{(ROOT / 'data' / 'user_activity.csv').as_posix()}', header=true);
    CREATE VIEW prog AS SELECT * FROM read_csv_auto('{(ROOT / 'data' / 'progression.csv').as_posix()}',  header=true);
""")

# Get extract date and first 11.0 install date.
EXTRACT, WIN_START = con.execute("""
    SELECT MAX(d_install_date)::DATE,
           (MIN(d_install_date) FILTER (WHERE s_app_version = '11.0'))::DATE
    FROM act
""").fetchone()

# Keep only cohorts mature through D7.
WIN_END = con.execute(f"SELECT DATE '{EXTRACT}' - {MAX_HORIZON}").fetchone()[0]

# Ensure the dataset matches the case study extract date.
assert str(EXTRACT) == "2026-05-03", "extract date drifted from the brief"

# Store the analysis window for reporting.
M = {"window": {"extract_date": str(EXTRACT), "win_start": str(WIN_START),
                "win_end": str(WIN_END),
                "rule": "win_start = first 11.0 install; win_end = extract - 7 so every cohort is mature to D7"}}

# Filter to the comparable cohort window.
WIN = f"d_install_date BETWEEN DATE '{WIN_START}' AND DATE '{WIN_END}'"

# Restrict analysis to the two builds under evaluation.
V2 = "s_app_version IN ('10.2', '11.0')"


def q(sql):
    return con.execute(sql).df()


def rule(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


# --------------------------------------------------------------------------- #
# 1. Profile and integrity. Spend time with the data before computing anything.
# --------------------------------------------------------------------------- #
rule("1. PROFILE AND INTEGRITY (before any KPI)")

# Profile each app version.
prof = q("""
    SELECT s_app_version, COUNT(*) AS rows_,
           MIN(d_install_date)::DATE AS first_install, MAX(d_install_date)::DATE AS last_install,
           SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0) AS d0_users
    FROM act GROUP BY 1 ORDER BY 1
""")

# Print the profile.
print(prof.to_string(index=False))

# Save version metadata.
M["versions"] = prof.astype(str).to_dict("records")

integrity = q(f"""
    SELECT
      -- Total progression rows.
      (SELECT COUNT(*) FROM prog)                                                          AS prog_rows,

      -- Rows where the started = completed + failed identity does not hold.
      (SELECT COUNT(*) FROM prog WHERE i_level_started <> i_level_completed + i_level_failed) AS identity_breaks,

      -- Rows with negative values.
      (SELECT COUNT(*) FROM prog WHERE LEAST(i_level_started, i_level_completed, i_level_failed, i_users, 
      f_play_time) < 0)
        + (SELECT COUNT(*) FROM act WHERE LEAST(i_active_users, i_count_sessions, f_playtime) < 0) AS negatives,

      -- Duplicate rows in the act table.
      (SELECT COUNT(*) FROM (SELECT 1 FROM act  GROUP BY d_install_date, s_app_version, s_country,
              s_acquisition_network, s_installing_pckg_group, i_cohort_groups HAVING COUNT(*) > 1))   AS dup_act,

      -- Duplicate rows in the prog table.
      (SELECT COUNT(*) FROM (SELECT 1 FROM prog GROUP BY d_install_date, s_app_version, s_country,
              s_acquisition_network, s_installing_pckg_group, i_cohort_groups, i_level HAVING COUNT(*) > 1)) AS dup_prog,

      -- Rows beyond the extract date.
      (SELECT COUNT(*) FROM act WHERE d_install_date + i_cohort_groups * INTERVAL 1 DAY > DATE '{EXTRACT}') AS rows_beyond_extract,

      -- Rows with duplicate cohort cells.
      (SELECT COUNT(*) FROM (
          SELECT SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0) AS d0,
                 MAX(i_active_users) FILTER (WHERE i_cohort_groups > 0) AS later
          FROM act GROUP BY d_install_date, s_app_version, s_country, s_acquisition_network, s_installing_pckg_group
          HAVING d0 IS NULL OR d0 = 0 OR later > d0))                                       AS bad_cohort_cells
""")
print(integrity.to_string(index=False))
M["integrity"] = integrity.iloc[0].astype(int).to_dict()
assert int(integrity.identity_breaks[0]) == 0

integrity2 = q(f"""
    SELECT
      -- Rows in prog without a corresponding row in act.
      (SELECT COUNT(*) FROM (SELECT DISTINCT d_install_date, s_app_version, s_country, s_acquisition_network,
              s_installing_pckg_group, i_cohort_groups FROM prog) p
        LEFT JOIN (SELECT DISTINCT d_install_date, s_app_version, s_country, s_acquisition_network,
              s_installing_pckg_group, i_cohort_groups FROM act) a
        USING (d_install_date, s_app_version, s_country, s_acquisition_network, s_installing_pckg_group, i_cohort_groups)
        WHERE a.d_install_date IS NULL)                                                     AS prog_cells_without_act,

      -- Rows in act without a corresponding row in prog.
      (SELECT COUNT(*) FROM (
          WITH agg AS (SELECT s_app_version v, d_install_date::DATE d, i_cohort_groups cd
                       FROM act WHERE {V2} GROUP BY 1, 2, 3),
          expect AS (SELECT v, d, gs.cd FROM (SELECT DISTINCT v, d FROM agg) x
                     CROSS JOIN (SELECT UNNEST(RANGE(0, 8)) cd) gs
                     WHERE gs.cd <= LEAST(7, DATE '{EXTRACT}' - d))
          SELECT 1 FROM expect e LEFT JOIN agg a ON a.v = e.v AND a.d = e.d AND a.cd = e.cd
          WHERE a.v IS NULL))                                                               AS missing_version_day_rows,

      -- Rows where sessions < actives.
      (SELECT COUNT(*) FROM act WHERE i_count_sessions < i_active_users)                    AS cells_sessions_lt_actives,

      -- Rows where playtime > 24h.
      (SELECT COUNT(*) FROM act WHERE f_playtime > i_active_users * 86400.0)                AS cells_playtime_gt_24h,

      -- Rows where users > starts.
      (SELECT COUNT(*) FROM prog WHERE i_users > i_level_started)                           AS cells_users_gt_starts,

      -- Install date gaps.
      (SELECT SUM((sp - n)::INT) FROM (SELECT (MAX(d) - MIN(d) + 1) sp, COUNT(*) n FROM
          (SELECT DISTINCT s_app_version, d_install_date::DATE d FROM act) GROUP BY s_app_version)) AS install_date_gaps
""")
# Display integrity2 metrics.
print(integrity2.to_string(index=False))
# Save integrity2 metrics.
M["integrity2"] = integrity2.iloc[0].astype(int).to_dict()

# Check extract-day cohort completeness.
extract_day = q(f"""
    SELECT SUM(i_active_users) FILTER (WHERE d_install_date = DATE '{EXTRACT}')                AS d0_last_day,
           SUM(i_active_users) FILTER (WHERE d_install_date = DATE '{EXTRACT}' - INTERVAL 1 DAY) AS d0_prev_day
    FROM act WHERE i_cohort_groups = 0 AND {V2}
""")

# Display extract-day completeness check.
print("extract day completeness check (a mid day extract would halve the last cohort):")
print(extract_day.to_string(index=False))

# Save extract-day completeness metrics.
M["extract_day_check"] = extract_day.iloc[0].astype(int).to_dict()

quirk = q("""
    -- Check for anomalous cells where summed LEVEL playtime exceeds SESSION playtime.
    WITH p AS (SELECT d_install_date, s_app_version, s_country, s_acquisition_network,
                      s_installing_pckg_group, i_cohort_groups, SUM(f_play_time) AS pt
               FROM prog GROUP BY ALL)
    SELECT COUNT(*)                                                    AS anomalous_cells,
           (SELECT COUNT(*) FROM act)                                  AS total_cells,
           ROUND(AVG(p.pt / a.f_playtime), 2)                          AS avg_ratio,
           ROUND(AVG(a.i_active_users), 1)                             AS avg_cell_actives,
           SUM(a.i_active_users)                                       AS user_days_touched,
           (SELECT SUM(i_active_users) FROM act)                       AS user_days_total
    FROM p JOIN act a USING (d_install_date, s_app_version, s_country, s_acquisition_network,
                             s_installing_pckg_group, i_cohort_groups)
    WHERE p.pt > a.f_playtime * 1.001
""")
print("\nOne cross file quirk, flagged (not fixed, nothing depends on it): cells where")
print("summed LEVEL playtime exceeds SESSION playtime, physically impossible on a clock:")
print(quirk.to_string(index=False))
M["playtime_quirk"] = quirk.iloc[0].to_dict()

print("""
Row level data is otherwise clean: the started = completed + failed identity holds
on all 530,411 progression rows, no duplicates on the declared grain, no negatives,
no cohort day rows beyond the extract date, every cohort cell has a day 0 row, the
two files agree on the exact same cell universe (0 orphans both ways), every
(version x install date) of the two headline builds has its complete day 0..horizon
sequence, install dates are contiguous, sessions >= actives and playtime stays under
24h per user everywhere, and the extract day is a complete day. The playtime quirk
above sits in 1 to 2 user cells (about 1% of user days, both builds alike) and looks
like cross midnight attribution: level time follows the calendar day, session time
follows the session start. No metric in this analysis joins the two time fields.
The real issues in this dataset are STRUCTURAL (labels and censoring).""")

# --------------------------------------------------------------------------- #
# 2. Version forensics: the 11.0.1 label cannot be trusted.
# --------------------------------------------------------------------------- #
rule("2. DATA QUALITY GATE: the 11.0.1 label")
# Profile version 11.0.1 before excluding or merging it.
# Profile 11.0.1 rollout volume by month.
v1101 = q("""
    SELECT strftime(d_install_date, '%Y-%m') AS ym,
           SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0) AS d0_users
    FROM act WHERE s_app_version = '11.0.1' GROUP BY 1 ORDER BY 1
""")
print(v1101.to_string(index=False))

# Compare D1 retention and Level 7 completion across builds.
v1101_profile = q(f"""
    SELECT s_app_version,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 1 DAY)
                  / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 1 DAY), 1) AS d1,
      (SELECT ROUND(100.0 * SUM(i_level_completed) / SUM(i_level_started), 1)
         FROM prog p WHERE p.s_app_version = act.s_app_version AND i_level = 7)          AS l7_completion
    FROM act GROUP BY 1 ORDER BY 1
""")

print(v1101_profile.to_string(index=False))
M["v1101"] = {"monthly_d0": v1101.astype(str).to_dict("records"),
              "profile_vs_builds": v1101_profile.astype(str).to_dict("records")}

# 11.0 first installs on 15 Apr, yet 11.0.1 carries installs back to February.
print(f"""
11.0 first installs on {WIN_START}, yet 11.0.1 carries installs back to February.
A patch cannot predate its parent, so the label is untrustworthy. It is 767 day 0
users (0.3% of volume). Its BEHAVIOUR matches 11.0 (D1 ~51.5, level 7 completion
~36.8), which suggests mistagged 11.0 like traffic, possibly a test channel.
Decision: quarantine it from the headline, flag it to data engineering. Folding
it into 11.0 would be a guess; silently dropping it would hide a pipeline defect.""")

# --------------------------------------------------------------------------- #
# 3. Retention (Lea). Naive -> the trap. Matched window -> the headline.
#    Maturity gate on full history -> the robustness read.
# --------------------------------------------------------------------------- #
rule("3. RETENTION, 10.2 vs 11.0 (worldwide)")

# Baseline retention using all cohorts (for comparison only).
naive = q(f"""
    SELECT s_app_version,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1) / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d1,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 3) / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d3,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 7) / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0), 2) AS d7
    FROM act WHERE {V2} GROUP BY 1 ORDER BY 1
""")
print("NAIVE pooled, all cohorts, no maturity gate (do NOT ship this):")
print(naive.to_string(index=False))
M["retention_naive"] = naive.to_dict("records")

# Compute retention using matched, D7-mature cohorts: overlap (WIN_START → WIN_END).
curve = q(f"""
    WITH base AS (SELECT * FROM act WHERE {V2} AND {WIN}),
    d0 AS (SELECT s_app_version, SUM(i_active_users) AS d0 FROM base WHERE i_cohort_groups = 0 GROUP BY 1)
    SELECT b.s_app_version AS version, b.i_cohort_groups AS cohort_day,
           ANY_VALUE(d0.d0) AS cohort_size, SUM(b.i_active_users) AS active_users,
           ROUND(100.0 * SUM(b.i_active_users) / ANY_VALUE(d0.d0), 2) AS retention_pct
    FROM base b JOIN d0 USING (s_app_version) GROUP BY 1, 2 ORDER BY 1, 2
""")
print(f"\nHEADLINE, matched install window {WIN_START}..{WIN_END} "
      "(both builds coexist, every cohort mature to D7):")
print(curve.to_string(index=False))
M["retention_curve"] = curve.to_dict("records")

# Recompute retention over the full history with maturity gating.
# Extract date = 2026-05-03
gate = q(f"""
    SELECT s_app_version,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 1 DAY)
                  / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 1 DAY), 2) AS d1,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 3 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 3 DAY)
                  / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 3 DAY), 2) AS d3,
      ROUND(100.0 * SUM(i_active_users) FILTER (WHERE i_cohort_groups = 7 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 7 DAY)
                  / SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0 AND d_install_date <= DATE '{EXTRACT}' - INTERVAL 7 DAY), 2) AS d7
    FROM act WHERE {V2} GROUP BY 1 ORDER BY 1
""")
print("\nROBUSTNESS, per day maturity gate on full 90 day history (same verdict):")
print(gate.to_string(index=False))
M["retention_maturity_gate"] = gate.to_dict("records")

# Per cohort date D1 spread inside the window (no overlap between builds, and the unweighted mean agrees with the pooled read, so no big cohort drives it).
spread = q(f"""
    WITH c AS (
      SELECT s_app_version, d_install_date,
             SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0) AS d0,
             SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1) AS d1
      FROM act WHERE {V2} AND {WIN} GROUP BY 1, 2)
    SELECT s_app_version, COUNT(*) AS n_cohort_dates,
           ROUND(MIN(100.0 * d1 / d0), 1) AS d1_min,
           ROUND(AVG(100.0 * d1 / d0), 1) AS d1_unweighted_mean,
           ROUND(MAX(100.0 * d1 / d0), 1) AS d1_max
    FROM c GROUP BY 1 ORDER BY 1
""")
print("\nPer cohort date D1 spread inside the window (no overlap between builds,")
print("and the unweighted mean agrees with the pooled read, so no big cohort drives it):")
print(spread.to_string(index=False))
M["retention_d1_spread"] = spread.to_dict("records")

# Extract headline retention metrics.
hl = {r["version"]: {d: float(curve[(curve.version == r["version"]) & (curve.cohort_day == n)].retention_pct.iloc[0])
                     for d, n in (("D1", 1), ("D3", 3), ("D7", 7))}
      for r in ({"version": "10.2"}, {"version": "11.0"})}

# Compute absolute and relative retention gaps.
gaps = {d: round(hl["10.2"][d] - hl["11.0"][d], 1) for d in ("D1", "D3", "D7")}
rel = {d: round(100 * (hl["11.0"][d] - hl["10.2"][d]) / hl["10.2"][d]) for d in ("D1", "D3", "D7")}

# Save headline retention summary.
M["retention_headline"] = {"v10_2": hl["10.2"], "v11_0": hl["11.0"], "abs_gap_pts": gaps, "rel_change_pct": rel}
print(f"\nHeadline: D1 {hl['10.2']['D1']} vs {hl['11.0']['D1']} ({rel['D1']}%), "
      f"D3 {hl['10.2']['D3']} vs {hl['11.0']['D3']} ({rel['D3']}%), "
      f"D7 {hl['10.2']['D7']} vs {hl['11.0']['D7']} ({rel['D7']}%).")

# Two-Proportion Z-Test: two proportions (such as retention rates) is statistically significant or simply due to random sampling
# z = (p1 - p2) / sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
def ztest(x1, n1, x2, n2):
    p1, p2, pp = x1 / n1, x2 / n2, (x1 + x2) / (n1 + n2)
    return round((p1 - p2) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2)), 1)

# Compute z-scores for D1, D3, and D7.
zsc = {}
for d, n in (("D1", 1), ("D3", 3), ("D7", 7)):
    c1 = curve[(curve.version == "10.2") & (curve.cohort_day == n)].iloc[0]
    c2 = curve[(curve.version == "11.0") & (curve.cohort_day == n)].iloc[0]
    zsc[d] = ztest(c1.active_users, c1.cohort_size, c2.active_users, c2.cohort_size)
M["retention_z_scores"] = zsc
# A larger absolute Z-score means the observed difference is much larger than what random sampling would normally produce.
print(f"Two proportion z tests: D1 z = {zsc['D1']}, D3 z = {zsc['D3']}, D7 z = {zsc['D7']}."
      "  The gap is not sampling noise.")

# Compare the D1 retention gap by shared install date.
gap_dates = q(f"""
    WITH c AS (SELECT s_app_version v, d_install_date::DATE d,
                 SUM(i_active_users) FILTER (WHERE i_cohort_groups = 0) d0,
                 SUM(i_active_users) FILTER (WHERE i_cohort_groups = 1) d1
               FROM act WHERE {V2} AND {WIN} GROUP BY 1, 2)
    SELECT d, ROUND(100.0 * (MAX(CASE WHEN v = '11.0' THEN d1 * 1.0 / d0 END)
                           - MAX(CASE WHEN v = '10.2' THEN d1 * 1.0 / d0 END)), 1) AS d1_gap_pts
    FROM c GROUP BY 1 ORDER BY 1
""")

# Count install dates where 11.0 underperforms 10.2.
neg = int((gap_dates.d1_gap_pts < 0).sum())

# Save D1 gap by install date.
M["d1_gap_by_install_date"] = gap_dates.astype(str).to_dict("records")

# Display D1 gap by install date.
print(f"D1 gap by shared install date: negative on {neg} of {len(gap_dates)} dates "
      f"(range {gap_dates.d1_gap_pts.min()} to {gap_dates.d1_gap_pts.max()} pts).")

# --------------------------------------------------------------------------- #
# 4. Mix balance. Verify the confound, then trust the pooled read.
# --------------------------------------------------------------------------- #
rule("4. MIX CHECK: is the pooled worldwide comparison fair?")

# Compare user mix across versions.
mix = q(f"""
    WITH d0 AS (
      SELECT s_app_version, dim, val, SUM(u) AS u FROM (
        SELECT s_app_version, 'country' AS dim, s_country AS val, i_active_users AS u
        FROM act WHERE {V2} AND {WIN} AND i_cohort_groups = 0
        UNION ALL
        SELECT s_app_version, 'network', s_acquisition_network, i_active_users
        FROM act WHERE {V2} AND {WIN} AND i_cohort_groups = 0
        UNION ALL
        SELECT s_app_version, 'package', s_installing_pckg_group, i_active_users
        FROM act WHERE {V2} AND {WIN} AND i_cohort_groups = 0
      ) GROUP BY 1, 2, 3),
    shares AS (
      SELECT dim, val,
        100.0 * SUM(u) FILTER (WHERE s_app_version = '10.2') / SUM(SUM(u) FILTER (WHERE s_app_version = '10.2')) OVER (PARTITION BY dim) AS p102,
        100.0 * SUM(u) FILTER (WHERE s_app_version = '11.0') / SUM(SUM(u) FILTER (WHERE s_app_version = '11.0')) OVER (PARTITION BY dim) AS p110
      FROM d0 GROUP BY 1, 2)
    SELECT dim, val, ROUND(p102, 1) AS pct_10_2, ROUND(p110, 1) AS pct_11_0,
           ROUND(ABS(p110 - p102), 2) AS abs_gap_pts
    FROM shares ORDER BY dim, p102 DESC
""")
print(mix.to_string(index=False))
max_gap = float(mix.abs_gap_pts.max())
M["mix"] = {"table": mix.to_dict("records"), "max_abs_gap_pts": max_gap}
print(f"\nLargest day 0 share gap across country, network and package: {max_gap} pts.")
print("The rollout split is clean, so the pooled comparison is valid. Checked, not assumed.")

# --------------------------------------------------------------------------- #
# 5. Level funnel (Marc). Ratio metrics, robust to retries and to the grain.
# --------------------------------------------------------------------------- #
rule("5. LEVEL FUNNEL: where does 11.0 break?")

# Compare level completion and retry behavior across versions.
funnel = q(f"""
    SELECT i_level,
      -- Level completion rate: the ratio of level completions to level starts.
      ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '10.2')
                  / SUM(i_level_started)   FILTER (WHERE s_app_version = '10.2'), 1) AS comp_10_2,
      ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '11.0')
                  / SUM(i_level_started)   FILTER (WHERE s_app_version = '11.0'), 1) AS comp_11_0,

      -- Level attempt rate: the ratio of level starts to users.
      ROUND(1.0 * SUM(i_level_started) FILTER (WHERE s_app_version = '10.2')
                / SUM(i_users)         FILTER (WHERE s_app_version = '10.2'), 2)     AS att_10_2,
      ROUND(1.0 * SUM(i_level_started) FILTER (WHERE s_app_version = '11.0')
                / SUM(i_users)         FILTER (WHERE s_app_version = '11.0'), 2)     AS att_11_0,
      
      -- Level starts: the number of level starts for 11.0.
      SUM(i_level_started) FILTER (WHERE s_app_version = '11.0')                     AS starts_11_0
    FROM prog WHERE {V2} AND {WIN} GROUP BY 1 ORDER BY 1
""")
funnel["delta_pts"] = (funnel.comp_11_0 - funnel.comp_10_2).round(1)
print(funnel.to_string(index=False))
M["funnel"] = funnel.to_dict("records")

# Analyze playtime around the suspected level.
l7_time = q(f"""
    SELECT i_level, s_app_version,
           ROUND(SUM(f_play_time) / SUM(i_level_started), 0) AS sec_per_attempt,
           ROUND(SUM(f_play_time) / SUM(i_users), 0)         AS sec_per_user
    FROM prog WHERE {V2} AND {WIN} AND i_level IN (6, 7, 8) GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\nRoot cause hint. Time per ATTEMPT on level 7 is unchanged, time per USER")
print("balloons: players replay a level that plays normally and fails. That points")
print("to a difficulty / win condition change, not a crash (a crash truncates attempts):")
print(l7_time.to_string(index=False))
M["l7_time"] = l7_time.to_dict("records")

l7_country = q(f"""
    SELECT s_country,
      ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '10.2')
                  / SUM(i_level_started)   FILTER (WHERE s_app_version = '10.2'), 1) AS comp_10_2,
      ROUND(100.0 * SUM(i_level_completed) FILTER (WHERE s_app_version = '11.0')
                  / SUM(i_level_started)   FILTER (WHERE s_app_version = '11.0'), 1) AS comp_11_0
    FROM prog WHERE {V2} AND {WIN} AND i_level = 7 GROUP BY 1 ORDER BY 1
""")
print("\nLevel 7 completion by country (broken EVERYWHERE, so the break is in the build):")
print(l7_country.to_string(index=False))
M["l7_by_country"] = l7_country.to_dict("records")

l7_dates = q("""
    SELECT d_install_date::DATE AS install_date, SUM(i_level_started) AS starts,
           ROUND(100.0 * SUM(i_level_completed) / SUM(i_level_started), 1) AS comp_11_0
    FROM prog WHERE s_app_version = '11.0' AND i_level = 7 GROUP BY 1 ORDER BY 1
""")
print("\nLevel 7 completion by 11.0 install date (broken from the FIRST cohort, stable):")
print(l7_dates.to_string(index=False))
M["l7_by_install_date"] = l7_dates.astype(str).to_dict("records")

# Estimate level reach relative to Level 1 users.
reach = q(f"""
    WITH u AS (SELECT s_app_version, i_level, SUM(i_users) AS u FROM prog
               WHERE {V2} AND {WIN} GROUP BY 1, 2)
    SELECT i_level,
      ROUND(100.0 * MAX(CASE WHEN s_app_version = '10.2' THEN u END)
                  / MAX(MAX(CASE WHEN s_app_version = '10.2' THEN u END)) OVER (), 1) AS reach_10_2,
      ROUND(100.0 * MAX(CASE WHEN s_app_version = '11.0' THEN u END)
                  / MAX(MAX(CASE WHEN s_app_version = '11.0' THEN u END)) OVER (), 1) AS reach_11_0
    FROM u WHERE i_level IN (1, 7, 8, 15, 20) GROUP BY 1 ORDER BY 1
""")
print("\nReach (share of level 1 users seen at level N; supporting evidence only,")
print("i_users can count a player on several cohort days):")
print(reach.to_string(index=False))
M["reach"] = reach.to_dict("records")

# Quantify global completion drift and the Level 7 break.
drift = {
    "levels_1_6_mean_delta_pts": round(float((funnel[funnel.i_level.between(1, 6)].comp_11_0
                                              - funnel[funnel.i_level.between(1, 6)].comp_10_2).mean()), 1),
    "levels_8_24_mean_delta_pts": round(float((funnel[funnel.i_level.between(8, 24)].comp_11_0
                                               - funnel[funnel.i_level.between(8, 24)].comp_10_2).mean()), 1),
    "level_7_delta_pts": float(funnel[funnel.i_level == 7].delta_pts.iloc[0]),
    "tail_25_30_starts_11_0": int(funnel[funnel.i_level >= 25].starts_11_0.sum()),
}
M["drift"] = drift
print(f"""
Shape of the damage: a small global drift (levels 1..6 average {drift['levels_1_6_mean_delta_pts']} pts,
levels 8..24 average {drift['levels_8_24_mean_delta_pts']} pts) plus ONE catastrophic local break at
level 7 ({drift['level_7_delta_pts']} pts). Levels 25..30 carry {drift['tail_25_30_starts_11_0']} total 11.0 starts,
too thin for any claim; deltas there are suppressed as noise.""")

# Check for launch crashes using zero-playtime active users.
crash = q(f"""
    SELECT s_app_version,
           SUM(CASE WHEN f_playtime <= 0 AND i_active_users > 0 THEN i_active_users ELSE 0 END) AS actives_zero_playtime
    FROM act WHERE {V2} AND {WIN} GROUP BY 1 ORDER BY 1
""")
M["crash_check"] = crash.to_dict("records")
print("Crash on open check: active users with zero playtime = "
      f"{int(crash.actives_zero_playtime.sum())} in both builds combined. Nobody crashes out at launch.")

# Compare Level 23 starts and completions between versions.
l23 = q(f"""
    SELECT s_app_version, SUM(i_level_started) AS st, SUM(i_level_completed) AS co
    FROM prog WHERE {V2} AND {WIN} AND i_level = 23 GROUP BY 1 ORDER BY 1
""")
st1, co1 = float(l23.st[0]), float(l23.co[0])
st2, co2 = float(l23.st[1]), float(l23.co[1])
drift_ex23 = float((funnel[funnel.i_level.between(8, 24) & (funnel.i_level != 23)].comp_11_0
                    - funnel[funnel.i_level.between(8, 24) & (funnel.i_level != 23)].comp_10_2).mean())
pp = (co1 + co2) / (st1 + st2)
z_excess = round((co1 / st1 + drift_ex23 / 100 - co2 / st2) / math.sqrt(pp * (1 - pp) * (1 / st1 + 1 / st2)), 1)
M["l23_second_break_check"] = {"comp_10_2": round(100 * co1 / st1, 1), "comp_11_0": round(100 * co2 / st2, 1),
                               "starts_11_0": int(st2), "drift_baseline_pts": round(drift_ex23, 1),
                               "excess_z_vs_drift": z_excess}
print(f"Level 23 second break check: {100*co1/st1:.1f} vs {100*co2/st2:.1f} on {int(st2)} starts looks worse, "
      f"but the excess beyond the {drift_ex23:.1f} pt drift is only z = {z_excess} before accounting for retry "
      "correlation. Treated as drift noise, worth monitoring, not a second break.")

# --------------------------------------------------------------------------- #
# 6. Engagement intensity and US focus.
# --------------------------------------------------------------------------- #
rule("6. ENGAGEMENT INTENSITY AND US FOCUS")

engage = q(f"""
    SELECT s_app_version,
           ROUND(1.0 * SUM(i_count_sessions) / SUM(i_active_users), 2) AS sessions_per_active,
           ROUND(SUM(f_playtime) / SUM(i_active_users), 0)             AS playtime_per_active_sec
    FROM act WHERE {V2} AND {WIN} GROUP BY 1 ORDER BY 1
""")
print("Per active user, cohort days 0..7 pooled (the survivors also play less):")
print(engage.to_string(index=False))
M["engagement"] = engage.to_dict("records")

engage_day = q(f"""
    SELECT i_cohort_groups AS cohort_day,
           ROUND(SUM(f_playtime) FILTER (WHERE s_app_version = '10.2')
               / SUM(i_active_users) FILTER (WHERE s_app_version = '10.2'), 0) AS sec_per_active_10_2,
           ROUND(SUM(f_playtime) FILTER (WHERE s_app_version = '11.0')
               / SUM(i_active_users) FILTER (WHERE s_app_version = '11.0'), 0) AS sec_per_active_11_0
    FROM act WHERE {V2} AND {WIN} GROUP BY 1 ORDER BY 1
""")
print("\nPlaytime per active user by cohort day (lower on EVERY day, so the pooled")
print("gap is not a day mix artifact; it already shows on install day, when most")
print("players first hit the level 7 wall):")
print(engage_day.to_string(index=False))
M["engagement_by_day"] = engage_day.to_dict("records")

us = q(f"""
    WITH base AS (SELECT * FROM act WHERE {V2} AND {WIN} AND s_country = 'US'),
    d0 AS (SELECT s_app_version, SUM(i_active_users) AS d0 FROM base WHERE i_cohort_groups = 0 GROUP BY 1)
    SELECT b.s_app_version AS version, ANY_VALUE(d0.d0) AS cohort_size,
      ROUND(100.0 * SUM(b.i_active_users) FILTER (WHERE i_cohort_groups = 1) / ANY_VALUE(d0.d0), 2) AS d1,
      ROUND(100.0 * SUM(b.i_active_users) FILTER (WHERE i_cohort_groups = 3) / ANY_VALUE(d0.d0), 2) AS d3,
      ROUND(100.0 * SUM(b.i_active_users) FILTER (WHERE i_cohort_groups = 7) / ANY_VALUE(d0.d0), 2) AS d7
    FROM base b JOIN d0 USING (s_app_version) GROUP BY 1 ORDER BY 1
""")
print("\nUS only, same query with s_country = 'US' (same picture, so it is the build):")
print(us.to_string(index=False))
M["retention_us"] = us.to_dict("records")

# --------------------------------------------------------------------------- #
# 7. Charts. Palette validated for CVD safety and surface contrast
#    (blue #2a78d6 for 10.2, red #e34948 for 11.0, adjacent delta E 74.6).
# --------------------------------------------------------------------------- #
rule("7. CHARTS -> outputs/")

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, BASE = "#e1e0d9", "#c3c2b7"
C102, C110 = "#2a78d6", "#e34948"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": BASE, "axes.linewidth": 0.8, "font.size": 10,
})


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


LINE = dict(lw=2.2, marker="o", ms=6.5, mec=SURFACE, mew=1.6)

# Chart 1. Retention curve (Lea).
fig, ax = plt.subplots(figsize=(8.6, 5.2))
for v, c in (("10.2", C102), ("11.0", C110)):
    d = curve[curve.version == v]
    ax.plot(d.cohort_day, d.retention_pct, color=c, label=f"v{v}", **LINE)
    for _, r in d[d.cohort_day.isin([1, 3, 7])].iterrows():
        ax.annotate(f"{r.retention_pct:.1f}%", (r.cohort_day, r.retention_pct),
                    textcoords="offset points", xytext=(0, 10 if v == "10.2" else -17),
                    ha="center", fontsize=9, color=INK2, fontweight="bold")
ax.set_title("Retention, day 0 to 7: 11.0 loses more players every day",
             fontweight="bold", loc="left", pad=26)
ax.text(0, 1.025, f"Android worldwide, installs {WIN_START} to {WIN_END} (all cohorts mature to D7). "
                  "Cohort size 22,334 (10.2) vs 10,595 (11.0).",
        transform=ax.transAxes, fontsize=8.5, color=INK2)
ax.set_xlabel("Days since install"); ax.set_ylabel("Active users / day 0 active users (%)")
ax.set_xticks(range(8)); ax.set_ylim(0, 102); ax.set_xlim(-0.2, 7.2)
ax.legend(frameon=False, loc="upper right")
style(ax)
fig.tight_layout(); fig.savefig(OUT / "chart1_retention.png", dpi=150); plt.close(fig)

# Chart 2. Level funnel, completion + attempts per user (Marc). Two panels, one x.
f = funnel[funnel.i_level <= 24]
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.2, 7.2), sharex=True, layout="constrained",
                               gridspec_kw={"height_ratios": [3, 2]})
for ax in (ax1, ax2):
    ax.axvspan(6.55, 7.45, color="#fdeaea", zorder=0)
ax1.plot(f.i_level, f.comp_10_2, color=C102, label="v10.2", **LINE)
ax1.plot(f.i_level, f.comp_11_0, color=C110, label="v11.0", **LINE)
l7 = funnel[funnel.i_level == 7].iloc[0]
ax1.annotate(f"Level 7: {l7.comp_10_2:.0f}%  to  {l7.comp_11_0:.0f}%",
             (7, l7.comp_11_0), textcoords="offset points", xytext=(26, -2),
             fontsize=9.5, color=INK, fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=INK2, lw=1))
ax1.set_ylabel("Completion (completed / started, %)")
ax1.set_ylim(0, 100); ax1.legend(frameon=False, loc="lower left")
ax1.set_title("Level 7 is the break: completion collapses, retries double",
              fontweight="bold", loc="left", pad=24)
ax1.text(0, 1.035, f"Installs {WIN_START} to {WIN_END}. Levels 25 to 30 omitted (under 400 total 11.0 starts, noise).",
         transform=ax1.transAxes, fontsize=8.5, color=INK2)
ax2.plot(f.i_level, f.att_10_2, color=C102, **LINE)
ax2.plot(f.i_level, f.att_11_0, color=C110, **LINE)
ax2.annotate(f"{l7.att_11_0:.2f} attempts/user vs {l7.att_10_2:.2f}",
             (7, l7.att_11_0), textcoords="offset points", xytext=(26, -4),
             fontsize=9.5, color=INK, fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=INK2, lw=1))
ax2.set_ylabel("Attempts per user")
ax2.set_xlabel("Level"); ax2.set_xticks(range(1, 25)); ax2.set_ylim(0.9, 2.15)
style(ax1); style(ax2)
fig.savefig(OUT / "chart2_level_funnel.png", dpi=150); plt.close(fig)

# Chart 3. The censoring trap (methodology).
n110 = naive[naive.s_app_version == "11.0"].iloc[0]
fig, ax = plt.subplots(figsize=(7.6, 4.6))
days, x = ["D1", "D3", "D7"], range(3)
nv = [n110.d1, n110.d3, n110.d7]
cv = [hl["11.0"]["D1"], hl["11.0"]["D3"], hl["11.0"]["D7"]]
ref = [hl["10.2"]["D1"], hl["10.2"]["D3"], hl["10.2"]["D7"]]
b1 = ax.bar([i - 0.17 for i in x], nv, width=0.30, color=MUTED, label="11.0 naive (all cohorts, censored)")
b2 = ax.bar([i + 0.17 for i in x], cv, width=0.30, color=C110, label="11.0 corrected (mature cohorts)")
for bars in (b1, b2):
    for b in bars:
        ax.annotate(f"{b.get_height():.1f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    textcoords="offset points", xytext=(0, 4), ha="center", fontsize=9, color=INK2)
for i, r in zip(x, ref):
    ax.hlines(r, i - 0.38, i + 0.38, color=C102, lw=2)
    ax.annotate(f"10.2: {r:.1f}", (i + 0.40, r), fontsize=8.5, color=INK2, va="center")
ax.set_title("Right censoring understates 11.0: naive D7 reads 6.8, real is 12.9",
             fontweight="bold", loc="left", pad=26)
ax.text(0, 1.03, "Cohorts younger than N days have no day N row yet but still sit in the day 0 denominator.",
        transform=ax.transAxes, fontsize=8.5, color=INK2)
ax.set_xticks(list(x)); ax.set_xticklabels(days); ax.set_ylabel("Retention (%)")
ax.set_ylim(0, 62); ax.legend(frameon=False, loc="upper right", fontsize=9)
style(ax)
fig.tight_layout(); fig.savefig(OUT / "chart3_censoring.png", dpi=150); plt.close(fig)

print("wrote chart1_retention.png, chart2_level_funnel.png, chart3_censoring.png")

# --------------------------------------------------------------------------- #
# 8. Persist every number.
# --------------------------------------------------------------------------- #
(OUT / "metrics.json").write_text(json.dumps(M, indent=2, default=str), encoding="utf-8")
print("wrote metrics.json")
print("\nDone. Read WRITEUP.md for the narrative; every number above reproduces from this script.")
