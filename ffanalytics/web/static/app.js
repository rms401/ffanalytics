/* Draft board client: poll /api/state, render the board, ticker and roster.
   All state lives here; the server is a read-only view of the database. */

"use strict";

const POLL_MS = 1000;
const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB"];

const ui = {
  leagueName: document.getElementById("league-name"),
  leagueDetail: document.getElementById("league-detail"),
  search: document.getElementById("search"),
  chips: document.getElementById("pos-chips"),
  hideDrafted: document.getElementById("hide-drafted"),
  freshness: document.getElementById("freshness"),
  head: document.getElementById("board-head"),
  body: document.getElementById("board-body"),
  inspectorBody: document.getElementById("inspector-body"),
  inspectorClose: document.getElementById("inspector-close"),
  me: document.getElementById("me"),
  rosterSlots: document.getElementById("roster-slots"),
  teamsBtn: document.getElementById("teams-btn"),
  teamsPanel: document.getElementById("teams-panel"),
  teamsList: document.getElementById("teams-list"),
  teamsClose: document.getElementById("teams-close"),
  tickerList: document.getElementById("ticker-list"),
  advisorStatus: document.getElementById("advisor-status"),
  advisorBody: document.getElementById("advisor-body"),
};

let state = null;            // last /api/state payload
let posFilter = "ALL";
let seenPicks = null;        // sleeper_ids seen on the previous poll
let flashPicks = new Set();  // picks to highlight this render
let selectedId = null;       // player id open in the inspector
let sortKey = "rank";        // column the board is ordered by
let sortDir = 1;             // 1 ascending, -1 descending

/* ``desc`` marks columns where the first click should put the biggest value
   on top; rank-like and text columns start ascending. */
const COLUMNS = [
  { key: "rank", label: "RK", digits: 0 },
  { key: "tier", label: "TIER", digits: 0 },
  { key: "pos", label: "POS", left: true, render: posBadge },
  { key: "player", label: "PLAYER", left: true, render: playerCell },
  { key: "team", label: "TEAM", left: true },
  { key: "pos_rank", label: "PRK", digits: 0 },
  { key: "points", label: "PTS", digits: 1, desc: true },
  { key: "points_vor", label: "VOR", digits: 1, desc: true },
  { key: "floor", label: "FLOOR", digits: 1, desc: true },
  { key: "ceiling", label: "CEIL", digits: 1, desc: true },
  { key: "dropoff", label: "DROP", digits: 1, desc: true },
  { key: "uncertainty", label: "UNC", digits: 0 },
  { key: "pos_ecr", label: "ECR", digits: 0 },
  { key: "adp", label: "ADP", digits: 1 },
  { key: "adp_diff", label: "ADP±", digits: 0, signed: true, desc: true },
  { key: "drafted_by", label: "DRAFTED", left: true, render: draftedCell },
];

/* ---- helpers ---------------------------------------------------------- */

function fmt(value, digits, signed) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  if (typeof value !== "number") return String(value);
  let text = value.toFixed(digits === undefined ? 1 : digits);
  if (/^-0(\.0+)?$/.test(text)) text = text.slice(1);
  return signed && value > 0 ? "+" + text : text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pickLabel(row) {
  const teams = state && state.meta && state.meta.teams;
  if (row.draft_round == null || row.draft_pick == null) return "";
  if (teams) {
    const inRound = row.draft_pick - (row.draft_round - 1) * teams;
    if (inRound >= 1 && inRound <= teams) {
      return `${row.draft_round}.${String(inRound).padStart(2, "0")}`;
    }
  }
  return `#${row.draft_pick}`;
}

function myTeamKey(row) {
  return row.manager || row.team_name || "";
}

function storageKey() {
  const id = state && state.meta && state.meta.league_id;
  return `ffanalytics-me-${id || "league"}`;
}

/* ---- cells ------------------------------------------------------------ */

function posBadge(row) {
  const cell = el("td", "left");
  cell.appendChild(el("span", `pos-badge pos-${row.pos}`, row.pos || "?"));
  return cell;
}

