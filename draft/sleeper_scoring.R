# Sleeper -> ffanalytics scoring translation.
#
# The mapping table below covers the COMPLETE universe of NFL scoring keys that
# Sleeper's league-settings UI can emit (extracted from Sleeper's production web
# bundle on 2026-07-29 and cross-checked against real league JSON, the Sleeper
# stats endpoint, sleeper-go's typed ScoringSettings, Sleeper support docs, and
# ffscrapr's stat mapping). Every nonzero key in a league's scoring_settings is
# classified into exactly one of three states, decided here at build time:
#
#   mapped            -> value written into the ffanalytics scoring list
#   known-unmappable  -> "disclosed": no ffanalytics rule expresses this stat.
#                        draft/gap_fill.R estimates these from Sleeper's own
#                        historical stats and injects them; anything it cannot
#                        estimate is reported with its measured points impact.
#   unknown           -> hard stop (a key outside the universe means Sleeper
#                        shipped a new scoring feature; update this table)
#
# ffanalytics rule names come from R/scoring_rules.R in this repo.
#
# DST points-allowed (verified empirically against R/calc_projections.R):
# score_dst_pts_allowed() bracket-scores the dst_pts_allowed STAT and
# source_points() then multiplies the result by the dst$dst_pts_allowed RULE
# value (default 0, which silently zeroes points-allowed). The translator
# therefore always sets dst$dst_pts_allowed = 1, and encodes linear `pts_allow`
# EXACTLY by expanding the bracket to one step per integer point allowed
# (bracket(i) + i * pts_allow for i in 0..99).

if (!exists("%||%")) {
  `%||%` <- function(x, y) {
    if (is.null(x) || length(x) == 0 || (length(x) == 1 && is.na(x))) y else x
  }
}

# ---- universe --------------------------------------------------------------

# Direct copies: one Sleeper key -> one ffanalytics rule.
sleeper_map_direct <- data.frame(
  sleeper_key = c(
    "pass_yd", "pass_td", "pass_att", "pass_cmp", "pass_inc", "pass_int",
    "pass_sack", "pass_cmp_40p", "bonus_pass_yd_300", "bonus_pass_yd_400",
    "rush_yd", "rush_td", "rush_att", "rush_40p",
    "bonus_rush_yd_100", "bonus_rush_yd_200",
    "rec", "rec_yd", "rec_td", "rec_40p", "bonus_rec_yd_100", "bonus_rec_yd_200",
    "fum", "fum_lost",
    "xpm", "fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgmiss",
    "def_td", "sack", "int", "fum_rec", "safe", "blk_kick",
    "idp_tkl_solo", "idp_tkl_ast", "idp_sack", "idp_int", "idp_ff",
    "idp_fum_rec", "idp_pass_def", "idp_def_td", "idp_safe"
  ),
  ffa_group = c(
    "pass", "pass", "pass", "pass", "pass", "pass",
    "misc", "pass", "pass", "pass",
    "rush", "rush", "rush", "rush",
    "rush", "rush",
    "rec", "rec", "rec", "rec", "rec", "rec",
    "misc", "misc",
    "kick", "kick", "kick", "kick", "kick", "kick",
    "dst", "dst", "dst", "dst", "dst", "dst",
    "idp", "idp", "idp", "idp", "idp",
    "idp", "idp", "idp", "idp"
  ),
  ffa_key = c(
    "pass_yds", "pass_tds", "pass_att", "pass_comp", "pass_inc", "pass_int",
    "sacks", "pass_40_yds", "pass_300_yds", "pass_400_yds",
    "rush_yds", "rush_tds", "rush_att", "rush_40_yds",
    "rush_100_yds", "rush_200_yds",
    "rec", "rec_yds", "rec_tds", "rec_40_yds", "rec_100_yds", "rec_200_yds",
    "fumbles_total", "fumbles_lost",
    "xp", "fg_0019", "fg_2029", "fg_3039", "fg_4049", "fg_miss",
    "dst_td", "dst_sacks", "dst_int", "dst_fum_rec", "dst_safety", "dst_blk",
    "idp_solo", "idp_asst", "idp_sack", "idp_int", "idp_fum_force",
    "idp_fum_rec", "idp_pd", "idp_td", "idp_safety"
  ),
  stringsAsFactors = FALSE
)

