"use strict";
const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["scheduling", "connecting", "loading", "embedding", "indexing", "querying"]);
const LINGER_S = 5;
const ROW_H = 58;
const COLORS = {
  scheduling: "#ffbf47", connecting: "#ffbf47", loading: "#a77bff", embedding: "#38e1ff", indexing: "#5b8cff",
  querying: "#3ddc97", completed: "#3ddc97", failed: "#ff5c7a", stopped: "#ff5c7a",
  downloading: "#ffbf47", preparing: "#ffbf47", loading_seed: "#ffbf47",
};
const RANKS = {
  ndcg10: { label: "nDCG@10", fmt: (v) => v.toFixed(3), higher: true },
  recall10: { label: "Recall@10", fmt: (v) => v.toFixed(3), higher: true },
  mrr10: { label: "MRR@10", fmt: (v) => v.toFixed(3), higher: true },
  docs_per_sec: { label: "docs/s", fmt: (v) => v.toFixed(0), higher: true },
  search_ms_p50: { label: "search p50", fmt: (v) => v.toFixed(2) + "ms", higher: false },
  score: { label: "Balanced", fmt: (v) => v.toFixed(3), higher: true },
};

let snap = null;
let clockOffset = 0;
let rankBy = localStorage.getItem("arena.rankBy") || "ndcg10";
const slots = new Map();          // entity key -> slot index
const cards = new Map();          // entity key -> DOM element
const wires = new Map();          // entity key -> {g, path, glow, dot}
const rows = new Map();           // model id -> DOM element
let lastEventKey = "";

// ------------------------------------------------------------------ helpers
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
const fmtDur = (s) => { s = Math.max(0, Math.floor(s)); const m = Math.floor(s / 60); return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
const fmtAge = (s) => s < 60 ? `${Math.floor(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`;
const fmtNum = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? (n / 1e3).toFixed(1) + "k" : String(n ?? 0);
const serverNow = () => Date.now() / 1000 + clockOffset;
const SVGNS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) flash(j.error || `request failed (${r.status})`);
  return j;
}
function flash(msg) { addLocalEvent(msg, "warn"); }
function addLocalEvent(msg, level) {
  const d = document.createElement("div");
  d.innerHTML = `<span class="t">${fmtTime(Date.now() / 1000)}</span><span class="${level}">${esc(msg)}</span>`;
  $("log").appendChild(d); $("log").scrollTop = 1e9;
}

// ------------------------------------------------------------------ transport
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "● live"; $("conn").style.color = "var(--green)"; };
  ws.onmessage = (ev) => { const s = JSON.parse(ev.data); clockOffset = s.now - Date.now() / 1000; snap = s; render(); };
  ws.onclose = () => { $("conn").textContent = "reconnecting…"; $("conn").style.color = "var(--amber)"; setTimeout(connect, 1000); };
  ws.onerror = () => ws.close();
}

// ------------------------------------------------------------------ controls
$("btn-start").onclick = () => api("/api/start");
$("btn-stop").onclick = () => api("/api/stop");
$("btn-reset").onclick = () => { if (snap && ["running", "seeding", "starting"].includes(snap.state) && !confirm("Reset the arena? All results and the database will be wiped.")) return; api("/api/reset"); };
$("btn-settings").onclick = (e) => { e.stopPropagation(); $("settings").classList.toggle("hidden"); };
document.addEventListener("click", (e) => { if (!$("settings").contains(e.target)) $("settings").classList.add("hidden"); });

const sliders = { parallel: "max_parallel", threads: "threads", corpus: "corpus_size" };
let settingsTimer = null, editingUntil = 0;
for (const [id, key] of Object.entries(sliders)) {
  const el = $("s-" + id);
  el.addEventListener("input", () => {
    $("v-" + id).textContent = el.value;
    editingUntil = Date.now() + 1500;
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => api("/api/settings", Object.fromEntries(Object.entries(sliders).map(([i, k]) => [k, +$("s-" + i).value]))), 250);
  });
}
$("s-rank").value = rankBy;
$("s-rank").onchange = () => { rankBy = $("s-rank").value; localStorage.setItem("arena.rankBy", rankBy); render(); };

