"use strict";
const $ = (s, r = document) => r.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {           // DOM builder: text is always set via textContent (no HTML injection)
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};
const fmtBytes = n => { if (!n) return "0 B"; const u = ["B","KB","MB","GB","TB"]; const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024))); return (n / 1024 ** i).toFixed(i ? 1 : 0) + " " + u[i]; };
const fmtTime = t => new Date(t * 1000).toLocaleString();
const ago = t => { if (!t) return "never"; const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 60 ? s + "s ago" : s < 3600 ? Math.round(s / 60) + "m ago" : Math.round(s / 3600) + "h ago"; };

let toastT;
function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (bad ? " bad" : ""); clearTimeout(toastT); toastT = setTimeout(() => t.className = "toast", 3500); }

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "X-Requested-With": "debrid-fetch" }, credentials: "same-origin" };
  if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  if (r.status === 403) { showLogin(); throw new Error("signed out"); }
  let data = null; try { data = await r.json(); } catch (e) {}
  if (!r.ok) { const e = new Error((data && (data.error || JSON.stringify(data.errors))) || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}

// ---------------------------------------------------------------- state
const S = { status: null, torrents: [], settings: null, draft: {}, reset: new Set(), page: "dashboard", logAfter: 0, logs: [], events: [], evAfter: 0, speed: 0, lastBytes: null, rd: null };
let timers = [];

// ---------------------------------------------------------------- auth / boot
function showLogin() { timers.forEach(clearInterval); timers = []; $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); const f = new FormData(e.target);
  try { await api("/api/ui/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } }); $("#loginErr").textContent = ""; boot(); }
  catch (err) { $("#loginErr").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { try { await api("/api/ui/logout", { method: "POST" }); } catch (e) {} showLogin(); });

async function boot() {
  const s = await (await fetch("/api/ui/session")).json();
  if (!s.authed) return showLogin();
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#logout").classList.toggle("hidden", !s.auth_required);
  await Promise.all([loadSettings(), refreshStatus()]);
  route();
  timers.push(setInterval(refreshStatus, 3000), setInterval(tick, 3000));
}
window.addEventListener("hashchange", route);

function route() {
  S.page = (location.hash || "#dashboard").slice(1);
  if (!$(`[data-page="${S.page}"]`)) S.page = "dashboard";
  document.querySelectorAll("[data-page]").forEach(s => s.classList.toggle("hidden", s.dataset.page !== S.page));
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.p === S.page));
  render(); tick();
}
async function tick() {
  try {
    if (S.page === "torrents" || S.page === "dashboard") { S.torrents = await api("/api/ui/torrents"); }
    if (S.page === "logs") { const l = await api("/api/ui/logs?after=" + S.logAfter); if (l.length) { S.logs = S.logs.concat(l).slice(-500); S.logAfter = l[l.length - 1].id; } }
    if (S.page === "activity") { const ev = await api("/api/ui/events?after=" + S.evAfter); if (ev.length) { S.events = S.events.concat(ev).slice(-500); S.evAfter = ev[ev.length - 1].id; } }
    render(true);
  } catch (e) {}
}
async function refreshStatus() {
  try {
    const st = await api("/api/ui/status");
    if (S.status && S.lastBytes != null) S.speed = Math.max(0, (st.bytes_total - S.lastBytes) / Math.max(0.5, st.now - S.status.now));
    S.lastBytes = st.bytes_total; S.status = st; renderChrome();
    if (S.page === "dashboard" || S.page === "protection") render(true);
  } catch (e) {}
}

// ---------------------------------------------------------------- chrome (banners, nav dot)
function renderChrome() {
  const st = S.status, b = $("#banners"); b.replaceChildren();
  document.querySelectorAll(".brand b").forEach(e => e.title = "v" + st.version);
  if (!st.has_token) b.append(h("div", { class: "banner warn" }, h("span", {}, "No Real-Debrid token configured. Nothing will download until you add one."), h("a", { class: "btn small", href: "#settings" }, "Open settings")));
  if (st.rd_error) b.append(h("div", { class: "banner bad" }, h("span", {}, st.rd_error)));
  (st.config_warnings || []).forEach(msg => b.append(h("div", { class: "banner warn" }, h("span", {}, msg), h("a", { class: "btn small", href: "#settings" }, "Settings"))));
  if (st.arr_error) b.append(h("div", { class: "banner bad" }, h("span", {}, st.arr_error)));
  if (st.blocked) b.append(h("div", { class: "banner bad" }, h("span", {}, "Downloads are stopped: " + st.blocked), st.paused ? h("button", { class: "btn small", onclick: () => setPaused(false) }, "Resume") : null));
  if (!st.auth_required) b.append(h("div", { class: "banner warn" }, h("span", {}, "No password set: anyone who can reach this port can change settings and see your downloads."), h("a", { class: "btn small", href: "#settings" }, "Set a login")));
  const p = st.protection, d = $("#navDot");
  d.className = "dot " + (!p.enabled ? "" : p.tripped ? "bad" : p.status === "protected" ? "ok" : "warn");
}
async function setPaused(paused) { await api("/api/ui/pause", { method: "POST", body: { paused } }); await refreshStatus(); toast(paused ? "Downloads paused" : "Downloads resumed"); }

// ---------------------------------------------------------------- pages
function head(title, sub, ...actions) { return h("div", { class: "pagehead" }, h("div", {}, h("h1", {}, title), h("p", { class: "sub" }, sub)), ...actions); }
const stateLabel = t => ({ queued_add: ["Waiting to send to RD", "warn"], rd_pending: ["Queued at RD", "info"], rd_wait_select: ["Selecting files", "info"], rd_downloading: ["Real-Debrid caching", "info"], rd_ready: ["Ready to fetch", "warn"], downloading: ["Downloading", "ok"], completed: ["Completed", "ok"], error: ["Error", "bad"] }[t.raw_state] || [t.raw_state, ""]);

function render(soft) {
  const el = $(`[data-page="${S.page}"]`);
  if (!S.status || !S.settings) return;
  if (soft && (S.page === "settings" || S.page === "protection" || S.page === "check")) { if (S.page === "protection") renderProtLive(); return; }  // don't clobber edits
  ({ dashboard: pageDashboard, torrents: pageTorrents, check: pageCheck, activity: pageActivity, settings: pageSettings, protection: pageProtection, logs: pageLogs })[S.page](el, soft);
}

function pageDashboard(el) {
  const st = S.status, c = st.counts, p = st.protection;
  const prot = !p.enabled ? ["Off", "", "Enable in Protection"] : p.tripped ? ["Killswitch", "bad", p.tripped] : [p.status[0].toUpperCase() + p.status.slice(1), p.status === "protected" ? "ok" : "warn", p.detail];
  const active = S.torrents.filter(t => t.raw_state === "downloading");
  el.replaceChildren(head("Dashboard", "What debrid-fetch is doing right now.",
      h("button", { class: "btn", onclick: () => setPaused(!st.paused) }, st.paused ? "Resume downloads" : "Pause downloads")),
    h("div", { class: "grid" },
      stat("Speed", fmtBytes(S.speed) + "/s", st.speed_limit_mbps ? "limit " + st.speed_limit_mbps + " MB/s" : "no limit"),
      stat("Downloading", c.downloading, c.active + " waiting at Real-Debrid"),
      stat("Completed", c.completed, c.total + " tracked"),
      stat("Errors", c.error, c.error ? "needs attention" : "all good"),
      h("div", { class: "stat wide" }, h("div", { class: "k" }, "Protection"), h("div", { class: "v" }, h("span", { class: "pill " + prot[1] }, prot[0])), h("div", { class: "s" }, prot[2]))),
    h("div", { class: "card" }, h("h2", {}, "Active downloads"), h("p", { class: "hint" }, "Saving to " + st.download_path),
      active.length ? torrentTable(active, false) : h("div", { class: "empty" }, "Nothing downloading.")));
}
const stat = (k, v, s) => h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, String(v)), h("div", { class: "s" }, s));

