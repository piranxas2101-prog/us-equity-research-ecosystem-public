"use strict";
// Κέντρο Ελέγχου — single-page view over the local ecosystem.
// Every value from data files is escaped before it reaches the page.

const TOKEN = document.querySelector('meta[name="control-token"]').content;
const app = document.getElementById("app");
const state = { run: null, symbols: null, sort: { key: "rank", dir: 1 }, page: 0, filters: {}, taxonomy: null };
const PAGE_SIZE = 100;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (value, digits = 1) => (value === null || value === undefined || value === "") ? "—"
  : (typeof value === "number" ? value.toLocaleString("el-GR", { maximumFractionDigits: digits }) : esc(value));
const when = (iso) => { if (!iso) return "—"; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString("el-GR"); };

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: "Μη έγκυρη απάντηση" }));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
async function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Control-Token": TOKEN }, body: JSON.stringify(body) });
}
function toast(message, error = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (error ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 6000);
}

const STATUS_GR = {
  running_pipeline: ["Τρέχει", "info"], building_receipt: ["Σύνοψη αποτελεσμάτων", "info"],
  recording_learning_episodes: ["Καταγραφή μάθησης", "info"], completed: ["Ολοκληρώθηκε", "good"],
  completed_review_required: ["Ολοκληρώθηκε · θέλει έλεγχο", "warn"], failed: ["Απέτυχε", "bad"],
  stopped_by_owner: ["Σταμάτησε από τον ιδιοκτήτη", "warn"],
  interrupted: ["Διακόπηκε (έκλεισε ο υπολογιστής ή σταμάτησε η διεργασία)", "bad"],
};
const pill = (text, cls = "") => `<span class="pill ${cls}">${esc(text)}</span>`;
const verdictPill = (v) => {
  if (!v) return "—";
  const s = String(v).toLowerCase();
  const cls = /buy|long|strong|pass|ready|accumul/.test(s) ? "good" : /avoid|reject|short|sell|fail|block/.test(s) ? "bad" : /watch|hold|review|wait/.test(s) ? "warn" : "";
  return pill(v, cls);
};

