/* Issue browser shared by the static report (report.html, inlined) and the
 * web interface (served as /ui/issues.js).
 *
 *   const browser = AuditorUI.IssueBrowser.create(rootElement, {
 *     mode: "static" | "live",
 *     storageKey: "…",                    // sessionStorage key (per tab)
 *     checks: {name: description},
 *     command: "python main.py",          // static: prefix of copied commands
 *     sort: "newest" | "severity" | …,    // the sort a new tab starts with
 *     activityHref: (activity) => "#…",   // live: link for the activity name
 *     onAck: async (numbers, note) => {}, // live: acknowledge issues
 *     onUnack: async (numbers) => {},     // live: report issues again
 *   });
 *   browser.setData(activities);          // re-render, keeping filters/selection
 *   browser.destroy();
 *
 * Static mode builds `ack`/`unack` commands to copy; live mode calls onAck/
 * onUnack. Everything else (search, filters, sorting, selection) is shared.
 */
(function () {
  "use strict";

  const SEVERITIES = ["high", "medium", "low", "info"];

  // ---------- helpers ----------
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const shellQuote = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  function duration(sec) {
    sec = Math.round(sec || 0);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + "h" + String(m).padStart(2, "0") + "m";
    if (m) return m + "m" + String(s).padStart(2, "0") + "s";
    return s + "s";
  }
  function distance(m) {
    if (!m) return "no distance";
    return m < 1000 ? Math.round(m) + " m" : (m / 1000).toFixed(1) + " km";
  }
  function localParts(value) {
    // start_local is the athlete's wall-clock time; show it as written.
    if (!value) return { date: "–", time: "" };
    const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(value);
    if (!m) return { date: value, time: "" };
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    const wd = d.toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" });
    return { date: m[1] + "-" + m[2] + "-" + m[3], time: wd + " " + m[4] + ":" + m[5] };
  }
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  const sevRank = (s) => { const r = SEVERITIES.indexOf(s); return r < 0 ? 9 : r; };

  function toast(msg) {
    let t = document.getElementById("auditor-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "auditor-toast"; t.className = "toast"; t.setAttribute("role", "status");
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("show"), msg.includes("\n") ? 3500 : 2000);
  }
  async function copy(text, label, hint) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else throw new Error("no clipboard api");
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
      ta.remove();
    }
    toast((label || "Copied") + ": " + (text.length > 70 ? text.slice(0, 67) + "…" : text) + (hint ? "\n" + hint : ""));
  }
  const RELOAD_KEY = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? "⌘R" : "Ctrl+R";

  function renderStats(el, activities) {
    const all = activities.flatMap((a) => a.issues);
    const open = all.filter((i) => i.state === "open");
    const openActs = activities.filter((a) => a.issues.some((i) => i.state === "open")).length;
    const acked = all.filter((i) => i.state === "acknowledged").length;
    const serious = open.filter((i) => i.severity === "high" || i.severity === "medium").length;
    el.innerHTML = [
      [open.length, "open issues"],
      [openActs, openActs === 1 ? "activity with open issues" : "activities with open issues"],
      [serious, "high or medium severity"],
      [acked, "acknowledged"],
    ].map(([n, l]) => `<div class="stat"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`).join("");
  }

  // ---------- the browser ----------
  function create(root, options) {
    const opts = Object.assign({ mode: "static", storageKey: "intervals-auditor", checks: {}, command: "python main.py",
                                 sort: "newest" }, options);
    const live = opts.mode === "live";
    const state = {
      q: "", show: "open", sev: new Set(), checks: new Set(), type: "",
      sort: opts.sort, details: false, prefix: opts.command,
      selected: new Set(), ackForm: null, busy: false,
    };
    let activities = [], allIssues = [], byNumber = new Map(), types = [], checkNames = [];

    // Remembered per tab only (sessionStorage): survives reloads, not new tabs.
    try {
      const saved = JSON.parse(sessionStorage.getItem(opts.storageKey) || "{}");
      if (["open", "acknowledged", "all"].includes(saved.show)) state.show = saved.show;
      if (typeof saved.sort === "string") state.sort = saved.sort;
      if (typeof saved.details === "boolean") state.details = saved.details;
      if (typeof saved.prefix === "string" && saved.prefix) state.prefix = saved.prefix;
      if (typeof saved.q === "string") state.q = saved.q;
      if (Array.isArray(saved.sev)) saved.sev.forEach((x) => state.sev.add(x));
      if (Array.isArray(saved.checks)) saved.checks.forEach((x) => state.checks.add(x));
      if (typeof saved.type === "string") state.type = saved.type;
    } catch (e) { /* storage unavailable: defaults are fine */ }
    function persist() {
      try {
        sessionStorage.setItem(opts.storageKey, JSON.stringify({
          show: state.show, sort: state.sort, details: state.details, prefix: state.prefix,
          q: state.q, sev: [...state.sev], checks: [...state.checks], type: state.type,
        }));
      } catch (e) { /* ignore */ }
    }

    root.innerHTML = `
      <div class="toolbar"><div class="wrap">
        <div class="row">
          <input data-el="q" class="search" type="search" placeholder="Search name, id, issue number, text…  (press / )" aria-label="Search">
          <div class="seg" role="group" aria-label="Show" data-el="seg">
            <button data-state="open">Open</button>
            <button data-state="acknowledged">Acknowledged</button>
            <button data-state="all">All</button>
          </div>
          <select data-el="type" aria-label="Activity type"></select>
          <select data-el="sort" aria-label="Sort">
            <option value="newest">Newest activity first</option>
            <option value="oldest">Oldest activity first</option>
            <option value="severity">Most severe first</option>
            <option value="count">Most issues first</option>
            <option value="number">Newest issue first</option>
          </select>
        </div>
        <div class="row"><span class="label">Severity</span><span data-el="sevchips" class="row"></span></div>
        <div class="row">
          <span class="label">Check</span><span data-el="checkchips" class="row"></span>
          <button class="linkbtn" data-el="reset">Reset filters</button>
          <label class="toggle"><input type="checkbox" data-el="details"> Show details</label>
        </div>
      </div></div>
      <div class="wrap"><main>
        <div class="result-line" data-el="resultbar">
          <label title="Select or unselect every issue the current filters show">
            <input type="checkbox" data-el="selectall"> <span data-el="selectalllabel">Select all</span>
          </label>
          <span data-el="resultline"></span>
        </div>
        <div data-el="list"></div>
        <div class="settings">
          ${live ? `Tip: tick issues (or "Select all", which follows the filters) to acknowledge them or report them again in one go.
            Filters are kept while this tab is open. <kbd>/</kbd> search, <kbd>Esc</kbd> clear selection.`
          : `Commands are copied as <input data-el="prefix" aria-label="Command prefix" spellcheck="false">
            <span class="help">(e.g. <code>./main.py</code>).
            Tip: tick issues (or "Select all" above the list, which follows the filters) to build one
            <code>ack</code> command for open ones and one <code>unack</code> command for acknowledged ones.
            <code>ack</code>/<code>unack</code> update this file; reload the page afterwards. Filters and
            settings are kept while this tab is open, and start fresh in a new tab.
            <kbd>/</kbd> search, <kbd>Esc</kbd> clear selection.</span>`}
        </div>
      </main></div>
      <div class="selbar" data-el="selbar" aria-live="polite"><div class="wrap">
        <div class="row">
          <strong data-el="selcount"></strong>
          <input data-el="note" class="note" placeholder="Why these are fine (optional note for acknowledging)" aria-label="Note">
          ${live ? `<button class="primary" data-el="ackbtn"></button><button class="secondary" data-el="unackbtn"></button>` : ""}
          <button class="secondary" data-el="clearsel">Clear selection</button>
        </div>
        ${live ? "" : `
        <div class="cmdrow" data-el="ackrow">
          <span class="cmdlabel" data-el="acklabel"></span><code class="cmd mono" data-el="ackcmd"></code>
          <button class="primary" data-el="copyack">Copy</button>
        </div>
        <div class="cmdrow" data-el="unackrow">
          <span class="cmdlabel" data-el="unacklabel"></span><code class="cmd mono" data-el="unackcmd"></code>
          <button class="primary" data-el="copyunack">Copy</button>
        </div>`}
      </div></div>`;
    const el = (name) => root.querySelector(`[data-el="${name}"]`);

    // ---------- data ----------
    function setData(list) {
      activities = list.map((a) => {
        const parts = localParts(a.start_local);
        const hay = [a.id, a.name, a.type, parts.date].join(" ").toLowerCase();
        a.issues.forEach((i) => {
          i._hay = [hay, i.number, "#" + i.number, i.check, i.summary,
            i.acknowledgment ? i.acknowledgment.note : "",
            (i.related || []).map((r) => r.id).join(" ")].join(" ").toLowerCase();
        });
        return Object.assign(a, { _parts: parts, _start: a.start_local || "" });
      });
      allIssues = activities.flatMap((a) => a.issues);
      byNumber = new Map(allIssues.map((i) => [i.number, i]));
      types = [...new Set(activities.map((a) => a.type).filter(Boolean))].sort();
      checkNames = [...new Set(allIssues.map((i) => i.check))].sort();
      [...state.selected].forEach((n) => { if (!byNumber.has(n)) state.selected.delete(n); });
      if (state.ackForm != null && !byNumber.has(state.ackForm)) state.ackForm = null;
      render();
    }

    // ---------- filtering ----------
    const stateMatch = (i) => state.show === "all" || i.state === state.show;
    function matches(i, a, ignore) {
      if (!stateMatch(i)) return false;
      if (ignore !== "sev" && state.sev.size && !state.sev.has(i.severity)) return false;
      if (ignore !== "check" && state.checks.size && !state.checks.has(i.check)) return false;
      if (state.type && a.type !== state.type) return false;
      if (state.q) {
        const words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.every((w) => i._hay.includes(w))) return false;
      }
      return true;
    }
    function countBy(field, ignore) {
      const counts = {};
      activities.forEach((a) => a.issues.forEach((i) => {
        if (matches(i, a, ignore)) counts[i[field]] = (counts[i[field]] || 0) + 1;
      }));
      return counts;
    }
    function visibleNumbers() {
      const seen = new Set();
      activities.forEach((a) => a.issues.forEach((i) => { if (matches(i, a)) seen.add(i.number); }));
      return seen;
    }

    // ---------- rendering ----------
    function renderControls() {
      el("seg").querySelectorAll("button").forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.state === state.show)));
      el("type").innerHTML = `<option value="">All activity types</option>` +
        types.map((t) => `<option value="${esc(t)}"${t === state.type ? " selected" : ""}>${esc(t)}</option>`).join("");
      el("sort").value = state.sort;
      el("details").checked = state.details;
      if (el("prefix") && document.activeElement !== el("prefix")) el("prefix").value = state.prefix;
      if (document.activeElement !== el("q")) el("q").value = state.q;
      const sevCounts = countBy("severity", "sev");
      el("sevchips").innerHTML = SEVERITIES.map((s) => {
        const n = sevCounts[s] || 0;
        return `<button class="chip" data-sev="${s}" aria-pressed="${state.sev.has(s)}" ${n || state.sev.has(s) ? "" : "disabled"}>
          <span class="sev ${s}">${s}</span><span class="count">${n}</span></button>`;
      }).join("");
      const checkCounts = countBy("check", "check");
      el("checkchips").innerHTML = checkNames.map((c) => {
        const n = checkCounts[c] || 0;
        return `<button class="chip" data-check="${esc(c)}" title="${esc(opts.checks[c] || "")}" aria-pressed="${state.checks.has(c)}" ${n || state.checks.has(c) ? "" : "disabled"}>
          ${esc(c)}<span class="count">${n}</span></button>`;
      }).join("");
    }

    function issueActions(i) {
      const acked = i.state === "acknowledged";
      if (!live) {
        return acked
          ? `<button class="copy" data-copy-unack="${i.number}" title="Copy the command that reports this issue again">Copy unack</button>`
          : `<button class="copy" data-copy-ack="${i.number}" title="Copy the command that acknowledges this issue">Copy ack</button>`;
      }
      return acked
        ? `<button class="copy" data-unack="${i.number}" ${state.busy ? "disabled" : ""}>Unacknowledge</button>`
        : `<button class="copy" data-open-ack="${i.number}" ${state.busy ? "disabled" : ""}>Acknowledge…</button>`;
    }

    function issueHtml(i) {
      const acked = i.state === "acknowledged";
      const related = (i.related || []).map((r) =>
        `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.id)} ↗</a>`).join("");
      const details = (i.details || []).map((d) => {
        const rest = Object.entries(d).filter(([k]) => k !== "rule").map(([k, v]) => `${k}=${v}`).join(", ");
        return `<li><code>${esc(d.rule || "detail")}</code>${rest ? ": " + esc(rest) : ""}</li>`;
      }).join("");
      const ack = i.acknowledgment;
      const form = live && state.ackForm === i.number ? `
        <form class="inline-form" data-ack-form="${i.number}">
          <input type="text" name="note" placeholder="Why it's fine (optional)" aria-label="Note for issue ${i.number}">
          <button class="primary" type="submit" ${state.busy ? "disabled" : ""}>Acknowledge</button>
          <button class="secondary" type="button" data-cancel-ack>Cancel</button>
        </form>` : "";
      return `<div class="issue${acked ? " acked" : ""}">
        <div><input type="checkbox" data-select="${i.number}" aria-label="Select issue ${i.number}" ${state.selected.has(i.number) ? "checked" : ""}></div>
        <div>
          <div class="top">
            <span class="num">Issue ${i.number}</span>
            <span class="sev ${esc(i.severity)}">${esc(i.severity)}</span>
            <span class="check" title="${esc(opts.checks[i.check] || "")}">${esc(i.check)}</span>
            ${acked ? `<span class="badge-ack">acknowledged</span>` : ""}
          </div>
          <div class="summary">${esc(i.summary)}</div>
          ${related ? `<div class="sub">Related: ${related}</div>` : ""}
          ${ack ? `<div class="ack-note">Acknowledged ${esc(when(ack.created_at))}${ack.note ? ": " + esc(ack.note) : ""}</div>` : ""}
          ${i.reopen_reason && !acked ? `<div class="sub">Reopened: ${esc(i.reopen_reason)}</div>` : ""}
          ${details ? `<details class="det"${state.details ? " open" : ""}><summary>Details${i.first_seen_at ? " · found " + esc(when(i.first_seen_at)) : ""}</summary><ul>${details}</ul></details>` : ""}
          ${form}
        </div>
        <div class="actions">${issueActions(i)}</div>
      </div>`;
    }

    function cardHtml(a, issues) {
      const p = a._parts;
      const allSelected = issues.every((i) => state.selected.has(i.number));
      const name = a.deleted ? "(deleted activity)" : esc(a.name || "(no name)");
      const href = opts.activityHref ? opts.activityHref(a) : null;
      return `<section class="card">
        <div class="card-head">
          <div class="when"><div class="date">${esc(p.date)}</div><div class="time">${esc(p.time)}</div></div>
          <div class="what">
            <div class="name">${href ? `<a href="${esc(href)}">${name}</a>` : name}</div>
            <div class="facts"><span class="type">${esc(a.type || "?")}</span>${a.deleted ? "" : esc(duration(a.duration_seconds)) + " · " + esc(distance(a.distance))}</div>
          </div>
          <div class="ids">
            <div class="idline">activity: <span class="mono">${esc(a.id)}</span>
              <button class="copy" data-copy-id="${esc(a.id)}" title="Copy activity id">Copy</button></div>
            <div><a href="${esc(a.url)}" target="_blank" rel="noopener">Open in Intervals.icu ↗</a></div>
            ${issues.length > 1 ? `<div><button class="linkbtn" data-select-activity="${esc(a.id)}">${allSelected ? "Unselect" : "Select"} all ${issues.length}</button></div>` : ""}
          </div>
        </div>
        ${issues.map(issueHtml).join("")}
      </section>`;
    }

    function sortedGroups() {
      const groups = activities
        .map((a) => ({ a, issues: a.issues.filter((i) => matches(i, a)) }))
        .filter((g) => g.issues.length);
      const newest = (x, y) => (y.a._start > x.a._start ? 1 : y.a._start < x.a._start ? -1 : 0);
      const sorters = {
        newest,
        oldest: (x, y) => -newest(x, y),
        severity: (x, y) => Math.min(...x.issues.map((i) => sevRank(i.severity))) -
          Math.min(...y.issues.map((i) => sevRank(i.severity))) || newest(x, y),
        count: (x, y) => y.issues.length - x.issues.length || newest(x, y),
        number: (x, y) => Math.max(...y.issues.map((i) => i.number)) - Math.max(...x.issues.map((i) => i.number)),
      };
      groups.sort(sorters[state.sort] || newest);
      groups.forEach((g) => g.issues.sort((i, j) => sevRank(i.severity) - sevRank(j.severity) || i.number - j.number));
      return groups;
    }

    function renderList() {
      const groups = sortedGroups();
      const n = groups.reduce((sum, g) => sum + g.issues.length, 0);
      const filtered = state.q || state.sev.size || state.checks.size || state.type;
      el("resultbar").hidden = !n;
      el("resultline").textContent = n
        ? `Showing ${plural(n, "issue", "issues")} on ${plural(groups.length, "activity", "activities")}` + (filtered ? " (filtered)" : "")
        : "";
      const shown = groups.flatMap((g) => g.issues.map((i) => i.number));
      const picked = shown.filter((num) => state.selected.has(num)).length;
      el("selectall").checked = n > 0 && picked === n;
      el("selectall").indeterminate = picked > 0 && picked < n;
      el("selectalllabel").textContent = picked === n && n ? `Unselect all ${n}` : `Select all ${n}`;
      if (!groups.length) {
        const nothingAtAll = !allIssues.some(stateMatch);
        el("list").innerHTML = `<div class="empty">${nothingAtAll
          ? (state.show === "open" ? "No open issues. Nothing to look at." : "Nothing here.")
          : "No issues match these filters."}</div>`;
      } else {
        el("list").innerHTML = groups.map((g) => cardHtml(g.a, g.issues)).join("");
      }
      const form = root.querySelector("form[data-ack-form] input[name=note]");
      if (form && !form.dataset.focused) { form.focus(); form.dataset.focused = "1"; }
    }

    function selectedByState() {
      const nums = [...state.selected].filter((n) => byNumber.has(n)).sort((a, b) => a - b);
      return {
        all: nums,
        toAck: nums.filter((n) => byNumber.get(n).state === "open"),
        toUnack: nums.filter((n) => byNumber.get(n).state === "acknowledged"),
      };
    }
    const ackCommand = (numbers) => {
      const note = el("note").value.trim();
      return `${state.prefix} ack ${numbers.join(" ")}` + (note ? ` -n ${shellQuote(note)}` : "");
    };
    const unackCommand = (numbers) => `${state.prefix} unack ${numbers.join(" ")}`;

    function renderSelection() {
      const sel = selectedByState();
      const visible = visibleNumbers();
      const hidden = sel.all.filter((n) => !visible.has(n)).length;
      el("selbar").classList.toggle("show", sel.all.length > 0);
      el("selcount").textContent = plural(sel.all.length, "issue selected", "issues selected") +
        (hidden ? ` (${hidden} hidden by the filters)` : "");
      el("note").hidden = !sel.toAck.length;
      if (live) {
        el("ackbtn").hidden = !sel.toAck.length;
        el("ackbtn").disabled = state.busy;
        el("ackbtn").textContent = "Acknowledge " + sel.toAck.length;
        el("unackbtn").hidden = !sel.toUnack.length;
        el("unackbtn").disabled = state.busy;
        el("unackbtn").textContent = "Report " + sel.toUnack.length + " again";
      } else {
        el("ackrow").hidden = !sel.toAck.length;
        el("acklabel").textContent = "Acknowledge " + plural(sel.toAck.length, "open issue", "open issues");
        el("ackcmd").textContent = sel.toAck.length ? ackCommand(sel.toAck) : "";
        el("unackrow").hidden = !sel.toUnack.length;
        el("unacklabel").textContent = "Report " + plural(sel.toUnack.length, "acknowledged issue", "acknowledged issues") + " again";
        el("unackcmd").textContent = sel.toUnack.length ? unackCommand(sel.toUnack) : "";
      }
    }

    function render() {
      persist();
      renderControls();
      renderList();
      renderSelection();
    }

    // ---------- live actions ----------
    async function act(fn, numbers, ...args) {
      if (state.busy || !numbers.length) return;
      state.busy = true; renderSelection(); renderList();
      try {
        await fn(numbers, ...args);
        numbers.forEach((n) => state.selected.delete(n));
        state.ackForm = null;
        el("note").value = "";
      } catch (e) {
        toast("Failed: " + (e && e.message ? e.message : e));
      } finally {
        state.busy = false; render();
      }
    }
    const ack = (numbers, note) => act(opts.onAck, numbers, note);
    const unack = (numbers) => act(opts.onUnack, numbers);

    // ---------- events ----------
    const copyCommand = (text) => copy(text, "Copied", `Run it, then reload this page (${RELOAD_KEY}) to see the result`);
    el("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
    el("seg").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      state.show = b.dataset.state; render();
    });
    el("type").addEventListener("change", (e) => { state.type = e.target.value; render(); });
    el("sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
    el("details").addEventListener("change", (e) => {
      state.details = e.target.checked; persist();
      root.querySelectorAll("details.det").forEach((d) => { d.open = state.details; });
    });
    if (el("prefix")) {
      el("prefix").addEventListener("input", (e) => {
        state.prefix = e.target.value.trim() || opts.command; persist(); renderSelection();
      });
    }
    el("reset").addEventListener("click", () => {
      state.q = ""; el("q").value = ""; state.sev.clear(); state.checks.clear(); state.type = ""; render();
    });
    el("sevchips").addEventListener("click", (e) => {
      const b = e.target.closest("[data-sev]"); if (!b) return;
      const s = b.dataset.sev; state.sev.has(s) ? state.sev.delete(s) : state.sev.add(s); render();
    });
    el("checkchips").addEventListener("click", (e) => {
      const b = e.target.closest("[data-check]"); if (!b) return;
      const c = b.dataset.check; state.checks.has(c) ? state.checks.delete(c) : state.checks.add(c); render();
    });
    el("selectall").addEventListener("change", () => {
      const shown = [...visibleNumbers()];
      const all = shown.every((n) => state.selected.has(n));
      shown.forEach((n) => (all ? state.selected.delete(n) : state.selected.add(n)));
      renderSelection(); renderList();
    });
    el("list").addEventListener("click", (e) => {
      const t = e.target.closest("button, input"); if (!t) return;
      if (t.dataset.select) {
        const n = +t.dataset.select;
        t.checked ? state.selected.add(n) : state.selected.delete(n);
        renderSelection(); renderList();
      } else if (t.dataset.selectActivity) {
        const a = activities.find((x) => x.id === t.dataset.selectActivity);
        const nums = a.issues.filter((i) => matches(i, a)).map((i) => i.number);
        const all = nums.every((n) => state.selected.has(n));
        nums.forEach((n) => (all ? state.selected.delete(n) : state.selected.add(n)));
        renderSelection(); renderList();
      } else if (t.dataset.copyAck) {
        copyCommand(ackCommand([+t.dataset.copyAck]));
      } else if (t.dataset.copyUnack) {
        copyCommand(unackCommand([+t.dataset.copyUnack]));
      } else if (t.dataset.copyId) {
        copy(t.dataset.copyId, "Copied activity id");
      } else if (t.dataset.openAck) {
        state.ackForm = +t.dataset.openAck; renderList();
      } else if (t.hasAttribute("data-cancel-ack")) {
        state.ackForm = null; renderList();
      } else if (t.dataset.unack) {
        unack([+t.dataset.unack]);
      }
    });
    el("list").addEventListener("submit", (e) => {
      const f = e.target.closest("form[data-ack-form]"); if (!f) return;
      e.preventDefault();
      ack([+f.dataset.ackForm], f.elements.note.value.trim());
    });
    el("note").addEventListener("input", renderSelection);
    el("clearsel").addEventListener("click", () => { state.selected.clear(); renderSelection(); renderList(); });
    if (live) {
      el("ackbtn").addEventListener("click", () => ack(selectedByState().toAck, el("note").value.trim()));
      el("unackbtn").addEventListener("click", () => unack(selectedByState().toUnack));
    } else {
      el("copyack").addEventListener("click", () => {
        const sel = selectedByState(); if (sel.toAck.length) copyCommand(ackCommand(sel.toAck));
      });
      el("copyunack").addEventListener("click", () => {
        const sel = selectedByState(); if (sel.toUnack.length) copyCommand(unackCommand(sel.toUnack));
      });
    }
    function onKey(e) {
      const a = document.activeElement;
      const typing = !!a && (/^(TEXTAREA|SELECT)$/.test(a.tagName) ||
        (a.tagName === "INPUT" && !/^(checkbox|radio|button)$/.test(a.type)));
      if (e.key === "/" && !typing) { e.preventDefault(); el("q").focus(); }
      else if (e.key === "Escape") {
        if (typing) a.blur();
        else if (state.ackForm != null) { state.ackForm = null; renderList(); }
        else if (state.selected.size) { state.selected.clear(); renderSelection(); renderList(); }
      }
    }
    document.addEventListener("keydown", onKey);

    return {
      setData,
      destroy() { document.removeEventListener("keydown", onKey); root.innerHTML = ""; },
    };
  }

  window.AuditorUI = {
    IssueBrowser: { create },
    renderStats, esc, when, duration, distance, localParts, toast, copy, plural,
  };
})();
