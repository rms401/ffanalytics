# Thin Sleeper API client. No auth; rate limit is 1000 req/min (we poll picks at
# 2 req/sec worst case). Style follows R/get_league_info.R's httr2 usage.
# Deps: httr2 (used only by draft/).

SLEEPER_BASE_URL <- "https://api.sleeper.app/v1"

sleeper_get <- function(path) {
  paste0(SLEEPER_BASE_URL, path) |>
    httr2::request() |>
    httr2::req_user_agent("ffanalytics draft tool (https://github.com/rms401/ffanalytics)") |>
    httr2::req_retry(max_tries = 2) |>
    httr2::req_perform() |>
    httr2::resp_body_json(check_type = FALSE)
}

# League settings: scoring_settings, roster_positions, total_rosters, name, season.
sleeper_league <- function(league_id) {
  sleeper_get(paste0("/league/", league_id))
}

# All drafts for a league (most recent first). Used to cache draft_id.
sleeper_drafts <- function(league_id) {
  sleeper_get(paste0("/league/", league_id, "/drafts"))
}

# A single draft: status (pre_draft / drafting / complete), settings, order.
sleeper_draft <- function(draft_id) {
  sleeper_get(paste0("/draft/", draft_id))
}

# Picks made so far. App-only, polled; returns a list of picks, each with
# player_id (Sleeper id), pick_no, round, picked_by.
sleeper_picks <- function(draft_id) {
  sleeper_get(paste0("/draft/", draft_id, "/picks"))
}

# Full NFL player map (~5MB). Batch-only; fetch at most daily.
# Returns a data.frame: sleeper_id, name, pos, team, status.
sleeper_players <- function() {
  pl <- sleeper_get("/players/nfl")
  data.frame(
    sleeper_id = names(pl),
    name = vapply(pl, function(x) x$full_name %||% paste(x$first_name, x$last_name), character(1)),
    pos = vapply(pl, function(x) x$position %||% NA_character_, character(1)),
    team = vapply(pl, function(x) x$team %||% NA_character_, character(1)),
    status = vapply(pl, function(x) x$status %||% NA_character_, character(1)),
    row.names = NULL
  )
}