function playerCell(row) {
  const cell = el("td", "left");
  cell.appendChild(el("span", "player-name", row.player || ""));
  if (row.injury_status) {
    cell.appendChild(el("span", "injury", row.injury_status));
  }
  return cell;
}

function draftedCell(row) {
  const cell = el("td", "left");
  if (row.drafted_by || row.draft_pick != null) {
    const label = pickLabel(row);
    cell.appendChild(el("span", "drafted-by",
      [label, row.drafted_by].filter(Boolean).join(" ")));
  }
  return cell;
}

/* ---- board ------------------------------------------------------------ */

function visibleColumns() {
  const present = new Set();
  for (const row of state.board) {
    for (const key of Object.keys(row)) if (row[key] != null) present.add(key);
  }
  present.add("drafted_by"); // always show the column once a draft may start
  return COLUMNS.filter(c => present.has(c.key));
}

function compareRows(a, b) {
  const av = a[sortKey], bv = b[sortKey];
  const aMissing = av == null || av === "" ||
    (typeof av === "number" && Number.isNaN(av));
  const bMissing = bv == null || bv === "" ||
    (typeof bv === "number" && Number.isNaN(bv));
  if (aMissing || bMissing) return aMissing - bMissing; // blanks sink either way
  const cmp = typeof av === "string" || typeof bv === "string"
    ? String(av).localeCompare(String(bv))
    : av - bv;
  return cmp * sortDir || (a.rank ?? 0) - (b.rank ?? 0);
}

function renderHead(columns) {
  ui.head.textContent = "";
  for (const column of columns) {
    const active = column.key === sortKey;
    const th = el("th", column.left ? "left" : "",
      active ? `${column.label} ${sortDir === 1 ? "▴" : "▾"}`
             : column.label);
    th.classList.add("sortable");
    if (active) th.classList.add("sorted");
    th.addEventListener("click", () => {
      if (sortKey === column.key) {
        sortDir = -sortDir;
      } else {
        sortKey = column.key;
        sortDir = column.desc ? -1 : 1;
      }
      renderBoard();
    });
    ui.head.appendChild(th);
  }
}

function renderBoard() {
  const columns = visibleColumns();
  renderHead(columns);

  const query = ui.search.value.trim().toLowerCase();
  const hideDrafted = ui.hideDrafted.checked;
  const mine = ui.me.value;

  ui.body.textContent = "";
  let lastTier = null;
  let shown = 0;

  // Tier breaks only mean something in the default rank order.
  const rankOrder = sortKey === "rank" && sortDir === 1;
  const rows = rankOrder ? state.board : state.board.slice().sort(compareRows);

  for (const row of rows) {
    if (posFilter !== "ALL" && row.pos !== posFilter) continue;
    if (query && !(row.player || "").toLowerCase().includes(query)) continue;
    const drafted = row.drafted_by != null || row.draft_pick != null;
    if (hideDrafted && drafted) continue;

    if (rankOrder && posFilter !== "ALL" && row.tier != null && row.tier !== lastTier) {
      const breakRow = el("tr", "tier-break");
      const cell = el("td", "", `Tier ${row.tier}`);
      cell.colSpan = columns.length;
      breakRow.appendChild(cell);
      ui.body.appendChild(breakRow);
      lastTier = row.tier;
    }

    const tr = document.createElement("tr");
    tr.dataset.id = row.id;
    if (drafted) tr.classList.add("drafted");
    if (mine && drafted && (row.drafted_manager === mine || row.drafted_by === mine)) {
      tr.classList.add("mine");
    }
    if (flashPicks.has(String(row.sleeper_id))) tr.classList.add("flash");
    if (row.id === selectedId) tr.classList.add("selected");

    for (const column of columns) {
      if (column.render) {
        tr.appendChild(column.render(row));
      } else {
        tr.appendChild(el("td", column.left ? "left" : "",
          fmt(row[column.key], column.digits, column.signed)));
      }
    }
    tr.addEventListener("click", () => openInspector(row));
    ui.body.appendChild(tr);
    shown += 1;
  }

  if (!shown) {
    const tr = document.createElement("tr");
    const cell = el("td", "", "nothing matches");
    cell.id = "empty-note";
    cell.colSpan = columns.length;
    tr.appendChild(cell);
    ui.body.appendChild(tr);
  }
}

