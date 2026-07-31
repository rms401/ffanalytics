# Gap fill: estimate Sleeper scoring stats that no ffanalytics rule expresses.
#
# WHY THIS IS SMALL
# -----------------
# Three quarters of Sleeper's "unmappable" key universe never needs an estimator:
#
#   * Keys with an exact ffanalytics rule are MAPPED, not estimated. `st_td` is
#     the example: Sleeper credits it for kick/punt/blocked-kick return TDs,
#     which is precisely `ret$return_tds` (projected by the NFL source). It moved
#     into sleeper_map_sets$return_tds in sleeper_scoring.R.
#
#   * Keys whose ffanalytics column the PACKAGE already synthesises need nothing
#     from us. impute_bonus_cols() (R/impute_funcs.R) builds rec_40_yds,
#     rush_40_yds, pass_40_yds and the *_100/150/200/300/400_yds families from
#     bonus_col_coefs whenever rec_yds/rush_yds/pass_yds are present, CREATING
#     the column when no source projects it. Injecting our own values there would
#     double-count. calibration_spec() below only measures those, never writes.
#
# What is left is a single estimator shape, applied through a declarative table:
#
#     estimate = rate(player) * projected_driver
#     rate     = (hist_num + k * pos_rate) / (hist_den + k)        [empirical Bayes]
#
# calibrated against Sleeper's own historical stats endpoint, which reports the
# exact keys the league scores. A future league key is a new ROW in gap_spec(),
# not new code. Everything here is closed-form arithmetic: no RNG, so two runs
# on the same inputs are bit-identical without seeding anything.
#
# Deps: jsonlite + base R only (so the unit tests run without the package).

if (!exists("%||%")) {
  `%||%` <- function(x, y) {
    if (is.null(x) || length(x) == 0 || (length(x) == 1 && is.na(x))) y else x
  }
}

SLEEPER_STATS_URL <- "https://api.sleeper.app/v1/stats/nfl/regular/%s"

# Seasons pooled for rate estimation, most recent first.
gap_seasons <- function(season) as.integer(season) - 1:3

# ---- the declarative key table ----------------------------------------------