# Common-value sets: several Sleeper keys funnel into ONE ffanalytics rule.
# All nonzero members must carry the same value; a conflict discloses the whole
# set (never averaged, never guessed).
sleeper_map_sets <- list(
  two_pts    = list(keys = c("pass_2pt", "rush_2pt", "rec_2pt"),
                    ffa_group = "misc", ffa_key = "two_pts"),
  return_yds = list(keys = c("kr_yd", "pr_yd"),
                    ffa_group = "ret", ffa_key = "return_yds"),
  # kr_td/pr_td are legacy (current Sleeper UI offers st_td instead) but old
  # leagues may still carry them. st_td is Sleeper's current special-teams TD
  # for a PLAYER, which is exactly what ffanalytics scores as return_tds:
  # verified against the 2025 stats endpoint, st_td (27 leaguewide) covers
  # kr_td (7) + pr_td (15) plus blocked-kick / special-teams fumble return TDs,
  # and no source distinguishes those. Mapping beats estimating here.
  return_tds = list(keys = c("kr_td", "pr_td", "st_td"),
                    ffa_group = "ret", ffa_key = "return_tds"),
  fg_50      = list(keys = c("fgm_50p", "fgm_50_59", "fgm_60p"),
                    ffa_group = "kick", ffa_key = "fg_50")
)

# Exact decomposition: a Sleeper "Tackle" point stacks on top of solo/assisted
# tackles (per Sleeper support), and every tackle is either solo or assisted,
# so adding its value to both idp_solo and idp_asst is exact.
sleeper_key_decomp_tkl <- "idp_tkl"

# TE premium (and RB/WR reception bonuses) -> per-position rec values.
sleeper_keys_te_premium <- c("bonus_rec_rb", "bonus_rec_wr", "bonus_rec_te")

# Points-allowed bracket + linear rate -> pts_bracket (see header note).
sleeper_keys_bracket <- c("pts_allow_0", "pts_allow_1_6", "pts_allow_7_13",
                          "pts_allow_14_20", "pts_allow_21_27",
                          "pts_allow_28_34", "pts_allow_35p")
sleeper_key_linear_pts_allow <- "pts_allow"
sleeper_bracket_upper <- c(0, 6, 13, 20, 27, 34, 99)

# Known-unmappable: rules ffanalytics cannot express (no stat or no rule).
# Nonzero values here are DISCLOSED, never silently dropped.
sleeper_keys_unmappable <- c(
  # first downs
  "pass_fd", "rush_fd", "rec_fd", "bonus_fd_qb", "bonus_fd_rb", "bonus_fd_wr",
  "bonus_fd_te",
  # TD-distance bonuses and pick-six
  "pass_td_40p", "pass_td_50p", "rush_td_40p", "rush_td_50p", "rec_td_40p",
  "rec_td_50p", "pass_int_td",
  # volume / combined bonuses
  "bonus_pass_cmp_25", "bonus_rush_att_20", "bonus_rush_rec_yd_100",
  "bonus_rush_rec_yd_200",
  # reception-distance brackets
  "rec_0_4", "rec_5_9", "rec_10_19", "rec_20_29", "rec_30_39",
  # kicking
  "xpmiss", "fgm_yds", "fgm_yds_over_30", "fgmiss_0_19", "fgmiss_20_29",
  "fgmiss_30_39", "fgmiss_40_49", "fgmiss_50_59", "fgmiss_50p", "fgmiss_60p",
  # offense misc
  "fum_rec_td",
  # team defense
  "ff", "qb_hit", "sack_yd", "int_ret_yd", "fum_ret_yd", "tkl", "tkl_solo",
  "tkl_ast", "tkl_loss", "def_2pt", "def_pass_def", "def_3_and_out",
  "def_4_and_stop", "def_forced_punts",
  "yds_allow", "yds_allow_0_100", "yds_allow_100_199", "yds_allow_200_299",
  "yds_allow_300_349", "yds_allow_350_399", "yds_allow_400_449",
  "yds_allow_450_499", "yds_allow_500_549", "yds_allow_550p",
  # special-teams defense (team) and special-teams player
  "def_st_td", "def_st_ff", "def_st_fum_rec", "def_st_tkl_solo", "def_kr_yd",
  "def_pr_yd", "fg_ret_yd", "blk_kick_ret_yd",
  # st_td is MAPPED (see sleeper_map_sets$return_tds above); these three have no
  # ffanalytics rule and are estimated by draft/gap_fill.R.
  "st_ff", "st_fum_rec", "st_tkl_solo",
  # IDP
  "idp_tkl_loss", "idp_qb_hit", "idp_sack_yd", "idp_int_ret_yd",
  "idp_fum_ret_yd", "idp_blk_kick", "idp_pass_def_3p", "bonus_sack_2p",
  "bonus_tkl_10p", "bonus_def_fum_td_50p", "bonus_def_int_td_50p"
)

