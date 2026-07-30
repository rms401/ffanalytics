# Rscript-runnable baseline tests.
# Usage: Rscript draft/tests/test_baseline.R

file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
test_dir <- dirname(normalizePath(file_arg))
source(file.path(dirname(test_dir), "vor_baseline.R"))

# Standard 12-team: QB/2RB/2WR/TE/FLEX/K/DEF
std <- derive_vor_baseline(
  c("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
    rep("BN", 6)),
  12
)
stopifnot(
  std[["QB"]] == 12,
  std[["RB"]] == 28,   # (2 + 1/3) * 12 = 28
  std[["WR"]] == 28,
  std[["TE"]] == 16,   # (1 + 1/3) * 12 = 16
  std[["K"]] == 12,
  std[["DST"]] == 12,
  !"DL" %in% names(std)
)

# Superflex TE-premium (the fixture league): QB/2RB/3WR/TE/2REC_FLEX/SUPER_FLEX, 12 teams
sf <- derive_vor_baseline(
  c("QB", "RB", "RB", "WR", "WR", "WR", "TE", "REC_FLEX", "REC_FLEX",
    "SUPER_FLEX", rep("BN", 15)),
  12
)
stopifnot(
  sf[["QB"]] == 24,    # QB + SUPER_FLEX
  sf[["RB"]] == 24,
  sf[["WR"]] == 48,    # (3 + 2/2) * 12
  sf[["TE"]] == 24,    # (1 + 2/2) * 12
  !"K" %in% names(sf), # zero-starter positions omitted (and not scraped)
  !"DST" %in% names(sf)
)

# IDP league: adds DL/LB/DB starters and IDP_FLEX
idp <- derive_vor_baseline(
  c("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
    "DL", "LB", "LB", "DB", "IDP_FLEX", rep("BN", 6)),
  10
)
stopifnot(
  idp[["DL"]] == round((1 + 1 / 3) * 10),  # 13
  idp[["LB"]] == round((2 + 1 / 3) * 10),  # 23
  idp[["DB"]] == round((1 + 1 / 3) * 10)   # 13
)

# Tiny league: nonzero starter share never rounds below 1
tiny <- derive_vor_baseline(c("QB", "TE", "FLEX"), 2)
stopifnot(tiny[["TE"]] >= 1, tiny[["QB"]] == 2)

cat("test_baseline.R: ALL PASSED\n")
