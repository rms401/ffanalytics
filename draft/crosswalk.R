# MFL-id -> Sleeper-id crosswalk.
#
# Primary source: DynastyProcess db_playerids.csv (auto-updated weekly), which
# carries both mfl_id (the package's player id) and sleeper_id. Fallback for
# rows it misses: exact cleaned-name + position match against the cached
# Sleeper player table, requiring a UNIQUE candidate (team as tiebreak) —
# never a guess between two players. DSTs match by team code, which IS the
# Sleeper player_id for defenses.

DP_PLAYERIDS_URL <-
  "https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv"

fetch_dp_playerids <- function(cache_dir = file.path(draft_root(), "cache"),
                               max_age_hours = 24) {
  dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  dest <- file.path(cache_dir, "db_playerids.csv")
  age <- if (file.exists(dest)) {
    as.numeric(difftime(Sys.time(), file.mtime(dest), units = "hours"))
  } else {
    Inf
  }
  # A failed download must never clobber a good cache: fetch to a temp file,
  # validate it actually looks like the player-id CSV, then promote it.
  looks_valid <- function(path) {
    if (!file.exists(path) || file.size(path) < 1000) return(FALSE)
    header <- tryCatch(readLines(path, n = 1), error = function(e) "")
    grepl("mfl_id", header) && grepl("sleeper_id", header)
  }
  if (age > max_age_hours) {
    tmp <- tempfile(fileext = ".csv")
    ok <- tryCatch({
      utils::download.file(DP_PLAYERIDS_URL, tmp, quiet = TRUE,
                           method = "libcurl")
      looks_valid(tmp)
    }, error = function(e) FALSE, warning = function(w) FALSE)
    if (ok) {
      file.copy(tmp, dest, overwrite = TRUE)
    } else if (!looks_valid(dest)) {
      stop("Could not download DynastyProcess player ids (",
           DP_PLAYERIDS_URL, ") and no valid cached copy exists at ", dest)
    } else {
      warning("DynastyProcess download failed; using cached copy from ",
              format(file.mtime(dest)))
    }
  }
  df <- utils::read.csv(dest, stringsAsFactors = FALSE,
                        colClasses = "character")
  df[df == ""] <- NA
  df
}

clean_player_name <- function(x) {
  x <- tolower(x)
  x <- gsub("\\.", "", x)
  x <- gsub("\\b(jr|sr|ii|iii|iv|v)\\b", "", x)
  x <- gsub("[^a-z ]", "", x)
  trimws(gsub(" +", " ", x))
}

# rankings: data.frame with mfl_id, player, pos, team (one row per
#   league/avg_type/player; sleeper_id filled identically across avg_types).
# sleeper_players: data.frame from db (sleeper_id, name, pos, team).
# Returns list(rankings = <with sleeper_id>, report = list(...)).
attach_sleeper_ids <- function(rankings, sleeper_players,
                               cache_dir = file.path(draft_root(), "cache")) {
  dp <- fetch_dp_playerids(cache_dir)
  stopifnot(all(c("mfl_id", "sleeper_id") %in% names(dp)))
  xw <- dp[!is.na(dp$mfl_id) & !is.na(dp$sleeper_id), c("mfl_id", "sleeper_id")]
  xw <- xw[!duplicated(xw$mfl_id), ]
  id_map <- stats::setNames(xw$sleeper_id, xw$mfl_id)

  rankings$sleeper_id <- unname(id_map[as.character(rankings$mfl_id)])

  # DSTs: Sleeper's player_id for a defense is the team code.
  dst_teams <- sleeper_players$sleeper_id[sleeper_players$pos %in% "DEF"]
  dst_idx <- is.na(rankings$sleeper_id) & rankings$pos %in% "DST" &
    rankings$team %in% dst_teams
  rankings$sleeper_id[dst_idx] <- rankings$team[dst_idx]

  # Name + position fallback, unique candidate only.
  sp <- sleeper_players[!is.na(sleeper_players$pos), ]
  sp$pos[sp$pos == "DEF"] <- "DST"
  sp$clean <- clean_player_name(sp$name)
  miss_idx <- which(is.na(rankings$sleeper_id))
  for (i in miss_idx) {
    cand <- sp[sp$clean == clean_player_name(rankings$player[i]) &
                 sp$pos == rankings$pos[i], ]
    if (nrow(cand) > 1 && !is.na(rankings$team[i])) {
      cand <- cand[!is.na(cand$team) & cand$team == rankings$team[i], ]
    }
    if (nrow(cand) == 1) rankings$sleeper_id[i] <- cand$sleeper_id
  }

  # Gate G: a sleeper_id must not appear on two different players.
  by_type <- split(rankings, rankings$avg_type)
  for (chunk in by_type) {
    ids <- chunk$sleeper_id[!is.na(chunk$sleeper_id)]
    if (anyDuplicated(ids)) {
      dup <- unique(ids[duplicated(ids)])
      stop("Crosswalk produced duplicate sleeper_id(s): ",
           paste(dup, collapse = ", "))
    }
  }

  top <- rankings[!is.na(rankings$rank) & rankings$rank <= 200, ]
  report <- list(
    total = nrow(rankings),
    matched = sum(!is.na(rankings$sleeper_id)),
    top200 = nrow(top),
    top200_matched = sum(!is.na(top$sleeper_id)),
    unmatched_top200 = unique(
      top[is.na(top$sleeper_id), c("player", "pos", "team", "rank")]
    )
  )
  list(rankings = rankings, report = report)
}
