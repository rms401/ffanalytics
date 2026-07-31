# Draft-day tool (Sleeper + ffanalytics)

A local draft board: league-true rankings computed by ffanalytics from YOUR
Sleeper league's scoring/rosters, served from SQLite, with drafted players
vanishing live during the draft. Not part of the R package — nothing outside
`draft/` is touched.

## Layout

| file | role |
|---|---|
| `run_projections.R` | batch: scrape -> league-true scoring -> SQLite snapshot |
| `app.R` | Shiny board: filters the snapshot, polls picks every 500ms |
| `sleeper_api.R` | thin Sleeper API client (no auth) |
| `sleeper_scoring.R` | complete Sleeper->ffanalytics scoring translation |
| `vor_baseline.R` | VOR baseline derived from roster slots x team count |
| `crosswalk.R` | MFL id -> Sleeper id (DynastyProcess + name fallback) |
| `gap_fill.R` | estimates scoring stats no ffanalytics rule expresses |
| `db.R` / `schema.sql` | SQLite persistence (`draft/draft.sqlite`, gitignored) |
| `tests/` | `Rscript`-runnable stopifnot tests (no package install needed) |

## Setup

```r
install.packages(c("DBI", "RSQLite", "httr2", "jsonlite", "shiny", "reactable"))
# plus the ffanalytics package itself (this repo) for the batch step
```

## Use

```sh
# before draft day (and again the morning of):
Rscript draft/run_projections.R <league_id>

# tweak scoring/weights without re-scraping every source:
Rscript draft/run_projections.R <league_id> --rescore

# run tests:
Rscript draft/tests/test_translation.R
Rscript draft/tests/test_baseline.R
Rscript draft/tests/test_gap_fill.R
Rscript draft/tests/test_gap_injection.R   # needs the package installed

# during the draft:
R -e 'shiny::runApp("draft", launch.browser = TRUE)'
```

The draft-day dry-run checklist is at the top of `run_projections.R`.

## How scoring translation works

Every nonzero key in the league's `scoring_settings` is classified at build
time (the full Sleeper NFL key universe is encoded in `sleeper_scoring.R`):

- **mapped** — copied into the ffanalytics scoring rules (including exact
  handling for TE premium, 2-pt sets, `idp_tkl` decomposition, and
  points-allowed brackets with optional linear `pts_allow`);
- **known-unmappable** — no ffanalytics rule exists, so `gap_fill.R` estimates
  the stat from Sleeper's own historical stats endpoint (see below);
- **unknown** — a key outside the universe hard-stops the batch (means
  Sleeper shipped a new scoring option; update the table).

Note: board points come from ffanalytics' aggregated projection sources, NOT
Sleeper's own projections — numbers will not match the Sleeper app, by design.

## How gap fill works

A nonzero scoring rule that contributes nothing is a silently wrong ranking, so
every one of them has to land somewhere. Three things happen before estimation
is even considered:

1. **Map it.** `st_td` (Sleeper's player special-teams TD) is exactly
   ffanalytics `return_tds`, so it is mapped, not estimated.
2. **Let the package fill it.** `impute_bonus_cols()` already synthesises
   `rec_40_yds`, `rush_40_yds` and the `*_100/150/200/300/400_yds` families from
   `bonus_col_coefs`. Gap fill never writes those — that would double-count.
3. **Estimate what is left**, through one declarative table in `gap_spec()`:

   ```
   estimate = rate(player) x projected_driver
   rate     = (hist_num + k * pos_rate) / (hist_den + k)      # empirical Bayes
   ```

   Rates are fit from `api.sleeper.app/v1/stats/nfl/regular/{season}` pooled over
   three seasons — the same stat keys the league scores. Players with no history
   get the position rate. It is closed-form arithmetic with no RNG, so two runs
   on the same inputs are byte-identical.

Rows come in two modes. `new_rule` (Class A) creates both the stat column and
the scoring rule — always under `misc`, because a TE-premium league nests `rec`
per position and a key added there is silently dropped by
`make_scoring_tables()`. `fill_column` (Class B) fills an EXISTING ffanalytics
column that no source in this scrape projected, and adds no rule; if any source
does project it, the estimate stands down.

Adding support for a new Sleeper key is one row in `gap_spec()`, not new code.

### The gate

The batch stops if a rule is mapped or estimated yet contributes exactly zero —
that means the column never reached the scoring table, which is a bug at any
size. A rule with no estimator only stops the batch if its measured impact
exceeds `GAP_MATERIALITY_TOL` (0.5 season points for a top-50 player at the
affected position); below that it is recorded, not ignored. Every nonzero key
lands in `league.gap_method_json` with its method and measured points, and the
app's Status tab shows the whole table.

### Calibration warning

The batch also prints a band-matched comparison of the package's bonus columns
against what players historically actually did. As of the 2026 season the
`*_40_yds`, `pass_300_yds` and `pass_400_yds` columns run roughly 2.5-4.5x
higher than reality, because `bonus_col_coefs` was fit per game and is applied
to season totals. This is reported, never overridden — but in a league that
scores those bonuses it inflates the affected players.

## During the draft

- Picks are polled every 500ms; the board re-renders only when picks change.
- Manual "Mark drafted / undrafted" overrides always win over the API
  (pre-mark keepers before the draft starts).
- Picks that can't be matched to a board row show a red banner and are
  resolved (durably) on the Status tab — never silently dropped.
- No internet? The board still serves from SQLite; the poll dot goes red and
  keeps the last good picks; mark picks manually.
