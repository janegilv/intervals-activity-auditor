/* Intervals auditor web interface: a small hash-routed single-page app on
 * top of the JSON API (/api/docs). The issue list is the same component as
 * the static report (/ui/issues.js), in "live" mode. */
(function () {
  "use strict";
  const UI = window.AuditorUI;
  const esc = UI.esc;
  const $ = (id) => document.getElementById(id);
  const viewEl = $("view");

  // ---------- API ----------
  async function request(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : res.status + " " + res.statusText));
    }
    return data;
  }
  const api = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body || {}),
    put: (path, body) => request("PUT", path, body || {}),
    del: (path) => request("DELETE", path),
  };

  const ackIssues = (numbers, note) => api.post("/api/acks", { issues: numbers, note });
  const unackIssues = (numbers) => api.post("/api/unacks", { issues: numbers });
  const pluralize = UI.plural;

  // ---------- jobs / sync status in the navigation ----------
  let lastJobId = null, jobRunning = false, pollTimer = null;
  async function refreshNav() {
    clearTimeout(pollTimer);
    try {
      const s = await api.get("/api/status");
      const cur = s.jobs.current, last = s.jobs.last;
      jobRunning = !!cur;
      $("syncnow").disabled = jobRunning;
      const bits = [];
      if (cur) bits.push(`<span class="spinner"></span> ${esc(jobLabel(cur.kind))}…`);
      else if (s.sync_failing_since) bits.push(`<span class="sev high">sync failing</span>`);
      bits.push("Last sync " + esc(s.last_sync ? UI.when(s.last_sync) : "never"));
      $("syncinfo").innerHTML = bits.join(" · ");
      if (last && last.id !== lastJobId) {
        if (lastJobId !== null) {
          UI.toast(`${jobLabel(last.kind)} ${last.status === "ok" ? "finished" : last.status}: ${last.message}`);
          if (current && current.refresh) current.refresh();
        }
        lastJobId = last.id;
      }
      if (lastJobId === null) lastJobId = last ? last.id : 0;
    } catch (e) {
      $("syncinfo").textContent = "Can't reach the server";
    }
    pollTimer = setTimeout(refreshNav, jobRunning ? 2000 : 30000);
  }
  function jobLabel(kind) {
    return { sync: "Sync", resync: "Full resync", checks: "Re-running checks", monitor: "Scheduled monitor" }[kind] || kind;
  }
  async function startJob(kind) {
    try {
      await api.post("/api/jobs", { kind });
      UI.toast(jobLabel(kind) + " started");
    } catch (e) {
      UI.toast("Couldn't start: " + e.message);
    }
    refreshNav();
  }
  $("syncnow").addEventListener("click", () => startJob("sync"));

  // ---------- helpers ----------
  const sevBadge = (s) => `<span class="sev ${esc(s)}">${esc(s)}</span>`;
  const stateBadge = (i) => i.state === "acknowledged" ? `<span class="badge-ack">acknowledged</span>`
    : i.state === "resolved" ? `<span class="badge-resolved">resolved</span>` : "";
  function activityLine(a) {
    const p = UI.localParts(a.start_local);
    return `${esc(p.date)} ${esc(p.time)} · ${esc(a.type || "?")} · ${esc(a.name || "(no name)")}` +
      (a.duration_seconds != null ? ` · ${esc(UI.duration(a.duration_seconds))} · ${esc(UI.distance(a.distance))}` : "");
  }
  const activityLink = (id, text) => `<a href="#/activity/${encodeURIComponent(id)}">${esc(text || id)}</a>`;
  function pageShell(title, extra) {
    viewEl.innerHTML = `<div class="wrap page">${title ? `<h2>${esc(title)}</h2>` : ""}<div id="page">${extra || ""}</div></div>`;
    return $("page");
  }
  function showError(e) {
    viewEl.innerHTML = `<div class="wrap page"><div class="banner error">${esc(e.message || e)}</div></div>`;
  }

  // ---------- views ----------
  async function issuesView() {
    const data = await api.get("/api/issues");
    viewEl.innerHTML = `
      <div class="wrap"><header class="top">
        <div class="meta" id="meta"></div>
        <div class="stats" id="stats"></div>
      </header></div>
      <div id="browser"></div>`;
    const browser = UI.IssueBrowser.create($("browser"), {
      mode: "live",
      storageKey: "intervals-auditor-web-issues",
      checks: data.checks,
      activityHref: (a) => "#/activity/" + encodeURIComponent(a.id),
      onAck: async (numbers, note) => {
        const r = await ackIssues(numbers, note);
        UI.toast(`Acknowledged ${pluralize(r.count, "issue", "issues")}`);
        await load();
      },
      onUnack: async (numbers) => {
        const r = await unackIssues(numbers);
        UI.toast(`${pluralize(r.count, "issue is", "issues are")} reported again`);
        await load();
      },
    });
    function show(d) {
      $("meta").textContent = d.last_sync ? "Last sync with Intervals.icu " + UI.when(d.last_sync) : "Not synced yet";
      UI.renderStats($("stats"), d.activities);
      browser.setData(d.activities);
    }
    async function load() { show(await api.get("/api/issues")); }
    show(data);
    return { destroy: () => browser.destroy(), refresh: load };
  }

  async function activityView(ref) {
    const a = await api.get("/api/activities/" + encodeURIComponent(ref));
    const openForm = new Set();
    function render() {
      const open = a.issues.filter((i) => i.state === "open");
      const current = a.issues.filter((i) => i.state !== "resolved");
      const resolved = a.issues.filter((i) => i.state === "resolved");
      const p = UI.localParts(a.start_local);
      viewEl.innerHTML = `<div class="wrap page">
        <p><a href="#/">← All issues</a></p>
        <div class="panel">
          <div class="check-head">
            <h3>${esc(a.deleted ? (a.name || "(deleted activity)") + " (deleted)" : a.name || "(no name)")}</h3>
            <span class="type">${esc(a.type || "?")}</span>
          </div>
          <div class="sub">${esc(p.date)} ${esc(p.time)}${a.duration_seconds != null ? " · " + esc(UI.duration(a.duration_seconds)) + " · " + esc(UI.distance(a.distance)) : ""}</div>
          <dl class="kv" style="margin-top:10px">
            <dt>Activity</dt><dd><span class="mono">${esc(a.id)}</span> <button class="copy" data-copy="${esc(a.id)}">Copy</button>
              · <a href="${esc(a.url)}" target="_blank" rel="noopener">Open in Intervals.icu ↗</a></dd>
            ${a.deleted ? `<dt>Status</dt><dd>No longer in Intervals.icu</dd>` : `
            <dt>Source</dt><dd>${esc(a.source || "–")} · Strava id ${esc(a.strava_id || "–")} · ${esc(a.device || "no device")}</dd>
            <dt>First seen</dt><dd>${esc(UI.when(a.first_seen_at))}</dd>
            <dt>Last changed</dt><dd>${esc(UI.when(a.updated_at))} (synced ${esc(UI.when(a.synced_at))})</dd>
            <dt>Fingerprint</dt><dd class="mono">${esc(a.fingerprint || "–")}</dd>`}
          </dl>
        </div>
        <h2>Issues</h2>
        ${open.length > 1 ? `<form class="inline-form" id="ackall">
            <input type="text" name="note" placeholder="Why these are fine (optional)">
            <button class="primary" type="submit">Acknowledge all ${open.length} open issues</button></form>` : ""}
        <div class="card">${current.length ? current.map(issueRow).join("")
          : `<div class="empty">${resolved.length ? "No open issues: everything found on this activity has been resolved." : "No issues on this activity."}</div>`}</div>
        ${resolved.length ? `<details class="history"><summary>${pluralize(resolved.length, "resolved issue", "resolved issues")} (no longer found)</summary>
          <div class="card">${resolved.map(issueRow).join("")}</div></details>` : ""}
        <h2>Acknowledgments</h2>
        ${ackTable(a.acknowledgments, { activityColumn: false, currentId: a.id })}
      </div>`;
    }
    function issueRow(i) {
      const related = (i.related || []).map((r) => `${activityLink(r.id)} <a href="${esc(r.url)}" target="_blank" rel="noopener">↗</a>`).join(", ");
      const details = (i.details || []).map((d) => {
        const rest = Object.entries(d).filter(([k]) => k !== "rule").map(([k, v]) => `${k}=${v}`).join(", ");
        return `<li><code>${esc(d.rule || "detail")}</code>${rest ? ": " + esc(rest) : ""}</li>`;
      }).join("");
      const history = [`found ${UI.when(i.first_seen_at)}`];
      if (i.notified_at) history.push(`notified ${UI.when(i.notified_at)}`);
      if (i.resolved_at && i.state === "resolved") history.push(`resolved ${UI.when(i.resolved_at)}`);
      let action = "";
      if (i.state === "open") action = `<button class="copy" data-open="${i.number}">Acknowledge…</button>`;
      if (i.state === "acknowledged") action = `<button class="copy" data-unack="${i.number}">Unacknowledge</button>`;
      return `<div class="issue nosel ${i.state === "acknowledged" ? "acked" : ""} ${i.state === "resolved" ? "resolved" : ""}">
        <div>
          <div class="top"><span class="num">Issue ${i.number}</span>${sevBadge(i.severity)}<span class="check">${esc(i.check)}</span>${stateBadge(i)}</div>
          <div class="summary">${esc(i.summary)}</div>
          ${related ? `<div class="sub">Related: ${related}</div>` : ""}
          ${i.acknowledgment ? `<div class="ack-note">Acknowledged ${esc(UI.when(i.acknowledgment.created_at))}${i.acknowledgment.note ? ": " + esc(i.acknowledgment.note) : ""}</div>` : ""}
          ${i.reopen_reason && i.state !== "resolved" ? `<div class="sub">Reopened: ${esc(i.reopen_reason)}</div>` : ""}
          <div class="sub">${esc(history.join(" · "))}</div>
          ${details ? `<details class="det"><summary>Details</summary><ul>${details}</ul></details>` : ""}
          ${openForm.has(i.number) ? `<form class="inline-form" data-ack-form="${i.number}">
            <input type="text" name="note" placeholder="Why it's fine (optional)">
            <button class="primary" type="submit">Acknowledge</button>
            <button class="secondary" type="button" data-cancel="${i.number}">Cancel</button></form>` : ""}
        </div>
        <div class="actions">${action}</div>
      </div>`;
    }
    async function reload() { Object.assign(a, await api.get("/api/activities/" + encodeURIComponent(ref))); render(); }
    async function run(fn, message) {
      try { const r = await fn(); UI.toast(message(r)); openForm.clear(); await reload(); }
      catch (e) { UI.toast("Failed: " + e.message); }
    }
    viewEl.addEventListener("click", onClick);
    viewEl.addEventListener("submit", onSubmit);
    function onClick(e) {
      const t = e.target.closest("button"); if (!t) return;
      if (t.dataset.copy) UI.copy(t.dataset.copy, "Copied activity id");
      else if (t.dataset.open) { openForm.add(+t.dataset.open); render(); const f = viewEl.querySelector(`[data-ack-form="${t.dataset.open}"] input`); if (f) f.focus(); }
      else if (t.dataset.cancel) { openForm.delete(+t.dataset.cancel); render(); }
      else if (t.dataset.unack) run(() => unackIssues([+t.dataset.unack]), (r) => `${pluralize(r.count, "issue is", "issues are")} reported again`);
    }
    function onSubmit(e) {
      e.preventDefault();
      const f = e.target;
      if (f.id === "ackall") {
        run(() => api.post("/api/acks", { activities: [a.id], note: f.elements.note.value.trim() }),
          (r) => `Acknowledged ${pluralize(r.count, "issue", "issues")}`);
      } else if (f.dataset.ackForm) {
        run(() => ackIssues([+f.dataset.ackForm], f.elements.note.value.trim()),
          (r) => `Acknowledged ${pluralize(r.count, "issue", "issues")}`);
      }
    }
    render();
    return {
      destroy() { viewEl.removeEventListener("click", onClick); viewEl.removeEventListener("submit", onSubmit); },
      refresh: reload,
    };
  }

  function ackTable(acks, { activityColumn = true, currentId = null, selectable = false, selected = new Set() } = {}) {
    if (!acks.length) return `<div class="panel empty">No acknowledgments.</div>`;
    return `<div class="panel" style="overflow-x:auto"><table class="grid">
      <thead><tr>${selectable ? `<th><input type="checkbox" data-selall aria-label="Select all active" ${acks.some((k) => k.active) && acks.filter((k) => k.active).every((k) => selected.has(k.issue_number)) ? "checked" : ""}></th>` : ""}<th>Issue</th>${activityColumn ? "<th>Activity</th>" : ""}<th>Check</th><th>Note</th><th>Acknowledged</th><th>Status</th><th></th></tr></thead>
      <tbody>${acks.map((k) => {
        const snap = (k.snapshot || {})[k.activity_id] || {};
        // On an activity's page, a mirrored acknowledgment belongs to the other activity.
        const related = currentId && k.activity_id !== currentId
          ? `<div class="sub">on ${activityLink(k.activity_id)}</div>`
          : k.related_activity_ids.length ? `<div class="sub">with ${k.related_activity_ids.map((r) => activityLink(r)).join(", ")}</div>` : "";
        return `<tr>
          ${selectable ? `<td>${k.active ? `<input type="checkbox" data-sel="${k.issue_number}" ${selected.has(k.issue_number) ? "checked" : ""}>` : ""}</td>` : ""}
          <td class="num">${k.issue_number != null ? esc(k.issue_number) : "–"}</td>
          ${activityColumn ? `<td>${activityLink(k.activity_id, snap.name || k.activity_id)}<div class="sub">${esc(k.activity_id)}${snap.start_local ? " · " + esc(UI.localParts(snap.start_local).date) : ""}</div>${related}</td>` : ""}
          <td>${esc(k.check)}${activityColumn ? "" : related}</td>
          <td>${esc(k.note || "")}</td>
          <td>${esc(UI.when(k.created_at))}</td>
          <td>${k.active ? `<span class="badge ok">active</span>` : `<span class="badge">revoked</span><div class="sub">${esc(UI.when(k.revoked_at))}: ${esc(k.revoked_reason || "")}</div>`}</td>
          <td>${k.active && k.issue_number != null ? `<button class="copy" data-unack="${k.issue_number}">Unacknowledge</button>` : ""}</td>
        </tr>`;
      }).join("")}</tbody></table></div>`;
  }

  async function acksView() {
    let includeRevoked = false, acks = [];
    const selected = new Set();
    async function load() {
      acks = (await api.get("/api/acks?include_revoked=" + includeRevoked)).acks;
      const selectable = new Set(acks.filter((k) => k.active).map((k) => k.issue_number));
      [...selected].forEach((n) => { if (!selectable.has(n)) selected.delete(n); });
      render();
    }
    function render() {
      const active = acks.filter((k) => k.active).length;
      pageShell("Acknowledgments", `
        <div class="row" style="margin-bottom:10px">
          <label class="toggle"><input type="checkbox" id="revoked" ${includeRevoked ? "checked" : ""}> Include revoked (history)</label>
          <span class="help">${pluralize(active, "active acknowledgment", "active acknowledgments")}</span>
          <span class="spacer"></span>
          <button class="secondary" id="unacksel" ${selected.size ? "" : "disabled"}>${selected.size ? `Unacknowledge ${selected.size} selected` : "Unacknowledge selected"}</button>
        </div>
        ${ackTable(acks, { selectable: true, selected })}`);
      $("revoked").addEventListener("change", (e) => { includeRevoked = e.target.checked; load(); });
      $("unacksel").addEventListener("click", () => unack([...selected]));
    }
    async function unack(numbers) {
      try {
        const r = await unackIssues(numbers);
        UI.toast(`${pluralize(r.count, "issue is", "issues are")} reported again`);
        selected.clear(); await load();
      } catch (e) { UI.toast("Failed: " + e.message); }
    }
    function onClick(e) {
      const t = e.target.closest("button, input"); if (!t) return;
      if (t.dataset.unack) unack([+t.dataset.unack]);
      else if (t.dataset.sel) {
        t.checked ? selected.add(+t.dataset.sel) : selected.delete(+t.dataset.sel);
        render();
      } else if (t.hasAttribute("data-selall")) {
        acks.filter((k) => k.active && k.issue_number != null)
          .forEach((k) => (t.checked ? selected.add(k.issue_number) : selected.delete(k.issue_number)));
        render();
      }
    }
    viewEl.addEventListener("click", onClick);
    await load();
    return { destroy: () => viewEl.removeEventListener("click", onClick), refresh: load };
  }

  async function statusView() {
    let lines = 200;
    async function load() {
      const [s, runs, logs] = await Promise.all([
        api.get("/api/status"), api.get("/api/runs?limit=25"), api.get("/api/logs?lines=" + lines)]);
      const banners = [];
      if (!s.credentials.intervals) banners.push(`<div class="banner warn">INTERVALS_API_KEY / INTERVALS_ATHLETE_ID aren't set, so syncing can't work. Set them in .env and restart.</div>`);
      if (s.sync_failing_since) banners.push(`<div class="banner error">Syncing has failed since ${esc(UI.when(s.sync_failing_since))}: ${esc(s.sync_last_error || "")}</div>`);
      if (!s.notifiers.length) banners.push(`<div class="banner warn">No notifiers configured (NOTIFIERS in .env), so the monitor won't send notifications.</div>`);
      const job = s.jobs.current;
      const last = s.jobs.last;
      pageShell("Status", `
        ${banners.join("")}
        <div class="panel"><dl class="kv">
          <dt>Activities</dt><dd>${esc(s.activity_count)}</dd>
          <dt>Issues</dt><dd>${esc(s.open_issues)} open · ${esc(s.acknowledged_issues)} acknowledged · ${esc(s.resolved_issues)} resolved</dd>
          <dt>Last sync</dt><dd>${esc(s.last_sync ? UI.when(s.last_sync) : "never")}</dd>
          <dt>Last full sync</dt><dd>${esc(s.last_full_sync ? UI.when(s.last_full_sync) : "never")}</dd>
          <dt>Monitor</dt><dd>${s.monitor.enabled ? `every ${esc(s.monitor.interval_minutes)} minutes · next run ${esc(UI.when(s.monitor.next_run_at))}` : "off (MONITOR_INTERVAL_MINUTES=0)"}</dd>
          <dt>Notifications</dt><dd>${esc(s.notifiers.join(", ") || "none")} · after ${esc(s.notify_after_minutes)} minutes</dd>
          <dt>Strava</dt><dd>${s.credentials.strava ? "configured" : "not configured"}</dd>
          ${s.streams.types.length ? `<dt>Streams</dt><dd>${esc(s.streams.stored)} stored · ${esc(s.streams.waiting)} waiting to be fetched
            <span class="help">(${esc(s.streams.unavailable)} activities have none; for ${esc(s.streams.types.join(", "))})</span></dd>` : ""}
          <dt>Job</dt><dd>${job ? `<span class="spinner"></span> ${esc(jobLabel(job.kind))} since ${esc(UI.when(job.started_at))}`
            : last ? `last: ${esc(jobLabel(last.kind))} <span class="badge ${esc(last.status)}">${esc(last.status)}</span> ${esc(UI.when(last.finished_at))} – ${esc(last.message)}` : "none yet"}</dd>
          <dt>Version</dt><dd>${esc(s.version)} · <a href="/api/docs">API docs</a> · <a href="/report.html" target="_blank">static report</a></dd>
        </dl>
        <div class="buttons">
          <button class="primary" data-job="sync" ${job ? "disabled" : ""}>Sync now</button>
          <button class="secondary" data-job="resync" ${job ? "disabled" : ""} title="Fetch the whole history, also finds deleted and edited old activities">Full resync</button>
          <button class="secondary" data-job="checks" ${job ? "disabled" : ""} title="Run the checks on the stored activities without syncing">Re-run checks</button>
        </div></div>
        <h2>Recent runs</h2>
        <div class="panel" style="overflow-x:auto"><table class="grid">
          <thead><tr><th>#</th><th>Command</th><th>Started</th><th>Took</th><th>Status</th><th>Result</th></tr></thead>
          <tbody>${runs.runs.map(runRow).join("") || `<tr><td colspan="6">No runs yet.</td></tr>`}</tbody>
        </table></div>
        <h2>Log</h2>
        <div class="row" style="margin-bottom:8px">
          <select id="loglines">${[100, 200, 500, 1000, 3000].map((n) => `<option value="${n}" ${n === lines ? "selected" : ""}>last ${n} lines</option>`).join("")}</select>
          <button class="secondary" id="logrefresh">Refresh</button>
          <span class="help mono">${esc(logs.file)}</span>
        </div>
        <pre class="log" id="log">${esc(logs.lines.join("\n")) || "(empty)"}</pre>`);
      const pre = $("log"); pre.scrollTop = pre.scrollHeight;
      $("loglines").addEventListener("change", (e) => { lines = +e.target.value; load(); });
      $("logrefresh").addEventListener("click", load);
    }
    function runRow(r) {
      const took = r.finished_at ? Math.max(0, Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000)) + "s" : "…";
      const audit = (r.summary || {}).audit || {};
      const sync = (r.summary || {}).sync || {};
      const bits = [];
      if (r.summary && r.summary.offline) bits.push("offline");
      if (sync.fetched != null) bits.push(`${sync.full ? "full sync" : "sync"}: ${sync.fetched} fetched, ${sync.inserted} new, ${sync.changed} changed, ${(sync.removed || []).length} removed`);
      const streams = (r.summary || {}).streams;
      if (streams && (streams.fetched || streams.unavailable)) bits.push(`streams: ${streams.fetched} fetched, ${streams.remaining} waiting`);
      if (audit.new != null) bits.push(`issues: ${audit.new} new, ${audit.reopened} reopened, ${audit.resolved} resolved`);
      return `<tr><td class="num">${esc(r.id)}</td><td>${esc(r.command)}</td><td>${esc(UI.when(r.started_at))}</td><td class="num">${esc(took)}</td>
        <td><span class="badge ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${esc(bits.join(" · "))}${r.error ? `<div class="sub">${esc(r.error)}</div>` : ""}</td></tr>`;
    }
    function onClick(e) {
      const t = e.target.closest("button[data-job]"); if (!t) return;
      startJob(t.dataset.job).then(load);
    }
    viewEl.addEventListener("click", onClick);
    await load();
    return { destroy: () => viewEl.removeEventListener("click", onClick), refresh: load };
  }

  async function checksView() {
    let checks = [];
    async function load() { checks = (await api.get("/api/checks")).checks; render(); }
    function field(c, p) {
      const id = `f-${c.name}-${p.key}`;
      const hint = p.changed ? `<div class="changed">changed here · file: ${esc(JSON.stringify(p.file_value))}</div>` : "";
      let input;
      if (p.kind === "bool") input = `<input type="checkbox" id="${id}" data-key="${esc(p.key)}" ${p.value ? "checked" : ""}>`;
      else if (p.kind === "int" || p.kind === "float") input = `<input type="number" id="${id}" data-key="${esc(p.key)}" value="${esc(p.value)}" step="${p.kind === "int" ? "1" : "any"}">`;
      else if (p.kind === "list") input = `<input type="text" id="${id}" data-key="${esc(p.key)}" value="${esc((p.value || []).join(", "))}"><div class="hint">Comma separated</div>`;
      else if (p.kind === "str") input = `<input type="text" id="${id}" data-key="${esc(p.key)}" value="${esc(p.value)}">`;
      else input = `<textarea id="${id}" data-key="${esc(p.key)}" rows="${jsonRows(p.value)}" spellcheck="false">${esc(jsonText(p.value))}</textarea><div class="hint">JSON</div>`;
      return `<label for="${id}"><code>${esc(p.key)}</code></label><div>${input}${hint}</div>`;
    }
    // Tables (e.g. per activity type) one entry per line, so they're easy to edit.
    function jsonText(value) {
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const entries = Object.entries(value);
        if (!entries.length) return "{}";
        return "{\n" + entries.map(([k, v]) => `  ${JSON.stringify(k)}: ${JSON.stringify(v)}`).join(",\n") + "\n}";
      }
      return JSON.stringify(value);
    }
    const jsonRows = (value) => Math.min(16, Math.max(2, jsonText(value).split("\n").length));
    function render() {
      pageShell("Checks", `
        <p class="help">Settings come from <code>checks.toml</code> (and <code>checks.local.toml</code>). Changes made here are
        stored in the database and win over the files; "Reset" goes back to the files. Saving re-runs the checks.</p>
        ${checks.map((c) => `
          <form class="panel" data-check="${esc(c.name)}">
            <div class="check-head">
              <h3>${esc(c.name)}</h3>
              <label class="toggle"><input type="checkbox" data-key="enabled" ${c.enabled ? "checked" : ""}> enabled</label>
              ${c.changed ? `<span class="changed">changed here</span>` : ""}
              <div class="desc">${esc(c.description)}</div>
            </div>
            ${c.params.length ? `<div class="form-grid">${c.params.map((p) => field(c, p)).join("")}</div>` : ""}
            <div class="buttons">
              <button class="primary" type="submit">Save</button>
              <button class="secondary" type="button" data-reset="${esc(c.name)}" ${c.changed ? "" : "disabled"}>Reset to file</button>
            </div>
          </form>`).join("")}`);
    }
    function readForm(form, check) {
      const values = { enabled: form.querySelector('[data-key="enabled"]').checked };
      for (const p of check.params) {
        const input = form.querySelector(`[data-key="${CSS.escape(p.key)}"]`);
        if (p.kind === "bool") values[p.key] = input.checked;
        else if (p.kind === "int" || p.kind === "float") {
          if (input.value.trim() === "" || isNaN(+input.value)) throw new Error(`${p.key} must be a number`);
          values[p.key] = p.kind === "int" ? parseInt(input.value, 10) : parseFloat(input.value);
        } else if (p.kind === "list") {
          const numeric = (p.file_value || []).length && (p.file_value || []).every((x) => typeof x === "number");
          values[p.key] = input.value.split(",").map((x) => x.trim()).filter(Boolean).map((x) => (numeric ? +x : x));
        } else if (p.kind === "str") values[p.key] = input.value;
        else {
          try { values[p.key] = JSON.parse(input.value); } catch (e) { throw new Error(`${p.key}: not valid JSON`); }
        }
      }
      return values;
    }
    async function onSubmit(e) {
      const form = e.target.closest("form[data-check]"); if (!form) return;
      e.preventDefault();
      const check = checks.find((c) => c.name === form.dataset.check);
      try {
        const r = await api.put("/api/checks/" + encodeURIComponent(check.name), { values: readForm(form, check) });
        UI.toast(r.message); refreshNav(); await load();
      } catch (err) { UI.toast("Not saved: " + err.message); }
    }
    async function onClick(e) {
      const t = e.target.closest("button[data-reset]"); if (!t) return;
      try {
        const r = await api.del("/api/checks/" + encodeURIComponent(t.dataset.reset));
        UI.toast(r.message); refreshNav(); await load();
      } catch (err) { UI.toast("Failed: " + err.message); }
    }
    viewEl.addEventListener("submit", onSubmit);
    viewEl.addEventListener("click", onClick);
    await load();
    return {
      destroy() { viewEl.removeEventListener("submit", onSubmit); viewEl.removeEventListener("click", onClick); },
      refresh: load,
    };
  }

  // ---------- routing ----------
  let current = null, routeSeq = 0;
  const routes = [
    [/^#?\/?$/, "issues", () => issuesView()],
    [/^#\/activity\/(.+)$/, "issues", (m) => activityView(decodeURIComponent(m[1]))],
    [/^#\/acks$/, "acks", () => acksView()],
    [/^#\/status$/, "status", () => statusView()],
    [/^#\/checks$/, "checks", () => checksView()],
  ];
  async function route() {
    if (current && current.destroy) current.destroy();
    current = null;
    const seq = ++routeSeq;
    const hash = location.hash || "#/";
    const match = routes.find(([re]) => re.test(hash)) || routes[0];
    document.querySelectorAll(".nav a.tab").forEach((a) => a.classList.toggle("active", a.dataset.route === match[1]));
    viewEl.innerHTML = `<div class="wrap page"><span class="spinner"></span> Loading…</div>`;
    try {
      const view = await match[2](hash.match(match[0]));
      if (seq !== routeSeq) {  // navigated elsewhere while this view was loading
        if (view && view.destroy) view.destroy();
        if (current) route();  // ...and the newer view had already drawn: draw it again
        return;
      }
      current = view;
      window.scrollTo(0, 0);
    } catch (e) { if (seq === routeSeq) showError(e); }
  }
  window.addEventListener("hashchange", route);
  route();
  refreshNav();
})();
