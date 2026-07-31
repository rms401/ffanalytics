# ffanalytics

Fantasy football projections, scraped from the public projection sites and
scored for **your** league.

A generic projection is only half an answer. A quarterback worth 4 points a
passing touchdown is not the same asset at 3; a tight end with a reception
bonus is not the same asset without one; and "value over replacement" means
nothing until you know how many of each position twelve teams actually start.
So this package pulls both halves and puts them together:

1. **Scrape** projected stat lines from every site that publishes them.
2. **Read your Sleeper league** — its scoring settings, its roster slots, and
   who already owns whom.
3. **Combine them** into one ranked table: projected points under your rules,
   how much the sources disagree, floor and ceiling, positional rank, tier,
   drop-off, value over replacement, expert consensus rank, ADP, and who has
   the player rostered.

```bash
pip install -e .
python -m ffanalytics --league 1328634493078110208 --out projections.csv
```

```
People's Republic of Collusion (2026) -- 12 teams
  starting slots: QB, RB, RB, WR, WR, TE, FLEX, SUPER_FLEX, K
  bench: 6
  scoring: 1 PPR, TE premium (2.5), 3 pt pass TD, 0.05/pass yd, superflex
  replacement level: K12, QB24, RB24, TE23, WR25
  sources used: CBS, ESPN, FFToday, RTSports, WalterFootball

 rank pos  pos_rank  tier              player team  points  points_vor  floor  ceiling  dropoff   adp
    1  TE         1     1        Trey McBride  ARI   407.7       184.4  325.8    451.5      3.0  29.7
    2  TE         2     1        Brock Bowers   LV   404.7       181.4  380.9    421.0     66.3  27.2
    3  RB         1     1        Jahmyr Gibbs  DET   381.3       175.5  369.7    399.3      2.9   2.5
    ...
```

Find your league id in the Sleeper URL — `sleeper.com/leagues/<LEAGUE_ID>/team`
— or look it up by name:

```bash
python -m ffanalytics --user your_sleeper_name
```

## What the league actually changes

**Scoring.** Sleeper's settings are translated into point values per stat.
Reception bonuses land on the right position, field goal values combine the
flat rate with the distance bonus, and the points-allowed brackets come across
in order.

First downs are not published by any site, but they track yardage closely
enough to estimate: rushing first downs at 0.0508 per rushing yard, receiving
first downs at 0.0450 (RB), 0.0483 (WR) and 0.0503 (TE) per receiving yard.
Passing first downs have no rate behind them and stay unscored.

What is genuinely unprojectable — how long a touchdown was, mostly — is
**reported, not silently dropped**:

```
  scoring settings with no projectable stat (points your league awards
  that no source projects):
    pass_td_50p = 2
    rush_td_40p = 1
    ...
```

so you know exactly which parts of your scoring the table is blind to.

**Replacement level.** Dedicated slots are arithmetic: twelve teams starting
one tight end means the thirteenth is replaceable. Flex slots are not — whether
a superflex holds a quarterback or a running back depends on who is worth more.
So flex slots are filled greedily from the projections themselves, and each
position's replacement rank is however many of them ended up starting. In the
superflex league above that puts quarterback replacement level at QB24 rather
than QB13, which is the whole reason quarterbacks cost what they do there.

**Availability.** Rostered players are tagged with who holds them, so
`--available-only` shows just the players you can actually get.

## Using it as a library

```python
import ffanalytics as ffa

result = ffa.build_league_projections("1328634493078110208")
result.table                  # the whole thing
result.available              # only players nobody rosters
result.top(20, position="RB")
print(result.report())
```

The pieces work on their own too:

```python
scrape = ffa.scrape_data(sources=["CBS", "ESPN"], positions=["QB", "RB"], week=0)
scrape.summary()                      # rows per source per position
scrape["QB"]                          # one row per player per source

rules, unscored = ffa.scoring_rules_from_sleeper(settings)
ffa.projections_table(scrape, rules, avg_type="robust")
```

Or build scoring by hand, with no Sleeper involved:

```python
from ffanalytics import ScoringRules, PointsAllowedTier

rules = ScoringRules(
    stats={"pass_yds": 0.04, "pass_tds": 4, "pass_int": -2,
           "rush_yds": 0.1, "rush_tds": 6,
           "rec": 1, "rec_yds": 0.1, "rec_tds": 6},
    by_pos={"TE": {"rec": 1.5}},                       # TE premium
    pts_bracket=(PointsAllowedTier(0, 10), PointsAllowedTier(13, 4),
                 PointsAllowedTier(float("inf"), -4)),
)
```

## The columns