# One row per estimable stat.
#   id           row name, and the stat column written for mode "new_rule"
#   league_keys  "+"-separated Sleeper scoring keys that activate the row; the
#                row fires if ANY of them is nonzero in the league
#   hist_num     "+"-separated Sleeper stat keys, summed, as the numerator
#   hist_den     "+"-separated denominator keys ("gp" = per game played)
#   driver_col   "+"-separated ffanalytics data columns, summed, that the rate
#                multiplies. "__games__" uses a flat 17-game season (the same
#                convention score_dst_pts_allowed uses for 2021+).
#   pos_scope    positions whose source tibbles receive the column
#   k            shrinkage prior weight, in denominator units. Large k means the
#                stat is dominated by the position rate, which is the honest
#                answer when per-player samples are a handful of events.
#   mode         "new_rule"    Class A: no ffanalytics rule exists. Creates the
#                              stat column AND the scoring rule.
#                "fill_column" Class B: the rule exists but no source in this
#                              scrape projects the stat. Fills the EXISTING
#                              ffanalytics column and adds no rule. Sources win:
#                              a column that any source actually projects is
#                              left completely alone.
#   target_col   ffanalytics column written (equals id for "new_rule")
#
# For "new_rule" the group is always `misc`, and that is deliberate: `misc` is
# flat and all_pos = TRUE in BOTH branches of make_scoring_tables()
# (R/custom_scoring.R). A TE-premium league sets rec$all_pos = FALSE and nests
# rec under QB/RB/WR/TE, so a key added at the top level of `rec` is silently
# dropped by `temp_scoring[[col]] = temp_scoring[[col]][[pos]]`. Position
# targeting is enforced at injection instead: a tibble that never receives the
# column cannot score it, because source_points() intersects data columns with
# the scoring table (R/calc_projections.R:250).
gap_spec <- function() {
  spec <- rbind(
    # ---- Class A: no ffanalytics rule exists ---------------------------------
    # TD-distance bonuses: share of that position's TDs travelling >= N yards.
    c("rec_td_40p",  "rec_td_40p",  "rec_td_40p",  "rec_td",   "rec_tds",   "QB,RB,WR,TE",  "20", "new_rule",    "rec_td_40p"),
    c("rec_td_50p",  "rec_td_50p",  "rec_td_50p",  "rec_td",   "rec_tds",   "QB,RB,WR,TE",  "20", "new_rule",    "rec_td_50p"),
    c("rush_td_40p", "rush_td_40p", "rush_td_40p", "rush_td",  "rush_tds",  "QB,RB,WR,TE",  "20", "new_rule",    "rush_td_40p"),
    c("rush_td_50p", "rush_td_50p", "rush_td_50p", "rush_td",  "rush_tds",  "QB,RB,WR,TE",  "20", "new_rule",    "rush_td_50p"),
    c("pass_td_40p", "pass_td_40p", "pass_td_40p", "pass_td",  "pass_tds",  "QB,RB,WR,TE",  "20", "new_rule",    "pass_td_40p"),
    c("pass_td_50p", "pass_td_50p", "pass_td_50p", "pass_td",  "pass_tds",  "QB,RB,WR,TE",  "20", "new_rule",    "pass_td_50p"),
    # pick-six thrown, per interception
    c("pass_int_td", "pass_int_td", "pass_int_td", "pass_int", "pass_int",  "QB,RB,WR,TE",  "20", "new_rule",    "pass_int_td"),
    # first downs, per completion / carry / reception
    c("pass_fd",     "pass_fd",     "pass_fd",     "pass_cmp", "pass_comp", "QB,RB,WR,TE", "200", "new_rule",    "pass_fd"),
    c("rush_fd",     "rush_fd",     "rush_fd",     "rush_att", "rush_att",  "QB,RB,WR,TE", "100", "new_rule",    "rush_fd"),
    c("rec_fd",      "rec_fd",      "rec_fd",      "rec",      "rec",       "QB,RB,WR,TE",  "50", "new_rule",    "rec_fd"),
    # rare per-game events for offensive players: heavily shrunk to the pos rate
    c("fum_rec_td",  "fum_rec_td",  "fum_rec_td",  "gp",       "__games__", "QB,RB,WR,TE", "200", "new_rule",    "fum_rec_td"),
    c("st_ff",       "st_ff",       "st_ff",       "gp",       "__games__", "QB,RB,WR,TE", "200", "new_rule",    "st_ff"),
    c("st_fum_rec",  "st_fum_rec",  "st_fum_rec",  "gp",       "__games__", "QB,RB,WR,TE", "200", "new_rule",    "st_fum_rec"),
    c("st_tkl_solo", "st_tkl_solo", "st_tkl_solo", "gp",       "__games__", "QB,RB,WR,TE", "200", "new_rule",    "st_tkl_solo"),

    # ---- Class B: rule exists, but only one source ever projects the stat ----
    # two_pts and return_tds come solely from the NFL.com scrape (nfl_columns in
    # R/source_objects.R). When that source is missing or empty the rule scores
    # nothing at all, so we estimate the column rather than lose the rule.
    c("two_pts",     "pass_2pt+rush_2pt+rec_2pt", "pass_2pt+rush_2pt+rec_2pt",
      "pass_td+rush_td+rec_td", "pass_tds+rush_tds+rec_tds", "QB,RB,WR,TE", "30", "fill_column", "two_pts"),
    c("return_tds",  "st_td+kr_td+pr_td",         "st_td+kr_td+pr_td",
      "gp",                     "__games__",                 "QB,RB,WR,TE", "200", "fill_column", "return_tds")
  )
  out <- data.frame(spec, stringsAsFactors = FALSE)
  names(out) <- c("id", "league_keys", "hist_num", "hist_den", "driver_col",
                  "pos_scope", "k", "mode", "target_col")
  out$k <- as.numeric(out$k)
  out
}

# Split a "+"-separated field into its parts.
gap_parts <- function(x) trimws(strsplit(x, "+", fixed = TRUE)[[1]])

# Sum of one or more history columns.
hist_sum <- function(hist, field) {
  cols <- gap_parts(field)
  cols <- cols[cols %in% colnames(hist)]
  if (!length(cols)) return(rep(0, nrow(hist)))
  rowSums(hist[, cols, drop = FALSE])
}

# Rows of the spec activated by this league's nonzero scoring keys.
gap_spec_for_league <- function(spec, league_vals) {
  keep <- vapply(spec$league_keys, function(lk) {
    any(gap_parts(lk) %in% names(league_vals))
  }, logical(1))
  spec[keep, , drop = FALSE]
}

