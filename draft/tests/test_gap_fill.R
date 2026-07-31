# Rscript-runnable gap-fill tests (no ffanalytics install, no network).
# Usage: Rscript draft/tests/test_gap_fill.R
#
# Every expectation below is hand-computed from the two tiny fixture seasons in
# fixtures/gap_cache/, so a change in the estimator has to be justified against
# arithmetic rather than against whatever the code happens to produce.

file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
test_dir <- dirname(normalizePath(file_arg))
draft_dir <- dirname(test_dir)
source(file.path(draft_dir, "sleeper_scoring.R"))
source(file.path(draft_dir, "gap_fill.R"))

cache <- file.path(test_dir, "fixtures", "gap_cache")
near <- function(a, b, tol = 1e-9) all(abs(a - b) < tol)

# ---- pooled history ---------------------------------------------------------
# Fixtures pool to:
#   id 1: gp 34, rec 200, rec_td 20, rec_td_40p 4, rec_fd 100, fum_rec_td 1
#   id 2: gp 34, rec  40, rec_td 10, rec_td_40p 0, rec_fd  20
#   id 3: gp 17, rush_td 10, rush_td_40p 1
hist <- gap_hist_totals(c(2024, 2023),
                        c("rec_td_40p", "rec_td", "rec_fd", "rec",
                          "rush_td_40p", "rush_td", "fum_rec_td"),
                        cache, max_age_hours = Inf)

stopifnot(
  setequal(rownames(hist), c("1", "2", "3")),
  hist["1", "rec_td"] == 20, hist["1", "rec_td_40p"] == 4, hist["1", "gp"] == 34,
  hist["2", "rec_td"] == 10, hist["2", "rec_td_40p"] == 0,
  hist["3", "rush_td"] == 10, hist["3", "rush_td_40p"] == 1,
  # a stat absent from a player's record pools as 0, never NA
  hist["3", "rec_td"] == 0, !anyNA(hist)
)

# ---- rate fitting -----------------------------------------------------------
pos_map <- c("1" = "WR", "2" = "WR", "3" = "RB")
spec <- gap_spec()
spec <- spec[spec$id %in% c("rec_td_40p", "fum_rec_td"), , drop = FALSE]
rates <- fit_gap_rates(hist, spec, pos_map)

# rec_td_40p: overall = 4/30; WR pos rate = 4/30; RB has no rec_td -> overall.
r <- rates$rec_td_40p
overall <- 4 / 30
stopifnot(
  near(r$overall, overall),
  near(r$pos_rate[["WR"]], overall),
  near(r$pos_rate[["RB"]], overall)     # zero denominator falls back to overall
)
# k = 20: player rate = (num + k*prior) / (den + k)
stopifnot(
  near(r$player[["1"]], (4 + 20 * overall) / (20 + 20)),   # 0.1666667
  near(r$player[["2"]], (0 + 20 * overall) / (10 + 20)),   # 0.0888889
  near(r$player[["3"]], (0 + 20 * overall) / (0 + 20))     # = prior exactly
)
# The heavier a player's own sample, the further the estimate moves off the
# prior - and a player with no denominator at all IS the prior.
stopifnot(r$player[["1"]] > overall, r$player[["2"]] < overall,
          near(r$player[["3"]], overall))

# fum_rec_td is a per-game rate: overall = 1/85, WR = 1/68, k = 200.
f <- rates$fum_rec_td
stopifnot(
  near(f$overall, 1 / 85),
  near(f$pos_rate[["WR"]], 1 / 68),
  near(f$player[["1"]], (1 + 200 * (1 / 68)) / (34 + 200))
)

# All estimates finite and non-negative, always.
for (nm in names(rates)) {
  stopifnot(all(is.finite(rates[[nm]]$player)), all(rates[[nm]]$player >= 0))
}

# ---- injection --------------------------------------------------------------
mk_src <- function(ids, rec_tds, src) {
  data.frame(id = ids, data_src = src, rec_tds = rec_tds,
             stringsAsFactors = FALSE)
}
# m9 has no Sleeper history: it must fall back to the WR position rate.
m2s <- c(m1 = "1", m2 = "2", m9 = "99")
dr <- list(WR = rbind(mk_src(c("m1", "m2", "m9"), c(10, 5, 8), "A"),
                      mk_src(c("m1", "m2", "m9"), c(12, NA, 8), "B")),
           K  = data.frame(id = "k1", data_src = "A", xp = 40,
                           stringsAsFactors = FALSE))