| Column | What it is |
|---|---|
| `points` | the central estimate across sources, under your scoring |
| `sd_pts` | how much the sources disagree |
| `floor` / `ceiling` | 5th and 95th percentiles of what they project |
| `dropoff` | points lost by taking the next player at the position instead |
| `points_vor` | points above a freely available player at that position |
| `rank` / `pos_rank` | overall by value over replacement, and within position |
| `tier` | players grouped by where the meaningful gaps fall |
| `uncertainty` | 1-99, combining source disagreement with ranker disagreement |
| `pos_ecr` / `sd_ecr` | FantasyPros expert consensus rank, and its spread |
| `adp` / `adp_diff` | average draft position, and how it differs from `rank` |
| `rostered_by` / `starting` | who holds the player in your league |
| `sources` | how many sites projected this player |

`--avg-type` picks how the sources are combined: `average` (the plain mean),
`robust` (resists one site being far out on its own), or `weighted` (uses each
site's published accuracy weight).

## Sources

| Source | Season-long | Weekly | Notes |
|---|---|---|---|
| CBS | yes | yes | |
| ESPN | yes | yes | IDP needs `espn_league_id=` |
| FFToday | yes | yes | |
| FanDuel | yes | yes | formerly NumberFire; nothing published out of season |
| FantasySharks | yes | yes | blocks datacenter IPs, so often empty from a server |
| NFL | yes | yes | publishes late in the off-season |
| RTSports | yes | — | |
| WalterFootball | yes | — | one spreadsheet a year |
| FleaFlicker | — | yes | no published accuracy weight, so it sits out `weighted` |
| FantasyPros | yes | yes | a consensus of the others, and capped at ten rows a position — **not scraped by default** |

A site that is down, blocked, or has not published yet is reported and skipped;
you get everything the rest of them had. `--list-sources` prints this table.

Expert consensus rankings come from FantasyPros' rankings pages, which are not
capped, and ADP is pooled from RTSports, CBS, Yahoo, NFL, FantasyFootballCalculator,
MyFantasyLeague and ESPN.

## Caching

Scrapes are cached for an hour, rankings and ADP for eight hours, and Sleeper's
player list for a day (they ask for no more than one call a day). `--refresh`
ignores the cache, `--clear-cache` empties it, and `FFANALYTICS_CACHE_DIR` moves
it (default `~/.cache/ffanalytics`).

## Layout

```
ffanalytics/
  scrape.py        pull every site, stack by position
  sources/         one module per site, plus the column maps
  sleeper.py       league, scoring translation, rosters, player list
  scoring.py       ScoringRules: point values per stat
  impute.py        fill the stats a source did not report
  projections.py   score, aggregate, rank, tier, value over replacement
  league.py        put the scrape and the league together
  ecr.py, adp.py   expert rankings and draft position
  players.py       the player universe and the id resolver
  stats.py         the numeric helpers behind the averages
  cache.py         time-to-live cache on disk
  data/            player id crosswalk and two fitted models
```

`data/` is a snapshot of the four internal tables the R package keeps in
`R/sysdata.rda`: the player id crosswalk, the milestone-bonus regressions, the
nesting rules for those bonuses, and the per-team model of how much a defense's
points allowed swings week to week. The last two came from models fitted
against play-by-play data in R and cannot be regenerated here, so they are
carried as data. Nothing in this folder reads outside it.

## Tests

```bash
python -m pytest tests -m "not network"   # fast, offline, deterministic
python -m pytest tests -m network         # hits every site and the Sleeper API
```

The offline suite covers the scoring translation, the aggregation maths, the
imputation rules and replacement-level allocation on fixtures. The network
suite is what tells you a site has changed its markup: it scrapes each source
in turn and asserts the rows come back identified, unduplicated and attached to
real players, then runs the whole pipeline against a live league.

## Relationship to the R package

This began as a direct port of the [`ffanalytics` R
package](https://github.com/FantasyFootballAnalytics/ffanalytics) in the root of
this repository, and the scraping logic, the source accuracy weights, the
imputation approach and the projection maths are all still its work. The Python
side has since been simplified and pointed at Sleeper; it no longer tracks the R
API call for call, and a handful of quirks faithfully reproduced from R have
been corrected rather than carried:

- points allowed above every bracket scores the *worst* bracket, not the best;
- the points-allowed bracket applies to team defenses only, not to IDP
  positions as well;
- receptions are imputed from receiving yards rather than falling through to a
  plain column mean;
- estimated bonus columns are kept for every position, not only those with a
  nested threshold to roll up;
- each source gets its own cache slot;
- FFToday's player ids are read from the links they belong to, so a page's
  navigation link no longer shifts every id by one.

Season-long defensive points allowed are simulated (a season total cannot be
run through a weekly bracket once). R seeded that with `set.seed(1)`; this uses
NumPy's generator with a fixed seed, so it is reproducible here but does not
match R draw for draw.

## Licence

GPL, matching the R package.