/* ---- chips ------------------------------------------------------------ */

function renderChips() {
  const positions = [...new Set(state.board.map(r => r.pos).filter(Boolean))];
  positions.sort((a, b) => POS_ORDER.indexOf(a) - POS_ORDER.indexOf(b));
  ui.chips.textContent = "";
  for (const pos of ["ALL", ...positions]) {
    const chip = el("button", "chip" + (pos === posFilter ? " on" : ""), pos);
    chip.addEventListener("click", () => {
      posFilter = pos;
      renderChips();
      renderBoard();
    });
    ui.chips.appendChild(chip);
  }
}

/* ---- ticker ----------------------------------------------------------- */

function renderTicker() {
  const picks = state.picks.slice().sort((a, b) => b.draft_pick - a.draft_pick);
  const posOf = new Map(state.board.map(r => [String(r.sleeper_id), r.pos]));
  ui.tickerList.textContent = "";
  for (const pick of picks.slice(0, 30)) {
    const item = document.createElement("li");
    if (flashPicks.has(String(pick.sleeper_id))) item.classList.add("flash");
    item.appendChild(el("span", "pick-no", pickLabel(pick)));
    const pos = posOf.get(String(pick.sleeper_id));
    item.appendChild(el("span", "pick-player",
      (pick.player || pick.sleeper_id) + (pos ? ` (${pos})` : "")));
    item.appendChild(el("span", "pick-mgr", myTeamKey(pick)));
    ui.tickerList.appendChild(item);
  }
  if (!picks.length) {
    ui.tickerList.appendChild(el("li", "dim", "no picks yet"));
  }
}

/* ---- my roster -------------------------------------------------------- */

function renderManagers() {
  const current = ui.me.value;
  const options = state.managers.map(m => ({
    value: myTeamKey(m),
    label: m.manager && m.team_name && m.manager !== m.team_name
      ? `${m.manager} (${m.team_name})` : myTeamKey(m),
  })).filter(o => o.value);

  ui.me.textContent = "";
  ui.me.appendChild(new Option("— pick your team —", ""));
  for (const option of options) {
    ui.me.appendChild(new Option(option.label, option.value));
  }
  const stored = localStorage.getItem(storageKey());
  const wanted = current || stored || "";
  if ([...ui.me.options].some(o => o.value === wanted)) ui.me.value = wanted;
}

/* Fill a team's picks into the league's starting slots in draft order,
   greedily: dedicated slots first, then flex-style slots by eligibility.
   Whatever doesn't fit is the bench. */
function fillStarters(picks) {
  const open = [];
  for (const slot of state.slots.filter(s => s.kind === "slot")) {
    for (let i = 0; i < slot.count; i += 1) {
      open.push({ name: slot.name, player: null });
    }
  }
  const posOf = new Map(state.board.map(r => [String(r.sleeper_id), r.pos]));
  const bench = [];
  const ordered = [...picks].sort((a, b) => a.draft_pick - b.draft_pick);
  for (const pick of ordered) {
    const pos = posOf.get(String(pick.sleeper_id));
    const eligible = slot =>
      !slot.player &&
      (!state.slot_positions[slot.name] ||
        (pos && state.slot_positions[slot.name].includes(pos)));
    const target = open.find(eligible);
    if (target) target.player = pick;
    else bench.push(pick);
  }
  return { open, bench };
}

function renderRoster() {
  const mine = ui.me.value;
  ui.rosterSlots.textContent = "";

  const { open, bench } = fillStarters(
    mine ? state.picks.filter(p => myTeamKey(p) === mine) : []
  );

  for (const slot of open) {
    const chip = el("div", "slot" + (slot.player ? " filled" : ""));
    chip.appendChild(el("div", "slot-name", slot.name.replace(/_/g, " ")));
    chip.appendChild(slot.player
      ? el("div", "slot-player", slot.player.player || slot.player.sleeper_id)
      : el("div", "slot-player empty", "—"));
    ui.rosterSlots.appendChild(chip);
  }

  // The bench: rounds beyond the starting slots, shown even while empty.
  const rounds = state.draft.length ? state.draft[0].rounds || 0 : 0;
  const benchCount = Math.max(rounds - open.length, bench.length);
  for (let i = 0; i < benchCount; i += 1) {
    const pick = bench[i];
    const chip = el("div", "slot" + (pick ? " filled" : ""));
    chip.appendChild(el("div", "slot-name", "BN"));
    chip.appendChild(pick
      ? el("div", "slot-player", pick.player || pick.sleeper_id)
      : el("div", "slot-player empty", "—"));
    ui.rosterSlots.appendChild(chip);
  }
}

