# Rscript-runnable translation tests (no ffanalytics install required).
# Usage: Rscript draft/tests/test_translation.R

file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
test_dir <- dirname(normalizePath(file_arg))
source(file.path(dirname(test_dir), "sleeper_scoring.R"))

fixture <- jsonlite::fromJSON(file.path(test_dir, "fixtures", "league_example.json"))
tr <- translate_scoring(fixture$scoring_settings)

# ---- Gate B: mapped values are copies of the league JSON --------------------
stopifnot(
  tr$scoring$pass$pass_yds == 0.04,
  tr$scoring$pass$pass_tds == 5,
  tr$scoring$pass$pass_int == -1,
  tr$scoring$pass$pass_300_yds == 1,
  tr$scoring$pass$pass_400_yds == 1,
  tr$scoring$rush$rush_yds == 0.1,
  tr$scoring$rush$rush_att == 0.1,
  tr$scoring$rush$rush_40_yds == 1,
  tr$scoring$rush$rush_100_yds == 1,
  tr$scoring$rush$rush_200_yds == 1,
  tr$scoring$rec$all_pos == FALSE,        # TE premium league
  tr$scoring$misc$fumbles_lost == -2,
  tr$scoring$misc$two_pts == 2            # pass/rush/rec 2pt all equal 2
)

# TE premium branch: TE rec = base rec (0 in this league) + bonus_rec_te (1)
# A position-custom group holds ONLY all_pos + position sublists; a leftover
# flat scalar makes projections_table() fail with "subscript out of bounds".
stopifnot(
  setequal(names(tr$scoring$rec), c("all_pos", "QB", "RB", "WR", "TE")),
  all(vapply(tr$scoring$rec[c("QB", "RB", "WR", "TE")], is.list, logical(1))),
  tr$scoring$rec$TE$rec == 1,
  tr$scoring$rec$RB$rec == 0,
  tr$scoring$rec$WR$rec == 0,
  tr$scoring$rec$TE$rec_yds == 0.1,       # other rec values copied through
  tr$scoring$rec$TE$rec_40_yds == 1
)

# DST bracket enable is always on (bracket points flow through source_points)
stopifnot(tr$scoring$dst$dst_pts_allowed == 1)

# This league has no points-allowed scoring: compact 7-step all-zero bracket
stopifnot(
  length(tr$scoring$pts_bracket) == 7,
  all(vapply(tr$scoring$pts_bracket, `[[`, numeric(1), "points") == 0),
  vapply(tr$scoring$pts_bracket, `[[`, numeric(1), "threshold") ==
    c(0, 6, 13, 20, 27, 34, 99)
)

# st_td is a MAPPING, not a gap: Sleeper's player special-teams TD is exactly
# ffanalytics return_tds (kick/punt/blocked-kick returns).
stopifnot(
  tr$scoring$ret$return_tds == 6,
  "st_td" %in% names(tr$mapped),
  !"st_td" %in% names(tr$disclosed)
)
# ...and it still shares the common-value set with the legacy kr_td/pr_td keys.
st_conf <- translate_scoring(list(st_td = 6, kr_td = 4))
stopifnot("conflict:return_tds" %in% names(st_conf$disclosed),
          st_conf$scoring$ret$return_tds == 0)

# ---- Gate A: disclosure and unknown-key behavior ----------------------------
# Known-unmappable keys nonzero in this league must be disclosed, not dropped.
for (k in c("fum_rec_td", "rec_td_40p", "rush_td_40p", "pass_td_40p")) {
  if (!is.null(fixture$scoring_settings[[k]]) && fixture$scoring_settings[[k]] != 0) {
    stopifnot(k %in% names(tr$disclosed))
  }
}
stopifnot(length(tr$unknown) == 0)

# Synthetic key outside the universe -> lands in unknown (batch hard-stops)
tr_bad <- translate_scoring(c(fixture$scoring_settings, list(made_up_stat = 1)))
stopifnot("made_up_stat" %in% names(tr_bad$unknown))

# ---- common-value set conflict ----------------------------------------------
conf <- translate_scoring(list(pass_yd = 0.04, pass_2pt = 2, rush_2pt = 3))
stopifnot(
  "conflict:two_pts" %in% names(conf$disclosed),
  conf$scoring$misc$two_pts == 0
)

# fgm 50+ split brackets agree -> single fg_50 value
fg <- translate_scoring(list(fgm_50_59 = 5, fgm_60p = 5))
stopifnot(fg$scoring$kick$fg_50 == 5)
fg2 <- translate_scoring(list(fgm_50_59 = 5, fgm_60p = 6))
stopifnot("conflict:fg_50" %in% names(fg2$disclosed), fg2$scoring$kick$fg_50 == 0)

# ---- idp_tkl exact decomposition ---------------------------------------------
idp <- translate_scoring(list(idp_tkl = 1, idp_tkl_solo = 2, idp_tkl_ast = 0.5))
stopifnot(
  idp$scoring$idp$idp_solo == 3,
  idp$scoring$idp$idp_asst == 1.5
)

# ---- points-allowed: bracket only, then bracket + exact linear expansion ----
br <- translate_scoring(list(
  pts_allow_0 = 10, pts_allow_1_6 = 7, pts_allow_7_13 = 4, pts_allow_14_20 = 1,
  pts_allow_21_27 = 0, pts_allow_28_34 = -1, pts_allow_35p = -4
))
pts <- vapply(br$scoring$pts_bracket, `[[`, numeric(1), "points")
stopifnot(length(pts) == 7, pts == c(10, 7, 4, 1, 0, -1, -4))

lin <- translate_scoring(list(
  pts_allow = -0.1,
  pts_allow_0 = 10, pts_allow_1_6 = 7, pts_allow_7_13 = 4, pts_allow_14_20 = 1,
  pts_allow_21_27 = 0, pts_allow_28_34 = -1, pts_allow_35p = -4
))
lb <- lin$scoring$pts_bracket
stopifnot(
  length(lb) == 100,
  lb[[1]]$threshold == 0,  lb[[1]]$points == 10,          # 0 allowed
  lb[[4]]$threshold == 3,  abs(lb[[4]]$points - (7 - 0.3)) < 1e-12,
  lb[[19]]$threshold == 18, abs(lb[[19]]$points - (1 - 1.8)) < 1e-12,
  lb[[100]]$threshold == 99, abs(lb[[100]]$points - (-4 - 9.9)) < 1e-12
)

# ---- structural proof against the installed package (optional) --------------
if (requireNamespace("ffanalytics", quietly = TRUE)) {
  ref <- ffanalytics::scoring
  flat_names <- function(x) lapply(x[setdiff(names(x), "pts_bracket")], names)
  got <- flat_names(tr$scoring)
  want <- flat_names(ref)
  for (grp in names(want)) {
    extra <- setdiff(setdiff(got[[grp]], want[[grp]]), c("QB", "RB", "WR", "TE"))
    stopifnot(length(extra) == 0)
  }
  tabs <- ffanalytics:::make_scoring_tables(tr$scoring)
  stopifnot(is.list(tabs$scoring_tables), length(tabs$pts_bracket) == 7)
  cat("structural check against installed ffanalytics: OK\n")
}

cat("test_translation.R: ALL PASSED\n")
