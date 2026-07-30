# League-true VOR baseline from Sleeper roster settings.
#
# Convention: the baseline is the last STARTER-quality player at each position
# (bench deliberately ignored). Flex slots are split evenly across their
# eligible positions; SUPER_FLEX counts as a full QB slot (it is nearly always
# filled with a QB).

# roster_positions: character vector from the league JSON, e.g.
#   c("QB","RB","RB","WR","WR","TE","FLEX","K","DEF","BN",...)
# total_rosters: number of teams.
# Returns a named numeric vector like ffanalytics::default_baseline, containing
# only positions with a nonzero starter share.
derive_vor_baseline <- function(roster_positions, total_rosters) {
  slots <- table(roster_positions)
  per_team <- c(QB = 0, RB = 0, WR = 0, TE = 0, K = 0, DST = 0,
                DL = 0, LB = 0, DB = 0)

  direct <- c(QB = "QB", RB = "RB", WR = "WR", TE = "TE", K = "K",
              DEF = "DST", DL = "DL", LB = "LB", DB = "DB",
              IDP = NA, DE = "DL", DT = "DL", CB = "DB", S = "DB")
  for (slot in names(slots)) {
    n <- as.numeric(slots[[slot]])
    if (slot %in% c("BN", "IR", "TAXI")) next
    if (slot %in% names(direct) && !is.na(direct[[slot]])) {
      per_team[[direct[[slot]]]] <- per_team[[direct[[slot]]]] + n
    } else if (slot == "FLEX") {
      per_team[c("RB", "WR", "TE")] <- per_team[c("RB", "WR", "TE")] + n / 3
    } else if (slot == "WRRB_FLEX") {
      per_team[c("RB", "WR")] <- per_team[c("RB", "WR")] + n / 2
    } else if (slot == "REC_FLEX") {
      per_team[c("WR", "TE")] <- per_team[c("WR", "TE")] + n / 2
    } else if (slot == "SUPER_FLEX") {
      per_team[["QB"]] <- per_team[["QB"]] + n
    } else if (slot == "IDP_FLEX") {
      per_team[c("DL", "LB", "DB")] <- per_team[c("DL", "LB", "DB")] + n / 3
    } else {
      warning("Unknown roster slot '", slot, "' ignored in baseline derivation")
    }
  }

  baseline <- round(per_team * total_rosters)
  baseline <- pmax(baseline, ifelse(per_team > 0, 1, 0))
  baseline[baseline > 0]
}