/* ---- teams panel (projected starter points) --------------------------- */

function renderTeams() {
  if (!ui.teamsPanel.classList.contains("open")) return;

  const ptsOf = new Map(state.board.map(r => [String(r.sleeper_id), r.points]));
  const byTeam = new Map();
  for (const pick of state.picks) {
    const key = myTeamKey(pick);
    if (!key) continue;
    if (!byTeam.has(key)) byTeam.set(key, []);
    byTeam.get(key).push(pick);
  }

  // Every team in the draft order shows, picks or none yet.
  const names = new Map();
  for (const row of state.draft || []) {
    const key = myTeamKey(row);
    if (key && !names.has(key)) names.set(key, row.team_name || row.manager);
  }
  for (const key of byTeam.keys()) {
    if (!names.has(key)) names.set(key, key);
  }

  const rows = [...names.keys()].map(key => {
    const { open } = fillStarters(byTeam.get(key) || []);
    let total = 0;
    let filled = 0;
    for (const slot of open) {
      if (!slot.player) continue;
      filled += 1;
      total += ptsOf.get(String(slot.player.sleeper_id)) || 0;
    }
    return { key, name: names.get(key), total, filled, starters: open.length };
  }).sort((a, b) => b.total - a.total);

  ui.teamsList.textContent = "";
  for (const row of rows) {
    const item = el("li", row.key === ui.me.value ? "mine" : "");
    item.appendChild(el("span", "team-name", row.name));
    item.appendChild(el("span", "team-filled",
                        `${row.filled}/${row.starters}`));
    item.appendChild(el("span", "team-pts", fmt(row.total, 1)));
    ui.teamsList.appendChild(item);
  }
}

/* ---- inspector (selected player box) ---------------------------------- */

async function openInspector(row) {
  selectedId = row.id;
  renderBoard();
  ui.inspectorClose.hidden = false;
  ui.inspectorBody.className = "";
  ui.inspectorBody.textContent = "loading…";

  let detail;
  try {
    const response = await fetch(`/api/player/${encodeURIComponent(row.id)}`);
    if (!response.ok) throw new Error(await response.text());
    detail = await response.json();
  } catch (error) {
    ui.inspectorBody.textContent = `could not load player: ${error.message}`;
    return;
  }

  const body = ui.inspectorBody;
  body.textContent = "";
  body.appendChild(el("h2", "", row.player || ""));
  const sub = el("div", "sub");
  sub.appendChild(el("span", `pos-badge pos-${row.pos}`, row.pos || "?"));
  sub.appendChild(document.createTextNode(
    ` ${row.team || ""}${row.injury_status ? " · " + row.injury_status : ""}` +
    `${row.drafted_by ? " · drafted " + pickLabel(row) + " by " + row.drafted_by : ""}`));
  body.appendChild(sub);

  const tiles = [
    ["points", "points", 1], ["points_vor", "vor", 1],
    ["uncertainty", "uncertainty", 0], ["floor", "floor", 1],
    ["ceiling", "ceiling", 1], ["dropoff", "dropoff", 1],
    ["pos_ecr", "pos ecr", 0], ["adp", "adp", 1], ["sources", "sources", 0],
  ];
  const grid = el("div", "stat-grid");
  for (const [key, label, digits] of tiles) {
    if (row[key] == null) continue;
    const tile = el("div", "stat-tile");
    tile.appendChild(el("div", "v", fmt(row[key], digits)));
    tile.appendChild(el("div", "k", label));
    grid.appendChild(tile);
  }
  body.appendChild(grid);

  const combined = detail.avg_types || [];
  if (combined.length > 1) {
    body.appendChild(el("h3", "", "by averaging method"));
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const label of ["method", "points", "floor", "ceiling", "vor"]) {
      head.appendChild(el("th", "", label));
    }
    table.appendChild(head);
    for (const rowAvg of combined) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", "", rowAvg.avg_type || ""));
      for (const key of ["points", "floor", "ceiling", "points_vor"]) {
        tr.appendChild(el("td", "", fmt(rowAvg[key], 1)));
      }
      table.appendChild(tr);
    }
    body.appendChild(table);
  }

  const sources = detail.sources || [];
  if (sources.length) {
    body.appendChild(el("h3", "", "what each site projects"));
    const stats = [...new Set(sources.flatMap(s => Object.keys(s.stats)))]
      .filter(stat => sources.some(s => s.stats[stat] != null));
    const table = document.createElement("table");
    const head = document.createElement("tr");
    head.appendChild(el("th", "", "stat"));
    for (const source of sources) head.appendChild(el("th", "", source.data_src));
    table.appendChild(head);
    for (const stat of stats) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", "", stat));
      for (const source of sources) {
        tr.appendChild(el("td", "", fmt(source.stats[stat], 1)));
      }
      table.appendChild(tr);
    }
    body.appendChild(table);
  }
}

