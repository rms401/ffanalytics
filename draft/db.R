# SQLite persistence for the draft tool. One file DB: draft/draft.sqlite.
# Deps: DBI, RSQLite (used only by draft/, not by the ffanalytics package).

`%||%` <- function(x, y) if (is.null(x) || length(x) == 0 || (length(x) == 1 && is.na(x))) y else x

now_iso <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z")

# Batch scripts run from the repo root; the Shiny app runs with draft/ as the
# working directory. Resolve draft/ from either.
draft_root <- function() {
  if (basename(getwd()) == "draft") getwd() else file.path(getwd(), "draft")
}

draft_db_path <- function() {
  Sys.getenv("DRAFT_DB", file.path(draft_root(), "draft.sqlite"))
}

open_db <- function(path = draft_db_path()) {
  con <- DBI::dbConnect(RSQLite::SQLite(), path)
  DBI::dbExecute(con, "PRAGMA foreign_keys = ON")
  con
}

init_db <- function(con, schema_path = file.path(draft_root(), "schema.sql")) {
  ddl <- paste(readLines(schema_path, warn = FALSE), collapse = "\n")
  ddl <- gsub("--[^\n]*", "", ddl)
  for (stmt in strsplit(ddl, ";", fixed = TRUE)[[1]]) {
    if (nzchar(trimws(stmt))) DBI::dbExecute(con, stmt)
  }
  migrate_db(con)
  invisible(con)
}

# CREATE TABLE IF NOT EXISTS cannot add a column to a database that already
# exists, so columns added after the first release are patched in here. Each
# ALTER is a no-op error on a database that already has the column.
migrate_db <- function(con) {
  added <- list(league = c(gap_method_json = "TEXT"))
  for (tbl in names(added)) {
    have <- DBI::dbGetQuery(con, sprintf("PRAGMA table_info(%s)", tbl))$name
    for (col in names(added[[tbl]])) {
      if (!col %in% have) {
        DBI::dbExecute(con, sprintf("ALTER TABLE %s ADD COLUMN %s %s",
                                    tbl, col, added[[tbl]][[col]]))
      }
    }
  }
  invisible(con)
}

# ---- league ----------------------------------------------------------------

upsert_league <- function(con, league) {
  league$updated_at <- now_iso()
  cols <- c("league_id", "name", "season", "total_rosters", "roster_positions_json",
            "scoring_settings_json", "translated_scoring_json", "unmapped_keys_json",
            "gap_method_json", "vor_baseline_json", "draft_id", "scraped_at",
            "updated_at")
  league <- league[intersect(cols, names(league))]
  sets <- paste0(setdiff(names(league), "league_id"), " = excluded.",
                 setdiff(names(league), "league_id"), collapse = ", ")
  sql <- sprintf(
    "INSERT INTO league (%s) VALUES (%s) ON CONFLICT(league_id) DO UPDATE SET %s",
    paste(names(league), collapse = ", "),
    paste0("$", names(league), collapse = ", "),
    sets
  )
  DBI::dbExecute(con, sql, params = league)
  invisible(TRUE)
}

get_league <- function(con, league_id) {
  df <- DBI::dbGetQuery(con, "SELECT * FROM league WHERE league_id = $id",
                        params = list(id = league_id))
  if (nrow(df) == 0) NULL else as.list(df[1, ])
}

list_leagues <- function(con) {
  DBI::dbGetQuery(con, "SELECT league_id, name, season, scraped_at FROM league ORDER BY updated_at DESC")
}

set_league_field <- function(con, league_id, field, value) {
  stopifnot(field %in% c("draft_id", "scraped_at", "gap_method_json"))
  DBI::dbExecute(
    con,
    sprintf("UPDATE league SET %s = $val, updated_at = $now WHERE league_id = $id", field),
    params = list(val = value, now = now_iso(), id = league_id)
  )
  invisible(TRUE)
}

# ---- params ----------------------------------------------------------------

default_params <- function() {
  list(
    sources_json = jsonlite::toJSON(c("CBS", "ESPN", "FantasyPros", "FantasySharks",
                                      "FFToday", "FleaFlicker", "FantasyFootballNerd",
                                      "NFL", "RTSports", "Walterfootball", "FanDuel"),
                                    auto_unbox = FALSE),
    avg_type_default = "weighted",
    src_weights_json = NA_character_,          # NULL = package default_weights
    tier_thresholds_json = NA_character_,      # NULL = package default_threshold
    vor_baseline_override_json = NA_character_ # NULL = derived baseline
  )
}

ensure_params <- function(con, league_id) {
  existing <- DBI::dbGetQuery(con, "SELECT league_id FROM params WHERE league_id = $id",
                              params = list(id = league_id))
  if (nrow(existing) == 0) {
    p <- default_params()
    DBI::dbExecute(con, paste(
      "INSERT INTO params (league_id, sources_json, avg_type_default, src_weights_json,",
      "tier_thresholds_json, vor_baseline_override_json)",
      "VALUES ($id, $src, $avg, $wts, $tier, $vor)"),
      params = list(id = league_id, src = as.character(p$sources_json),
                    avg = p$avg_type_default, wts = p$src_weights_json,
                    tier = p$tier_thresholds_json, vor = p$vor_baseline_override_json))
  }
  get_params(con, league_id)
}