# Keys the PACKAGE fills via impute_bonus_cols(). We never write these; we only
# compare the package's implied rate against Sleeper's actual rate and report the
# delta, because bonus_col_coefs were fit on a single prior season via nflfastR
# and the *_40_yds coefficients apply a per-game intercept to a season total.
calibration_spec <- function() {
  spec <- rbind(
    c("rec_40_yds",   "rec_40p",           "rec_yd",  "rec_yds"),
    c("rush_40_yds",  "rush_40p",          "rush_yd", "rush_yds"),
    c("pass_40_yds",  "pass_cmp_40p",      "pass_yd", "pass_yds"),
    c("rec_100_yds",  "bonus_rec_yd_100",  "rec_yd",  "rec_yds"),
    c("rec_200_yds",  "bonus_rec_yd_200",  "rec_yd",  "rec_yds"),
    c("rush_100_yds", "bonus_rush_yd_100", "rush_yd", "rush_yds"),
    c("rush_200_yds", "bonus_rush_yd_200", "rush_yd", "rush_yds"),
    c("pass_300_yds", "bonus_pass_yd_300", "pass_yd", "pass_yds"),
    c("pass_400_yds", "bonus_pass_yd_400", "pass_yd", "pass_yds")
  )
  out <- data.frame(spec, stringsAsFactors = FALSE)
  names(out) <- c("ffa_col", "hist_num", "hist_den", "driver_col")
  out
}

# ---- historical stats -------------------------------------------------------

# Cached per season. A failed download never clobbers a good cache (same
# temp-validate-promote discipline as crosswalk.R).
fetch_sleeper_season_stats <- function(season, cache_dir,
                                       max_age_hours = 24 * 7) {
  dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  dest <- file.path(cache_dir, sprintf("sleeper_stats_%s.json", season))
  age <- if (file.exists(dest)) {
    as.numeric(difftime(Sys.time(), file.mtime(dest), units = "hours"))
  } else {
    Inf
  }
  looks_valid <- function(path) {
    if (!file.exists(path) || file.size(path) < 10000) return(FALSE)
    ok <- tryCatch({
      x <- jsonlite::fromJSON(path, simplifyVector = FALSE)
      is.list(x) && length(x) > 100
    }, error = function(e) FALSE)
    isTRUE(ok)
  }
  if (age > max_age_hours) {
    tmp <- tempfile(fileext = ".json")
    ok <- tryCatch({
      utils::download.file(sprintf(SLEEPER_STATS_URL, season), tmp,
                           quiet = TRUE, method = "libcurl")
      looks_valid(tmp)
    }, error = function(e) FALSE, warning = function(w) FALSE)
    if (ok) {
      file.copy(tmp, dest, overwrite = TRUE)
    } else if (!looks_valid(dest)) {
      stop("Could not fetch Sleeper stats for ", season, " (",
           sprintf(SLEEPER_STATS_URL, season), ") and no valid cache at ", dest)
    } else {
      warning("Sleeper stats download failed for ", season,
              "; using cached copy from ", format(file.mtime(dest)))
    }
  }
  jsonlite::fromJSON(dest, simplifyVector = FALSE)
}

# One numeric matrix per season: rows = sleeper_id, cols = the requested keys.
# An absent stat counts as 0, which is correct - Sleeper omits a key entirely
# for a player who never recorded it.
gap_hist_season_matrices <- function(seasons, keys, cache_dir,
                                     max_age_hours = 24 * 7) {
  keys <- unique(c(keys, "gp"))
  per_season <- list()
  for (s in seasons) {
    st <- fetch_sleeper_season_stats(s, cache_dir, max_age_hours)
    st <- st[!vapply(st, is.null, logical(1))]
    sid <- names(st)
    m <- matrix(0, nrow = length(sid), ncol = length(keys),
                dimnames = list(sid, keys))
    for (k in keys) {
      m[, k] <- vapply(st, function(x) {
        v <- x[[k]]
        if (is.null(v) || length(v) != 1 || !is.numeric(v) || is.na(v)) {
          0
        } else {
          as.numeric(v)
        }
      }, numeric(1))
    }
    per_season[[as.character(s)]] <- m
  }
  per_season
}