/* ---- advisor ---------------------------------------------------------- */

async function renderAdvisor() {
  const mine = ui.me.value;
  if (!mine) {
    ui.advisorStatus.textContent = "";
    ui.advisorBody.className = "dim";
    ui.advisorBody.textContent = "pick your team to see the plan";
    return;
  }

  let rec;
  try {
    const response = await fetch(`/api/recommend?me=${encodeURIComponent(mine)}`);
    if (!response.ok) throw new Error(`${response.status}`);
    rec = await response.json();
  } catch (error) {
    ui.advisorStatus.textContent = "";
    ui.advisorBody.className = "dim";
    ui.advisorBody.textContent = `advisor unreachable: ${error.message}`;
    return;
  }

  if (!rec.available) {
    ui.advisorStatus.textContent = "";
    ui.advisorBody.className = "dim";
    ui.advisorBody.textContent = rec.reason || "unavailable";
    return;
  }

  ui.advisorStatus.textContent =
    `pick ${rec.current_label}${rec.i_am_on_clock ? " — YOU" : ""}` +
    ` · yours: ${rec.my_next_label} · slot ${rec.my_slot}`;

  const body = ui.advisorBody;
  body.className = "";
  body.textContent = "";

  const recLine = el("div", "");
  recLine.id = "advisor-rec";
  recLine.appendChild(el("span", `pos-badge pos-${rec.recommendation.pos}`,
    rec.recommendation.pos));
  recLine.appendChild(el("span", "rec-player", rec.recommendation.player));
  const why = [`plan ${fmt(rec.recommendation.plan_total, 1)}`];
  if (rec.recommendation.cost_of_next_best != null) {
    why.push(`next best −${fmt(rec.recommendation.cost_of_next_best, 1)}`);
  }
  if (rec.stage === "bench") why.push("bench pick");
  recLine.appendChild(el("span", "rec-why", why.join(" · ")));
  body.appendChild(recLine);

  const cols = el("div", "");
  cols.id = "advisor-cols";

  const planCol = el("div", "");
  planCol.appendChild(el("div", "col-title", "the plan"));
  const planTable = document.createElement("table");
  for (const step of rec.plan) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "left" + (step.starter ? "" : " bench"), step.label));
    tr.appendChild(el("td", "left" + (step.starter ? "" : " bench"), step.pos));
    tr.appendChild(el("td", "left" + (step.starter ? "" : " bench"),
      step.player + (step.starter ? "" : " (bn)")));
    tr.appendChild(el("td", step.starter ? "" : "bench", fmt(step.points, 1)));
    planTable.appendChild(tr);
  }
  planCol.appendChild(planTable);
  cols.appendChild(planCol);

  const posCol = el("div", "");
  posCol.appendChild(el("div", "col-title", "cost of waiting"));
  const posTable = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["pos", "best now", "vor", "cost", "drop"]) {
    head.appendChild(el("th", label === "pos" || label === "best now" ? "left" : "", label));
  }
  posTable.appendChild(head);
  for (const row of rec.positions) {
    if (!row.startable) continue;
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "left", row.pos));
    tr.appendChild(el("td", "left", row.best_now.player));
    tr.appendChild(el("td", "", fmt(row.best_now.points_vor, 1)));
    tr.appendChild(el("td", "", row.waiting_cost == null ? "" : fmt(row.waiting_cost, 1)));
    tr.appendChild(el("td", "", row.dropoff == null ? "" : fmt(row.dropoff, 1)));
    posTable.appendChild(tr);
  }
  posCol.appendChild(posTable);
  cols.appendChild(posCol);

  body.appendChild(cols);
}