attr(dr, "season") <- 2026
attr(dr, "week") <- 0

league_vals <- list(rec_td_40p = 1, fum_rec_td = 6, rec_td = 6)
inj <- inject_gap_stats(dr, rates, spec, league_vals, m2s)
wr <- inj$data_result$WR

stopifnot(setequal(inj$applied, c("rec_td_40p", "fum_rec_td")),
          setequal(inj$new_rules, c("rec_td_40p", "fum_rec_td")),
          setequal(names(inj$covered), c("rec_td_40p", "fum_rec_td")))

# estimate = player rate * that source's own driver value
stopifnot(
  near(wr$rec_td_40p[1], 10 * r$player[["1"]]),
  near(wr$rec_td_40p[2], 5 * r$player[["2"]]),
  near(wr$rec_td_40p[3], 8 * overall),          # rookie -> position rate
  near(wr$rec_td_40p[4], 12 * r$player[["1"]])
)
# A source that does not project the driver yields NA on purpose: the package's
# impute_via_rates_and_mean() fills it from the player's other sources.
stopifnot(is.na(wr$rec_td_40p[5]))

# __games__ driver uses a flat 17-game season and ignores the source columns
stopifnot(near(wr$fum_rec_td[1], 17 * f$player[["1"]]))

# Position scope is enforced at injection: K never receives an offense column,
# so it can never score one (source_points intersects columns with the table).
stopifnot(!"rec_td_40p" %in% names(inj$data_result$K),
          !"fum_rec_td" %in% names(inj$data_result$K))

# Scrape attributes survive injection (projections_table reads them).
stopifnot(attr(inj$data_result, "season") == 2026,
          attr(inj$data_result, "week") == 0)

# Only rules the league actually scores activate a spec row.
full <- gap_spec()
stopifnot(
  nrow(gap_spec_for_league(full, list(rec_td = 6, pass_yd = 0.04))) == 0,
  gap_spec_for_league(full, list(rec_td_40p = 1))$id == "rec_td_40p",
  # a Class-B row fires on ANY of its member keys
  gap_spec_for_league(full, list(rush_2pt = 2))$id == "two_pts",
  gap_spec_for_league(full, list(st_td = 6))$id == "return_tds",
  gap_spec_for_league(full, list(kr_td = 6))$id == "return_tds"
)

# ---- Class B: fill only what no source projects -----------------------------
b_spec <- full[full$id == "return_tds", , drop = FALSE]
b_rates <- fit_gap_rates(hist, b_spec, pos_map)
lv <- list(st_td = 6)

# column entirely absent -> estimated
b1 <- inject_gap_stats(list(WR = data.frame(id = "m1", data_src = "A",
                                            stringsAsFactors = FALSE)),
                       b_rates, b_spec, lv, m2s)
stopifnot("return_tds" %in% names(b1$data_result$WR),
          b1$covered[["st_td"]] == "return_tds",
          length(b1$new_rules) == 0)     # Class B adds no rule

# column present and projected -> left completely alone, and NOT reported as
# covered, so the batch labels the rule "sources" rather than "estimated"
b2 <- inject_gap_stats(list(WR = data.frame(id = "m1", data_src = "A",
                                            return_tds = 0.4,
                                            stringsAsFactors = FALSE)),
                       b_rates, b_spec, lv, m2s)
stopifnot(b2$data_result$WR$return_tds == 0.4,
          length(b2$covered) == 0, length(b2$applied) == 0,
          grepl("left alone", unlist(b2$skipped)[1]))

# column present but all-NA -> treated as missing and filled
b3 <- inject_gap_stats(list(WR = data.frame(id = "m1", data_src = "A",
                                            return_tds = NA_real_,
                                            stringsAsFactors = FALSE)),
                       b_rates, b_spec, lv, m2s)
stopifnot(!is.na(b3$data_result$WR$return_tds),
          b3$covered[["st_td"]] == "return_tds")

# multi-key numerators sum across the component stats
two <- full[full$id == "two_pts", , drop = FALSE]
stopifnot(setequal(gap_parts(two$hist_num),
                   c("pass_2pt", "rush_2pt", "rec_2pt")),
          setequal(gap_parts(two$driver_col),
                   c("pass_tds", "rush_tds", "rec_tds")))
h2 <- gap_hist_totals(c(2024), c("rec_td", "rush_td"), cache, max_age_hours = Inf)
stopifnot(near(hist_sum(h2, "rec_td+rush_td"),
               h2[, "rec_td"] + h2[, "rush_td"]),
          # a stat absent from the fixture contributes 0, never an error
          all(hist_sum(h2, "not_a_stat") == 0))

