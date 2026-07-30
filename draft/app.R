# Live draft board. Launch from the repo root:
#   R -e 'shiny::runApp("draft", launch.browser = TRUE)'
#
# Serves the precomputed board from SQLite (run draft/run_projections.R first),
# polls Sleeper picks every POLL_MS and FILTERS the board - it never recomputes
# projections live. Works offline: the poll keeps the last good picks and the
# board still serves from disk.
#
# Deps: shiny, reactable, DBI, RSQLite, httr2, jsonlite.

library(shiny)
library(reactable)

source("db.R")
source("sleeper_api.R")

POLL_MS <- 500
STALE_HOURS <- 72

con <- open_db()
init_db(con)
leagues <- list_leagues(con)
if (nrow(leagues) == 0) {
  stop("No leagues in draft/draft.sqlite - run: ",
       "Rscript draft/run_projections.R <league_id>")
}

fmt1 <- colFormat(digits = 1)
tier_colors <- c("#e8f4fd", "#fdf3e8", "#eafbea", "#fdeaea", "#f3eafd",
                 "#fdfde8", "#e8fdfa", "#fde8f5")

ui <- fluidPage(
  titlePanel("ffanalytics draft board"),
  fluidRow(
    column(3, selectInput("league", NULL, choices = stats::setNames(
      leagues$league_id, sprintf("%s (%s)", leagues$name, leagues$season)))),
    column(9, uiOutput("status_line"))
  ),
  tabsetPanel(
    tabPanel(
      "Board",
      fluidRow(
        column(2, radioButtons("avg_type", "Average",
                               c("weighted", "average", "robust"))),
        column(2, selectInput("pos_filter", "Position",
                              c("ALL", "QB", "RB", "WR", "TE",
                                "FLEX (RB/WR/TE)" = "FLEX", "K", "DST",
                                "IDP (DL/LB/DB)" = "IDP"))),
        column(2, textInput("search", "Search", "")),
        column(2, checkboxInput("targets_only", "Targets only", FALSE),
               checkboxInput("pause", "Pause polling", FALSE)),
        column(4, uiOutput("side_panel"))
      ),
      reactableOutput("board")
    ),
    tabPanel(
      "Status",
      h4("Snapshot"),
      uiOutput("snapshot_info"),
      h4("Draft"),
      fluidRow(
        column(4, uiOutput("draft_info")),
        column(4, textInput("draft_id_override", "Draft ID override", "")),
        column(4, br(), actionButton("save_draft_id", "Use this draft ID"))
      ),
      h4("League configuration"),
      verbatimTextOutput("league_config"),
      h4("Scoring rules NOT reflected in projections (disclosed)"),
      tableOutput("disclosed_tbl"),
      h4("Unmatched picks (drafted on Sleeper, no board match)"),
      uiOutput("resolver"),
      h4("Crosswalk misses in top 200"),
      tableOutput("xw_miss_tbl")
    )
  )
)

