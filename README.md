# Wonders Kingdom Build Quality Analysis

A reproducible pipeline that compares two builds of Wonders Kingdom, version 10.2 and version 11.0, on how well they keep players and how far players progress. One command reads the data, runs the checks, and writes the charts and the numbers.

## What it answers

Is build 11.0 healthier, worse, or the same as 10.2, and why. The short answer is under Results below.

## Requirements

Python 3.10 or newer, plus three packages: `duckdb`, `pandas` and `matplotlib`. They are listed in `requirements.txt`.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
# Full analysis. Rebuilds every number, chart, and the metrics file.
python analysis.py
```

The run prints the whole analysis as it goes: the data checks, the retention read, the level funnel, and the country slice. It writes three charts and one numbers file to `outputs/`.

Optional, run any single query on its own:

```bash
duckdb -c ".read queries/retention.sql"
```

No API key and no network are needed.

## What you get

A run writes these to `outputs/`:

```
chart1_retention.png     how many players return, day 0 to day 7
chart2_level_funnel.png  completion and retries, level by level
chart3_censoring.png     the method check behind the headline
metrics.json             every number the analysis produced, ready to reuse
```

These ship with the project, ready to read:

```
homa_build_quality_report.pdf   the report, charts included
WRITEUP.md                      the one page executive brief
```



## Project layout

```
analysis.py        the pipeline, one entry point
queries/           the four SQL queries, each runnable on its own
outputs/           charts and numbers, created by the run
data/              the two input files go here (see data/README.md)
requirements.txt   dependencies
```



## Results

**Build 11.0 is worse than 10.2. The cause is level 7.**


| Signal                | 10.2   | 11.0   |
| --------------------- | ------ | ------ |
| Day 1 return          | 55.4%  | 50.1%  |
| Day 7 return          | 19.6%  | 12.9%  |
| Level 7 completion    | 85.4%  | 39.6%  |
| Daily play per player | 44 min | 31 min |


Build 11.0 keeps fewer players at every point of the first week, and the gap grows with time. The loss traces to one broken level early in the game, not a broad decline. The one page summary is in `WRITEUP.md`. The full reasoning is in the PDF report.

## How it works

Three choices carry the result.

1. Compare the two builds only over the period when both were live and every group of players is old enough to measure to day 7. This removes a common trap that would otherwise double the apparent damage.
2. Set aside one install group whose release date is impossible, so a labelling error never reaches the headline.
3. Judge level health by completion per attempt, so heavy retries on a hard level never hide the problem.