function torrentTable(list, actions = true) {
  return h("table", { class: "tt" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Category"), h("th", {}, "State"), h("th", {}, "Progress"), h("th", { class: "num" }, "Size"), actions ? h("th", {}) : null)),
    h("tbody", {}, list.map(t => {
      const [lab, cls] = stateLabel(t), pct = Math.round(t.progress * 100);
      return h("tr", {}, h("td", { class: "name", title: t.name }, t.name,
          t.packed ? h("span", { class: "pill warn", title: "This download is an archive, not a video: Sonarr/Radarr cannot import it until it is extracted." }, "needs extracting") : null), h("td", { class: "c-cat" }, t.category || "—"),
        h("td", { class: "c-state" }, h("span", { class: "pill " + cls, title: t.error }, lab)),
        h("td", { class: "c-prog" }, h("div", { class: "row", style: "gap:8px;flex-wrap:nowrap" }, h("div", { class: "bar " + (cls === "bad" ? "bad" : t.raw_state === "completed" ? "done" : ""), style: "flex:1" }, h("b", { style: `width:${pct}%` })), h("span", { class: "pct" }, pct + "%"))),
        h("td", { class: "num c-size" }, fmtBytes(t.size)),
        actions ? h("td", { class: "num c-act" }, t.raw_state === "error" ? h("button", { class: "btn small", onclick: () => act(`/api/ui/torrents/${t.hash}/retry`, {}, "Retrying") }, "Retry") : null,
          h("button", { class: "btn small danger", onclick: () => removeTorrent(t) }, "Remove")) : null);
    })));
}
async function act(path, body, msg) { try { await api(path, { method: "POST", body }); toast(msg); tick(); } catch (e) { toast(e.message, true); } }
function removeTorrent(t) {
  // files are kept: Sonarr/Radarr may still be importing (or have hardlinked) them
  if (confirm(`Remove "${t.name}" from debrid-fetch and Real-Debrid?\n\nDownloaded files stay on disk.`)) act(`/api/ui/torrents/${t.hash}/delete`, { delete_files: false }, "Removed");
}
function pageTorrents(el) {
  el.replaceChildren(head("Torrents", "Everything Sonarr/Radarr (or you) sent to Real-Debrid.",
      h("button", { class: "btn small", title: "Find torrents in your Real-Debrid account that debrid-fetch is not tracking", onclick: cleanRd }, "Clean up Real-Debrid")),
    h("div", { class: "card" }, S.torrents.length ? torrentTable(S.torrents) : h("div", { class: "empty" }, "No torrents yet. Add one from Sonarr/Radarr and it will appear here.")));
}
async function cleanRd() {
  try {
    const { items } = await api("/api/ui/rd/untracked");
    if (!items.length) return toast("Nothing to clean: every torrent in your Real-Debrid account is tracked here");
    const names = items.slice(0, 8).map(i => "  " + (i.name || i.id)).join("\n") + (items.length > 8 ? `\n  ...and ${items.length - 8} more` : "");
    if (!confirm(`Delete ${items.length} torrent(s) from your Real-Debrid account that debrid-fetch is not tracking?\n\n${names}\n\nThis cannot be undone.`)) return;
    const r = await api("/api/ui/rd/purge-untracked", { method: "POST", body: { ids: items.map(i => i.id) } });
    toast(`Removed ${r.removed} from Real-Debrid`);
  } catch (e) { toast(e.message, true); }
}
// ---------------------------------------------------------------- check a release
function pageCheck(el) {
  const out = h("div", { class: "card" }, h("div", { class: "empty" }, "Results appear here."));
  const ta = h("textarea", { class: "ta", rows: "6", spellcheck: "false",
    placeholder: "magnet:?xt=urn:btih:…\n\nor a bare info hash:\n0123456789abcdef0123456789abcdef01234567" });
  const btn = h("button", { class: "btn primary", onclick: run }, "Check with Real-Debrid");
  async function run() {
    const items = ta.value.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
    if (!items.length) return toast("Paste a magnet link or an info hash first", true);
    btn.disabled = true; btn.textContent = "Checking…";
    out.replaceChildren(h("div", { class: "empty" }, "Asking Real-Debrid…"));
    try {
      const { results } = await api("/api/ui/rd-check", { method: "POST", body: { items } });
      const cls = r => r.verdict === "cached" || r.verdict === "accepted" ? "ok" : r.verdict === "refused" ? "bad" : "warn";
      const label = r => r.verdict === "cached" ? "cached · ready now" : r.verdict === "accepted" ? "accepted · will cache" : r.verdict;
      out.replaceChildren(h("table", { class: "tt" },
        h("thead", {}, h("tr", {}, h("th", {}, "Verdict"), h("th", {}, "Release"), h("th", {}, "Detail"))),
        h("tbody", {}, results.map(r => h("tr", {},
          h("td", { class: "c-state" }, h("span", { class: "pill " + cls(r) }, label(r))),
          h("td", { class: "name", title: r.item }, r.filename || r.hash || r.item),
          h("td", { class: "detail" }, r.detail || [r.bytes ? fmtBytes(r.bytes) : "", r.status || ""].filter(Boolean).join(" · ")))))));
      const ok = results.filter(r => r.ok).length;
      toast(`${ok} of ${results.length} accepted by Real-Debrid`, ok === 0);
    } catch (e) { out.replaceChildren(h("div", { class: "banner bad" }, e.message)); toast(e.message, true); }
    btn.disabled = false; btn.textContent = "Check with Real-Debrid";
  }
  el.replaceChildren(head("Check a release", "Will Real-Debrid serve it? Answers without grabbing anything or touching your queue."),
    h("div", { class: "card" }, h("div", { class: "field" }, ta),
      h("div", { class: "row", style: "margin-top:12px" }, btn,
        h("span", { class: "hint" }, "One per line. Nothing is added to your downloads or to Real-Debrid."))), out);
}

// ---------------------------------------------------------------- activity history
const EVENT_STYLE = { added: "info", queued: "warn", not_cached: "warn", ready: "ok", completed: "ok", archive: "warn",
  arr_blocklisted: "info", arr_capped: "warn", rd_token: "bad", rd_refused: "bad", rd_failed: "bad",
  rd_removed: "bad", stalled: "bad", download_failed: "bad", removed: "" };

function pageActivity(el) {
  const keep = el.querySelector(".log"), atEnd = !keep || keep.scrollTop + keep.clientHeight >= keep.scrollHeight - 30;
  const box = h("div", { class: "log" }, S.events.length
    ? S.events.map(e => h("div", { class: "ev" },
        h("span", { class: "t" }, new Date(e.t * 1000).toLocaleTimeString() + " "),
        h("span", { class: "pill " + (EVENT_STYLE[e.kind] || "") }, e.kind),
        h("span", { class: "ev-name", title: e.hash }, " " + (e.name || e.hash || "")),
        h("span", { class: "ev-detail" }, e.detail || "")))
    : h("div", { class: "empty" }, "Nothing has happened yet."));
  el.replaceChildren(head("Activity", "What happened to each download, and why — kept across restarts.",
      h("button", { class: "btn small", onclick: clearEvents }, "Clear history")),
    h("div", { class: "card" }, box));
  if (atEnd) box.scrollTop = box.scrollHeight; else box.scrollTop = keep.scrollTop;
}
async function clearEvents() {
  if (!confirm("Clear the activity history?")) return;
  try { await api("/api/ui/events", { method: "DELETE" }); S.events = []; S.evAfter = 0; tick(); toast("History cleared"); }
  catch (e) { toast(e.message, true); }
}

function pageLogs(el) {
  const keep = el.querySelector(".log"), atEnd = !keep || keep.scrollTop + keep.clientHeight >= keep.scrollHeight - 30;
  const box = h("div", { class: "log" }, S.logs.map(l => h("div", { class: l.level }, h("span", { class: "t" }, new Date(l.t * 1000).toLocaleTimeString() + " "), `${l.level} ${l.name}: ${l.msg}`)));
  el.replaceChildren(head("Logs", "Recent activity, newest at the bottom. Set the level under Settings."), box);
  if (atEnd) box.scrollTop = box.scrollHeight; else box.scrollTop = keep.scrollTop;
}

// ---------------------------------------------------------------- settings
async function loadSettings() { S.settings = await api("/api/ui/settings"); S.draft = {}; S.reset = new Set(); }
const fieldOf = k => S.settings.fields.find(f => f.key === k);
const dirtyCount = () => Object.keys(S.draft).length + S.reset.size;

function control(f) {
  const cur = f.key in S.draft ? S.draft[f.key] : f.value;
  const set = v => { if (f.type !== "secret" && v === f.value) delete S.draft[f.key]; else S.draft[f.key] = v; S.reset.delete(f.key); updateBar(); };
  if (f.readonly) return h("input", { value: String(f.value), readonly: true });
  if (f.type === "bool") return h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!cur, onchange: e => set(e.target.checked) }), h("i"));
  if (f.type === "enum") return h("select", { onchange: e => set(e.target.value) }, f.choices.map(c => h("option", { value: c, selected: c === cur }, c)));
  if (f.type === "secret") {
    const inp = h("input", { type: "password", autocomplete: "new-password", placeholder: f.is_set ? "Saved — type to replace" : "Not set", oninput: e => { if (e.target.value) set(e.target.value); else { delete S.draft[f.key]; updateBar(); } } });
    return h("div", { class: "ctl", style: "width:100%" }, inp, f.is_set ? h("button", { class: "btn small danger", type: "button", onclick: () => { S.draft[f.key] = ""; inp.value = ""; inp.placeholder = "Will be cleared on save"; updateBar(); } }, "Clear") : null,
      f.key === "RD_API_TOKEN" ? h("button", { class: "btn small", type: "button", onclick: () => testRd(inp.value) }, "Test") : null);
  }
  return h("input", { type: f.type === "int" ? "number" : "text", value: cur, min: f.min, max: f.max, placeholder: f.placeholder || String(f.default ?? ""), oninput: e => set(f.type === "int" ? e.target.value : e.target.value) });
}
function fieldRow(f) {
  const msg = h("div", { class: "msg err", "data-err": f.key });
  const tags = [h("span", { class: "tag " + f.source }, f.source === "ui" ? "set in UI" : f.source === "env" ? "from env" : "default")];
  const row = h("div", { class: "field" + (f.type === "bool" ? " sw" : "") },
    h("div", {}, h("div", { class: "lab" }, f.label, " ", ...tags), h("div", { class: "help" }, f.help)),
    h("div", { class: "ctl" }, control(f),
      f.source === "ui" && !f.readonly ? h("button", { class: "btn small ghost", title: "Forget the saved value and follow the environment / default again", onclick: () => { S.reset.add(f.key); delete S.draft[f.key]; updateBar(); row.style.opacity = .5; msg.className = "msg"; msg.textContent = "Will revert to " + (f.env_set ? "the environment value" : "the default") + " on save"; } }, "Reset") : null),
    msg);
  return row;
}
function groupCard(g) {
  const fs = S.settings.fields.filter(f => f.group === g);
  return h("div", { class: "card" }, h("h2", {}, g), g.startsWith("Runtime") ? h("p", { class: "hint" }, "Set these in your compose file; they apply before the app starts.") : null, fs.map(fieldRow));
}
function updateBar() { const n = dirtyCount(), b = $("#sb"); if (!b) return; b.classList.toggle("hidden", !n); $("#sbn").textContent = n + " unsaved change" + (n > 1 ? "s" : ""); }
function savebar() {
  return h("div", { class: "savebar hidden", id: "sb" }, h("span", { id: "sbn" }),
    h("button", { class: "btn", onclick: async () => { await loadSettings(); S.page === "protection" ? pageProtection($(`[data-page="protection"]`)) : pageSettings($(`[data-page="settings"]`)); } }, "Discard"),
    h("button", { class: "btn primary", onclick: saveSettings }, "Save changes"));
}
async function saveSettings() {
  document.querySelectorAll("[data-err]").forEach(e => e.textContent = "");
  try {
    const r = await api("/api/ui/settings", { method: "PUT", body: { values: S.draft, reset: [...S.reset] } });
    S.settings.fields = r.fields; S.draft = {}; S.reset = new Set(); toast("Settings saved"); refreshStatus();
    S.page === "protection" ? pageProtection($(`[data-page="protection"]`)) : pageSettings($(`[data-page="settings"]`));
  } catch (e) {
    if (e.status === 422) { for (const [k, m] of Object.entries(e.data.errors)) { const n = document.querySelector(`[data-err="${k}"]`); if (n) n.textContent = m; } toast("Fix the highlighted fields", true); }
    else toast(e.message, true);
  }
}
async function testRd(typed) {
  try { const r = await api("/api/ui/test-rd", { method: "POST", body: { token: typed || null } });
    if (r.ok) toast(`Connected as ${r.username} (${r.type}${r.expiration ? ", premium until " + r.expiration.slice(0, 10) : ""})`); else toast(r.error, true);
  } catch (e) { toast(e.message, true); }
}
function pageSettings(el) {
  el.dataset.built = "settings";
  const groups = S.settings.groups.filter(g => g !== "Protection");
  el.replaceChildren(head("Settings", "Saved to " + S.settings.config_file + ". A value saved here overrides the same variable in your .env / compose file; Reset goes back to it."),
    ...groups.map(groupCard), savebar());
  updateBar();
}

