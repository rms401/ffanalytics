#!/usr/bin/env Rscript
# Batch pipeline: league-true rankings into SQLite for the draft board.
#
#   Rscript draft/run_projections.R <league_id> [--rescore]
#
# --rescore skips the web scrape and re-scores the cached raw scrape (tweak
# scoring/weights/baseline without re-hitting every projection source).
#
# DRAFT-DAY DRY-RUN CHECKLIST
#   [ ] morning of draft: run this script; it should end with "SNAPSHOT WRITTEN"
#   [ ] scoring report clean (no unknown keys; disclosed list acknowledged)
#   [ ] crosswalk coverage >= 95% of top-200; resolve misses on the Status tab
#   [ ] app: snapshot badge green (< 72h old)
#   [ ] keepers pre-marked as drafted (overrides); target list set
#   [ ] poll dot green against the real draft_id (status pre_draft, 0 picks)
#   [ ] mark/unmark a player once to confirm override round-trip
#   [ ] laptop plugged in
#
# Requires the ffanalytics package (plus DBI/RSQLite/httr2/jsonlite).

suppressPackageStartupMessages(library(ffanalytics))

args <- commandArgs(trailingOnly = TRUE)
league_id <- args[!startsWith(args, "--")][1]
rescore <- "--rescore" %in% args
if (is.na(league_id)) {
  stop("Usage: Rscript draft/run_projections.R <league_id> [--rescore]")
}

source("draft/db.R")
source("draft/sleeper_api.R")
source("draft/sleeper_scoring.R")
source("draft/vor_baseline.R")
source("draft/crosswalk.R")

con <- open_db()
init_db(con)

# ---- 1. league settings -> translated scoring + derived baseline ------------

message("Fetching Sleeper league ", league_id)
lg <- sleeper_league(league_id)
if (!identical(tolower(lg$sport %||% "nfl"), "nfl")) {
  stop("League ", league_id, " is not an NFL league")
}

tr <- translate_scoring(lg$scoring_settings)
cat("\n---- scoring translation report ----\n")
cat(format_scoring_report(tr), "\n")
cat("------------------------------------\n\n")
if (length(tr$unknown) > 0) {
  stop("Nonzero scoring keys OUTSIDE the mapped universe: ",
       paste(names(tr$unknown), collapse = ", "),
       "\nSleeper shipped a new scoring option - update the universe table in ",
       "draft/sleeper_scoring.R before drafting with these rankings.")
}

roster_positions <- unlist(lg$roster_positions)
baseline <- derive_vor_baseline(roster_positions, lg$total_rosters)
cat("Derived VOR baseline:\n")
print(baseline)

drafts <- sleeper_drafts(league_id)
draft_id <- if (length(drafts) > 0) drafts[[1]]$draft_id else NA_character_

upsert_league(con, list(
  league_id = league_id,
  name = lg$name,
  season = as.integer(lg$season),
  total_rosters = lg$total_rosters,
  roster_positions_json = as.character(jsonlite::toJSON(roster_positions)),
  scoring_settings_json = as.character(jsonlite::toJSON(lg$scoring_settings,
                                                        auto_unbox = TRUE)),
  translated_scoring_json = as.character(jsonlite::toJSON(tr$scoring,
                                                          auto_unbox = TRUE)),
  unmapped_keys_json = as.character(jsonlite::toJSON(tr$disclosed,
                                                     auto_unbox = TRUE)),
  vor_baseline_json = as.character(jsonlite::toJSON(as.list(baseline),
                                                    auto_unbox = TRUE)),
  draft_id = draft_id
))

params <- ensure_params(con, league_id)

# ---- 2. sleeper player table (global, refreshed at most daily) --------------

if (sleeper_players_age_hours(con) > 24) {
  message("Refreshing Sleeper player table (daily cache)")
  replace_sleeper_players(con, sleeper_players())
}

# ---- 3. scrape (or reload cached raw scrape) --------------------------------

season <- as.integer(lg$season)
cache_dir <- file.path(draft_root(), "cache")
dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
cache_file <- file.path(cache_dir, paste0("scrape_", season, ".rds"))