// ------------------------------------------------------------------ render
function render() {
  if (!snap) return;
  const s = snap;
  $("ns").textContent = s.namespace;
  const pill = $("state-pill");
  pill.textContent = s.state; pill.className = `pill state-${s.state}`;
  const busy = ["starting", "seeding", "running"].includes(s.state);
  $("btn-start").disabled = busy || s.state === "completed" || s.state === "error";
  $("btn-start").textContent = s.state === "stopped" ? "▶ RESUME" : "▶ START";
  $("btn-stop").disabled = !busy;
  $("btn-reset").disabled = s.state === "idle" && !s.started_at;
  const end = s.finished_at || (busy ? serverNow() : s.started_at);
  $("clock").textContent = s.started_at ? fmtDur(end - s.started_at) : "00:00";

  if (Date.now() > editingUntil) {
    for (const [id, key] of Object.entries(sliders)) { $("s-" + id).value = s.settings[key]; $("v-" + id).textContent = s.settings[key]; }
  }
  renderDb(s);
  renderStage(s);
  renderQueue(s);
  const board = renderBoard(s);
  renderPods(s);
  renderLog(s);
  renderBanner(s, board);
}

function renderBanner(s, { done, vals, cfg }) {
  const hint = $("idle-hint");
  if (s.state === "idle") {
    hint.innerHTML = "Press <b>START</b> to spin up the database and begin the arena";
  } else if (s.state === "completed" && done.length) {
    const top = done[0];
    const fastest = done.reduce((a, b) => (b.metrics.docs_per_sec > a.metrics.docs_per_sec ? b : a));
    hint.innerHTML = `🏆 <b>${esc(top.name)}</b> wins on ${esc(cfg.label)} (${cfg.fmt(vals.get(top.id))})` +
      ` · ⚡ fastest: <b>${esc(fastest.name)}</b> (${fastest.metrics.docs_per_sec.toFixed(0)} docs/s)`;
  }
  hint.classList.toggle("winner", s.state === "completed");
  hint.classList.toggle("hidden", !(s.state === "idle" || (s.state === "completed" && done.length)));
}

function renderDb(s) {
  const db = s.db, el = $("db");
  el.className = `db ${db.phase}`;
  const seed = db.seed;
  let phaseText = db.phase;
  if (db.phase === "starting") phaseText = db.pod ? `starting · ${db.pod.reason}` : "scheduling";
  if (db.phase === "ready" && seed && seed.phase !== "completed") phaseText = "ready · seeding";
  $("db-phase").textContent = phaseText;

  const st = db.stats || {};
  const nModels = s.models.length;
  let fill = 0;
  if (db.phase === "ready") fill = 0.12 + 0.88 * Math.min(1, (st.tables || 0) / nModels);
  if (seed && seed.phase !== "completed") fill = 0.12 * (seed.progress || 0);
  const H = 114;
  $("db-fill").setAttribute("y", 134 - H * fill);
  $("db-fill").setAttribute("height", H * fill);

  if (db.phase === "offline") { $("db-stats").innerHTML = ""; return; }
  const conns = (db.connections || []).reduce((a, c) => a + c.count, 0);
  let html = `
    <span>clients</span><b>${conns}</b><span>size</span><b>${st.size_mb ?? "–"} MB</b>
    <span>vectors</span><b>${fmtNum(st.vectors || 0)}</b><span>tables</span><b>${st.tables ?? 0}</b>
    <span>tx/s</span><b>${fmtNum(st.tps || 0)}</b><span>rows/s</span><b>${fmtNum(st.inserts_ps || 0)}</b>`;
  if (db.dataset) html += `<span class="full">${esc(db.dataset.name)} · ${db.dataset.docs} docs · ${db.dataset.queries} queries</span>`;
  else if (seed) html += `<span class="full">seeding: ${esc(seed.message || seed.phase)}<div class="seedbar"><div style="width:${(seed.progress || 0) * 100}%"></div></div></span>`;
  $("db-stats").innerHTML = html;
}

function stageEntities(s) {
  const now = s.now;
  const out = [];
  const seed = s.db.seed;
  if (seed && (seed.phase !== "completed" || ["seeding", "starting"].includes(s.state))) {
    const pod = s.pods.find((p) => p.role === "seeder" && p.run === s.run_id && !["Completed", "Terminating"].includes(p.reason));
    out.push({ key: "__seed__", seeder: true, name: "Dataset seeder", org: "BEIR SciFact → pgvector", status: seed.phase,
      progress: seed.progress || 0, message: seed.message, app: "arena-seeder", pod: pod?.name, pod_status: pod?.reason });
  }
  for (const m of s.models) {
    const lingering = ["completed", "failed"].includes(m.status) && m.finished_at && now - m.finished_at < LINGER_S;
    if (ACTIVE.has(m.status) || lingering) out.push({ ...m, key: m.id, app: `arena-${m.slug}` });
  }
  return out;
}

