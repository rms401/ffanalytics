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
#   [ ] scoring report clean (no unknown keys)
#   [ ] scoring coverage table: every nonzero rule has a method, none blank
#   [ ] calibration table: note any bonus column running >1.5x actual
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
source("draft/gap_fill.R")

# A mapped or estimated rule contributing exactly 0 is a bug and always stops the
# batch. A rule with no estimator stops it only if its measured season-points
# impact on a draftable player exceeds this; below it, the number is recorded in
# gap_method_json and shown on the app's Status tab.
GAP_MATERIALITY_TOL <- 0.5

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

# ---- 3b. gap fill -----------------------------------------------------------
# Estimate the nonzero league rules that no ffanalytics rule expresses, from
# Sleeper's own historical stats, and inject them as scored stat columns.

all_vals <- vapply(lg$scoring_settings, as.numeric, numeric(1))
league_vals <- as.list(all_vals[!is.na(all_vals) & all_vals != 0])

seasons_hist <- gap_seasons(season)
spec <- gap_spec_for_league(gap_spec(), league_vals)

message("Fetching Sleeper historical stats (", paste(rev(seasons_hist), collapse = ", "), ")")
hist_keys <- unique(c(unlist(lapply(c(spec$hist_num, spec$hist_den), gap_parts)),
                      names(league_vals),
                      calibration_spec()$hist_num, calibration_spec()$hist_den))
hist <- gap_hist_totals(seasons_hist, hist_keys, cache_dir)

sp_tbl <- get_sleeper_players(con)
sleeper_pos <- stats::setNames(sp_tbl$pos, sp_tbl$sleeper_id)
mfl2sleeper <- mfl_to_sleeper_map(cache_dir)

rates <- fit_gap_rates(hist, spec, sleeper_pos)
inj <- inject_gap_stats(data_result, rates, spec, league_vals, mfl2sleeper)
data_result <- inj$data_result
rules_ext <- extend_rules_with_gap_keys(tr$scoring, inj$new_rules, league_vals)

if (length(inj$applied) > 0) {
  cat("\nGap-filled from Sleeper ",
      paste(rev(range(seasons_hist)), collapse = "-"), ":\n", sep = "")
  for (id in inj$applied) {
    mode <- spec$mode[match(id, spec$id)]
    cat(sprintf("  %-12s -> %-12s (%s)\n", id, spec$target_col[match(id, spec$id)],
                if (mode == "new_rule") "new rule" else "no source projects it"))
  }
}
if (length(inj$skipped) > 0) {
  cat("Gap rows skipped:\n   ",
      paste(unlist(inj$skipped), collapse = "\n   "), "\n")
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
  scoring_rules = rules_ext,
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

# ---- 4b. materiality gate ----------------------------------------------------
# Every nonzero league rule must be accounted for: mapped and contributing,
# estimated and contributing, or measurably immaterial. Nothing is dropped.

default_type <- params$avg_type_default

message("Measuring the contribution of every nonzero league rule")
raw_stats <- projections_table(data_result, scoring_rules = rules_ext,
                               src_weights = src_weights,
                               return_raw_stats = TRUE)

key_cols <- mapped_key_columns(names(tr$mapped))
pkg_cols <- package_imputed_columns()
est_label <- sprintf("estimated(%s)", paste(rev(range(seasons_hist)), collapse = "-"))

gap_method <- list()
offenders <- character(0)
report_rows <- list()

for (key in names(league_vals)) {
  val <- league_vals[[key]]
  # Gap fill is checked first: a Class-B key is both mapped AND estimated, and
  # what matters for the report is where its points actually came from.
  if (key %in% names(inj$covered)) {
    col <- unname(inj$covered[[key]])
    method <- est_label
  } else if (key %in% names(key_cols)) {
    col <- unname(key_cols[[key]])
    method <- if (col %in% pkg_cols) "package-imputed" else "sources"
  } else {
    col <- NA_character_
    method <- "no-estimator"
  }

  total <- if (!is.na(col) && col %in% names(raw_stats)) {
    sum(raw_stats[[col]], na.rm = TRUE)
  } else {
    0
  }

  if (method == "no-estimator") {
    # No column exists, so impact has to come from history: the 90th-percentile
    # nonzero player-season for this stat, at the league's point value.
    x <- if (key %in% colnames(hist)) hist[, key] / length(seasons_hist) else numeric(0)
    x <- x[x > 0]
    impact <- if (length(x)) unname(stats::quantile(x, 0.9)) * abs(val) else 0
    mat_pos <- NA_character_
    if (impact > GAP_MATERIALITY_TOL) offenders <- c(offenders, key)
  } else {
    mat <- gap_materiality(raw_stats, proj, col, val, default_type)
    impact <- mat$pts
    mat_pos <- mat$pos
    # Mapped or estimated yet contributing literally nothing is a defect,
    # however small the rule: the column never reached the scoring table.
    if (!is.finite(total) || total == 0) offenders <- c(offenders, key)
  }

  gap_method[[key]] <- list(
    value = val, method = method, column = col %||% NA_character_,
    total_contribution = if (is.finite(total)) round(total, 4) else NA_real_,
    materiality_pts = round(impact, 3), materiality_pos = mat_pos %||% NA_character_
  )
  report_rows[[length(report_rows) + 1]] <- data.frame(
    key = key, value = val, method = method, column = col %||% NA_character_,
    pts = round(impact, 3), pos = mat_pos %||% NA_character_,
    stringsAsFactors = FALSE
  )
}

coverage <- do.call(rbind, report_rows)
cat("\n---- scoring coverage (points = mean season impact, top-50 at pos) ----\n")
print(coverage[order(-coverage$pts), ], row.names = FALSE)
cat("-----------------------------------------------------------------------\n")

if (length(offenders) > 0) {
  stop("These nonzero league rules contribute nothing to projections:\n  ",
       paste(vapply(offenders, function(k) {
         sprintf("%s = %s (%s, %.2f pts)", k, league_vals[[k]],
                 gap_method[[k]]$method, gap_method[[k]]$materiality_pts)
       }, character(1)), collapse = "\n  "),
       "\nAdd a gap_spec() row for each, or raise GAP_MATERIALITY_TOL only if ",
       "you have decided the impact is acceptable.")
}

# Class-B calibration: the package synthesises rec_40_yds / *_100_yds /
# pass_300_yds from bonus_col_coefs, fit on one prior season via nflfastR. This
# reports drift against Sleeper's actual rates; it never overrides them.
hist_stacked <- gap_hist_stacked(seasons_hist, hist_keys, cache_dir)
calib <- calibrate_bonus_columns(raw_stats, hist_stacked, default_type)
if (nrow(calib) > 0) {
  cat("\n---- package bonus-column calibration vs Sleeper actuals ----\n")
  print(calib, row.names = FALSE, digits = 4)
  drift <- calib[is.finite(calib$ratio) & (calib$ratio > 1.5 | calib$ratio < 0.67), ]
  if (nrow(drift) > 0) {
    cat("NOTE: package output is off by more than 1.5x on ",
        paste(drift$column, collapse = ", "),
        "\n  (ratio > 1 = the package awards more of the bonus than players earn).\n",
        sep = "")
  }
  cat("-------------------------------------------------------------\n")
}

set_league_field(con, league_id, "gap_method_json",
                 as.character(jsonlite::toJSON(gap_method, auto_unbox = TRUE)))

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