# Totals pooled across seasons, one row per player. This is what the rate
# estimator wants: more seasons means a bigger denominator and less shrinkage.
gap_hist_totals <- function(seasons, keys, cache_dir, max_age_hours = 24 * 7) {
  per_season <- gap_hist_season_matrices(seasons, keys, cache_dir, max_age_hours)
  ids <- unique(unlist(lapply(per_season, rownames), use.names = FALSE))
  cols <- colnames(per_season[[1]])
  out <- matrix(0, nrow = length(ids), ncol = length(cols),
                dimnames = list(ids, cols))
  for (m in per_season) {
    out[rownames(m), ] <- out[rownames(m), , drop = FALSE] + m
  }
  out
}

# One row per PLAYER-SEASON, stacked. Calibration needs this: a season total is
# the unit a projection predicts, and pooling three seasons would compare a
# one-season projection against a three-season total.
gap_hist_stacked <- function(seasons, keys, cache_dir, max_age_hours = 24 * 7) {
  per_season <- gap_hist_season_matrices(seasons, keys, cache_dir, max_age_hours)
  do.call(rbind, per_season)
}

# ---- rate fitting -----------------------------------------------------------

# hist:        matrix from gap_hist_totals()
# spec:        rows of gap_spec() to fit
# sleeper_pos: named character, sleeper_id -> position
# Returns list keyed by sleeper_key: list(player = named numeric by sleeper_id,
#   pos_rate = named numeric by position, overall = numeric).
fit_gap_rates <- function(hist, spec, sleeper_pos) {
  ids <- rownames(hist)
  pos <- unname(sleeper_pos[ids])
  pos[is.na(pos)] <- "UNK"
  out <- list()
  for (i in seq_len(nrow(spec))) {
    key <- spec$id[i]
    num <- hist_sum(hist, spec$hist_num[i])
    den <- hist_sum(hist, spec$hist_den[i])

    overall <- if (sum(den) > 0) sum(num) / sum(den) else 0
    pos_num <- tapply(num, pos, sum)
    pos_den <- tapply(den, pos, sum)
    pos_rate <- ifelse(pos_den > 0, pos_num / pos_den, overall)
    pos_rate <- pmax(pos_rate, 0)

    prior <- unname(pos_rate[pos])
    prior[!is.finite(prior)] <- overall

    k <- spec$k[i]
    player <- (num + k * prior) / (den + k)
    player[!is.finite(player)] <- prior[!is.finite(player)]
    player <- pmax(player, 0)
    names(player) <- ids

    out[[key]] <- list(player = player, pos_rate = pos_rate,
                       overall = max(overall, 0))
  }
  out
}

# ---- injection --------------------------------------------------------------

