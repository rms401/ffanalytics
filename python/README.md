# ffanalytics (Python)

A Python port of the [`ffanalytics`](https://github.com/FantasyFootballAnalytics/ffanalytics)
R package that lives in the root of this repository. It scrapes projected stats
from the same public sources, calculates projected points under your league's
scoring rules, and aggregates the sources into a ranked projections table.

Nothing outside this folder is modified. The R package is untouched and remains
the reference implementation; this port reads its internal data file
(`../R/sysdata.rda`) directly rather than duplicating it.

## Install

```bash
cd python
pip install -e .
```

Requires Python 3.9+. Dependencies: pandas, numpy, scipy, requests, lxml,
cssselect, openpyxl.

## Usage

The API mirrors the R package's exports.

```python
import ffanalytics as ffa

# season = None grabs the current season, week = None the current week.
# week = 0 means season-long projections.
scrape = ffa.scrape_data(
    src=["CBS", "NFL", "FanDuel"],
    pos=["QB", "RB", "WR", "TE", "DST"],
    season=None,
    week=None,
)

projections = ffa.projections_table(scrape)
```

`scrape` is a mapping of position to `DataFrame` — one row per player per
source, with a `data_src` column — plus the `season` and `week` it was for.

`projections_table` returns points, spread, 5th/95th percentile floor and
ceiling, positional rank, drop-off, tier, and value over replacement, under
three averaging methods (`average`, `robust`, `weighted`). Restrict them with
`avg_type=`, and pass `return_raw_stats=True` to aggregate the underlying stats
instead of fantasy points.

Add rankings, draft data and player details:

```python
projections = ffa.add_ecr(projections)          # must come before add_uncertainty
projections = ffa.add_adp(projections)
projections = ffa.add_aav(projections)
projections = ffa.add_uncertainty(projections)
projections = ffa.add_player_info(projections)

projections.df.sort_values("rank").head(20)
```

### Scoring

`ffa.scoring` is the default rule set. Build your own with `custom_scoring`,
which takes stat values directly and per-position overrides as dicts:

```python
rules = ffa.custom_scoring(
    pass_yds=0.04, pass_tds=4, pass_int=-3,
    rush_yds=0.1, rush_tds=6,
    RB={"rec": 1, "rec_yds": 0.1, "rec_tds": 6},
    WR={"rec": 1, "rec_yds": 0.1, "rec_tds": 6},
    TE={"rec": 1.5, "rec_yds": 0.1, "rec_tds": 6},   # TE premium
)
rules["pts_bracket"] = ffa.scoring["pts_bracket"]     # DST points allowed

ffa.projections_table(scrape, scoring_rules=rules)
```

As in R, `custom_scoring` does not add a `pts_bracket`; add one yourself if you
score DSTs.

**Scoring DST points allowed.** The bracket result is written into the
`dst_pts_allowed` column, which is then multiplied by that stat's scoring value
— and the package default for it is `0`. A league that scores points allowed by
bracket must set `dst_pts_allowed` to `1`, or the bracket contributes nothing.
This is the R package's behaviour, reproduced here.

## Caching

Scraped data is cached on disk, as in R: ADP/AAV and ECR for 8 hours,
projection scrapes for 1 hour.

```python
ffa.list_ffanalytics_cache()
ffa.clear_ffanalytics_cache()
```

The cache lives under `~/.cache/ffanalytics/python` (override with
`FFANALYTICS_CACHE_DIR`). It is deliberately **not** shared with the R package's
cache: R writes `.rds` files, which Python cannot read or write, so a shared
directory would leave each side with a false view of what is cached.

## How this maps onto the R package

| R | Python |
|---|---|
| `R/scrape_funcs.R` | `ffanalytics/scrape_funcs.py` |
| `R/source_scrapes.R` | `ffanalytics/source_scrapes/` (one module per site) |
| `R/source_objects.R` | `ffanalytics/source_objects.py` |
| `R/calc_projections.R` | `ffanalytics/calc_projections.py` |
| `R/scoring_rules.R`, `R/custom_scoring.R` | `ffanalytics/scoring_rules.py`, `custom_scoring.py` |
| `R/impute_funcs.R` | `ffanalytics/impute_funcs.py` |
| `R/helper_funcs.R` | `ffanalytics/helper_funcs.py` |
| `R/adp_functions.R` | `ffanalytics/adp_functions.py` |
| `R/scrape_ecr.R` | `ffanalytics/scrape_ecr.py` |
| `R/caching_helpers.R` | `ffanalytics/caching.py` |
| `R/recode_vars.R` | `ffanalytics/recode_vars.py` |
| `R/schedule_data.R` | `ffanalytics/schedule_data.py` |
| `R/get_league_info.R` | `ffanalytics/get_league_info.py` |
| `R/sysdata.rda` | read in place by `ffanalytics/rdata.py` + `sysdata.py` |
| — | `ffanalytics/rcompat/` (R semantics: RNG, ranks, quantiles) |

### Deliberate differences

These are the only places where the Python API departs from R's, and each is
forced rather than chosen:

- **`get_mfl_id` takes an explicit `id_col_name`.** R derives the crosswalk
  column from the text of the calling expression via
  `deparse(substitute(id_col))`. Python has no equivalent, so callers name the
  column. Behaviour is unchanged, including the two call sites where R's
  derived name is not a real crosswalk column and the lookup silently falls
  through to name matching.
- **Season/week/league type ride on wrapper objects** (`ScrapeResult`,
  `ProjectionsTable`) rather than attributes. R attaches them as attributes and
  every `add_*` function re-attaches them by hand because dplyr drops them;
  pandas' `DataFrame.attrs` is just as lossy, so the contract is made explicit.
  `ProjectionsTable` proxies attribute access to the frame, and `.df` gets you
  the frame itself.
- **The cache uses pickle in its own directory**, for the reason above.

### Reproduced R behaviours worth knowing about

The port mirrors the R package rather than correcting it. Each of these is
marked in the code with the R line it comes from:

- `scrape_fantasypros` writes its cache under WalterFootball's filename
  (`R/source_scrapes.R:1187`), so the two sources share one cache slot.
- `scrape_fanduel` tests `week` when defaulting `season`
  (`R/source_scrapes.R:1457`).
- `clean_scoring_sleeper` assembles a scoring object and never returns it
  (`R/get_league_info.R:34-69`); the Sleeper integration is experimental in R.
- `impute_fun_list` defines `rec_tgt` twice; R's `[[` returns the first, so the
  second entry — evidently meant for `rec` — never runs, and `rec` falls through
  to the plain column mean.
- The `fg_1019` branch of the `fg_0019` imputation is unreachable in R: it tests
  `names(df)` where `df` is not an argument, so R resolves it to `stats::df`, a
  function whose `names()` is `NULL`.
- `make_scoring_tables` rebinds its shared table inside the position loop, so
  DL, LB and DB inherit DST's `pts_bracket` row.
- `score_pts_bracket` falls back to the *first* bracket for a value above every
  threshold, because `max.col(..., "first")` returns column 1 for an all-false
  row.

### Reproducibility of the DST simulation

Season-long DST points allowed are simulated with `set.seed(1)` in R.
`ffanalytics/rcompat/rng.py` reimplements R's seeding, Mersenne-Twister, and
inversion-based `rnorm` (Wichura's AS241 `qnorm`), so the draws match R's bit
for bit. The tests pin this against R's published `set.seed` output — if they
fail, the simulation cannot be trusted.

## Not ported

Scope is the R package's live functionality. Left out, deliberately:

- **`R/to_be_deprecated.R`** — 1000 lines of the superseded v2 pipeline
  (`add_risk`, `set_vor`, `projected_points`, `scrape_source`, …). None of it is
  exported; `add_uncertainty` replaced `add_risk` in v3.
- **`actual_points_scoring`** — internal, and depends on the `nflfastR` R
  package, which is optional even in R. There is no bundled Python equivalent.
- **`data/nfl_cols.rda` and `data/projection_sources.rda`** — exported datasets
  that no R code references. `projection_sources` holds the retired v2 R6
  objects; `ffanalytics/rdata.py` refuses files containing R code objects rather
  than decoding them into something misleading.

## Source availability

Same sources as R. Two are affected by the state of the sites themselves, not by
the port:

- **NumberFire** now redirects to a client-rendered FanDuel Research app with no
  HTML tables, so the scraper returns nothing. The R package already routes
  around this — `scrape_data` rewrites `NumberFire` to `FanDuel`.
- **FantasySharks** has no season-to-segment mapping past 2025 (the R package's
  table stops there), and the site currently returns HTTP 403 to datacenter IPs
  regardless of user agent.

Historical seasons generally do not scrape successfully, as the R README notes.

## Tests

```bash
python -m pytest tests -m "not network"   # offline, deterministic
python -m pytest tests -m network         # live scrape + full pipeline
```

The offline suite covers the RData reader against the real `sysdata.rda`, the R
semantics layer against R's published values, scoring and imputation on
hand-computed fixtures, and the full aggregation pipeline on synthetic scrapes.

If you have R available, the R and Python results can be compared directly:

```r
library(ffanalytics)
s <- scrape_data(src = c("CBS","NFL"), pos = c("QB","RB"), season = 2025, week = 0)
projections_table(s, avg_type = "average")
```

```python
import ffanalytics as ffa
s = ffa.scrape_data(src=["CBS","NFL"], pos=["QB","RB"], season=2025, week=0)
ffa.projections_table(s, avg_type="average").df
```

## Licence

GPL, matching the R package.