function renderStage(s) {
  const stage = $("stage");
  const W = stage.clientWidth, H = stage.clientHeight;
  const cx = W / 2, cy = H / 2 - 30;
  const dbEl = $("db");
  dbEl.style.left = cx + "px"; dbEl.style.top = cy + "px";

  const ents = stageEntities(s);
  const keys = new Set(ents.map((e) => e.key));
  for (const k of [...slots.keys()]) if (!keys.has(k)) slots.delete(k);
  const used = new Set(slots.values());
  for (const e of ents) {
    if (!slots.has(e.key)) { let i = 0; while (used.has(i)) i++; slots.set(e.key, i); used.add(i); }
  }
  const slotCount = Math.max(2, s.settings.max_parallel + (ents.some((e) => e.seeder) ? 1 : 0), ...[...slots.values()].map((v) => v + 1));
  const perSide = Math.ceil(slotCount / 2);
  const spacing = Math.min(150, (H - 30) / perSide);
  const colX = Math.max(135, Math.min(W * 0.17, cx - 300));
  const pos = (i) => {
    const side = i % 2, row = Math.floor(i / 2);
    return { x: side ? W - colX : colX, y: cy + 30 + (row - (perSide - 1) / 2) * spacing, side };
  };

  const connected = new Map();
  for (const c of s.db.connections || []) {
    const prev = connected.get(c.app);
    connected.set(c.app, prev === "active" ? "active" : c.state);
  }

  const layer = $("pods-layer"), svg = $("wires");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  for (const e of ents) {
    let card = cards.get(e.key);
    if (!card) {
      card = document.createElement("div");
      card.className = "pod";
      layer.appendChild(card);
      cards.set(e.key, card);
      const p0 = pos(slots.get(e.key));
      card.style.left = p0.x + "px"; card.style.top = p0.y + "px";
      requestAnimationFrame(() => requestAnimationFrame(() => card.classList.add("show")));
    }
    const p = pos(slots.get(e.key));
    card.style.left = p.x + "px"; card.style.top = p.y + "px";
    card.className = `pod show st-${e.status}${e.seeder ? " seeder" : ""}`;
    const live = e.live || {};
    const liveBits = [];
    if (live.docs_per_sec) liveBits.push(`${live.docs_per_sec} docs/s`);
    if (live.ndcg10 != null) liveBits.push(`nDCG ${live.ndcg10.toFixed(3)}`);
    if (live.rss_mb) liveBits.push(`${live.rss_mb} MB`);
    if (e.status === "completed" && e.metrics) liveBits.splice(0, liveBits.length, `nDCG ${e.metrics.ndcg10.toFixed(3)}`, `${e.metrics.total_s}s`);
    const sub = e.seeder ? e.org : `${esc(e.org)} · ${e.dim}d · ${esc(e.params)}`;
    card.innerHTML = `
      <div class="pod-top"><div class="pod-name" title="${esc(e.name)}">${e.seeder ? "📦 " : ""}${esc(e.name)}</div><span class="chip ${e.status}">${esc(e.status)}</span></div>
      <div class="pod-org">${sub}</div>
      <div class="bar"><div style="width:${Math.round((e.progress || 0) * 100)}%"></div></div>
      <div class="pod-live"><span>${Math.round((e.progress || 0) * 100)}%</span><span>${liveBits.join(" · ")}</span></div>
      <div class="pod-msg" title="${esc(e.message)}">${esc(e.message || "")}</div>
      <div class="pod-k8s">${e.pod ? `pod/${esc(e.pod)} · ${esc(e.pod_status || "")}` : "waiting for scheduler…"}</div>`;

    // wire from card to database
    let w = wires.get(e.key);
    if (!w) {
      const g = svgEl("g"), glow = svgEl("path", { class: "wire-glow" }), path = svgEl("path", { class: "wire", id: "w-" + e.key.replace(/[^a-z0-9]/gi, "_") });
      const dot = svgEl("circle", { r: 3.5, class: "packet" });
      const anim = svgEl("animateMotion", { dur: "1.1s", repeatCount: "indefinite", calcMode: "linear" });
      const mp = svgEl("mpath"); mp.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#" + path.id); mp.setAttribute("href", "#" + path.id);
      anim.appendChild(mp); dot.appendChild(anim);
      g.append(glow, path, dot); svg.appendChild(g);
      w = { g, glow, path, dot, anim }; wires.set(e.key, w);
    }
    const x1 = p.side ? p.x - 118 : p.x + 118, y1 = p.y;
    const x2 = p.side ? cx + 62 : cx - 62, y2 = cy;
    const mx = (x1 + x2) / 2;
    const d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    const conn = connected.get(e.app);
    const color = COLORS[e.status] || "#5b8cff";
    for (const el of [w.path, w.glow]) { el.setAttribute("d", d); el.setAttribute("stroke", color); }
    w.path.setAttribute("class", `wire ${conn ? (conn === "active" ? "" : "idle") : "pending"}`);
    const streaming = conn && ["embedding", "querying", "loading_seed", "loading", "indexing"].includes(e.status) || (e.seeder && conn);
    w.dot.setAttribute("fill", color);
    w.dot.style.display = streaming ? "" : "none";
    // inserts flow towards the DB; queries flow back to the worker
    const reverse = e.status === "querying";
    w.anim.setAttribute("keyPoints", reverse ? "1;0" : "0;1");
    w.anim.setAttribute("keyTimes", "0;1");
  }
  for (const [k, card] of [...cards]) {
    if (keys.has(k)) continue;
    cards.delete(k);
    card.classList.add("leaving"); card.classList.remove("show");
    setTimeout(() => card.remove(), 600);
    const w = wires.get(k); if (w) { w.g.remove(); wires.delete(k); }
  }

  const active = s.models.filter((m) => ACTIVE.has(m.status)).length;
  const done = s.models.filter((m) => m.status === "completed").length;
  const waiting = s.models.filter((m) => ["queued", "stopped"].includes(m.status)).length;
  $("stage-summary").textContent = `${active}/${s.settings.max_parallel} slots busy · ${done}/${s.models.length} completed · ${waiting} waiting`;
}