/* ---- header / freshness ----------------------------------------------- */

function renderHeader() {
  const meta = state.meta || {};
  ui.leagueName.textContent = meta.name || "draft board";
  const bits = [];
  if (meta.season) bits.push(meta.season);
  if (meta.week === 0) bits.push("season-long");
  else if (meta.week != null) bits.push(`week ${meta.week}`);
  if (meta.teams) bits.push(`${meta.teams} teams`);
  if (state.avg_type) bits.push(`${state.avg_type} avg`);
  ui.leagueDetail.textContent = bits.join(" · ");
}

function clockTime(iso) {
  return new Date(Date.parse(iso)).toLocaleTimeString([], { hour12: false });
}

function renderFreshness() {
  if (!state) return;
  const refresh = state.refresh || {};
  // Last successful pick refresh; the database write stamp covers the case
  // where no refresh loop is running (serving a file as-is).
  const good = refresh.last_success ||
    (state.meta && state.meta.written_at);
  ui.freshness.classList.remove("stale", "error");
  if (refresh.error) {
    ui.freshness.textContent = `refresh failing: ${refresh.error}` +
      (good ? ` · last good ${clockTime(good)}` : "");
    ui.freshness.classList.add("error");
    return;
  }
  if (!good) { ui.freshness.textContent = "—"; return; }
  const age = Math.max(0, Math.round((Date.now() - Date.parse(good)) / 1000));
  ui.freshness.textContent = `picks refreshed ${clockTime(good)} (${age}s ago)`;
  if (refresh.polling && age > Math.max(10, 4 * (refresh.poll_seconds || 1))) {
    ui.freshness.classList.add("stale");
  }
}

/* ---- polling ---------------------------------------------------------- */

function diffPicks() {
  const now = new Set(state.picks.map(p => String(p.sleeper_id)));
  flashPicks = seenPicks
    ? new Set([...now].filter(id => !seenPicks.has(id)))
    : new Set();
  seenPicks = now;
}

async function poll() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`${response.status}`);
    state = await response.json();
  } catch (error) {
    ui.freshness.textContent = `server unreachable: ${error.message}`;
    ui.freshness.classList.add("error");
    return;
  }
  diffPicks();
  renderHeader();
  renderChips();
  renderManagers();
  renderBoard();
  renderTicker();
  renderRoster();
  renderFreshness();
  renderAdvisor();
  renderTeams();
}

ui.search.addEventListener("input", renderBoard);
ui.hideDrafted.addEventListener("change", renderBoard);
ui.me.addEventListener("change", () => {
  localStorage.setItem(storageKey(), ui.me.value);
  renderBoard();
  renderRoster();
  renderAdvisor();
});
ui.teamsBtn.addEventListener("click", () => {
  ui.teamsPanel.classList.toggle("open");
  if (state) renderTeams();
});
ui.teamsClose.addEventListener("click", () => {
  ui.teamsPanel.classList.remove("open");
});
ui.inspectorClose.addEventListener("click", () => {
  ui.inspectorClose.hidden = true;
  ui.inspectorBody.className = "dim";
  ui.inspectorBody.textContent = "click a player";
  selectedId = null;
  renderBoard();
});

poll();
setInterval(poll, POLL_MS);
setInterval(renderFreshness, 1000);