// ---------------------------------------------------------------- protection
function pageProtection(el) {
  el.dataset.built = "protection";
  el.replaceChildren(head("Protection", "Verify you are behind your VPN, and optionally stop everything if you are not."),
    h("div", { class: "card", id: "protLive" }), groupCard("Protection"),
    h("div", { class: "card" }, h("h2", {}, "How it behaves"), h("ul", { style: "margin:6px 0 0;padding-left:18px;color:var(--muted)" },
      h("li", {}, "Off by default. It never blocks startup, and a check that merely fails is shown as “unknown”, not as a leak."),
      h("li", {}, "With the killswitch on, a definite “unprotected” result stops Real-Debrid polling and all downloads; partial files resume when protection returns."),
      h("li", {}, "While tripped, Sonarr/Radarr get a failure when adding torrents and retry on their own."),
      h("li", {}, "gluetun mode tells you whether the VPN container is up. ip mode checks the public IP this container actually uses, which is the stronger test when debrid-fetch is not routed through gluetun."))),
    savebar());
  renderProtLive(); updateBar();
}
function renderProtLive() {
  const box = $("#protLive"); if (!box || !S.status) return; const p = S.status.protection;
  const [lab, cls] = !p.enabled ? ["Checks off", ""] : p.tripped ? ["Killswitch active", "bad"] : p.status === "protected" ? ["Protected", "ok"] : p.status === "unprotected" ? ["Unprotected", "bad"] : ["Unknown", "warn"];
  box.replaceChildren(h("div", { class: "row" }, h("div", {}, h("h2", {}, "Status ", h("span", { class: "pill " + cls }, lab)), h("p", { class: "hint", style: "margin:6px 0 0" }, p.tripped || p.detail)),
    h("div", { class: "spacer" }), h("button", { class: "btn", onclick: async e => { e.target.disabled = true; try { await api("/api/ui/protection/check", { method: "POST" }); await refreshStatus(); } finally { e.target.disabled = false; } } }, "Check now")),
    h("dl", { class: "kv", style: "margin:14px 0 0" }, h("dt", {}, "Method"), h("dd", {}, p.mode), h("dt", {}, "Public IP"), h("dd", {}, p.public_ip || "—"), h("dt", {}, "Last check"), h("dd", {}, ago(p.checked_at)), h("dt", {}, "Killswitch"), h("dd", {}, p.killswitch ? "armed" : "off")));
}

boot();