# Missing driver column is recorded, not silently dropped.
dr_nodrv <- list(WR = data.frame(id = "m1", data_src = "A",
                                 stringsAsFactors = FALSE))
inj_skip <- inject_gap_stats(dr_nodrv, rates, spec, league_vals, m2s)
stopifnot(length(inj_skip$skipped) == 1,
          grepl("rec_tds", unlist(inj_skip$skipped)[1]),
          !"rec_td_40p" %in% names(inj_skip$data_result$WR))

# Never write over a column the package or a source already provides - that
# would double-count against impute_bonus_cols().
dr_dup <- list(WR = data.frame(id = "m1", data_src = "A", rec_tds = 10,
                               rec_td_40p = 1, stringsAsFactors = FALSE))
stopifnot(inherits(try(inject_gap_stats(dr_dup, rates, spec, league_vals, m2s),
                       silent = TRUE), "try-error"))

# ---- rule extension ---------------------------------------------------------
# misc is flat and all_pos = TRUE in both branches of make_scoring_tables(), so
# injected keys survive a TE-premium league's per-position rec nesting.
tr <- translate_scoring(list(rec_yd = 0.1, rec_td = 6, bonus_rec_te = 1,
                             rec_td_40p = 1, fum_rec_td = 6))
stopifnot(tr$scoring$rec$all_pos == FALSE)
ext <- extend_rules_with_gap_keys(tr$scoring, c("rec_td_40p", "fum_rec_td"),
                                  league_vals)
stopifnot(
  ext$misc$rec_td_40p == 1,
  ext$misc$fum_rec_td == 6,
  isTRUE(ext$misc$all_pos),          # still flat: not nested per position
  ext$rec$TE$rec == 1                # TE premium untouched
)

flat <- flatten_rules(ext)
stopifnot(
  "rec_td_40p" %in% flat$column,
  flat$val[flat$column == "rec_td_40p"] == 1,
  !"all_pos" %in% flat$column,
  # per-position nesting collapses to the largest-magnitude value
  flat$val[flat$column == "rec"] == 1
)

# ---- reporting helpers ------------------------------------------------------
# st_td is mapped, not estimated, and must resolve to the return_tds column.
km <- mapped_key_columns(c("st_td", "rec_td", "bonus_rec_te", "pts_allow_0"))
stopifnot(
  km[["st_td"]] == "return_tds",
  km[["rec_td"]] == "rec_tds",
  km[["bonus_rec_te"]] == "rec",
  km[["pts_allow_0"]] == "dst_pts_allowed"
)

# Nothing in gap_spec() may collide with a column the package synthesises.
stopifnot(length(intersect(gap_spec()$target_col, package_imputed_columns())) == 0)

# ...nor with a key translate_scoring() already maps to a real rule.
mapped_universe <- c(sleeper_map_direct$sleeper_key,
                     unlist(lapply(sleeper_map_sets, `[[`, "keys"), use.names = FALSE),
                     sleeper_key_decomp_tkl, sleeper_keys_te_premium,
                     sleeper_keys_bracket, sleeper_key_linear_pts_allow)
new_rule_keys <- gap_spec()$id[gap_spec()$mode == "new_rule"]
stopifnot(length(intersect(new_rule_keys, mapped_universe)) == 0)

# Every estimable key must be a key translate_scoring() knows about, or the
# batch would never see it in the disclosed list.
stopifnot(all(unlist(lapply(gap_spec()$league_keys, gap_parts)) %in%
                sleeper_scoring_universe))

# ---- materiality ------------------------------------------------------------
raw_stats <- data.frame(
  avg_type = "weighted", id = c("a", "b", "c"),
  rec_td_40p = c(1, 2, 3), stringsAsFactors = FALSE)
proj <- data.frame(
  avg_type = "weighted", id = c("a", "b", "c"), pos = c("WR", "WR", "RB"),
  points_vor = c(50, 40, 30), stringsAsFactors = FALSE)
mat <- gap_materiality(raw_stats, proj, "rec_td_40p", 2, "weighted")
stopifnot(mat$pos == "RB", near(mat$pts, 6))   # RB mean 3 * 2 beats WR 1.5 * 2

stopifnot(gap_materiality(raw_stats, proj, "not_a_column", 2, "weighted")$pts == 0)

cat("test_gap_fill.R: ALL PASSED\n")