# Adds one column per estimable nonzero league key to the source tibbles of the
# positions in scope, and extends the scoring rules with the league's value.
#
# The estimate is computed PER SOURCE from that source's own driver column, not
# as one constant per player: a source projecting more receiving TDs should earn
# proportionally more 40+ yard TD bonus, and this keeps the bonus correlated with
# the projection it derives from. Where a source does not project the driver the
# injected value is NA on purpose - the package's own
# impute_via_rates_and_mean() then fills it with the player's cross-source mean,
# because the injected column carries a nonzero rule and is not in
# impute_fun_list (R/impute_funcs.R:145-159).
#
# Returns list(data_result, estimates, skipped, keys). Extend the scoring rules
# with extend_rules_with_gap_keys(rules, result$keys, league_vals).
inject_gap_stats <- function(data_result, rates, spec, league_vals,
                             mfl_to_sleeper, games = 17) {
  est <- list()
  skipped <- list()
  applied <- character(0)   # spec ids actually written somewhere

  for (pos in names(data_result)) {
    df <- data_result[[pos]]
    if (is.null(df) || nrow(df) == 0) next
    sid <- unname(mfl_to_sleeper[as.character(df$id)])

    for (i in seq_len(nrow(spec))) {
      id <- spec$id[i]
      col <- spec$target_col[i]
      if (!pos %in% trimws(strsplit(spec$pos_scope[i], ",")[[1]])) next

      if (col %in% names(df)) {
        if (spec$mode[i] == "new_rule") {
          # A Class-A key must not already exist: writing it would double-count
          # against whatever produced it (a source, or impute_bonus_cols).
          stop("gap_fill would overwrite existing column '", col, "' in the ",
               pos, " source data - remove it from gap_spec() (the package or ",
               "a source already provides this stat).")
        }
        # Class B: real projections beat estimates. Only fill a column that is
        # entirely missing for this position.
        if (any(!is.na(df[[col]]))) {
          skipped[[paste(pos, id, sep = ":")]] <- sprintf(
            "%s: sources project '%s' for %s - left alone", id, col, pos)
          next
        }
      }

      drv <- spec$driver_col[i]
      if (drv == "__games__") {
        driver <- rep(games, nrow(df))
      } else {
        dcols <- gap_parts(drv)
        have <- dcols[dcols %in% names(df)]
        if (!length(have)) {
          skipped[[paste(pos, id, sep = ":")]] <- sprintf(
            "%s: no driver column '%s' in %s source data", id, drv, pos)
          next
        }
        driver <- rowSums(
          as.data.frame(lapply(df[have], function(x) suppressWarnings(as.numeric(x)))),
          na.rm = FALSE)
      }

      r <- rates[[id]]
      prior <- r$pos_rate[[pos]] %||% r$overall
      if (!is.finite(prior)) prior <- r$overall
      rate <- unname(r$player[sid])
      rate[is.na(rate)] <- prior

      v <- rate * driver
      v[is.finite(v) & v < 0] <- 0
      df[[col]] <- v
      applied <- union(applied, id)

      est[[length(est) + 1]] <- data.frame(
        pos = pos, id = id, column = col, mfl_id = as.character(df$id), est = v,
        stringsAsFactors = FALSE
      )
    }
    data_result[[pos]] <- df
  }

  # League keys whose points now flow through a gap-filled column, mapped to the
  # column carrying them. The batch uses this to label each rule's method.
  covered <- character(0)
  for (i in seq_len(nrow(spec))) {
    if (!spec$id[i] %in% applied) next
    for (k in intersect(gap_parts(spec$league_keys[i]), names(league_vals))) {
      covered[k] <- spec$target_col[i]
    }
  }

  list(data_result = data_result,
       estimates = if (length(est)) do.call(rbind, est) else NULL,
       skipped = skipped,
       applied = applied,
       covered = covered,
       new_rules = spec$id[spec$mode == "new_rule" & spec$id %in% applied])
}

# Add a scoring rule for each Class-A key that was injected. Class-B rows never
# reach here: their rule already exists, only the stat column was missing.
extend_rules_with_gap_keys <- function(rules, keys, league_vals) {
  for (key in keys) {
    rules$misc[[key]] <- as.numeric(league_vals[[key]])
  }
  rules
}

# ---- rule flattening / reporting helpers ------------------------------------

# Flat (group, column, val) view of a scoring list, handling the per-position
# nesting a TE-premium league produces. Used to ask "does this rule contribute?".
flatten_rules <- function(rules) {
  rows <- list()
  add <- function(g, col, val) {
    rows[[length(rows) + 1]] <<- data.frame(
      group = g, column = col, val = as.numeric(val), stringsAsFactors = FALSE)
  }
  for (g in setdiff(names(rules), "pts_bracket")) {
    grp <- rules[[g]]
    if (!is.list(grp)) next
    for (nm in names(grp)) {
      if (nm == "all_pos") next
      el <- grp[[nm]]
      if (is.list(el)) {
        for (c2 in names(el)) {
          if (c2 == "all_pos") next
          add(g, c2, el[[c2]])
        }
      } else {
        add(g, nm, el)
      }
    }
  }
  if (!length(rows)) return(data.frame(group = character(0), column = character(0),
                                       val = numeric(0)))
  d <- do.call(rbind, rows)
  # one row per column, keeping the largest magnitude across positions
  d <- d[order(d$column, -abs(d$val)), ]
  d[!duplicated(d$column), ]
}

# sleeper_key -> ffanalytics data column, for the keys translate_scoring mapped.
mapped_key_columns <- function(mapped_names) {
  m <- character(0)
  hit <- sleeper_map_direct[sleeper_map_direct$sleeper_key %in% mapped_names, ]
  if (nrow(hit)) m[hit$sleeper_key] <- hit$ffa_key
  for (s in sleeper_map_sets) {
    for (k in intersect(s$keys, mapped_names)) m[k] <- s$ffa_key
  }
  for (k in intersect(sleeper_keys_te_premium, mapped_names)) m[k] <- "rec"
  if (sleeper_key_decomp_tkl %in% mapped_names) {
    m[sleeper_key_decomp_tkl] <- "idp_solo"
  }
  for (k in intersect(c(sleeper_keys_bracket, sleeper_key_linear_pts_allow),
                      mapped_names)) {
    m[k] <- "dst_pts_allowed"
  }
  m
}