pos_scrape <- intersect(names(baseline),
                        c("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB"))
sources <- jsonlite::fromJSON(params$sources_json)

if (rescore) {
  if (!file.exists(cache_file)) {
    stop("--rescore given but no cached scrape at ", cache_file)
  }
  message("Re-scoring cached scrape ", cache_file)
  data_result <- readRDS(cache_file)
} else {
  message("Scraping ", length(sources), " sources for: ",
          paste(pos_scrape, collapse = ", "))
  data_result <- scrape_data(src = sources, pos = pos_scrape,
                             season = season, week = 0)
  saveRDS(data_result, cache_file)
}

# ---- Gate C: mapped rules with no projected stat are disclosed ---------------

mapped_cols <- unique(sleeper_map_direct$ffa_key[
  sleeper_map_direct$sleeper_key %in% names(tr$mapped)])
projected_cols <- unique(unlist(lapply(data_result, function(df) {
  names(df)[vapply(df, function(col) any(!is.na(col)), logical(1))]
})))
unprojected <- setdiff(mapped_cols, projected_cols)
if (length(unprojected) > 0) {
  cat("\nNOTE (Gate C): these league rules are mapped correctly but NO source",
      "projects the stat, so they contribute 0 to every projection:\n  ",
      paste(unprojected, collapse = ", "), "\n\n")
}

# ---- 4. league-true projections + enrichment --------------------------------

vor_override <- if (!is.na(params$vor_baseline_override_json)) {
  unlist(jsonlite::fromJSON(params$vor_baseline_override_json))
} else {
  NULL
}
src_weights <- if (!is.na(params$src_weights_json)) {
  unlist(jsonlite::fromJSON(params$src_weights_json))
} else {
  NULL
}
tiers <- if (!is.na(params$tier_thresholds_json)) {
  unlist(jsonlite::fromJSON(params$tier_thresholds_json))
} else {
  NULL
}

message("Computing projections (all avg types)")
proj <- projections_table(
  data_result,
  scoring_rules = tr$scoring,
  vor_baseline = vor_override %||% baseline,
  src_weights = src_weights,
  tier_thresholds = tiers
)
proj <- proj |>
  add_ecr() |>
  add_adp() |>
  add_aav() |>
  add_uncertainty() |>
  add_player_info()

# ---- 5. crosswalk + snapshot write -------------------------------------------

rank_df <- as.data.frame(proj)
rank_df$mfl_id <- as.character(rank_df$id)
rank_df$player <- trimws(paste(rank_df$first_name, rank_df$last_name))
rank_df$pos[rank_df$pos %in% "DEF"] <- "DST"

xw <- attach_sleeper_ids(rank_df, get_sleeper_players(con))
rank_df <- xw$rankings
rep <- xw$report
cat(sprintf("\nCrosswalk: %d/%d rows matched; top-200: %d/%d (%.1f%%)\n",
            rep$matched, rep$total, rep$top200_matched, rep$top200,
            100 * rep$top200_matched / max(rep$top200, 1)))
if (nrow(rep$unmatched_top200) > 0) {
  cat("Unmatched inside top-200 (fix on the app Status tab):\n")
  print(rep$unmatched_top200, row.names = FALSE)
}

n <- replace_rankings(con, league_id, rank_df)

# ---- 6. summary ---------------------------------------------------------------

cat(sprintf("\nSNAPSHOT WRITTEN: %d ranking rows for league %s (%s)\n",
            n, league_id, lg$name))
default_type <- params$avg_type_default
board <- rank_df[rank_df$avg_type == default_type, ]
for (p in intersect(c("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB"),
                    unique(board$pos))) {
  top5 <- board[board$pos == p, ]
  top5 <- top5[order(-top5$points_vor), ][1:min(5, nrow(top5)),
                                           c("player", "team", "points", "points_vor")]
  cat("\nTop 5", p, sprintf("(%s):\n", default_type))
  print(top5, row.names = FALSE)
}
cat("\nDone at", format(Sys.time()), "\n")

DBI::dbDisconnect(con)