function renderQueue(s) {
  const q = $("queue");
  const items = s.models.filter((m) => ["queued", "stopped"].includes(m.status));
  q.innerHTML = items.length
    ? items.map((m) => `<span class="qchip ${m.status}" title="${esc(m.id)}">${esc(m.name)}</span>`).join("")
    : `<span class="muted">${s.state === "completed" ? "all models benchmarked 🏁" : "empty"}</span>`;
}

function scoreOf(m, done) {
  if (rankBy !== "score") return m.metrics[rankBy];
  const maxQ = Math.max(...done.map((x) => x.metrics.ndcg10));
  const maxS = Math.max(...done.map((x) => Math.log10(1 + x.metrics.docs_per_sec)));
  return 0.7 * (m.metrics.ndcg10 / maxQ) + 0.3 * (Math.log10(1 + m.metrics.docs_per_sec) / maxS);
}

function renderBoard(s) {
  const cfg = RANKS[rankBy];
  $("metric-head").textContent = cfg.label;
  const done = s.models.filter((m) => m.status === "completed" && m.metrics);
  const failed = s.models.filter((m) => m.status === "failed");
  const vals = new Map(done.map((m) => [m.id, scoreOf(m, done)]));
  done.sort((a, b) => cfg.higher ? vals.get(b.id) - vals.get(a.id) : vals.get(a.id) - vals.get(b.id));
  const list = [...done, ...failed];
  const vmax = Math.max(...[...vals.values()], 1e-9), vmin = Math.min(...[...vals.values()]);
  const ds = s.db.dataset;
  $("board-sub").textContent = ds ? `${ds.name} · ${ds.docs} docs · ${ds.queries} queries` : "BEIR SciFact retrieval";
  $("board-empty").classList.toggle("hidden", list.length > 0);

  const board = $("board");
  const ids = new Set(list.map((m) => m.id));
  for (const [id, el] of [...rows]) if (!ids.has(id)) { el.remove(); rows.delete(id); }
  list.forEach((m, i) => {
    let el = rows.get(m.id);
    if (!el) {
      el = document.createElement("div");
      el.style.top = (list.length + 1) * ROW_H + "px"; el.style.opacity = "0";
      board.appendChild(el); rows.set(m.id, el);
      requestAnimationFrame(() => requestAnimationFrame(() => { el.style.opacity = "1"; el.style.top = i * ROW_H + "px"; }));
    } else {
      el.style.top = i * ROW_H + "px";
    }
    const fresh = m.finished_at && s.now - m.finished_at < 4;
    el.className = `row r${i + 1}${fresh ? " fresh" : ""}${m.status === "failed" ? " failed" : ""}`;
    if (m.status === "failed") {
      el.innerHTML = `<span class="rank">✕</span><div><div class="m-name">${esc(m.name)}</div><div class="m-sub">${esc(m.message)}</div></div>
        <span class="metric">failed</span><span class="num">–</span><span class="num">–</span><span class="num">–</span>`;
      return;
    }
    const v = vals.get(m.id), x = m.metrics;
    const span = vmax - vmin;
    const rel = span > 1e-12 ? (cfg.higher ? v - vmin : vmax - v) / span : 1;
    const frac = 0.12 + 0.88 * rel;
    el.innerHTML = `<span class="rank">${i + 1}</span>
      <div><div class="m-name" title="${esc(m.id)}">${esc(m.name)}</div><div class="m-sub">${esc(m.org)} · ${x.dim}d · ${esc(m.params)} · ${x.total_s}s</div></div>
      <div><span class="metric">${cfg.fmt(v)}</span><div class="mbar"><div style="width:${Math.max(3, frac * 100)}%"></div></div></div>
      <span class="num">${x.recall10.toFixed(2)}</span><span class="num">${x.docs_per_sec.toFixed(0)}</span><span class="num">${x.search_ms_p50.toFixed(1)}</span>`;
  });
  board.style.minHeight = list.length * ROW_H + "px";
  return { done, vals, cfg };
}