get_params <- function(con, league_id) {
  df <- DBI::dbGetQuery(con, "SELECT * FROM params WHERE league_id = $id",
                        params = list(id = league_id))
  if (nrow(df) == 0) NULL else as.list(df[1, ])
}

# ---- rankings --------------------------------------------------------------

rankings_cols <- c("league_id", "avg_type", "mfl_id", "sleeper_id", "player", "pos",
                   "team", "age", "points", "sd_pts", "dropoff", "floor", "ceiling",
                   "points_vor", "floor_vor", "ceiling_vor", "rank", "floor_rank",
                   "ceiling_rank", "pos_rank", "tier", "overall_ecr", "pos_ecr",
                   "sd_ecr", "adp", "adp_diff", "aav", "uncertainty")

# Snapshot replace inside ONE transaction: a crash mid-write leaves the old
# snapshot intact. targets/overrides are untouched (they key on mfl_id).
replace_rankings <- function(con, league_id, df) {
  missing <- setdiff(rankings_cols, names(df))
  for (col in missing) df[[col]] <- NA
  df <- df[rankings_cols]
  df$league_id <- league_id
  DBI::dbWithTransaction(con, {
    DBI::dbExecute(con, "DELETE FROM rankings WHERE league_id = $id",
                   params = list(id = league_id))
    DBI::dbAppendTable(con, "rankings", df)
    DBI::dbExecute(con,
      "UPDATE league SET scraped_at = $now, updated_at = $now WHERE league_id = $id",
      params = list(now = now_iso(), id = league_id))
  })
  invisible(nrow(df))
}

get_rankings <- function(con, league_id) {
  DBI::dbGetQuery(con, "SELECT * FROM rankings WHERE league_id = $id",
                  params = list(id = league_id))
}

set_ranking_sleeper_id <- function(con, league_id, mfl_id, sleeper_id) {
  DBI::dbExecute(con, paste(
    "UPDATE rankings SET sleeper_id = $sid",
    "WHERE league_id = $lid AND mfl_id = $mid"),
    params = list(sid = sleeper_id, lid = league_id, mid = mfl_id))
  invisible(TRUE)
}

# ---- sleeper players (global cache, refreshed at most daily) ---------------

replace_sleeper_players <- function(con, df) {
  df <- df[c("sleeper_id", "name", "pos", "team", "status")]
  df$fetched_at <- now_iso()
  DBI::dbWithTransaction(con, {
    DBI::dbExecute(con, "DELETE FROM sleeper_players")
    DBI::dbAppendTable(con, "sleeper_players", df)
  })
  invisible(nrow(df))
}

get_sleeper_players <- function(con) {
  DBI::dbGetQuery(con, "SELECT * FROM sleeper_players")
}

sleeper_players_age_hours <- function(con) {
  ts <- DBI::dbGetQuery(con, "SELECT MAX(fetched_at) AS ts FROM sleeper_players")$ts
  if (is.na(ts %||% NA)) return(Inf)
  as.numeric(difftime(Sys.time(), as.POSIXct(ts, format = "%Y-%m-%dT%H:%M:%S%z"),
                      units = "hours"))
}

# ---- targets / overrides ----------------------------------------------------

set_target <- function(con, league_id, mfl_id, priority) {
  DBI::dbExecute(con, paste(
    "INSERT INTO targets (league_id, mfl_id, priority, updated_at)",
    "VALUES ($lid, $mid, $pri, $now)",
    "ON CONFLICT(league_id, mfl_id) DO UPDATE SET priority = excluded.priority,",
    "updated_at = excluded.updated_at"),
    params = list(lid = league_id, mid = mfl_id, pri = priority, now = now_iso()))
  invisible(TRUE)
}

clear_target <- function(con, league_id, mfl_id) {
  DBI::dbExecute(con, "DELETE FROM targets WHERE league_id = $lid AND mfl_id = $mid",
                 params = list(lid = league_id, mid = mfl_id))
  invisible(TRUE)
}

get_targets <- function(con, league_id) {
  DBI::dbGetQuery(con, "SELECT * FROM targets WHERE league_id = $id",
                  params = list(id = league_id))
}

set_override <- function(con, league_id, mfl_id, status) {
  stopifnot(status %in% c("drafted", "undrafted"))
  DBI::dbExecute(con, paste(
    "INSERT INTO overrides (league_id, mfl_id, status, updated_at)",
    "VALUES ($lid, $mid, $st, $now)",
    "ON CONFLICT(league_id, mfl_id) DO UPDATE SET status = excluded.status,",
    "updated_at = excluded.updated_at"),
    params = list(lid = league_id, mid = mfl_id, st = status, now = now_iso()))
  invisible(TRUE)
}

clear_override <- function(con, league_id, mfl_id) {
  DBI::dbExecute(con, "DELETE FROM overrides WHERE league_id = $lid AND mfl_id = $mid",
                 params = list(lid = league_id, mid = mfl_id))
  invisible(TRUE)
}

get_overrides <- function(con, league_id) {
  DBI::dbGetQuery(con, "SELECT * FROM overrides WHERE league_id = $id",
                  params = list(id = league_id))
}
