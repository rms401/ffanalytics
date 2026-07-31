# Integration test: an injected gap column really is scored by the package, and
# it changes NOTHING else. Requires the ffanalytics package; skips cleanly if it
# is not installed (the container running the pure-R tests may not have it).
#
# Usage: Rscript draft/tests/test_gap_injection.R

file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
test_dir <- dirname(normalizePath(file_arg))
draft_dir <- dirname(test_dir)

if (!requireNamespace("ffanalytics", quietly = TRUE)) {
  cat("test_gap_injection.R: SKIPPED (ffanalytics not installed)\n")
  quit(save = "no", status = 0)
}
suppressPackageStartupMessages(library(ffanalytics))
source(file.path(draft_dir, "sleeper_scoring.R"))
source(file.path(draft_dir, "gap_fill.R"))

near <- function(a, b, tol = 1e-8) all(abs(a - b) < tol)

fx <- jsonlite::fromJSON(file.path(test_dir, "fixtures", "league_example.json"))
tr <- translate_scoring(fx$scoring_settings)

# This league is TE premium, so rec$all_pos is FALSE and rec is nested per
# position - the case where a key added under `rec` would vanish.
stopifnot(isFALSE(tr$scoring$rec$all_pos))

wr <- data.frame(
  id = c("101", "102"), data_src = "SrcA", pos = "WR",
  rec = c(90, 60), rec_yds = c(1200, 800), rec_tds = c(10, 5),
  rush_att = c(2, 1), rush_yds = c(10, 5), rush_tds = c(0, 0),
  fumbles_lost = c(1, 1), stringsAsFactors = FALSE)
dr <- list(WR = wr)
attr(dr, "season") <- 2025L
attr(dr, "week") <- 0L

base <- ffanalytics:::source_points(dr, tr$scoring)$raw_points

# ---- the injected column is scored at exactly value * estimate ---------------
est <- c(0.5, 0.25)
dr2 <- dr
dr2$WR$rec_td_40p <- est
rules2 <- extend_rules_with_gap_keys(tr$scoring, "rec_td_40p",
                                     list(rec_td_40p = 1))
got <- ffanalytics:::source_points(dr2, rules2)$raw_points

stopifnot(near(got - base, est * 1))

# ...and it scales with the league's point value, not with anything else.
rules5 <- extend_rules_with_gap_keys(tr$scoring, "rec_td_40p",
                                     list(rec_td_40p = 5))
got5 <- ffanalytics:::source_points(dr2, rules5)$raw_points
stopifnot(near(got5 - base, est * 5))

# ---- non-gap scoring is bit-identical ---------------------------------------
# Zeroing the gap estimate must reproduce the original points exactly.
dr0 <- dr
dr0$WR$rec_td_40p <- c(0, 0)
stopifnot(near(ffanalytics:::source_points(dr0, rules2)$raw_points, base, 1e-12))

# ---- the key reaches the scoring table for every position -------------------
tabs <- ffanalytics:::make_scoring_tables(rules2)
for (pos in c("QB", "RB", "WR", "TE")) {
  st <- tabs$scoring_tables[[pos]]
  stopifnot("rec_td_40p" %in% st$column,
            st$val[st$column == "rec_td_40p"] == 1)
}
# TE premium survived the extension
te <- tabs$scoring_tables$TE
stopifnot(te$val[te$column == "rec"] == 1)
wrt <- tabs$scoring_tables$WR
stopifnot(wrt$val[wrt$column == "rec"] == 0)

# ---- st_td is mapped, and return_tds scores like any other rule -------------
stopifnot(tr$scoring$ret$return_tds == 6, !"st_td" %in% names(tr$disclosed))
dr3 <- dr
dr3$WR$return_tds <- c(1, 0)
stopifnot(near(ffanalytics:::source_points(dr3, tr$scoring)$raw_points - base,
               c(6, 0)))

# ---- the package really does synthesise the Class-B bonus columns -----------
# If this ever stops being true, gap_spec() has to grow rows for them.
bonus <- ffanalytics:::impute_bonus_cols(dr, tabs$scoring_tables)$WR
for (col in c("rec_40_yds", "rec_100_yds", "rec_200_yds")) {
  stopifnot(col %in% names(bonus), all(is.finite(bonus[[col]])),
            all(bonus[[col]] >= 0))
}
# ...and the higher threshold is never larger than the lower one.
stopifnot(all(bonus$rec_100_yds >= bonus$rec_200_yds))

# gap_spec() must not claim any column the package already builds.
stopifnot(length(intersect(gap_spec()$sleeper_key, names(bonus))) == 0)

cat("test_gap_injection.R: ALL PASSED\n")