server <- function(input, output, session) {
  rv <- reactiveValues(bump = 0)

  league <- reactive({
    rv$bump
    get_league(con, input$league)
  })

  rankings <- reactive({
    rv$bump
    get_rankings(con, input$league)
  })

  params <- reactive(ensure_params(con, input$league))
  observeEvent(params(), {
    updateRadioButtons(session, "avg_type",
                       selected = params()$avg_type_default)
  }, once = TRUE)

  # ---- draft id (cached in db; API refresh if reachable; manual override) ----
  draft_id_rv <- reactiveVal(NA_character_)
  observeEvent(input$league, {
    lg <- get_league(con, input$league)
    did <- lg$draft_id %||% NA_character_
    fresh <- tryCatch(sleeper_drafts(input$league), error = function(e) NULL)
    if (length(fresh) > 0 && !is.null(fresh[[1]]$draft_id)) {
      did <- fresh[[1]]$draft_id
      set_league_field(con, input$league, "draft_id", did)
    }
    draft_id_rv(did)
  })
  observeEvent(input$save_draft_id, {
    req(nzchar(input$draft_id_override))
    set_league_field(con, input$league, "draft_id", input$draft_id_override)
    draft_id_rv(input$draft_id_override)
  })

  # ---- poll (request every POLL_MS; propagate only when the payload changes) --
  picks <- reactiveVal(list())
  poll_state <- reactiveVal(list(status = "idle", at = NA, n = 0))
  picks_hash <- reactiveVal("")
  observe({
    invalidateLater(POLL_MS)
    if (isTRUE(input$pause)) return()
    did <- draft_id_rv()
    if (is.na(did %||% NA) || !nzchar(did)) return()
    res <- tryCatch(sleeper_picks(did), error = function(e) NULL)
    if (is.null(res)) {
      st <- poll_state()
      poll_state(list(status = "down", at = st$at, n = st$n))
      return()
    }
    poll_state(list(status = "ok", at = Sys.time(), n = length(res)))
    h <- paste(length(res),
               if (length(res)) res[[length(res)]]$player_id else "")
    if (!identical(h, picks_hash())) {
      picks_hash(h)
      picks(res)
    }
  })

  # Stop polling once the draft is complete (checked every 30s).
  observe({
    invalidateLater(30000)
    did <- draft_id_rv()
    if (is.na(did %||% NA) || !nzchar(did) || isTRUE(input$pause)) return()
    d <- tryCatch(sleeper_draft(did), error = function(e) NULL)
    if (!is.null(d) && identical(d$status, "complete")) {
      updateCheckboxInput(session, "pause", value = TRUE)
      showNotification("Draft complete - polling stopped", type = "message")
    }
  })

  drafted_sleeper_ids <- reactive({
    vapply(picks(), function(p) as.character(p$player_id %||% NA), character(1))
  })

  # overrides WIN over the API (covers keepers and API glitches)
  drafted_mfl <- reactive({
    rv$bump
    r <- rankings()
    ov <- get_overrides(con, input$league)
    api <- unique(r$mfl_id[!is.na(r$sleeper_id) &
                             r$sleeper_id %in% drafted_sleeper_ids()])
    setdiff(union(api, ov$mfl_id[ov$status == "drafted"]),
            ov$mfl_id[ov$status == "undrafted"])
  })

  unmatched_picks <- reactive({
    sids <- drafted_sleeper_ids()
    setdiff(sids[!is.na(sids)], rankings()$sleeper_id)
  })

  targets <- reactive({
    rv$bump
    get_targets(con, input$league)
  })

  board_data <- reactive({
    r <- rankings()
    r <- r[r$avg_type == input$avg_type & !(r$mfl_id %in% drafted_mfl()), ]
    r <- merge(r, targets()[c("mfl_id", "priority")], by = "mfl_id",
               all.x = TRUE, sort = FALSE)
    if (input$pos_filter == "FLEX") {
      r <- r[r$pos %in% c("RB", "WR", "TE"), ]
    } else if (input$pos_filter == "IDP") {
      r <- r[r$pos %in% c("DL", "LB", "DB"), ]
    } else if (input$pos_filter != "ALL") {
      r <- r[r$pos == input$pos_filter, ]
    }
    if (nzchar(input$search)) {
      pat <- tolower(input$search)
      r <- r[grepl(pat, tolower(paste(r$player, r$team)), fixed = TRUE), ]
    }
    if (isTRUE(input$targets_only)) r <- r[!is.na(r$priority), ]
    r[order(-replace(r$points_vor, is.na(r$points_vor), -Inf)), ]
  })

  output$board <- renderReactable({
    d <- board_data()
    reactable(
      d[c("priority", "player", "pos", "team", "points", "points_vor", "rank",
          "pos_rank", "tier", "dropoff", "floor", "ceiling", "overall_ecr",
          "adp", "adp_diff", "uncertainty", "mfl_id")],
      columns = list(
        priority = colDef(name = "Tgt", width = 55),
        player = colDef(name = "Player", minWidth = 150),
        pos = colDef(width = 55), team = colDef(width = 60),
        points = colDef(name = "Pts", format = fmt1),
        points_vor = colDef(name = "VOR", format = fmt1),
        rank = colDef(name = "Rk", width = 55),
        pos_rank = colDef(name = "PosRk", width = 65),
        tier = colDef(width = 55),
        dropoff = colDef(name = "Drop", format = fmt1),
        floor = colDef(format = fmt1), ceiling = colDef(format = fmt1),
        overall_ecr = colDef(name = "ECR", format = fmt1),
        adp = colDef(name = "ADP", format = fmt1),
        adp_diff = colDef(name = "ADP+/-", format = fmt1),
        uncertainty = colDef(name = "Unc", width = 60),
        mfl_id = colDef(show = FALSE)
      ),
      rowStyle = function(index) {
        t <- d$tier[index]
        if (!is.na(t)) {
          list(background = tier_colors[(as.integer(t) - 1) %%
                                          length(tier_colors) + 1])
        }
      },
      selection = "single", onClick = "select",
      defaultPageSize = 25, searchable = FALSE, highlight = TRUE,
      defaultSorted = list(points_vor = "desc")
    )
  })

  selected_row <- reactive({
    idx <- getReactableState("board", "selected")
    if (is.null(idx)) NULL else board_data()[idx, ]
  })

  output$side_panel <- renderUI({
    row <- selected_row()
    if (is.null(row)) return(helpText("Click a row to target / mark players."))
    tagList(
      strong(sprintf("%s (%s, %s)", row$player, row$pos, row$team)),
      fluidRow(
        column(4, numericInput("tgt_priority", "Priority",
                               value = row$priority %||% 1, min = 1, max = 99)),
        column(8, br(),
               actionButton("set_target", "Target"),
               actionButton("clear_target", "Untarget"),
               actionButton("mark_drafted", "Mark drafted"),
               actionButton("mark_undrafted", "Mark undrafted"))
      )
    )
  })

  # every click is an immediate SQLite upsert, then a reactive bump (durable)
  observeEvent(input$set_target, {
    set_target(con, input$league, selected_row()$mfl_id, input$tgt_priority)
    rv$bump <- rv$bump + 1
  })
  observeEvent(input$clear_target, {
    clear_target(con, input$league, selected_row()$mfl_id)
    rv$bump <- rv$bump + 1
  })
  observeEvent(input$mark_drafted, {
    set_override(con, input$league, selected_row()$mfl_id, "drafted")
    rv$bump <- rv$bump + 1
  })
  observeEvent(input$mark_undrafted, {
    set_override(con, input$league, selected_row()$mfl_id, "undrafted")
    rv$bump <- rv$bump + 1
  })

  # ---- header status line ----------------------------------------------------
  output$status_line <- renderUI({
    st <- poll_state()
    lg <- league()
    age_h <- if (is.na(lg$scraped_at %||% NA)) Inf else {
      as.numeric(difftime(Sys.time(),
                          as.POSIXct(lg$scraped_at,
                                     format = "%Y-%m-%dT%H:%M:%S%z"),
                          units = "hours"))
    }
    dot <- switch(st$status, ok = "\U0001F7E2", down = "\U0001F534",
                  "⚪")
    stale <- if (age_h > STALE_HOURS) {
      span(style = "color:red;font-weight:bold",
           sprintf(" SNAPSHOT STALE (%.0fh old)", age_h))
    } else {
      span(style = "color:green", sprintf(" snapshot %.0fh old", age_h))
    }
    unm <- length(unmatched_picks())
    tagList(
      span(dot, sprintf(" poll: %s | picks: %d", st$status, st$n)),
      if (!is.na(st$at %||% NA)) {
        span(sprintf(" | last ok %s", format(st$at, "%H:%M:%S")))
      },
      stale,
      if (unm > 0) {
        span(style = "color:red;font-weight:bold",
             sprintf(" | %d UNMATCHED PICK(S) - resolve on Status tab", unm))
      }
    )
  })

  # ---- status tab -------------------------------------------------------------
  output$snapshot_info <- renderUI({
    lg <- league()
    p(sprintf("Scraped at: %s | rankings rows: %d",
              lg$scraped_at %||% "never", nrow(rankings())))
  })

  output$draft_info <- renderUI({
    p("Active draft ID: ", code(draft_id_rv() %||% "none"))
  })

  output$league_config <- renderText({
    lg <- league()
    paste0(
      "League: ", lg$name, " (", lg$season, ", ", lg$total_rosters, " teams)\n",
      "Roster: ", paste(jsonlite::fromJSON(lg$roster_positions_json),
                        collapse = " "), "\n",
      "Derived VOR baseline: ",
      paste(names(jsonlite::fromJSON(lg$vor_baseline_json)),
            unlist(jsonlite::fromJSON(lg$vor_baseline_json)),
            sep = "=", collapse = ", ")
    )
  })

  output$disclosed_tbl <- renderTable({
    lg <- league()
    d <- jsonlite::fromJSON(lg$unmapped_keys_json %||% "{}")
    if (length(d) == 0) {
      data.frame(note = "none - all nonzero rules translated")
    } else {
      data.frame(key = names(d),
                 value = vapply(d, function(x) {
                   paste(unlist(x), collapse = ", ")
                 }, character(1)))
    }
  })

  output$xw_miss_tbl <- renderTable({
    r <- rankings()
    r <- r[r$avg_type == input$avg_type & is.na(r$sleeper_id) &
             !is.na(r$rank) & r$rank <= 200,
           c("player", "pos", "team", "rank")]
    if (nrow(r) == 0) data.frame(note = "none in top 200") else r
  })

  output$resolver <- renderUI({
    unm <- unmatched_picks()
    if (length(unm) == 0) return(helpText("No unmatched picks."))
    sp <- get_sleeper_players(con)
    labels <- vapply(unm, function(sid) {
      m <- sp[sp$sleeper_id == sid, ]
      if (nrow(m)) sprintf("%s (%s, %s) [%s]", m$name[1], m$pos[1],
                           m$team[1], sid) else sid
    }, character(1))
    r <- rankings()
    r <- r[r$avg_type == input$avg_type, ]
    board_choices <- stats::setNames(
      r$mfl_id, sprintf("%s (%s, %s)", r$player, r$pos, r$team))
    tagList(
      fluidRow(
        column(4, selectInput("resolve_pick", "Sleeper pick",
                              stats::setNames(unm, labels))),
        column(4, selectizeInput("resolve_player", "is this board player",
                                 board_choices,
                                 options = list(maxOptions = 50))),
        column(4, br(), actionButton("resolve_go", "Link (persists)"))
      )
    )
  })
  observeEvent(input$resolve_go, {
    req(input$resolve_pick, input$resolve_player)
    set_ranking_sleeper_id(con, input$league, input$resolve_player,
                           input$resolve_pick)
    rv$bump <- rv$bump + 1
    showNotification("Linked - future polls will match this pick",
                     type = "message")
  })

  session$onSessionEnded(function() DBI::dbDisconnect(con))
}

shinyApp(ui, server)
