# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python package that scrapes fantasy football projections from public sites,
scores them under a Sleeper league's actual rules, and ranks the result
(points, VOR, tiers, floor/ceiling, uncertainty, ECR, ADP). It began as a port
of the FantasyFootballAnalytics R package; the R code has been removed from
this repo and the Python package is the whole project. A handful of R quirks
were deliberately corrected rather than ported — they are listed in README.md
("Relationship to the R package"); do not "fix" them back to R behavior.

## Commands

```bash
pip install -e ".[web]"        # core install is `pip install -e .`; [web] adds FastAPI+uvicorn
python -m ffanalytics --league <LEAGUE_ID> --db draft.sqlite   # full run: scrape, score, write SQLite
python -m ffanalytics --user <sleeper_username>                # find a league id
python -m ffanalytics --list-sources                           # what each site covers
python -m ffanalytics --league <ID> --db draft.sqlite --refresh-picks  # fast draft-pick refresh (~1s)
python -m ffanalytics --league <ID> --db draft.sqlite --serve  # draft-board web UI at 127.0.0.1:8000
python -m compileall -q ffanalytics                            # syntax check
```

**There is no test suite, no linter config, and no CI.** `compileall`, a
`--list-sources` smoke run, and exercising the code directly are the
verification tools available.

A full run does real network I/O: it scrapes every source site (rate-limited),
the Sleeper API, FantasyPros ECR, and ADP sources. Nothing is cached by
design — keep full runs out of loops; only `--refresh-picks` (or the `--serve`
background loop, which calls the same function) is built to run repeatedly.

## Architecture

Data flow: `sources/*` (one scraper per site, column maps in
`sources/columns.py`) → `scrape.py` stacks them per position → `sleeper.py`
translates the league's scoring settings and roster slots → `impute.py` fills
stats a site didn't publish (missing ≠ zero; published zeros are kept) →
`projections.py` scores, aggregates across sources (average/robust/weighted),
ranks, tiers, VOR → `league.py` (`build_league_projections`) orchestrates all
of it and attaches ownership → `db.py` writes everything to one SQLite file →
`web/` serves that file as the draft board.

Key invariants:

- **The SQLite file is the current picture, never a history.** Every table is
  dropped and rewritten per run; `db.refresh_picks()` rewrites only
  `ownership` and `meta.written_at` so it can loop during a draft. The web UI
  (`web/server.py`) is read-only over this file and re-reads it on every
  request, so external writes show up on the next poll.
- **`ffanalytics/data/` is carried, not generated.** `bonus_cols.json` and
  `pts_allowed_sd_coefs.csv` are snapshots of models fitted in the old R
  package against play-by-play data and cannot be regenerated here;
  `qb_games.csv` is refreshed by hand from Razzball snap projections.
- **Replacement level is computed, not configured**: dedicated slots are
  arithmetic, flex/superflex slots are filled greedily from the projections
  themselves (`league.replacement_ranks`).
- Weekly-bracket defensive scoring for season totals is simulated with a
  fixed NumPy seed — reproducible, but don't expect R-identical draws.
- Sources that fail (blocked, unpublished, down) are reported and skipped,
  never fatal; FantasySharks in particular blocks datacenter IPs.

## Web UI (`ffanalytics/web/`)

FastAPI app (`server.py`) + one static vanilla-JS page (`static/`), no build
step. `--serve` requires an existing `--db` from a full run. Two endpoints:
`/api/state` (board+picks+slots+meta, polled every 5s by the page) and
`/api/player/{id}` (per-source stat lines). The board shows the `weighted`
avg_type. FastAPI/uvicorn are optional deps (`[web]` extra) and are imported
lazily — keep it that way so the core package imports without them. New static
files must match the `web/static/*` glob in `pyproject.toml` package-data.