function kv(obj, skip = []) {
  const entries = Object.entries(obj || {}).filter(([k, v]) => !skip.includes(k) && v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length) && !(typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length));
  if (!entries.length) return `<p class="muted small">Δεν υπάρχουν στοιχεία.</p>`;
  return `<dl class="kv">${entries.map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${value(v)}</dd>`).join("")}</dl>`;
}
function value(v) {
  if (Array.isArray(v)) return `<ul class="plain">${v.map((x) => `<li>${typeof x === "object" ? esc(JSON.stringify(x)) : esc(x)}</li>`).join("")}</ul>`;
  if (v && typeof v === "object") return kv(v);
  if (typeof v === "boolean") return v ? "ναι" : "όχι";
  return fmt(v, 2);
}
function setActive(page) {
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
}

// ------------------------------------------------------------ Κατάσταση
const STEP_GR = {
  babis_refresh: "Σάρωση συμβόλων", giannis_triage: "Πρώτη διαλογή", information_ledger: "Καταγραφή πληροφοριών",
  control_plane_batch: "Προφίλ, τομεακοί agents, οικονομικά SEC, ανάλυση", petros_synthesis: "Σύνθεση",
  deep_research: "Βαθιά έρευνα εταιρειών", freeze_packet: "Πάγωμα Research Packet", yankos: "Βραχυπρόθεσμα σήματα",
  periklis: "Swing σήματα", lock_predictions: "Κλείδωμα προβλέψεων", outcome_baseline: "Βάση αποτελεσμάτων",
  outcome_refresh: "Ενημέρωση αποτελεσμάτων", backtest: "Backtest στρατηγικών", sokratis: "Μαθήματα traders",
  petros_training: "Εκπαίδευση μοντέλου", meeting_report: "Αναφορά συνάντησης", action_report: "Αναφορά ενεργειών",
  trader_handoff: "Έλεγχος παράδοσης σε traders", coordination_audit: "Συντονισμός και έλεγχος agents",
};
const AGENT_GR = { babis: "Babis", giannis: "Giannis", sector_agents: "Τομεακοί agents", petros: "Petros",
  yankos: "Yankos", periklis: "Periklis", sokratis: "Sokratis", system: "Σύστημα" };
const duration = (sec) => sec == null ? "—" : sec < 90 ? `${fmt(sec, 0)}″` : sec < 5400 ? `${fmt(sec / 60, 0)}′` : `${fmt(sec / 3600, 1)} ώρες`;
function stepsCard(info, active) {
  if (!info || !(info.steps || []).length) return "";
  const nowSec = (iso) => (Date.now() - new Date(iso).getTime()) / 1000;
  const rows = info.steps.map((st) => {
    const running = !st.finished_at && st.key === info.current_step;
    const state = st.finished_at ? pill("έγινε", "good") : running ? pill(active ? "τρέχει τώρα" : "σταμάτησε εδώ", active ? "info" : "bad") : "—";
    return `<tr><td>${esc(AGENT_GR[st.agent] || st.agent)}</td><td>${esc(STEP_GR[st.key] || st.key)}</td><td>${state}</td>
      <td class="num">${st.finished_at ? duration(st.seconds) : running ? duration(nowSec(st.started_at)) + "…" : "—"}</td></tr>`;
  }).join("");
  return `<div class="card"><h2>Βήματα του run · ποιος agent δουλεύει</h2>
    <div class="table-wrap"><table><thead><tr><th>Agent</th><th>Βήμα</th><th>Κατάσταση</th><th class="num">Διάρκεια</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted">Γράφεται από το ίδιο το pipeline σε κάθε αλλαγή βήματος · ${when(info.updated_at)}</p></div>`;
}
async function pageStatus() {
  setActive("status");
  const s = await api("/api/status");
  const run = s.run || {}, p = s.progress || {};
  const [label, cls] = STATUS_GR[run.status] || [run.status || "Κανένα run", ""];
  const active = run.status && !["completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted"].includes(run.status) && run.process_alive;
  const toScan = p.babis_to_scan ?? p.universe_total;
  const scanned = p.babis_scanned ?? p.babis_processed;
  const pct = toScan ? Math.min(100, (scanned / toScan) * 100) : 0;
  const lanes = p.lanes || {};
  const laneText = Object.keys(lanes).length
    ? `Εκτός σάρωσης: ${Object.entries(lanes).map(([k, v]) => `${esc(LANE_GR[k] || k)} ${fmt(v, 0)}`).join(" · ")}`
      + (p.babis_deferred != null ? ` (καταγράφηκαν ${fmt(p.babis_deferred, 0)})` : "")
    : "";
  app.innerHTML = `
    <h1>Κατάσταση οικοσυστήματος</h1>
    <div class="sub">Ενημέρωση ${when(s.generated_at)} · ανανεώνεται αυτόματα κάθε 30″</div>
    <div class="grid">
      <div class="card"><h3>Τρέχον run</h3><div class="kpi">${pill(label, cls)}</div>
        <div class="small muted">${esc(run.run_id || "—")}</div>
        ${run.stalled ? `<p>${pill("Η διεργασία δεν τρέχει πια — ίσως σταμάτησε", "bad")}</p>` : ""}
        <dl class="kv small"><dt>Ξεκίνησε</dt><dd>${when(run.started_at)}</dd><dt>Τέλος</dt><dd>${when(run.finished_at)}</dd>
        <dt>Έλεγχος agents</dt><dd>${esc(run.agent_audit_status || "—")}</dd><dt>Τεχνικά σφάλματα</dt><dd>${fmt(run.technical_errors)}</dd></dl></div>
      <div class="card"><h3>Πρόοδος Babis (σάρωση συμβόλων)</h3>
        <div class="kpi">${fmt(scanned, 0)} <span class="muted small">/ ${fmt(toScan, 0)} προς σάρωση</span></div>
        <div class="bar"><div id="progress-bar"></div></div>
        <div class="small muted">${p.babis_eta_minutes != null ? `Εκτίμηση για τη σάρωση: ~${fmt(p.babis_eta_minutes / 60, 1)} ώρες` : "—"} · log ${when(p.log_updated_at)}</div>
        ${laneText ? `<div class="small muted">${laneText} · σύνολο ${fmt(p.universe_total, 0)}</div>` : ""}</div>
      <div class="card"><h3>Έλεγχος κατηγοριοποίησης</h3><div class="kpi">${fmt(s.review_open, 0)}</div>
        <div class="small muted">ανοιχτές διαφωνίες πηγών</div><p><a href="#/review">Άνοιγμα γραφείου ελέγχου →</a></p></div>
      <div class="card"><h3>Νέο run έρευνας</h3>
        <p class="small muted">Πλήρες run όλου του σύμπαντος, μόνο έρευνα (κανένας trader, καμία εντολή). Τρέχει στο παρασκήνιο και σε ειδοποιεί όταν τελειώσει.</p>
        <button id="start-run" class="primary" ${active ? "disabled" : ""}>${active ? "Τρέχει ήδη run" : "Εκκίνηση run έρευνας"}</button></div>
    </div>
    ${stepsCard(s.steps, active)}
    <div class="grid wide">
      <div class="card"><h2>Τελευταίες γραμμές του log</h2><pre class="log">${esc((p.tail || []).join("\n")) || "—"}</pre></div>
      <div class="card"><h2>Αποθηκευμένα runs</h2><table><thead><tr><th>Run</th><th>Καταγραφή</th><th class="num">Αρχεία</th></tr></thead><tbody>
        ${(s.runs || []).map((r) => `<tr class="link" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}</td><td>${when(r.captured_at)}</td><td class="num">${fmt(r.artifacts, 0)}</td></tr>`).join("")}
      </tbody></table></div>
    </div>`;
  document.getElementById("progress-bar").style.width = pct.toFixed(1) + "%";
  document.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { state.run = tr.dataset.run; state.symbols = null; location.hash = "#/screener"; }));
  const btn = document.getElementById("start-run");
  btn.addEventListener("click", async () => {
    if (!confirm("Να ξεκινήσει νέο πλήρες run έρευνας; Κρατά αρκετές ώρες. Δεν ανοίγει θέσεις.")) return;
    btn.disabled = true;
    try { await post("/api/actions/start-research-run", {}); toast("Το run ξεκίνησε στο παρασκήνιο. Θα δεις ειδοποίηση όταν τελειώσει."); }
    catch (e) { toast("Δεν ξεκίνησε: " + e.message, true); btn.disabled = false; }
  });
}

