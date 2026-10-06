(() => {
  const $ = (id) => document.getElementById(id);
  const loginGate = $("loginGate");
  const app = $("app");
  let pendingControl = null;
  let timer = null;

  function showToast(msg, ok) {
    const el = $("toast");
    el.textContent = msg;
    el.className = "toast " + (ok ? "ok" : "bad");
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    return { res, data };
  }

  function setTheme(theme) {
    const t = theme === "night" ? "night" : "day";
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("fleet_theme", t);
    document.querySelectorAll("#themeToggle button").forEach((b) => {
      b.classList.toggle("on", b.dataset.theme === t);
    });
  }

  function tickClock() {
    try {
      const fmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
      const dfmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "short",
        day: "2-digit",
      });
      const now = new Date();
      $("clock").textContent = fmt.format(now);
      $("clockDate").textContent = dfmt.format(now) + " IST";
    } catch (_) {}
  }

  function fmtNum(n, digits = 0) {
    if (n === null || n === undefined || n === "") return "—";
    const x = Number(n);
    if (!Number.isFinite(x)) return "—";
    return x.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function pnlClass(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "";
    return x >= 0 ? "pos" : "neg";
  }

  function statusPill(desk) {
    const s = String(desk.status || "UNKNOWN").toUpperCase();
    if (!desk.reachable) return { cls: "bad", text: "UNREACHABLE" };
    if (s === "RUNNING") return { cls: "ok", text: "RUNNING" };
    if (s === "PAUSED") return { cls: "warn", text: "PAUSED" };
    if (s === "UNREACHABLE") return { cls: "bad", text: "UNREACHABLE" };
    return { cls: "info", text: s };
  }

  function gauge(pct, label, color) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    return `<div class="gauge" style="--pct:${p};--gcolor:${color}"><div><strong>${fmtNum(p, 0)}%</strong><span>${label}</span></div></div>`;
  }

  function dot(ok, label) {
    const cls = ok === true ? "ok" : ok === false ? "bad" : "warn";
    return `<span class="stat-dot ${cls}"><i></i>${label}</span>`;
  }

  function cardHtml(desk, totals) {
    const isMaster = desk.role_tag === "MASTER";
    const st = statusPill(desk);
    const runningOk = desk.reachable && String(desk.status || "").toUpperCase() === "RUNNING";
    const paused = String(desk.status || "").toUpperCase() === "PAUSED";
    const linkUp = desk.link_status === "UP" && desk.reachable !== false;
    const copyOn = !!desk.copy_on;
    const liveOk = String(desk.order_mode || "").toLowerCase() === "live";
    const modeLabel = String(desk.order_mode || "—").toUpperCase();
    const statusLabel = paused ? "PAUSED" : (st.text || "UNKNOWN");
    const statusOk = runningOk ? true : (paused ? null : false);
    const subOk = String(desk.subscription_status || "").toUpperCase() === "ACTIVE";
    const feederOk = desk.feeder_ok === true || String(desk.feeder_unit || "").toLowerCase() === "active";
    const feederLabel = feederOk ? "Data feeder OK" : "Data feeder DOWN";
    const t = totals || {};
    const fleetTotalPanel = isMaster ? `
          <section class="panel fleet-total-panel">
            <h3 class="panel-title">Fleet Total</h3>
            <div class="fleet-total-board">
              <div class="kv"><span>Total Day PnL</span><b class="${pnlClass(t.pnl_sum)}">${fmtNum(t.pnl_sum, 2)}</b></div>
              <div class="kv"><span>Total Margin Free</span><b class="${pnlClass(t.margin_sum)}">${fmtNum(t.margin_sum, 0)}</b></div>
            </div>
            <div class="fleet-refresh">
              <span class="fleet-updated" id="updatedAt">—</span>
              <div class="fleet-refresh-btns actions four">
                <button type="button" class="btn primary" data-fleet-refresh="1">Refresh</button>
                <button type="button" class="btn primary" data-fleet-refresh="margin">Refresh margin</button>
              </div>
            </div>
          </section>` : "";
    return `
      <article class="card glass desk-card ${isMaster ? "master three-panel" : "child"}" data-id="${desk.id || ""}" data-label="${desk.label || desk.id || ""}">
        <div class="card-head">
          <span class="tag ${isMaster ? "master" : "child"}">${isMaster ? "MASTER" : "CHILD"}</span>
          ${isMaster ? "" : `<h2 class="desk-name">${desk.label || desk.id}</h2>`}
        </div>
        <div class="panel-row ${isMaster ? "cols-3 master-cols" : "cols-3"}">
          <section class="panel status-panel">
            <h3 class="panel-title">Status / Health</h3>
            ${isMaster ? `
            <div class="status-body master-status">
              <div class="status-board stacked">
                ${dot(statusOk, statusLabel)}
                ${dot(linkUp, `Link ${desk.link_status || "?"} (${desk.link_type || "?"})`)}
                ${dot(!!desk.aws_account, `AWS · ${desk.aws_account || "—"}`)}
                ${dot(copyOn, `Copy ${copyOn ? "ON" : "OFF"}`)}
                ${dot(liveOk, `Mode ${modeLabel}`)}
                ${dot(desk.reachable !== false, desk.reachable === false ? "Desk DOWN" : "Desk reachable")}
                ${dot(feederOk, feederLabel)}
              </div>
              <div class="gauges tight grid2">
                ${gauge(desk.cpu_pct, "CPU", "var(--green)")}
                ${gauge(desk.ram_pct, "RAM", "var(--blue)")}
                ${gauge(desk.disk_pct, "DISK", "var(--amber)")}
                ${gauge(desk.feeder_cpu_pct, "FEED", feederOk ? "var(--cyan)" : "var(--red)")}
              </div>
            </div>` : `
            <div class="status-body child-status">
              <div class="status-board stacked">
                ${dot(statusOk, statusLabel)}
                ${dot(linkUp, `Link ${desk.link_status || "?"} (${desk.link_type || "?"})`)}
                ${dot(!!desk.aws_account, `AWS · ${desk.aws_account || "—"}`)}
                ${dot(copyOn, `Copy ${copyOn ? "ON" : "OFF"}`)}
                ${dot(liveOk, `Mode ${modeLabel}`)}
                ${dot(desk.reachable !== false, desk.reachable === false ? "Desk DOWN" : "Desk reachable")}
                ${dot(feederOk, feederLabel)}
              </div>
              <div class="gauges tight stack-col">
                ${gauge(desk.feeder_cpu_pct, "FEED", feederOk ? "var(--cyan)" : "var(--red)")}
                ${gauge(desk.disk_pct, "DISK", "var(--amber)")}
                ${gauge(desk.cpu_pct, "CPU", "var(--green)")}
                ${gauge(desk.ram_pct, "RAM", "var(--blue)")}
              </div>
            </div>`}
          </section>
          ${isMaster ? "" : `
          <section class="panel auth-panel">
            <h3 class="panel-title">Authorization</h3>
            <div class="lots-board stacked compact">
              <div class="kv"><span>Copy Trades</span><b>${fmtNum(desk.lots_allowed, 0)}</b></div>
              <div class="kv"><span>Batman</span><b>${fmtNum(desk.lots_subscription_sell, 0)}</b></div>
              <div class="kv"><span>Directional</span><b>${fmtNum(desk.lots_subscription_ato, 0)}</b></div>
              <div class="kv"><span>Subscription</span><b class="${subOk ? "pos" : ""}">${desk.subscription_status || "—"}</b></div>
            </div>
            ${desk.partial ? `<p class="muted note">Partial data — child fleet APIs pending.</p>` : ""}
            ${desk.error ? `<p class="err note">${desk.error}</p>` : ""}
          </section>`}
          <section class="panel pnl-side">
            <h3 class="panel-title">PnL / Margin</h3>
            <div class="pnl-panel">
              <div class="pnl-big kv">
                <span>Day PnL</span>
                <strong class="${pnlClass(desk.day_pnl)}">${fmtNum(desk.day_pnl, 2)}</strong>
              </div>
              <div class="pnl-big margin kv">
                <span>Margin free</span>
                <strong class="${pnlClass(desk.margin_available)}">${fmtNum(desk.margin_available, 0)}</strong>
              </div>
            </div>
            <div class="actions four">
              <button type="button" class="btn warn" data-act="pause">Pause</button>
              <button type="button" class="btn primary" data-act="resume">Resume</button>
              <button type="button" class="btn primary" data-act="copy_on">Copy ON</button>
              <button type="button" class="btn danger" data-act="copy_off">Copy OFF</button>
            </div>
          </section>
          ${fleetTotalPanel}
        </div>
      </article>`;
  }

  function placeholderChild(slot) {
    return `
      <article class="card glass desk-card child placeholder">
        <div class="card-head">
          <span class="tag child">CHILD SLOT ${slot}</span>
          <h2 class="desk-name muted">Waiting for child…</h2>
        </div>
        <p class="muted placeholder-copy">Register child #${slot} in fleet registry — card will fill automatically.</p>
      </article>`;
  }

  function render(snap) {
    const desks = snap.desks || [];
    const master = desks.filter((d) => d.role_tag === "MASTER");
    const kids = desks.filter((d) => d.role_tag !== "MASTER");
    const t = snap.totals || {};
    t.child_count = t.child_count ?? kids.length;
    t.desk_count = t.desk_count ?? desks.length;
    // Max 2 child slots — no empty slot 3 / 4.
    let childHtml = kids.map((d) => cardHtml(d, t)).join("");
    for (let i = kids.length + 1; i <= 2; i++) {
      childHtml += placeholderChild(i);
    }
    $("masterWrap").innerHTML = master.map((d) => cardHtml(d, t)).join("") || `<div class="glass card">No master in registry</div>`;
    $("childGrid").innerHTML = childHtml;

    // Footer kept slim — main fleet totals live on Master 4th card
    if ($("totPnl")) {
      $("totPnl").textContent = fmtNum(t.pnl_sum, 2);
      $("totPnl").className = pnlClass(t.pnl_sum);
      $("totMargin").textContent = fmtNum(t.margin_sum, 0);
      $("totKids").textContent = String(t.child_count ?? kids.length);
      $("totLinks").textContent = `${t.links_up ?? 0} / ${t.desk_count ?? desks.length}`;
    }

    const nifty = snap.nifty ?? (master[0] && master[0].nifty_ltp);
    $("niftyVal").textContent = nifty != null ? fmtNum(nifty, 2) : "—";

    const age = snap.updated_at ? new Date(snap.updated_at * 1000).toLocaleTimeString() : "—";
    if ($("updatedAt")) $("updatedAt").textContent = "Updated " + age;

    document.querySelectorAll(".card .actions [data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const card = btn.closest(".card");
        const label = (card.querySelector(".desk-name") && card.querySelector(".desk-name").textContent)
          || card.dataset.label
          || card.dataset.id;
        askControl(card.dataset.id, btn.dataset.act, label);
      });
    });
  }

  function askControl(deskId, action, label) {
    pendingControl = { desk_id: deskId, action };
    $("confirmTitle").textContent = "Confirm control";
    $("confirmBody").textContent = `Run "${action}" on ${label} (${deskId})?`;
    $("confirmModal").hidden = false;
  }

  async function loadSnap(extra = "") {
    const { res, data } = await api("/api/fleet/snapshot" + extra);
    if (res.status === 401) {
      showApp(false);
      return;
    }
    if (!data.ok && !data.desks) {
      showToast(data.error || "Snapshot failed", false);
      return;
    }
    render(data);
  }

  function showApp(on) {
    loginGate.hidden = on;
    app.hidden = !on;
    if (on) {
      loadSnap();
      if (timer) clearInterval(timer);
      timer = setInterval(() => loadSnap(), 15000);
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  async function boot() {
    $("confirmModal").hidden = true;
    pendingControl = null;
    setTheme(localStorage.getItem("fleet_theme") || "day");
    tickClock();
    setInterval(tickClock, 1000);
    const { res } = await api("/api/fleet/me");
    showApp(res.ok);
  }

  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("loginErr").hidden = true;
    const { res, data } = await api("/api/fleet/login", {
      method: "POST",
      body: JSON.stringify({ password: $("pw").value }),
    });
    if (!res.ok) {
      $("loginErr").textContent = data.error || "Login failed";
      $("loginErr").hidden = false;
      return;
    }
    showApp(true);
  });

  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/fleet/logout", { method: "POST", body: "{}" });
    showApp(false);
  });

  $("themeToggle").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-theme]");
    if (b) setTheme(b.dataset.theme);
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-fleet-refresh]");
    if (!btn) return;
    loadSnap(btn.dataset.fleetRefresh === "margin" ? "?refresh=1&margin=1" : "?refresh=1");
  });
  const legacyRefresh = $("refreshBtn");
  const legacyMargin = $("refreshMarginBtn");
  if (legacyRefresh) legacyRefresh.addEventListener("click", () => loadSnap("?refresh=1"));
  if (legacyMargin) legacyMargin.addEventListener("click", () => loadSnap("?refresh=1&margin=1"));

  $("confirmCancel").addEventListener("click", () => {
    pendingControl = null;
    $("confirmModal").hidden = true;
  });
  $("confirmOk").addEventListener("click", async () => {
    const p = pendingControl;
    $("confirmModal").hidden = true;
    pendingControl = null;
    if (!p) return;
    const { res, data } = await api("/api/fleet/control", {
      method: "POST",
      body: JSON.stringify({ ...p, confirm: true }),
    });
    if (res.ok && data.ok) {
      showToast(`${p.action} OK on ${p.desk_id}`, true);
      loadSnap("?refresh=1");
    } else {
      showToast((data && (data.error || (data.result && data.result.error))) || "Control failed", false);
    }
  });

  boot();
})();
