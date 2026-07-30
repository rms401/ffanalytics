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
- **known-unmappable** — disclosed loudly at batch time and on the app's
  Status tab (ffanalytics has no equivalent stat), never silently dropped;
- **unknown** — a key outside the universe hard-stops the batch (means
  Sleeper shipped a new scoring option; update the table).

Note: board points come from ffanalytics' aggregated projection sources, NOT
Sleeper's own projections — numbers will not match the Sleeper app, by design.

## During the draft

- Picks are polled every 500ms; the board re-renders only when picks change.
- Manual "Mark drafted / undrafted" overrides always win over the API
  (pre-mark keepers before the draft starts).
- Picks that can't be matched to a board row show a red banner and are
  resolved (durably) on the Status tab — never silently dropped.
- No internet? The board still serves from SQLite; the poll dot goes red and
  keeps the last good picks; mark picks manually.