# Columns the package synthesises rather than the sources projecting them.
package_imputed_columns <- function() calibration_spec()$ffa_col

# Mean absolute points a column contributes across the top 50 players (by VOR)
# at its most affected position. raw_stats is projections_table(return_raw_stats
# = TRUE) output; proj is the scored projections table.
gap_materiality <- function(raw_stats, proj, col, val, avg_type) {
  if (!col %in% names(raw_stats)) return(list(pts = 0, pos = NA_character_))
  rs <- as.data.frame(raw_stats)
  rs <- rs[rs$avg_type == avg_type, c("id", col)]
  p <- as.data.frame(proj)
  p <- p[p$avg_type == avg_type, c("id", "pos", "points_vor")]
  j <- merge(rs, p, by = "id")
  if (nrow(j) == 0) return(list(pts = 0, pos = NA_character_))
  best <- 0
  best_pos <- NA_character_
  for (ps in unique(j$pos)) {
    s <- j[j$pos == ps, ]
    s <- s[order(-s$points_vor), ]
    s <- s[seq_len(min(50, nrow(s))), ]
    mv <- mean(abs(s[[col]] * val), na.rm = TRUE)
    if (is.finite(mv) && mv > best) {
      best <- mv
      best_pos <- ps
    }
  }
  list(pts = best, pos = best_pos)
}

# Compare the package's imputed bonus columns against what actually happened.
#
# This has to be BAND MATCHED. The relationship between a season yardage total
# and the number of 40-yard plays or 100-yard games is convex, and the Sleeper
# stats dump contains thousands of low-volume players who are absent from the
# projected population. Comparing a ratio of totals across those two different
# population mixes understates the historical rate and makes every column look
# inflated. Instead: take the projected players who matter (driver at or above
# the median of the projected non-zero population), then score BOTH populations
# over the same driver range.
#
# Returns per column: the package's mean output and the historical mean actual
# for a player at the same driver level, with their ratio. ratio > 1 means the
# package awards more of the bonus than players historically earn.
# hist_stacked: one row per player-season, from gap_hist_stacked().
calibrate_bonus_columns <- function(raw_stats, hist_stacked, avg_type,
                                    min_hist_n = 20) {
  cs <- calibration_spec()
  rs <- as.data.frame(raw_stats)
  rs <- rs[rs$avg_type == avg_type, ]
  rows <- list()
  for (i in seq_len(nrow(cs))) {
    col <- cs$ffa_col[i]
    drv <- cs$driver_col[i]
    if (!all(c(col, drv) %in% names(rs))) next

    d <- suppressWarnings(as.numeric(rs[[drv]]))
    v <- suppressWarnings(as.numeric(rs[[col]]))
    keep <- is.finite(d) & d > 0 & is.finite(v)
    if (sum(keep) < 5) next
    d <- d[keep]
    v <- v[keep]
    # the half of the projected population that is actually draftable
    band <- d >= stats::median(d)
    if (!any(band)) next
    lo <- min(d[band])
    hi <- max(d[band])

    # historical player-seasons over the same driver range
    hd <- hist_sum(hist_stacked, cs$hist_den[i])
    hn <- hist_sum(hist_stacked, cs$hist_num[i])
    hb <- is.finite(hd) & hd >= lo & hd <= hi
    if (sum(hb) < min_hist_n) next

    pkg <- mean(v[band], na.rm = TRUE)
    act <- mean(hn[hb], na.rm = TRUE)
    rows[[length(rows) + 1]] <- data.frame(
      column = col, driver_lo = round(lo), driver_hi = round(hi),
      n_hist = sum(hb), package_mean = pkg, actual_mean = act,
      ratio = if (act > 0) pkg / act else NA_real_,
      stringsAsFactors = FALSE
    )
  }
  if (!length(rows)) {
    return(data.frame(column = character(0), driver_lo = numeric(0),
                      driver_hi = numeric(0), n_hist = integer(0),
                      package_mean = numeric(0), actual_mean = numeric(0),
                      ratio = numeric(0)))
  }
  do.call(rbind, rows)
}