sleeper_scoring_universe <- c(
  sleeper_map_direct$sleeper_key,
  unlist(lapply(sleeper_map_sets, `[[`, "keys"), use.names = FALSE),
  sleeper_key_decomp_tkl,
  sleeper_keys_te_premium,
  sleeper_keys_bracket,
  sleeper_key_linear_pts_allow,
  sleeper_keys_unmappable
)
stopifnot(!anyDuplicated(sleeper_scoring_universe))

# ---- skeleton ---------------------------------------------------------------

# Same shape as ffanalytics::scoring (R/scoring_rules.R) with every value
# zeroed, except dst_pts_allowed = 1 (bracket enable; see header note).
zeroed_ffa_scoring <- function() {
  list(
    pass = list(pass_att = 0, pass_comp = 0, pass_inc = 0, pass_yds = 0,
                pass_tds = 0, pass_int = 0, pass_40_yds = 0, pass_300_yds = 0,
                pass_350_yds = 0, pass_400_yds = 0),
    rush = list(all_pos = TRUE, rush_yds = 0, rush_att = 0, rush_40_yds = 0,
                rush_tds = 0, rush_100_yds = 0, rush_150_yds = 0,
                rush_200_yds = 0),
    rec = list(all_pos = TRUE, rec = 0, rec_yds = 0, rec_tds = 0,
               rec_40_yds = 0, rec_100_yds = 0, rec_150_yds = 0,
               rec_200_yds = 0),
    misc = list(all_pos = TRUE, fumbles_lost = 0, fumbles_total = 0,
                sacks = 0, two_pts = 0),
    kick = list(xp = 0, fg_0019 = 0, fg_2029 = 0, fg_3039 = 0, fg_4049 = 0,
                fg_50 = 0, fg_miss = 0),
    ret = list(all_pos = TRUE, return_tds = 0, return_yds = 0),
    idp = list(all_pos = TRUE, idp_solo = 0, idp_asst = 0, idp_sack = 0,
               idp_int = 0, idp_fum_force = 0, idp_fum_rec = 0, idp_pd = 0,
               idp_td = 0, idp_safety = 0),
    dst = list(dst_fum_rec = 0, dst_int = 0, dst_safety = 0, dst_sacks = 0,
               dst_td = 0, dst_blk = 0, dst_ret_yds = 0, dst_pts_allowed = 1),
    pts_bracket = list(list(threshold = 99, points = 0))
  )
}

# ---- translation ------------------------------------------------------------