// ------------------------------------------------------------ Σύμβολα
const COLUMNS = [
  ["rank", "#", "num"], ["ticker", "Σύμβολο"], ["company", "Εταιρεία"], ["sector_label", "Τομέας"], ["node_label", "Κλάδος (Babis)"],
  ["attention", "Babis", "num"], ["giannis_tier", "Γιάννης"], ["giannis_score", "Βαθμός Γ.", "num"],
  ["petros_verdict", "Πέτρος"], ["petros_score", "Βαθμός Π.", "num"], ["yankos", "Yankos"], ["periklis", "Periklis"],
];
async function pageScreener() {
  setActive("screener");
  if (!state.symbols || state.symbols.run_id !== state.run && state.run) {
    app.innerHTML = `<h1>Σύμβολα</h1><p class="muted">Φόρτωση και επαλήθευση του run… (την πρώτη φορά κρατά λίγο)</p>`;
    state.symbols = await api("/api/symbols" + (state.run ? "?run=" + encodeURIComponent(state.run) : ""));
    state.run = state.symbols.run_id;
  }
  const data = state.symbols, f = state.filters;
  const uniq = (key) => [...new Set(data.rows.map((r) => r[key]).filter(Boolean))].sort();
  const options = (key, label) => `<select data-filter="${key}"><option value="">${label}: όλα</option>${uniq(key).map((v) => `<option ${f[key] === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  app.innerHTML = `
    <h1>Σύμβολα</h1>
    <div class="sub">Run ${esc(data.run_id)} · ${fmt(data.rows.length, 0)} εταιρείες με ανάλυση · έλεγχος agents: ${esc(data.audit_status || "—")}</div>
    <div class="filters">
      <input id="q" placeholder="Αναζήτηση συμβόλου ή εταιρείας" value="${esc(f.q || "")}">
      ${options("sector_label", "Τομέας")} ${options("giannis_tier", "Γιάννης")} ${options("petros_verdict", "Πέτρος")}
      ${options("yankos", "Yankos")} ${options("periklis", "Periklis")}
      <label class="small"><input type="checkbox" id="only-review" ${f.review ? "checked" : ""}> μόνο σε έλεγχο κατηγοριοποίησης</label>
      <span id="count" class="muted small"></span>
    </div>
    <div class="table-wrap"><table><thead><tr>${COLUMNS.map(([k, l, c]) => `<th class="sortable ${c || ""}" data-sort="${k}">${l}${state.sort.key === k ? (state.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead><tbody id="rows"></tbody></table></div>
    <div class="pager"><button id="prev">‹ Προηγούμενα</button><span id="page" class="muted small"></span><button id="next">Επόμενα ›</button></div>`;
  const render = () => {
    const q = (f.q || "").toLowerCase();
    let rows = data.rows.filter((r) => (!q || r.ticker.toLowerCase().includes(q) || String(r.company || "").toLowerCase().includes(q))
      && ["sector_label", "giannis_tier", "petros_verdict", "yankos", "periklis"].every((k) => !f[k] || r[k] === f[k]) && (!f.review || r.in_review));
    const { key, dir } = state.sort;
    rows.sort((a, b) => { const x = a[key], y = b[key]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages - 1);
    const slice = rows.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
    document.getElementById("rows").innerHTML = slice.map((r) => `<tr class="link" data-ticker="${esc(r.ticker)}">
      <td class="num">${fmt(r.rank, 0)}</td><td><b>${esc(r.ticker)}</b>${r.in_review ? " " + pill("έλεγχος", "warn") : ""}</td><td>${esc(r.company)}</td>
      <td>${esc(r.sector_label || "—")}</td><td class="small">${esc(r.node_label || "—")}${r.classification_status === "approved_provisional" ? " " + pill("✓", "good") : ""}</td>
      <td class="num">${fmt(r.attention)}</td><td>${verdictPill(r.giannis_tier)}</td><td class="num">${fmt(r.giannis_score)}</td>
      <td>${verdictPill(r.petros_verdict)}</td><td class="num">${fmt(r.petros_score)}</td><td>${verdictPill(r.yankos)}</td><td>${verdictPill(r.periklis)}</td></tr>`).join("");
    document.getElementById("count").textContent = `${rows.length.toLocaleString("el-GR")} αποτελέσματα`;
    document.getElementById("page").textContent = `σελίδα ${state.page + 1} από ${pages}`;
    document.getElementById("prev").disabled = state.page === 0;
    document.getElementById("next").disabled = state.page >= pages - 1;
    document.querySelectorAll("tr[data-ticker]").forEach((tr) => tr.addEventListener("click", () => { location.hash = "#/symbol/" + encodeURIComponent(tr.dataset.ticker); }));
  };
  document.getElementById("q").addEventListener("input", (e) => { f.q = e.target.value; state.page = 0; render(); });
  document.getElementById("only-review").addEventListener("change", (e) => { f.review = e.target.checked; state.page = 0; render(); });
  document.querySelectorAll("select[data-filter]").forEach((s) => s.addEventListener("change", () => { f[s.dataset.filter] = s.value; state.page = 0; render(); }));
  document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    state.sort = { key: th.dataset.sort, dir: state.sort.key === th.dataset.sort ? -state.sort.dir : 1 }; pageScreener();
  }));
  document.getElementById("prev").addEventListener("click", () => { state.page--; render(); });
  document.getElementById("next").addEventListener("click", () => { state.page++; render(); });
  render();
}

// ------------------------------------------------------------ Σύμβολο
function chart(history) {
  const setups = [...(history.setups || [])].reverse();
  const petros = [...(history.petros || [])].reverse();
  const series = [
    { cls: "var(--accent)", points: setups.filter((s) => s.baseline_price != null).map((s) => [s.observed_at, s.baseline_price]) },
    { cls: "var(--good)", points: setups.filter((s) => s.giannis_score != null).map((s) => [s.observed_at, s.giannis_score]) },
    { cls: "var(--violet)", points: petros.filter((s) => s.petros_score != null).map((s) => [s.source_run_timestamp, s.petros_score]) },
  ].filter((s) => s.points.length > 1);
  if (!series.length) return `<p class="muted small">Δεν υπάρχει αρκετό ιστορικό για γράφημα.</p>`;
  const times = series.flatMap((s) => s.points.map((p) => new Date(p[0]).getTime())).filter((t) => !isNaN(t));
  const t0 = Math.min(...times), t1 = Math.max(...times) || t0 + 1;
  const W = 800, H = 180, P = 24;
  const paths = series.map((s) => {
    const vals = s.points.map((p) => Number(p[1])); const lo = Math.min(...vals), hi = Math.max(...vals) || lo + 1;
    const d = s.points.map((p, i) => {
      const x = P + ((new Date(p[0]).getTime() - t0) / ((t1 - t0) || 1)) * (W - 2 * P);
      const y = H - P - ((Number(p[1]) - lo) / ((hi - lo) || 1)) * (H - 2 * P);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    return `<path d="${d}" fill="none" stroke="${s.cls}" stroke-width="2"/>`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${paths}
    <text x="${P}" y="${H - 6}">${esc(new Date(t0).toLocaleDateString("el-GR"))}</text>
    <text x="${W - P}" y="${H - 6}" text-anchor="end">${esc(new Date(t1).toLocaleDateString("el-GR"))}</text></svg>
    <div class="legend"><span><i class="lg-price"></i>τιμή αναφοράς</span><span><i class="lg-giannis"></i>βαθμός Γιάννη</span><span><i class="lg-petros"></i>βαθμός Πέτρου</span>
    <span>(κάθε γραμμή στη δική της κλίμακα)</span></div>`;
}
const INSTRUMENT_GR = { warrant: "Warrant", preferred: "Προνομιούχος", unit: "Unit", right: "Δικαίωμα" };
const LINK_METHOD_GR = { sec_cik: "ίδιος αριθμός SEC (CIK)", ticker_and_issuer_name: "ticker και όνομα εκδότη" };
const LINK_REASON_GR = {
  no_listed_common_stock_with_same_cik: "ο εκδότης δεν έχει εισηγμένη κοινή μετοχή",
  no_sec_identity_and_no_matching_listed_parent: "δεν βρέθηκε ούτε στη SEC ούτε εισηγμένη μητρική με ίδιο όνομα",
  ambiguous_parent: "ταιριάζουν περισσότερες από μία μητρικές",
};
const LANE_GR = { etf_lane: "ETF/funds", spac_lane: "SPAC", issuer_card: "τίτλοι εκδότη" };
function laneCard(lane) {
  if (!lane) return "";
  if (lane.lane === "issuer_card") return "";  // shown by the "Συνδεδεμένοι τίτλοι" card
  const text = lane.lane === "spac_lane"
    ? "SPAC (εταιρεία-κέλυφος): ανήκει στη δική του κατηγορία και δεν σαρώνεται στο κεντρικό run. Αν ολοκληρώσει συγχώνευση και μπει στη λίστα εταιρειών της SEC, περνά αυτόματα στην έρευνα εταιρειών."
    : "ETF/fund: ανήκει στη δική του κατηγορία και δεν σαρώνεται στο κεντρικό run, που ασχολείται μόνο με εταιρείες.";
  const mergers = (lane.merger_filings || []).length
    ? `<p>${pill("κατέθεσε έγγραφα συγχώνευσης", "warn")} ${lane.merger_filings.map((f) => `${esc(f.form)} ${esc(f.filed_date)}`).join(", ")}</p>`
    : lane.lane === "spac_lane" ? `<p class="small muted">Καμία κατάθεση συγχώνευσης στις τελευταίες 30 ημέρες του ημερολογίου SEC.</p>` : "";
  return `<div class="card"><p>${pill(LANE_GR[lane.lane] || lane.lane, "info")} <span class="small muted">${esc(text)}</span></p>${mergers}</div>`;
}
const symbolLink = (t) => `<a href="#/symbol/${encodeURIComponent(t)}">${esc(t)}</a>`;
function instrumentsCard(info) {
  if (!info || (!info.link && !(info.children || []).length)) return "";
  const link = info.link;
  const own = !link ? "" : link.status === "linked"
    ? `<p>${esc(INSTRUMENT_GR[link.instrument_type] || link.instrument_type)} της εταιρείας <b>${symbolLink(link.parent_ticker)}</b>
       <span class="muted small">(σύνδεση: ${esc(LINK_METHOD_GR[link.method] || link.method)}${(link.evidence.alternatives || []).length ? " · άλλες κατηγορίες μετοχών: " + link.evidence.alternatives.map(symbolLink).join(", ") : ""})</span></p>`
    : `<p>${pill("μητρική άγνωστη", "warn")} <span class="small muted">${esc(LINK_REASON_GR[link.reason] || link.reason)}</span></p>`;
  const children = (info.children || []).length ? `<div class="table-wrap"><table><thead><tr><th>Σύμβολο</th><th>Τύπος</th><th>Όνομα</th></tr></thead><tbody>
    ${info.children.map((c) => `<tr><td>${symbolLink(c.ticker)}</td><td>${esc(INSTRUMENT_GR[c.instrument_type] || c.instrument_type)}</td><td class="small">${esc(c.name)}</td></tr>`).join("")}
    </tbody></table></div>` : "";
  return `<div class="card"><h2>Συνδεδεμένοι τίτλοι</h2>${own}${children}</div>`;
}
async function pageSymbol(ticker) {
  setActive("screener");
  app.innerHTML = `<p class="muted">Φόρτωση ${esc(ticker)}…</p>`;
  const d = await api("/api/symbol/" + encodeURIComponent(ticker) + (state.run ? "?run=" + encodeURIComponent(state.run) : ""));
  const r = d.record, c = d.classification || {}, h = d.history || {};
  const traders = (list) => (list || []).filter(Boolean).map((t) => kv(t)).join("<hr>") || `<p class="muted small">Καμία απόφαση σε αυτό το run.</p>`;
  app.innerHTML = `
    <div class="crumbs"><a href="#/screener">Σύμβολα</a> › ${esc(d.ticker)}</div>
    <h1>${esc(d.ticker)} <span class="muted">${esc(r ? r.company : "")}</span></h1>
    <div class="crumbs">${(c.path || []).map((n) => `<span><a href="#/sectors/${encodeURIComponent(n.id)}">${esc(n.label)}</a></span>`).join("") || "Χωρίς κατηγοριοποίηση"}
      ${c.status === "approved_provisional" ? pill("επιβεβαιωμένο", "good") : c.status ? pill(c.status) : ""}
      ${c.in_review ? `<a href="#/review">${pill("σε έλεγχο κατηγοριοποίησης", "warn")}</a>` : ""}</div>
    ${["equity_candidate", "adr_candidate"].includes(c.instrument_type) ? `<details class="card" id="fix"><summary>Διόρθωση κατηγοριοποίησης</summary>
      <p class="small muted">Η αλλαγή περνά από τον ελεγχόμενο μηχανισμό του Babis (batch με την αιτιολογία σου) και αναιρείται αν χρειαστεί.</p>
      <div class="actions"><select id="fix-node"><option value="">Διάλεξε κόμβο…</option></select><button id="fix-apply" class="primary">Εφαρμογή</button>
        ${c.node_id ? `<button id="fix-confirm" class="good">Ο τωρινός είναι σωστός</button>` : ""}</div>
      <textarea id="fix-note" placeholder="Σύντομη αιτιολογία (υποχρεωτική)"></textarea></details>` : ""}
    ${laneCard(d.lane)}
    ${instrumentsCard(d.instruments)}
    ${r ? "" : `<div class="card"><p class="muted">Το σύμβολο δεν έχει ανάλυση εταιρείας στο run ${esc(d.run_id)} (π.χ. ETF ή δεν πέρασε τα φίλτρα). Παρακάτω φαίνεται μόνο το ιστορικό.</p></div>`}
    ${r ? `<div class="grid wide">
      <div class="card"><h2>Μπάμπης · ταυτότητα και αρχική έρευνα</h2>${kv(r.babis)}</div>
      <div class="card"><h2>Γιάννης · θεμελιώδη</h2>${kv(r.giannis)}</div>
      <div class="card"><h2>Πέτρος · σύνθεση και αποτίμηση</h2>${kv(r.petros)}</div>
      <div class="card"><h2>Τομεακός agent · επιχειρηματικό πλαίσιο</h2>${kv(r.sector_context)}</div>
      <div class="card"><h2>Yankos · βραχυπρόθεσμα</h2>${traders(r.other_agents.yankos)}</div>
      <div class="card"><h2>Periklis · swing</h2>${traders(r.other_agents.periklis)}</div>
      <div class="card"><h2>Βαθιά έρευνα</h2>${kv(r.deep_research)}</div>
    </div>` : ""}
    <div class="card"><h2>Ιστορικό</h2>${chart(h)}
      <h3 class="small">Σήματα traders</h3>
      <div class="table-wrap"><table><thead><tr><th>Agent</th><th>Run</th><th>Ετυμηγορία</th><th>Κατεύθυνση</th><th class="num">Βαθμός</th></tr></thead><tbody>
      ${(h.signals || []).map((s) => `<tr><td>${esc(s.agent_name)}</td><td class="small">${esc(s.run_id || s.source_run_timestamp)}</td><td>${verdictPill(s.signal_verdict)}</td><td>${esc(s.trade_direction || "—")}</td><td class="num">${fmt(s.signal_score)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">—</td></tr>`}
      </tbody></table></div>
      <h3 class="small">Αποτελέσματα μετά τις προτάσεις</h3>
      <div class="table-wrap"><table><thead><tr><th>Ημερομηνία</th><th class="num">Ορίζοντας (ημ.)</th><th class="num">Τιμή</th><th class="num">Απόδοση %</th></tr></thead><tbody>
      ${(h.outcomes || []).slice(-30).reverse().map((o) => `<tr><td>${esc(o.price_session_date)}</td><td class="num">${fmt(o.horizon_days, 0)}</td><td class="num">${fmt(o.price, 2)}</td><td class="num">${fmt(o.return_pct, 2)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">—</td></tr>`}
      </tbody></table></div></div>`;
  const fix = document.getElementById("fix");
  if (fix) {
    fix.addEventListener("toggle", async () => {
      const select = document.getElementById("fix-node");
      if (!fix.open || select.options.length > 1) return;
      state.taxonomy = state.taxonomy || await api("/api/taxonomy");
      select.insertAdjacentHTML("beforeend", state.taxonomy.nodes.map((n) => `<option value="${esc(n.id)}" ${n.id === c.node_id ? "selected" : ""}>${"· ".repeat(n.level - 1)}${esc(n.label)}</option>`).join(""));
    });
    const send = async (decision, node) => {
      const note = document.getElementById("fix-note").value.trim();
      if (!note) { toast("Γράψε μια σύντομη αιτιολογία πρώτα.", true); return; }
      if (decision === "choose" && !node) { toast("Διάλεξε κόμβο.", true); return; }
      try {
        const res = await post("/api/actions/review-decision", { venue_code: c.venue_code, ticker: d.ticker, decision, node, note });
        toast(`${res.ticker}: εφαρμόστηκε (batch ${res.batch_id}).`); state.symbols = null; pageSymbol(d.ticker); refreshBadge();
      } catch (e) { toast("Δεν εφαρμόστηκε: " + e.message, true); }
    };
    document.getElementById("fix-apply").addEventListener("click", () => send("choose", document.getElementById("fix-node").value));
    document.getElementById("fix-confirm")?.addEventListener("click", () => send("confirm", null));
  }
}

// ------------------------------------------------------------ Τομείς
async function pageSectors(nodeId) {
  setActive("sectors");
  const d = await api("/api/sectors");
  const byParent = {};
  d.nodes.forEach((n) => { (byParent[n.parent || ""] = byParent[n.parent || ""] || []).push(n); });
  const branch = (parent) => (byParent[parent] || []).map((n) => {
    const kids = byParent[n.id] || [];
    const row = `<span class="row"><a href="#/sectors/${encodeURIComponent(n.id)}">${esc(n.label)}</a>
      <span class="muted small">${fmt(n.total, 0)}</span>${n.approved ? pill("✓ " + n.approved, "good") : ""}${n.in_review ? pill("έλεγχος " + n.in_review, "warn") : ""}</span>`;
    return kids.length ? `<details ${n.level === 1 ? "" : ""}><summary>${row}</summary>${branch(n.id)}</details>` : `<div class="leaf">${row}</div>`;
  }).join("");
  let members = "";
  if (nodeId) {
    const m = await api("/api/sector/" + encodeURIComponent(nodeId));
    members = `<div class="card"><h2>${esc(m.label)} <span class="muted small">${fmt(m.tickers.length, 0)} εταιρείες</span></h2>
      <p>${m.tickers.map((t) => `<a href="#/symbol/${encodeURIComponent(t)}">${esc(t)}</a>`).join(" · ") || "—"}</p></div>`;
  }
  app.innerHTML = `<h1>Τομείς</h1><div class="sub">Η κατηγοριοποίηση του Babis (taxonomy 2.1.0) · ${fmt(d.unknown, 0)} εταιρείες ακόμα χωρίς κόμβο</div>
    <div class="grid wide"><div class="card tree">${branch("")}</div>${members}</div>`;
}

// ------------------------------------------------------------ Agents
async function pageAgents() {
  setActive("agents");
  const d = await api("/api/agents");
  const table = (rows, cols) => rows.length ? `<div class="table-wrap"><table><thead><tr>${cols.map(([, l]) => `<th>${l}</th>`).join("")}</tr></thead><tbody>
    ${rows.map((r) => `<tr>${cols.map(([k]) => `<td>${typeof r[k] === "number" ? fmt(r[k], 0) : esc(r[k] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<p class="muted small">—</p>`;
  app.innerHTML = `<h1>Agents</h1><div class="sub">Έλεγχος του τελευταίου run: ${esc(d.audit.status || "—")} (${esc(d.audit.run_id || "—")})</div>
    <div class="grid wide">
      <div class="card"><h2>Εγγεγραμμένοι agents</h2>${table(d.registered, [["agent_id", "Agent"], ["agent_type", "Ρόλος"], ["owned_sector", "Τομέας"], ["status", "Κατάσταση"]])}</div>
      <div class="card"><h2>Ουρές τομεακών agents</h2>${table(d.queues, [["agent_id", "Agent"], ["status", "Κατάσταση"], ["items", "Σύμβολα"]])}</div>
      <div class="card"><h2>Μάθηση</h2>${table(d.learning, [["agent_id", "Agent"], ["episodes", "Επεισόδια"], ["outcomes", "Αποτελέσματα"], ["last_recorded", "Τελευταίο"]])}</div>
      <div class="card"><h2>Μοντέλα (shadow/champion)</h2>${table(d.models, [["agent_id", "Agent"], ["task_id", "Εργασία"], ["model_version", "Έκδοση"], ["state", "Κατάσταση"], ["training_rows", "Γραμμές"]])}</div>
    </div>`;
}

// ------------------------------------------------------------ Έλεγχος κατηγοριοποίησης
const REASON_GR = { no_two_sources_agree: "Δεν συμφωνούν δύο πηγές", would_contradict_prior_approval: "Αντιφάσκει με προηγούμενη έγκριση",
  probable_spac_needs_second_signal: "Πιθανό SPAC, θέλει δεύτερη ένδειξη" };
const SOURCE_GR = { directory: "Κατάλογος Nasdaq", profile: "Προφίλ FMP", sec: "SEC SIC", description: "Περιγραφή (Ollama, συμβουλευτικά)" };
async function pageReview() {
  setActive("review");
  const [d, tax] = await Promise.all([api("/api/review"), state.taxonomy ? Promise.resolve(state.taxonomy) : api("/api/taxonomy")]);
  state.taxonomy = tax;
  const reasons = [...new Set(d.items.map((i) => i.reason))];
  const f = state.filters;
  app.innerHTML = `<h1>Έλεγχος κατηγοριοποίησης</h1>
    <div class="sub">${fmt(d.items.length, 0)} σύμβολα όπου οι πηγές διαφωνούν. Η απόφασή σου περνά από τον ελεγχόμενο μηχανισμό του Babis και αναιρείται αν χρειαστεί.</div>
    <div class="filters"><select id="reason"><option value="">Όλοι οι λόγοι</option>${reasons.map((r) => `<option value="${esc(r)}" ${f.reason === r ? "selected" : ""}>${esc(REASON_GR[r] || r)}</option>`).join("")}</select>
      <input id="rq" placeholder="Σύμβολο" value="${esc(f.rq || "")}"><span id="rcount" class="muted small"></span></div>
    <div id="cards" class="grid wide"></div>`;
  const options = tax.nodes.map((n) => `<option value="${esc(n.id)}">${"· ".repeat(n.level - 1)}${esc(n.label)}</option>`).join("");
  const render = () => {
    const items = d.items.filter((i) => (!f.reason || i.reason === f.reason) && (!f.rq || i.ticker.toLowerCase().includes(f.rq.toLowerCase()))).slice(0, 60);
    document.getElementById("rcount").textContent = `εμφανίζονται ${items.length}`;
    document.getElementById("cards").innerHTML = items.map((i) => {
      const nodes = [...new Map(i.votes.filter((v) => v.node).map((v) => [v.node, v])).values()];
      return `<div class="card review-card" data-venue="${esc(i.venue_code)}" data-ticker="${esc(i.ticker)}">
        <h2><a href="#/symbol/${encodeURIComponent(i.ticker)}">${esc(i.ticker)}</a> <span class="muted small">${esc(i.name || "")}</span></h2>
        <div class="small">${pill(REASON_GR[i.reason] || i.reason, "warn")} · τωρινό: <b>${esc(i.current_label || "κανένα")}</b> · ανοιχτό από ${when(i.first_seen_at)}</div>
        <dl class="kv small">${i.votes.map((v) => `<dt>${esc(SOURCE_GR[v.source] || v.source)}</dt><dd>${esc(v.label || v.node)}</dd>`).join("")}
        ${i.advisory_votes.map((v) => `<dt>${esc(SOURCE_GR[v.source] || v.source)}</dt><dd>${pill(v.label || v.node, "violet")}</dd>`).join("")}</dl>
        <div class="vote-list">${nodes.map((v) => `<button data-node="${esc(v.node)}">Επιλογή: ${esc(v.label || v.node)}</button>`).join("")}
          ${i.current_node ? `<button class="good" data-confirm="1">Ο τωρινός είναι σωστός</button>` : ""}</div>
        <div class="actions"><select class="other"><option value="">…ή άλλος κόμβος</option>${options}</select><button data-other="1">Εφαρμογή</button></div>
        <textarea class="note" placeholder="Σύντομη αιτιολογία (υποχρεωτική)"></textarea></div>`;
    }).join("") || `<p class="muted">Καμία ανοιχτή διαφωνία.</p>`;
    document.querySelectorAll(".review-card").forEach((card) => {
      const send = async (decision, node) => {
        const note = card.querySelector(".note").value.trim();
        if (!note) { toast("Γράψε μια σύντομη αιτιολογία πρώτα.", true); return; }
        card.querySelectorAll("button").forEach((b) => (b.disabled = true));
        try {
          const res = await post("/api/actions/review-decision", { venue_code: card.dataset.venue, ticker: card.dataset.ticker, decision, node, note });
          toast(`${res.ticker}: εφαρμόστηκε (batch ${res.batch_id}).`);
          d.items = d.items.filter((i) => !(i.ticker === card.dataset.ticker && i.venue_code === card.dataset.venue));
          render(); refreshBadge();
        } catch (e) { toast("Δεν εφαρμόστηκε: " + e.message, true); card.querySelectorAll("button").forEach((b) => (b.disabled = false)); }
      };
      card.querySelectorAll("button[data-node]").forEach((b) => b.addEventListener("click", () => send("choose", b.dataset.node)));
      card.querySelector("button[data-confirm]")?.addEventListener("click", () => send("confirm", null));
      card.querySelector("button[data-other]").addEventListener("click", () => {
        const node = card.querySelector(".other").value; if (!node) { toast("Διάλεξε κόμβο από τη λίστα.", true); return; } send("choose", node);
      });
    });
  };
  document.getElementById("reason").addEventListener("change", (e) => { f.reason = e.target.value; render(); });
  document.getElementById("rq").addEventListener("input", (e) => { f.rq = e.target.value; render(); });
  render();
}

// ------------------------------------------------------------ router
async function refreshBadge() {
  try {
    const s = await api("/api/status");
    const b = document.getElementById("review-badge");
    b.textContent = s.review_open ?? ""; b.classList.toggle("hidden", !s.review_open);
  } catch (e) { /* the page still works without the badge */ }
}
// ------------------------------------------------------------ Αναφορές
// A small, escape-first Markdown renderer for the reports this project writes itself.
function markdown(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const out = []; let list = false, table = [];
  const flushTable = () => {
    if (!table.length) return;
    const cells = (row) => row.split("|").slice(1, -1).map((c) => c.trim());
    const [head, , ...body] = table;
    out.push(`<div class="table-wrap"><table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    table = [];
  };
  for (const line of String(text || "").split(/\r?\n/)) {
    if (line.startsWith("|")) { table.push(line); continue; }
    flushTable();
    if (line.startsWith("- ")) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(line.slice(2))}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    if (line.startsWith("## ")) out.push(`<h2>${inline(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) out.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  flushTable(); if (list) out.push("</ul>");
  return out.join("");
}
async function pageReports(runId) {
  setActive("reports");
  const d = await api("/api/reports" + (runId ? "?run=" + encodeURIComponent(runId) : ""));
  if (!d.run_id) { app.innerHTML = `<h1>Αναφορές</h1><p class="muted">Δεν υπάρχει ακόμα αναφορά. Γράφεται αυτόματα στο τέλος κάθε run.</p>`; return; }
  app.innerHTML = `<div class="filters"><select id="report-run">${d.reports.map((r) => `<option ${r === d.run_id ? "selected" : ""}>${esc(r)}</option>`).join("")}</select>
    <span class="muted small">Γράφεται από τα αρχεία του run, χωρίς μοντέλο · κάθε αιτιολογία είναι αυτή που κατέγραψε ο ίδιος ο agent</span></div>
    <div class="card report">${markdown(d.markdown)}</div>`;
  document.getElementById("report-run").addEventListener("change", (e) => { location.hash = "#/reports/" + encodeURIComponent(e.target.value); });
}

async function route() {
  const hash = decodeURIComponent(location.hash.slice(1) || "/");
  const [, page, arg] = hash.split("/");
  clearInterval(route.timer);
  try {
    if (!page) { await pageStatus(); route.timer = setInterval(() => (location.hash === "" || location.hash === "#/") && pageStatus().catch(() => {}), 30000); }
    else if (page === "screener") await pageScreener();
    else if (page === "symbol" && arg) await pageSymbol(arg);
    else if (page === "sectors") await pageSectors(arg);
    else if (page === "agents") await pageAgents();
    else if (page === "review") await pageReview();
    else if (page === "reports") await pageReports(arg);
    else app.innerHTML = `<p class="muted">Η σελίδα δεν υπάρχει.</p>`;
  } catch (e) {
    app.innerHTML = `<h1>Κάτι δεν πήγε καλά</h1><p class="muted">${esc(e.message)}</p>`;
  }
}
window.addEventListener("hashchange", route);
route();
refreshBadge();