// Keyed, in-place update so rows aren't rebuilt (and re-animated) on every push.
const podRows = new Map();
function setCell(el, text, cls) {
  if (el.textContent !== text) el.textContent = text;
  if (cls !== undefined && el.className !== cls) el.className = cls;
}
function renderPods(s) {
  const pods = [...s.pods].sort((a, b) => (a.created || 0) - (b.created || 0) || a.name.localeCompare(b.name));
  setCell($("pod-count"), `${pods.length} pod${pods.length === 1 ? "" : "s"}`);
  const host = $("pods-rows");
  let empty = host.querySelector(".pt-empty");
  if (!pods.length) {
    if (!empty) {
      empty = document.createElement("div");
      empty.className = "pt-row pt-empty";
      empty.innerHTML = `<span class="muted"></span>`;
      host.appendChild(empty);
    }
    setCell(empty.firstChild, `No resources found in ${s.namespace} namespace.`);
  } else if (empty) empty.remove();

  const seen = new Set();
  pods.forEach((p, i) => {
    seen.add(p.name);
    let row = podRows.get(p.name);
    if (!row) {
      row = document.createElement("div");
      row.className = "pt-row";
      row.innerHTML = "<span></span><span></span><span></span><span></span><span></span>";
      row.firstChild.title = p.name;
      podRows.set(p.name, row);
    }
    const c = row.children;
    setCell(c[0], p.name);
    setCell(c[1], p.role || "");
    setCell(c[2], p.reason || "", `ph-${p.reason}`);
    setCell(c[3], p.ip || "<none>");
    setCell(c[4], p.created ? fmtAge(s.now - p.created) : "");
    if (host.children[i] !== row) host.insertBefore(row, host.children[i] || null);
  });
  for (const [name, row] of podRows) {
    if (!seen.has(name)) { row.remove(); podRows.delete(name); }
  }
}

function renderLog(s) {
  const ev = s.events;
  const keyOf = (e) => `${e.t}|${e.level}|${e.msg}`;
  const key = ev.length ? keyOf(ev[ev.length - 1]) : "";
  if (key === lastEventKey) return;
  const log = $("log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const line = (e) => {
    const d = document.createElement("div");
    d.innerHTML = `<span class="t">${fmtTime(e.t)}</span><span class="${e.level}">${esc(e.msg)}</span>`;
    return d;
  };
  // Append only events after the last one already shown; rebuild only if it fell out of the window.
  let start = -1;
  if (lastEventKey) for (let i = ev.length - 1; i >= 0; i--) if (keyOf(ev[i]) === lastEventKey) { start = i + 1; break; }
  if (start < 0) log.replaceChildren(...ev.map(line));
  else ev.slice(start).forEach((e) => log.appendChild(line(e)));
  while (log.children.length > 120) log.firstChild.remove();
  lastEventKey = key;
  if (atBottom) log.scrollTop = log.scrollHeight;
}

window.addEventListener("resize", render);
setInterval(() => { if (snap) { const end = snap.finished_at || (["starting", "seeding", "running"].includes(snap.state) ? serverNow() : snap.started_at); if (snap.started_at) $("clock").textContent = fmtDur(end - snap.started_at); } }, 250);
connect();
