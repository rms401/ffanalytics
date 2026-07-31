-- Draft tool schema. Idempotent: every statement is CREATE ... IF NOT EXISTS.
-- All ids are TEXT; all metrics are REAL (IEEE double, lossless round-trip).

CREATE TABLE IF NOT EXISTS league (
  league_id               TEXT PRIMARY KEY,
  name                    TEXT,
  season                  INTEGER,
  total_rosters           INTEGER,
  roster_positions_json   TEXT,
  scoring_settings_json   TEXT,  -- raw Sleeper scoring_settings, verbatim (Gate A)
  translated_scoring_json TEXT,
  unmapped_keys_json      TEXT,  -- disclosed known-unmappable keys, nonzero only
  gap_method_json         TEXT,  -- per nonzero key: method, column, materiality
  vor_baseline_json       TEXT,
  draft_id                TEXT,  -- cached so the app works offline
  scraped_at              TEXT,
  updated_at              TEXT
);

CREATE TABLE IF NOT EXISTS params (
  league_id                  TEXT PRIMARY KEY,
  sources_json               TEXT,
  avg_type_default           TEXT,
  src_weights_json           TEXT,
  tier_thresholds_json       TEXT,
  vor_baseline_override_json TEXT
);

CREATE TABLE IF NOT EXISTS rankings (
  league_id    TEXT NOT NULL,
  avg_type     TEXT NOT NULL,
  mfl_id       TEXT NOT NULL,
  sleeper_id   TEXT,           -- the crosswalk; NULL = unmatched
  player       TEXT,
  pos          TEXT,
  team         TEXT,
  age          REAL,
  points       REAL,
  sd_pts       REAL,
  dropoff      REAL,
  floor        REAL,
  ceiling      REAL,
  points_vor   REAL,
  floor_vor    REAL,
  ceiling_vor  REAL,
  rank         REAL,
  floor_rank   REAL,
  ceiling_rank REAL,
  pos_rank     REAL,
  tier         REAL,
  overall_ecr  REAL,
  pos_ecr      REAL,
  sd_ecr       REAL,
  adp          REAL,
  adp_diff     REAL,
  aav          REAL,
  uncertainty  REAL,
  PRIMARY KEY (league_id, avg_type, mfl_id)
);

CREATE TABLE IF NOT EXISTS sleeper_players (
  sleeper_id TEXT PRIMARY KEY,
  name       TEXT,
  pos        TEXT,
  team       TEXT,
  status     TEXT,
  fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS targets (
  league_id  TEXT NOT NULL,
  mfl_id     TEXT NOT NULL,
  priority   INTEGER,
  updated_at TEXT,
  PRIMARY KEY (league_id, mfl_id)
);

CREATE TABLE IF NOT EXISTS overrides (
  league_id  TEXT NOT NULL,
  mfl_id     TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('drafted', 'undrafted')),
  updated_at TEXT,
  PRIMARY KEY (league_id, mfl_id)
);
