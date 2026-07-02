# Build 11.0 Rollout

## Executive Summary

### Objective
Decide whether the new game build, version 11.0, should go to a full rollout for every player. This brief compares 11.0 against the live build, 10.2, and gives a clear recommendation.

### Current Definition
Both builds are live today. 10.2 is the proven build. 11.0 is the new build, released to a share of players. The two ran side by side over the same period, so the comparison is fair. Health is measured by how many players come back and how far they progress in their first week.

## Business Rules
Both builds are judged on the same first week window and the same measures.
A player counts as retained when they return on the measured day.
One install group carried an impossible release date, so it was set aside as unreliable.
The rollout split was even across countries and channels, so the comparison needs no adjustment.

## Key Business Decision
**Should build 11.0 go to a 100% rollout now?**
Today the rollout is partly live and moving toward full release.

**Recommendation**
**Hold the rollout.** Build 11.0 keeps fewer players than 10.2 at every point of the first week, and the gap widens with time. The cause is one broken level, not a broad failure, so a targeted fix can recover it.

## Business Impact
Shipping 11.0 as it stands lowers every health signal against 10.2.

1. Day 1 return falls from **55% to 50%**.
2. Day 7 return falls from **20% to 13%**.
3. Daily play time per player falls from **44 minutes to 31 minutes**.

The damage is concentrated. Completion of level 7 falls from **85% to 40%**, and players hit that wall in every country from the first day of the release.

## Known Issue
Level 7 became far harder in 11.0 and blocks players early in the game. The pattern points to a difficulty or win condition change, not a crash. A rollback or rebalance of that single level is the fix, and it can be checked on a fresh group of players before full release.

## Next steps for Léa

1. Hold the 100% rollout of build 11.0 for now.
2. Roll back or rebalance level 7, then confirm completion returns near its old level.
3. Verify that day 1 and day 3 return recover on a fresh group of players before resuming the ramp.

## For the design team

Pin down what exactly changed on level 7 in build 11.0. Attempts run full and still fail, so the search points to its difficulty or win condition, not to a crash.