# scoring_settings: named list/vector straight from the Sleeper league JSON.
# Returns list(scoring, disclosed, unknown, mapped):
#   scoring   ffanalytics-shaped scoring list, ready for projections_table()
#   disclosed named list of nonzero known-unmappable keys (incl. set conflicts)
#   unknown   named list of nonzero keys OUTSIDE the universe (caller must stop)
#   mapped    named numeric of nonzero keys that were translated
translate_scoring <- function(scoring_settings) {
  vals <- vapply(scoring_settings, as.numeric, numeric(1))
  nz <- vals[!is.na(vals) & vals != 0]

  unknown <- as.list(nz[setdiff(names(nz), sleeper_scoring_universe)])
  disclosed <- list()
  mapped <- numeric(0)
  out <- zeroed_ffa_scoring()

  # direct copies
  hit <- sleeper_map_direct[sleeper_map_direct$sleeper_key %in% names(nz), ]
  for (i in seq_len(nrow(hit))) {
    out[[hit$ffa_group[i]]][[hit$ffa_key[i]]] <- nz[[hit$sleeper_key[i]]]
    mapped[hit$sleeper_key[i]] <- nz[[hit$sleeper_key[i]]]
  }

  # common-value sets
  for (set_name in names(sleeper_map_sets)) {
    set <- sleeper_map_sets[[set_name]]
    present <- intersect(set$keys, names(nz))
    if (length(present) == 0) next
    set_vals <- nz[present]
    if (length(unique(set_vals)) == 1) {
      out[[set$ffa_group]][[set$ffa_key]] <- unname(set_vals[1])
      mapped[present] <- set_vals
    } else {
      disclosed[[paste0("conflict:", set_name)]] <- as.list(set_vals)
    }
  }

  # exact decomposition: idp_tkl stacks on solo and assisted tackles
  if (sleeper_key_decomp_tkl %in% names(nz)) {
    v <- nz[[sleeper_key_decomp_tkl]]
    out$idp$idp_solo <- out$idp$idp_solo + v
    out$idp$idp_asst <- out$idp$idp_asst + v
    mapped[sleeper_key_decomp_tkl] <- v
  }

  # TE premium / per-position reception bonuses
  rec_bonus <- c(
    QB = 0,
    RB = unname(nz["bonus_rec_rb"] %||% 0),
    WR = unname(nz["bonus_rec_wr"] %||% 0),
    TE = unname(nz["bonus_rec_te"] %||% 0)
  )
  rec_bonus[is.na(rec_bonus)] <- 0
  if (any(rec_bonus != 0)) {
    flat_rec <- out$rec[setdiff(names(out$rec), "all_pos")]
    # A position-custom group must contain ONLY all_pos plus position sublists,
    # exactly as custom_scoring() builds it. Leaving the flat scalars alongside
    # the sublists blows up projections_table(), which does
    #   lapply(scoring_rules$rec[names != "all_pos"], `[[`, "rec")
    # to derive the league type and hits "subscript out of bounds" on a scalar.
    pos_rec_l <- list(all_pos = FALSE)
    for (pos in names(rec_bonus)) {
      pos_rec <- flat_rec
      pos_rec$rec <- pos_rec$rec + rec_bonus[[pos]]
      pos_rec_l[[pos]] <- pos_rec
    }
    out$rec <- pos_rec_l
    mapped[intersect(sleeper_keys_te_premium, names(nz))] <-
      nz[intersect(sleeper_keys_te_premium, names(nz))]
  }

  # points-allowed bracket (+ exact linear expansion when pts_allow is set)
  bracket_vals <- vapply(sleeper_keys_bracket,
                         function(k) unname(vals[k] %||% 0), numeric(1))
  bracket_vals[is.na(bracket_vals)] <- 0
  rate <- unname(nz[sleeper_key_linear_pts_allow] %||% 0)
  if (is.na(rate)) rate <- 0
  bracket_at <- function(pts) {
    bracket_vals[[which(pts <= sleeper_bracket_upper)[1]]]
  }
  if (rate == 0) {
    out$pts_bracket <- Map(function(thr, p) list(threshold = thr, points = p),
                           sleeper_bracket_upper, as.list(unname(bracket_vals)))
  } else {
    out$pts_bracket <- lapply(0:99, function(i) {
      list(threshold = i, points = bracket_at(i) + i * rate)
    })
    mapped[sleeper_key_linear_pts_allow] <- rate
  }
  mapped[intersect(sleeper_keys_bracket, names(nz))] <-
    nz[intersect(sleeper_keys_bracket, names(nz))]

  # known-unmappable disclosure
  for (k in intersect(sleeper_keys_unmappable, names(nz))) {
    disclosed[[k]] <- nz[[k]]
  }

  # Gate A assertion: every nonzero key landed in exactly one bucket.
  conflict_keys <- unlist(lapply(
    disclosed[grep("^conflict:", names(disclosed))], names), use.names = FALSE)
  classified <- c(names(mapped),
                  setdiff(names(disclosed), grep("^conflict:", names(disclosed), value = TRUE)),
                  conflict_keys, names(unknown))
  stopifnot(setequal(names(nz), classified))

  list(scoring = out, disclosed = disclosed, unknown = unknown, mapped = mapped)
}

# Loud, single-string report for batch output and the app's Status tab.
format_scoring_report <- function(tr) {
  lines <- character(0)
  if (length(tr$unknown)) {
    lines <- c(lines, "!! UNKNOWN SLEEPER SCORING KEYS (not in the mapped universe):",
               sprintf("   %s = %s", names(tr$unknown), unlist(tr$unknown)))
  }
  if (length(tr$disclosed)) {
    lines <- c(lines, "Known-unmappable scoring rules in this league (NOT reflected in projections):",
               vapply(names(tr$disclosed), function(k) {
                 v <- tr$disclosed[[k]]
                 if (is.list(v)) {
                   sprintf("   %s: %s", k,
                           paste(names(v), unlist(v), sep = "=", collapse = ", "))
                 } else {
                   sprintf("   %s = %s", k, v)
                 }
               }, character(1)))
  }
  if (!length(lines)) lines <- "All nonzero scoring rules translated cleanly."
  paste(lines, collapse = "\n")
}
