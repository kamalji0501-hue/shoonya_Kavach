(() => {
  const $ = (id) => document.getElementById(id);
  let page = "home";
  let live = {};
  const pageHtmlCache = Object.create(null);
  const pageCacheMeta = Object.create(null);

  const PAGE_LABELS = {
    home: "Home",
    status: "Status",
    summary: "Summary",
    ato: "ATO",
    register: "Register",
    deploy: "Deploy",
    payoff: "Payoff",
    token: "Token",
    alerts: "Alert logs",
  };

  function invalidatePageCache(keys) {
    if (keys == null) {
      for (const k of Object.keys(pageHtmlCache)) delete pageHtmlCache[k];
      for (const k of Object.keys(pageCacheMeta)) delete pageCacheMeta[k];
      return;
    }
    const list = Array.isArray(keys) ? keys : [keys];
    for (const k of list) {
      delete pageHtmlCache[k];
      delete pageCacheMeta[k];
    }
  }

  function invalidateHomeCache() {
    invalidatePageCache("home");
  }

  function savePageCache(key, html, meta) {
    const cacheKey = key + "::" + (strategyMode() || "none");
    pageHtmlCache[cacheKey] = html;
    pageHtmlCache[key] = html; // keep legacy key briefly for same-strategy nav
    if (meta !== undefined) {
      pageCacheMeta[cacheKey] = meta;
      pageCacheMeta[key] = meta;
    }
  }

  function pageLabel(key) {
    return PAGE_LABELS[key] || String(key || "Page");
  }

  function showNavPerf(key, mode, ms) {
    return;
  }

  function finishPageRender(key, view, meta, navT0) {
    savePageCache(key, view.innerHTML, meta);
    playViewIn(view);
    showNavPerf(key, "fetch", Math.max(1, Math.round(performance.now() - navT0)));
  }

  function restorePageFromCache(view) {
    const cacheKey = page + "::" + (strategyMode() || "none");
    const html = pageHtmlCache[cacheKey] || pageHtmlCache[page];
    if (!html) return false;
    // If legacy unscoped cache belongs to the other strategy, ignore it.
    if (!pageHtmlCache[cacheKey] && pageHtmlCache[page]) {
      const dirHtml = /dep-complete-dir|depBearPrev|depBullPrev/.test(html);
      if (isDirectional() !== dirHtml && (page === "deploy" || page === "register")) {
        delete pageHtmlCache[page];
        return false;
      }
    }
    const t0 = performance.now();
    view.innerHTML = html;
    wirePageHandlers(page, pageCacheMeta[page]);
    playViewIn(view);
    applyChrome(live);
    showNavPerf(page, "cache", Math.max(1, Math.round(performance.now() - t0)));
    return true;
  }

  function wirePageHandlers(key, meta) {
    if (key === "register" && meta && meta.register) wireRegisterHandlers(meta.register);
    else if (key === "ato") wireAtoMgrHandlers();
    else if (key === "deploy") wireDeployHandlers();
    else if (key === "home" || key === "positions") wireEmergencyExitBtn();
    else if (key === "payoff" && meta) wirePayoffChart(meta);
    else if (key === "token") wireTokenHandlers($("view"));
  }
  let clockTimer = 0;
  let toggling = false;
  let copyUiBusy = false;
  let toastTimer = 0;
  let goNotice = "";
  let goNoticeKind = "";
  let goNoticeTimer = 0;

  


  function paintStrategyPickLots(sub) {
    // Batman: tracker Max Sell Batman is total SELL (IC) → show buy-lots approx (/4).
    // Directional: tracker Max Sell Directional is deploy lots → show EXACT number (no /4).
    const fmtBatmanLots = (v) => {
      if (v === null || v === undefined || v === "") return "—";
      const n = Number(v);
      if (!Number.isFinite(n)) return "—";
      return Math.floor(n / 4) + " lots";
    };
    const fmtDirectionalLots = (v) => {
      if (v === null || v === undefined || v === "") return "—";
      const n = Number(v);
      if (!Number.isFinite(n)) return "—";
      return Math.floor(n) + " lots";
    };
    const b = document.getElementById("pickLotsBatman");
    const d = document.getElementById("pickLotsDirectional");
    if (b) b.textContent = fmtBatmanLots(sub && sub.max_sell_lots_batman);
    if (d) d.textContent = fmtDirectionalLots(sub && sub.max_sell_lots_directional);
  }

  async function refreshStrategyPickLots() {
    try {
      const d = await api("/api/subscription");
      window.__sub = d || window.__sub || {};
      paintStrategyPickLots(d || {});
    } catch (_) {
      try {
        const res = await fetch("/api/login-plan", { credentials: "same-origin" });
        const data = await res.json().catch(() => ({}));
        paintStrategyPickLots(data || {});
      } catch (__) {
        paintStrategyPickLots({});
      }
    }
  }

  function hideStrategyPick() {
    const el = $("strategyPick");
    if (!el) return;
    el.classList.add("hidden");
    el.setAttribute("aria-hidden", "true");
    try {
      if (window.StratGlobe && typeof window.StratGlobe.stop === "function") {
        window.StratGlobe.stop();
      }
    } catch (_) {}
  }

  function showStrategyPick() {
    $("login").classList.add("hidden");
    $("desk").classList.add("hidden");
    const el = $("strategyPick");
    if (!el) {
      showDesk();
      return;
    }
    el.classList.remove("hidden");
    el.setAttribute("aria-hidden", "false");
    const theme = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    el.dataset.globeTheme = theme;
    const bootGlobe = () => {
      try {
        if (window.StratGlobe && typeof window.StratGlobe.start === "function") {
          window.StratGlobe.start(theme);
          return true;
        }
      } catch (_) {}
      return false;
    };
    if (!bootGlobe()) {
      let tries = 0;
      const t = setInterval(() => {
        tries += 1;
        if (bootGlobe() || tries > 40) clearInterval(t);
      }, 50);
    }
    refreshStrategyPickLots().catch(() => {});
    wireStrategyPick();
  }

  function wireStrategyPick() {
    const root = $("strategyPick");
    if (!root || root.dataset.wired === "1") return;
    root.dataset.wired = "1";
    root.querySelectorAll("[data-strategy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const choice = String(btn.getAttribute("data-strategy") || "").trim();
        if (!choice) return;
        try { invalidatePageCache(); } catch (_) {}
        setStrategyMode(choice);
        hideStrategyPick();
        showDesk();
      });
    });
  }


  function strategyMode() {
    return String(window.__strategyChoice || document.body.dataset.strategy || "").trim();
  }
  function isDirectional() {
    return strategyMode() === "directional";
  }
  function isBatman() {
    return strategyMode() === "batman";
  }
  function setStrategyMode(choice) {
    const c = String(choice || "").trim();
    const prev = String(window.__strategyChoice || document.body.dataset.strategy || "").trim();
    window.__strategyChoice = c;
    if (c === "batman" || c === "directional") {
      document.body.dataset.strategy = c;
    } else {
      delete document.body.dataset.strategy;
    }
    try {
      if (c === "batman" || c === "directional") sessionStorage.setItem("kavach-strategy", c);
      else sessionStorage.removeItem("kavach-strategy");
    } catch (_) {}
    // Batman vs Directional paint different Deploy/Register/Home HTML.
    // Drop page cache whenever strategy changes so Directional never keeps Batman cards.
    if (prev !== c) {
      try { invalidatePageCache(); } catch (_) {}
    }
    applyStrategyChrome();
  }
  function applyStrategyChrome() {
    const dir = isDirectional();
    document.querySelectorAll('.nav[data-go="deploy"]').forEach((el) => {
      el.textContent = dir ? "DEPLOY" : "DEPLOY BATMAN 2.0";
    });
    document.querySelectorAll('.nav[data-go="register"]').forEach((el) => {
      el.textContent = dir ? "REGISTER" : "REGISTER BATMAN";
    });
    document.querySelectorAll('.tabbar [data-go="register"]').forEach((el) => {
      el.textContent = dir ? "REGISTER" : "REGISTER";
    });
    // Directional has no 35% dyn hedge — hide header toggle (Batman keeps it).
    const hedgeToggle = $("hedgeToggle");
    const hedgeLbl = hedgeToggle ? hedgeToggle.closest("label.toggle") : null;
    if (hedgeLbl) hedgeLbl.hidden = !!dir;
    if (dir && (page === "overnight" || page === "payoff")) {
      page = "home";
      document.querySelectorAll("[data-go]").forEach((el) => {
        el.classList.toggle("on", el.getAttribute("data-go") === "home");
      });
    }
    // Re-paint account strip so Directional hides broker/client id.
    if (brokerAcctPainted) {
      loadBrokerAcct();
    }
  }

  /** User Detail only: show approx Batman lots as Sheet sell-cap / 4. Backend value unchanged. */
  function displayBatmanLotsApprox(raw) {
    if (raw === null || raw === undefined || raw === "") return "-";
    const n = Number(raw);
    if (!Number.isFinite(n)) return "-";
    return String(Math.floor(n / 4));
  }

  async function refreshSubscriptionChip() {
    wireUserDetailUi();
    const chip = $("subChip");
    try {
      const d = await api("/api/subscription");
      window.__sub = d || {};
      const st = String((d && d.ui_status) || "SUBSCRIPTION_VALIDATION_ERROR");
      const statusLabel = (d && d.status) ? String(d.status) : st.replace(/_/g, " ");
      if (chip) {
        chip.className = "sub-chip";
        if (st === "ACTIVE") chip.classList.add("sub-active");
        else if (st === "EXPIRING_SOON") chip.classList.add("sub-soon");
        else if (st === "EXPIRED" || st === "NOT_AUTHORIZED" || st === "SERVER_NOT_AUTHORIZED" || st === "USER_NOT_REGISTERED") chip.classList.add("sub-bad");
        else chip.classList.add("sub-unknown");
        chip.textContent = "User Detail";
        chip.title = "User Detail - " + statusLabel;
      }
      window.__subAllowed = !!(d && d.allowed);
      window.__subReason = (d && d.reason) || "";
      const dash = (v) => (v === null || v === undefined || v === "" ? "-" : String(v));
      if ($("udName")) $("udName").textContent = dash(d && d.name);
      if ($("udStart")) $("udStart").textContent = dash(d && d.start_date);
      if ($("udEnd")) $("udEnd").textContent = dash(d && d.end_date);
      if ($("udMaxBatman")) $("udMaxBatman").textContent = displayBatmanLotsApprox(d && d.max_sell_lots_batman);
      if ($("udMaxDirectional")) $("udMaxDirectional").textContent = dash(d && d.max_sell_lots_directional);
      paintUserDetailStatus((d && d.status) || statusLabel, st);
      syncBrokerSubStatus(d);
      wireBrokerProfile();
    } catch (e) {
      if (chip) {
        chip.className = "sub-chip sub-unknown";
        chip.textContent = "User Detail";
        chip.title = "User Detail unavailable";
      }
      window.__subAllowed = false;
      window.__sub = {};
      syncBrokerSubStatus({});
      wireBrokerProfile();
    }
  }

  function closeUserDetailPanel() {
    const overlay = $("userDetailOverlay");
    const chip = $("subChip");
    const profile = $("brokerAcctProfile");
    if (overlay) {
      overlay.classList.add("hidden");
      overlay.setAttribute("aria-hidden", "true");
    }
    if (chip) chip.setAttribute("aria-expanded", "false");
    if (profile) profile.setAttribute("aria-expanded", "false");
    document.body.classList.remove("user-detail-open");
  }

  function openUserDetailPanel() {
    const overlay = $("userDetailOverlay");
    const chip = $("subChip");
    const profile = $("brokerAcctProfile");
    if (!overlay) return;
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    if (chip) chip.setAttribute("aria-expanded", "true");
    if (profile) profile.setAttribute("aria-expanded", "true");
    document.body.classList.add("user-detail-open");
    refreshSubscriptionChip().catch(() => {});
  }

  function toggleUserDetailPanel(ev) {
    if (ev) {
      ev.preventDefault();
      ev.stopPropagation();
    }
    const overlay = $("userDetailOverlay");
    if (!overlay) return;
    if (overlay.classList.contains("hidden")) openUserDetailPanel();
    else closeUserDetailPanel();
  }

  function paintUserDetailStatus(statusRaw, uiStatus) {
    const el = $("udStatus");
    if (!el) return;
    const st = String(statusRaw || uiStatus || "-").toUpperCase();
    el.textContent = st;
    el.classList.remove("is-active", "is-bad", "is-soon");
    if (st === "ACTIVE") el.classList.add("is-active");
    else if (st === "EXPIRING_SOON" || st.includes("EXPIR")) el.classList.add("is-soon");
    else if (st !== "-") el.classList.add("is-bad");
  }


  async function refreshPlanFromSheet() {
    const btn = $("userDetailRefresh");
    const msg = $("userDetailRefreshMsg");
    if (btn && btn.disabled) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Refreshing...";
    }
    if (msg) {
      msg.hidden = true;
      msg.classList.remove("is-error");
      msg.textContent = "";
    }
    try {
      const d = await api("/api/subscription", {
        method: "POST",
        body: JSON.stringify({ refresh: true }),
      });
      window.__sub = d || {};
      window.__subAllowed = !!(d && d.allowed);
      window.__subReason = (d && d.reason) || "";
      const dash = (v) => (v === null || v === undefined || v === "" ? "-" : String(v));
      if ($("udName")) $("udName").textContent = dash(d && d.name);
      if ($("udStart")) $("udStart").textContent = dash(d && d.start_date);
      if ($("udEnd")) $("udEnd").textContent = dash(d && d.end_date);
      if ($("udMaxBatman")) $("udMaxBatman").textContent = displayBatmanLotsApprox(d && d.max_sell_lots_batman);
      if ($("udMaxDirectional")) $("udMaxDirectional").textContent = dash(d && d.max_sell_lots_directional);
      paintUserDetailStatus((d && d.status) || (d && d.ui_status), (d && d.ui_status) || "");
      syncBrokerSubStatus(d);
      wireBrokerProfile();
      const chip = $("subChip");
      if (chip) {
        const st = String((d && d.ui_status) || "");
        chip.className = "sub-chip";
        if (st === "ACTIVE") chip.classList.add("sub-active");
        else if (st === "EXPIRING_SOON") chip.classList.add("sub-soon");
        else if (st === "EXPIRED" || st === "NOT_AUTHORIZED" || st === "SERVER_NOT_AUTHORIZED" || st === "USER_NOT_REGISTERED") chip.classList.add("sub-bad");
        else chip.classList.add("sub-unknown");
        chip.textContent = "User Detail";
      }
      if (d && d.refresh_ok === false) {
        const err = (d && d.error) || "Unable to refresh your plan. Please try again.";
        if (msg) {
          msg.hidden = false;
          msg.classList.add("is-error");
          msg.textContent = err;
        }
        toast(err, false);
        return;
      }
      if (msg) {
        msg.hidden = false;
        msg.classList.remove("is-error");
        msg.textContent = "Plan refreshed successfully";
      }
      toast("Plan refreshed successfully", true);
    } catch (e) {
      const err = "Unable to refresh your plan. Please try again.";
      if (msg) {
        msg.hidden = false;
        msg.classList.add("is-error");
        msg.textContent = err;
      }
      toast(err, false);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Refresh My Plan";
      }
    }
  }

  function wireUserDetailUi() {
    wireBrokerProfile();
    if (document.body.dataset.userDetailWired === "1") return;
    document.body.dataset.userDetailWired = "1";
    const chip = $("subChip");
    if (chip) chip.addEventListener("click", toggleUserDetailPanel);
    const closeBtn = $("userDetailClose");
    if (closeBtn) closeBtn.addEventListener("click", (e) => { e.stopPropagation(); closeUserDetailPanel(); });
    const backdrop = $("userDetailBackdrop");
    if (backdrop) backdrop.addEventListener("click", closeUserDetailPanel);
    const refreshBtn = $("userDetailRefresh");
    if (refreshBtn) refreshBtn.addEventListener("click", (e) => { e.stopPropagation(); refreshPlanFromSheet(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeUserDetailPanel();
    });
  }

  function subLotHintHtml() {
    const d = window.__sub || {};
    if (typeof isDirectional === "function" && isDirectional()) {
      const n = d.max_sell_lots_directional;
      if (n === null || n === undefined || n === "") return "";
      return `<p class="sub-lot-hint" id="subLotHint">Max Directional SELL lots: <strong>${esc(String(n))}</strong> (from tracker). ATO max: <strong>${esc(String(d.max_ato_lots_directional == null ? "-" : d.max_ato_lots_directional))}</strong>.</p>`;
    }
    const n = d.max_sell_lots_batman;
    if (n === null || n === undefined || n === "") return "";
    return `<p class="sub-lot-hint" id="subLotHint">Max Batman SELL lots: <strong>${esc(String(n))}</strong> (total book).</p>`;
  }

async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign(
      {
        "Content-Type": "application/json",
        "X-Kavach-Strategy": (typeof strategyMode === "function" ? strategyMode() : ""),
      },
      opts.headers || {}
    );
    const res = await fetch(path, Object.assign({ credentials: "include" }, opts, { headers }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }


  function fmt(v, d) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "-";
    return n.toLocaleString("en-IN", {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d,
    });
  }
  function fmtPnl(v) {
    if (v == null || v === "" || Number.isNaN(Number(v))) return "-";
    const n = Number(v);
    const abs = Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n > 0 ? "+" : n < 0 ? "-" : "") + "₹" + abs;
  }

  function clsPnl(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "buy" : "sell";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function reduceMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function setBusy(on) {
    const g = $("busyGate");
    if (!g) return;
    g.classList.toggle("hidden", !on);
    g.setAttribute("aria-hidden", on ? "false" : "true");
  }


  const NOTICE_MS = { red: 4000, orange: 4000, green: 3000 };
  let _noticeShowing = null;
  let _pendingNonRed = null;
  let _redSoundStreak = false;
  let _redAudio = null;
  let _seenAlertIds = new Set();
  let _alertsPrimed = false;

  function _noticeSev(v) {
    const s = String(v || "").toLowerCase();
    if (s === "red" || s === "green") return s;
    return "orange";
  }

  function stopRedAlertSound() {
    _redSoundStreak = false;
    if (_redAudio) {
      try {
        _redAudio.pause();
        _redAudio.currentTime = 0;
      } catch (_) {}
    }
  }

  function playRedAlertSound() {
    if (_redSoundStreak) return;
    _redSoundStreak = true;
    try { unlockAudio(); } catch (_) {}
    try {
      if (!_redAudio) _redAudio = new Audio("/static/redAlert.wav");
      _redAudio.pause();
      _redAudio.currentTime = 0;
      _redAudio.volume = 1;
      _redAudio.play().catch(() => {});
    } catch (_) {}
  }

  function hideNoticeCard() {
    _noticeShowing = null;
    stopRedAlertSound();
    const el = $("toast");
    if (!el) return;
    const cat = $("noticeCat");
    const line = $("noticeLine");
    if (cat) cat.textContent = "";
    if (line) line.textContent = "";
    el.className = "toast";
  }

  function paintNoticeCard(item) {
    const el = $("toast");
    if (!el) return;
    const sev = _noticeSev(item.severity);
    const cat = $("noticeCat");
    const line = $("noticeLine");
    if (cat) cat.textContent = String(item.category || "Desk");
    if (line) line.textContent = String(item.alert || "");
    const cls = sev === "red" ? "bad" : sev === "green" ? "ok" : "warn";
    el.className = "toast " + cls;
    if (sev === "red") playRedAlertSound();
    else stopRedAlertSound();
  }

  function _scheduleNoticeClear() {
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = 0;
    }
    const item = _noticeShowing;
    if (!item) return;
    const ms = NOTICE_MS[_noticeSev(item.severity)] || 4000;
    toastTimer = setTimeout(() => {
      toastTimer = 0;
      if (_pendingNonRed && _noticeShowing && _noticeSev(_noticeShowing.severity) === "red") {
        const nxt = _pendingNonRed;
        _pendingNonRed = null;
        _showNoticeNow(nxt);
        return;
      }
      hideNoticeCard();
    }, ms);
  }

  function _showNoticeNow(item) {
    _noticeShowing = item;
    paintNoticeCard(item);
    _scheduleNoticeClear();
  }

  function enqueueNotice(raw) {
    if (!raw) return;
    const alert = String(raw.alert || raw.text || "").trim();
    if (!alert) {
      hideNoticeCard();
      return;
    }
    const item = {
      id: raw.id || "",
      severity: _noticeSev(raw.severity),
      category: String(raw.category || "Desk"),
      alert: alert,
      log: String(raw.log || alert),
      side: raw.side || null,
    };
    if (item.severity === "red") {
      _showNoticeNow(item);
      return;
    }
    if (_noticeShowing && _noticeSev(_noticeShowing.severity) === "red") {
      _pendingNonRed = item;
      return;
    }
    _showNoticeNow(item);
  }

  function toastResult(r, fallbackOk) {
    if (r && r.desk_alert) {
      const id = String(r.desk_alert.id || "");
      if (id) _seenAlertIds.add(id);
      enqueueNotice(r.desk_alert);
      return;
    }
    const ok = fallbackOk != null ? !!fallbackOk : !!(r && r.ok);
    const text = r && (r.text || r.error) ? String(r.text || r.error) : (ok ? "Saved" : "Failed");
    toast(text, ok);
  }

  function drainDeskAlerts(s) {
    const rows = (s && s.desk_alerts) || [];
    if (!_alertsPrimed) {
      _alertsPrimed = true;
      for (const a of rows) {
        const id = String((a && a.id) || "");
        if (id) _seenAlertIds.add(id);
      }
      return;
    }
    for (const a of rows) {
      const id = String((a && a.id) || "");
      if (!id || _seenAlertIds.has(id)) continue;
      _seenAlertIds.add(id);
      enqueueNotice(a);
    }
    if (_seenAlertIds.size > 400) {
      _seenAlertIds = new Set(Array.from(_seenAlertIds).slice(-200));
    }
  }

  function toast(msg, ok) {
    const text = String(msg || "").trim();
    if (!text) {
      hideNoticeCard();
      return;
    }
    const low = text.toLowerCase();
    let sev = ok ? "green" : "orange";
    if (!ok && /halt|reject|timeout|mismatch|blocked|flatten|stop loss/.test(low)) sev = "red";
    enqueueNotice({ severity: sev, category: "Desk", alert: text, log: text });
  }

  function setGoNotice(msg, kind) {
    goNotice = msg || "";
    goNoticeKind = kind || "";
    if (goNoticeTimer) {
      clearTimeout(goNoticeTimer);
      goNoticeTimer = 0;
    }
    const el = $("goNotice");
    if (el) {
      el.textContent = goNotice;
      el.className = "go-notice" + (goNotice ? (goNoticeKind === "paused" ? " paused" : " ok") : " hidden");
    }
    if (goNotice && goNoticeKind !== "paused") {
      goNoticeTimer = setTimeout(() => {
        goNotice = "";
        goNoticeKind = "";
        const n = $("goNotice");
        if (n) {
          n.textContent = "";
          n.className = "go-notice hidden";
        }
      }, 8000);
    }
  }

  function skeleton() {
    return `<div>
      <div class="skel skel-title"></div>
      <p class="sub">Loading…</p>
      <div class="stats stats3">
        <div class="skel skel-card"></div>
        <div class="skel skel-card"></div>
        <div class="skel skel-card"></div>
      </div>
      <div class="skel skel-row"></div>
      <div class="skel skel-row"></div>
    </div>`;
  }

  function wrapPageFrame(view) {
    if (!view) return;
    if (view.querySelector(":scope > .home-dash, :scope > .page-frame")) return;
    const head = view.querySelector(":scope > .page-head");
    if (!head) return;
    const frame = document.createElement("div");
    frame.className = "page-frame";
    while (head.nextSibling) frame.appendChild(head.nextSibling);
    if (!frame.childNodes.length) return;
    view.appendChild(frame);
  }

  function playViewIn(view) {
    wrapPageFrame(view);
    if (!view || reduceMotion()) return;
    view.classList.remove("view-in");
    void view.offsetWidth;
    view.classList.add("view-in");
  }

  function tickClock() {
    const el = $("clock");
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function isMobileUi() {
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function paintToggle(el, lbl, on, onText, offText) {
    if (!el) return;
    if (document.activeElement !== el && !toggling) el.checked = !!on;
    const wrap = el.closest(".toggle");
    if (wrap) wrap.classList.toggle("is-on", !!on), wrap.classList.toggle("is-off", !on);
    if (lbl) lbl.textContent = on ? onText : offText;
  }


  function posRowsHtml(positions) {
    const rows = Array.isArray(positions) ? positions : [];
    if (!rows.length) {
      return '<tr><td colspan="6">No broker positions today.</td></tr>';
    }
    return rows.map((p) => {
      const qty = p.qty != null ? Number(p.qty) : 0;
      const closed = !!(p.closed || qty === 0);
      const avg = p.avg != null && p.avg !== "" ? fmt(p.avg) : "-";
      const ltp = p.ltp != null && p.ltp !== "" ? fmt(p.ltp) : "-";
      const typ = p.type || p.leg_type || "Extra";
      const pnl = p.pnl;
      const pnlTxt = pnl == null || pnl === "" ? "-" : fmtPnl(pnl);
      const pnlCls = clsPnl(pnl);
      const rowCls = closed ? "pos-closed" : (qty < 0 ? "hist-sell" : "hist-buy");
      return `<tr class="${rowCls}">
            <td>${esc(p.display_symbol || p.symbol || "")}</td>
            <td>${esc(String(typ))}</td>
            <td>${esc(String(qty))}</td>
            <td>${esc(avg)}</td>
            <td>${esc(ltp)}</td>
            <td class="${pnlCls}">${esc(pnlTxt)}</td>
          </tr>`;
    }).join("");
  }

  function posFootHtml(positions) {
    const rows = Array.isArray(positions) ? positions : [];
    if (!rows.length) return "";
    let total = 0;
    let any = false;
    for (const p of rows) {
      if (p.pnl == null || p.pnl === "") continue;
      const n = Number(p.pnl);
      if (!Number.isFinite(n)) continue;
      total += n;
      any = true;
    }
    const pnlCls = any ? clsPnl(total) : "";
    const pnlTxt = any ? esc(fmtPnl(total)) : "-";
    return `<tr class="hist-total"><td colspan="5"><b>TOTAL</b></td><td class="${pnlCls}"><b>${pnlTxt}</b></td></tr>`;
  }

  function paintPositions(positions) {
    const body = $("posBody");
    if (body) body.innerHTML = posRowsHtml(positions);
    const foot = $("posFoot");
    if (foot) foot.innerHTML = posFootHtml(positions);
  }


  function positionsCardHtml(positions) {
    return `<div class="card hist-wrap pos-card home-pos-card positions-page-card">
      <div class="home-pos-toolbar" id="homeEmergencyExit">
        <div class="home-pos-toolbar-copy">
          <span class="home-pos-toolbar-kicker">Broker positions</span>
          <span class="home-pos-toolbar-note">Exits every open position on broker immediately after you confirm.</span>
        </div>
        <button type="button" class="btn btn-red home-emergency-exit-btn" id="exitAllBtn">EMERGENCY EXIT</button>
      </div>
      <h4 class="sec-title positions-card-title">POSITIONS</h4>
      <div class="home-pos-table-wrap">
        <table class="hist-table pos-table"><thead><tr>
          <th>Symbol</th><th>Type</th><th>Qty</th><th>Avg</th><th>LTP</th><th>PnL</th>
        </tr></thead><tbody id="posBody">${posRowsHtml(positions)}</tbody><tfoot id="posFoot">${posFootHtml(positions)}</tfoot></table>
      </div>
    </div>`;
  }



  /* ---- ATO surety audio (always on; unlock on first gesture) ---- */
  const ATO_AUDIO = {
    down: "/static/audio/kavach_down_final.wav",
    up: "/static/audio/kavach_up_final.wav",
    welcome: "/static/audio/welcome_final.wav",
    register: "/static/audio/register_arm_final.wav",
    complete: "/static/audio/complete_final.wav",
    engage: "/static/audio/ato_engage_final.wav",
    exit: "/static/audio/ato_exit_final.wav",
  };
  let _audioUnlocked = false;
  let _lastArmed = null;
  let _lastBuyFillToken = null;
  let _lastSellFillToken = null;
  let _welcomePlayed = false;
  let _suppressReadinessAudioUntil = 0;
  const PROTECT_BOOK_BLOCK_REASONS = new Set([
    "pe_protect_in_book",
    "ce_protect_in_book",
  ]);

  function unlockAudio() {
    if (_audioUnlocked) return;
    _audioUnlocked = true;
    try {
      const a = new Audio(ATO_AUDIO.up);
      a.volume = 0.01;
      a.play().then(() => { a.pause(); a.currentTime = 0; }).catch(() => {});
    } catch (_) {}
  }

  function playAtoClip(kind) {
    const src = ATO_AUDIO[kind];
    if (!src) return;
    try {
      const a = new Audio(src);
      a.volume = 1;
      a.play().catch(() => {});
    } catch (_) {}
  }

  ["pointerdown", "keydown", "touchstart"].forEach((ev) => {
    window.addEventListener(ev, unlockAudio, { once: true, capture: true });
  });

  function feedAgeClass(age) {
    if (age == null || Number.isNaN(Number(age))) return "feed-bad";
    const n = Number(age);
    if (n <= 3) return "feed-ok";
    if (n <= 10) return "feed-amber";
    return "feed-bad";
  }

  function paintAtoBanner(s) {
    const el = $("atoBanner");
    if (!el) return;
    const ar = (s && s.ato_readiness) || {};
    const armed = !!ar.armed;
    const labels = ar.reason_labels || {};
    const hard = ar.hard_blocked_reasons || ar.blocked_reasons || [];
    const attention = ar.attention_reasons || [];
    const sideHalt = attention.filter((r) => r === "pe_side_halted" || r === "ce_side_halted" || r === "all_sides_halted");
    if (armed && !sideHalt.length) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    const focus = (!armed ? hard : sideHalt).slice(0, 2);
    const top = focus.map((r) => labels[r] || r).filter(Boolean);
    const line = top.join(" · ") || (ar.summary_line || (armed ? "ATO PARTIAL" : "ATO BLOCKED"));
    let action = "Check Datafeedbot service / Feeder cache.";
    if (sideHalt.length) action = "Resume after checking broker book (Pause then Resume).";
    else if (hard.indexOf("deployment_not_confirmed") >= 0) action = "Register / Arm Kavach first.";
    else if (hard.indexOf("algo_paused") >= 0) action = "Tap RESUME when the feed is healthy.";
    else if (hard.indexOf("datafeedbot_down") >= 0 || hard.indexOf("kavach2_down") >= 0) {
      action = "Check VPS: datafeedbot.service / batman-kavach2.service.";
    }
    el.classList.remove("hidden");
    el.innerHTML = `<div class="ato-banner-line">${esc(line)}</div><div class="ato-banner-actions">${esc(action)}</div>`;
  }

  function _hardBlockReasons(s) {
    const ar = (s && s.ato_readiness) || {};
    return ar.hard_blocked_reasons || ar.blocked_reasons || [];
  }

  function _protectBookOnlyBlock(hard) {
    if (!hard || !hard.length) return false;
    return hard.every((r) => PROTECT_BOOK_BLOCK_REASONS.has(r));
  }

  /** Trade clips (#6/#7) win over readiness (#4/#5) on the same tick. */
  function onAtoDeskAudioEdges(s) {
    if (!s) return;
    const ar = (s && s.ato_readiness) || {};
    const buy = String(s.ato_buy_fill_token || "");
    const sell = String(s.ato_sell_fill_token || "");
    const now = Date.now();
    const initTokens = _lastBuyFillToken === null && _lastSellFillToken === null;
    const buyNew = !initTokens && buy && buy !== _lastBuyFillToken;
    const sellNew = !initTokens && sell && sell !== _lastSellFillToken;

    if (initTokens) {
      _lastBuyFillToken = buy;
      _lastSellFillToken = sell;
      if (typeof ar.armed === "boolean") _lastArmed = ar.armed;
      return;
    }

    if (buyNew) {
      playAtoClip("engage");
      _suppressReadinessAudioUntil = now + 6000;
    }
    if (sellNew) {
      playAtoClip("exit");
      _suppressReadinessAudioUntil = now + 6000;
    }

    if (typeof ar.armed === "boolean" && _lastArmed !== null) {
      const hard = _hardBlockReasons(s);
      const readinessSuppressed = now < _suppressReadinessAudioUntil;

      if (_lastArmed === true && ar.armed === false) {
        const incidentBlock = !buyNew && !_protectBookOnlyBlock(hard) && !readinessSuppressed;
        if (incidentBlock) playAtoClip("down");
      }
      if (_lastArmed === false && ar.armed === true) {
        const incidentRecovery = !sellNew && !readinessSuppressed;
        if (incidentRecovery) playAtoClip("up");
      }
      _lastArmed = ar.armed;
    }

    _lastBuyFillToken = buy;
    _lastSellFillToken = sell;
  }


  function fmtExitLevel(v) {
    if (v == null || v === "") return "";
    const n = Number(v);
    return Number.isFinite(n) ? String(n) : "";
  }

  function paintPnlExitChrome(s) {
    const pe = (s && s.pnl_exit) || {};
    const safeOn = !!pe.safe_on;
    const tpOn = !!pe.tp_on;
    const safeIn = $("safeExitOn");
    const tpIn = $("tpExitOn");
    const safeLvl = $("safeExitLevel");
    const tpLvl = $("tpExitLevel");
    paintToggle(
      safeIn,
      $("safeExitLbl"),
      safeOn,
      "SL ON",
      "SL OFF"
    );
    paintToggle(
      tpIn,
      $("tpExitLbl"),
      tpOn,
      "TGT ON",
      "TGT OFF"
    );
    const slName = document.querySelector(".top-exit-box.exit-sl .tog-name");
    const tpName = document.querySelector(".top-exit-box.exit-tp .tog-name");
    if (slName) slName.textContent = "SL";
    if (tpName) tpName.textContent = "TGT";
    const hedgeName = document.querySelector("label.toggle.rg .tog-name");
    if (hedgeName) hedgeName.textContent = "35% DH";
    if (safeLvl && document.activeElement !== safeLvl) {
      safeLvl.value = fmtExitLevel(pe.safe_level_rs);
    }
    if (tpLvl && document.activeElement !== tpLvl) {
      tpLvl.value = fmtExitLevel(pe.tp_level_rs);
    }
  }

  let _lastPnlFireAt = null;
  let _pnlFireSeen = false;
  let _pnlFiringToast = false;

  function maybeToastPnlFire(s) {
    const pe = (s && s.pnl_exit) || {};
    const at = pe.last_at ? String(pe.last_at) : "";
    if (pe.firing) {
      _pnlFiringToast = true;
      return;
    }
    _pnlFiringToast = false;
    if (!_pnlFireSeen) {
      _pnlFireSeen = true;
      _lastPnlFireAt = at;
      return;
    }
    _lastPnlFireAt = at;
  }

  async function postPnlExit(path, on, levelEl) {
    const raw = levelEl ? levelEl.value : "";
    const body = { on: !!on, level_rs: raw === "" ? null : Number(raw) };
    try {
      setBusy(true);
      const r = await api(path, { method: "POST", body: JSON.stringify(body) });
      toastResult(r, !!r.ok);
      if (!r.ok) throw new Error(r.error || "failed");
      if (r.pnl_exit) live.pnl_exit = r.pnl_exit;
      applyChrome(live);
      await render();
    } catch (e) {
      toast(String(e.message || e), false);
      applyChrome(live);
    } finally {
      setBusy(false);
    }
  }

  function bindPnlExitControls() {
    const safeIn = $("safeExitOn");
    const tpIn = $("tpExitOn");
    const safeLvl = $("safeExitLevel");
    const tpLvl = $("tpExitLevel");
    if (safeIn && !safeIn.dataset.bound) {
      safeIn.dataset.bound = "1";
      safeIn.addEventListener("change", () => postPnlExit("/api/safe-exit", !!safeIn.checked, safeLvl));
    }
    if (tpIn && !tpIn.dataset.bound) {
      tpIn.dataset.bound = "1";
      tpIn.addEventListener("change", () => postPnlExit("/api/take-profit", !!tpIn.checked, tpLvl));
    }
    const armSave = (el, path, tog) => {
      if (!el || el.dataset.bound) return;
      el.dataset.bound = "1";
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          postPnlExit(path, !!(tog && tog.checked), el);
        }
      });
      el.addEventListener("change", () => {
        if (tog && tog.checked) postPnlExit(path, true, el);
      });
    };
    armSave(safeLvl, "/api/safe-exit", safeIn);
    armSave(tpLvl, "/api/take-profit", tpIn);
  }


  function applyChrome(s) {
    live = s || live;
    const paused = !!live.paused;
    const resumed = !paused;
    const hedgeOn = !!live.dyn_hedge;
    const nifty = $("niftyTop");
    if (nifty) {
      const ar = live.ato_readiness || {};
      const feed = ar.feed || {};
      const px = (feed.ltp != null && feed.ltp !== "")
        ? Number(feed.ltp).toLocaleString("en-IN", { maximumFractionDigits: 0 })
        : (live.nifty_ltp != null && live.nifty_ltp !== ""
          ? Number(live.nifty_ltp).toLocaleString("en-IN", { maximumFractionDigits: 0 })
          : (String(live.nifty || "").trim() || "-"));
      const age = feed.age_s != null ? Math.round(Number(feed.age_s)) : null;
      const ageTxt = age == null ? "?" : String(age) + "s";
      const src = String(feed.source || "-").toUpperCase();
      const col = String(feed.collector || "feeder");
      nifty.textContent = "NIFTY " + px + " · " + ageTxt + " · " + src;
      nifty.className = "";
      const stack = $("spotStack");
      if (stack) stack.className = "chip chip-spot chip-spot-stack " + feedAgeClass(age);
    }
    paintAtoBanner(live);
    onAtoDeskAudioEdges(live);
    drainDeskAlerts(live);
    const clock = $("clock");
    if (clock) clock.className = "";
    const pnlEl = $("statPnl");
    if (pnlEl) {
      const v = live.deployment ? live.day_pnl : 0;
      pnlEl.textContent = fmtPnl(v);
      const base = pnlEl.classList.contains("home-pnl-strip-val")
        ? "home-pnl-strip-val"
        : (pnlEl.classList.contains("home-pnl-big") ? "home-pnl-big" : "");
      const tone = (v == null || v === "") ? "" : clsPnl(v);
      pnlEl.className = [base, tone].filter(Boolean).join(" ");
    }
    const batEl = $("statBatmanPnl");
    if (batEl) {
      const v = live.deployment
        ? (live.batman_pnl != null ? live.batman_pnl : live.day_pnl)
        : 0;
      batEl.textContent = fmtPnl(v);
      const tone = (v == null || v === "") ? "" : clsPnl(v);
      batEl.className = ["home-pnl-strip-val", tone].filter(Boolean).join(" ");
    }
    paintToggle(
      $("runToggle"),
      $("runLbl"),
      resumed,
      isMobileUi() ? "KAVACH ON" : "KAVACH RESUMED",
      isMobileUi() ? "KAVACH OFF" : "KAVACH PAUSED"
    );
    paintToggle($("hedgeToggle"), $("hedgeLbl"), hedgeOn, "35% DH ON", "35% DH OFF");
    paintPnlExitChrome(live);
    maybeToastPnlFire(live);
    paintPositions(live.positions);
  }

  function pre(text) {
    return `<div class="card"><pre>${esc(text || "")}</pre></div>`;
  }

  function cap(s) {
    const t = String(s || "");
    if (!t) return "";
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  function fmtDays(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return "-";
    return v.toFixed(1) + " Days";
  }


  function kiteValidToday(saved) {
    const s = String(saved || "");
    if (s.length < 10) return false;
    const day = s.slice(0, 10);
    try {
      const todayIst = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
      const todayUtc = new Date().toISOString().slice(0, 10);
      return day === todayIst || day === todayUtc;
    } catch (e) {
      return false;
    }
  }

  function paintTokenPage(view, t) {
    const d = t.dhan || {};
    const totp = t.totp || {};
    const z = t.zerodha || {};
    const n = t.nifty || {};
    const rawMain = String(t.main_broker || "zerodha").toLowerCase();
    const allowed = ["zerodha", "dhan"];
    const main = allowed.includes(rawMain) ? rawMain : "zerodha";
    const s = t.shoonya || {};
    const names = {zerodha:"ZERODHA", dhan:"DHAN", shoonya:"SHOONYA", groww:"GROWW", angel:"ANGEL", fyers:"FYERS", kotak:"KOTAK"};
    const mainName = names[main] || "ZERODHA";
    const health = d.health_class || (d.present ? "healthy" : "missing");
    const healthLabel = d.present ? cap(d.status || health) : "Missing";
    const zTok = z.present ? (kiteValidToday(z.saved_at) ? "Token valid today" : "Token stale - paste fresh") : "Token missing";
    const dTok = d.present ? ("Token " + (healthLabel || "set").toLowerCase()) : "Token missing";
    const g = t.groww || {};
    const a = t.angel || {};
    const f = t.fyers || {};
    const k = t.kotak || {};
    const statusOf = {
      zerodha: zTok,
      dhan: dTok,
      shoonya: s.present ? "Session saved" : "Session missing",
      groww: g.present ? "Token saved" : "Token missing",
      angel: a.present ? "Session saved" : "Session missing",
      fyers: f.present ? "Token saved" : "Token missing",
      kotak: k.present ? "Session saved" : "Session missing",
    };
    const logoSrc = {
      zerodha: "/static/kite.svg",
      dhan: "/static/dhan.svg",
      shoonya: "/static/shoonya.svg",
      groww: "/static/groww.png",
      angel: "/static/angel.png",
      fyers: "/static/fyers.png",
      kotak: "/static/kotak.svg",
    };
    const logoOf = {
      zerodha: '<img class="tok-logo tok-logo-zerodha" src="'+logoSrc.zerodha+'" alt="" />',
      dhan: '<img class="tok-logo tok-logo-dhan" src="'+logoSrc.dhan+'" alt="" />',
      shoonya: '<img class="tok-logo tok-logo-shoonya" src="'+logoSrc.shoonya+'" alt="" />',
      groww: '<img class="tok-logo tok-logo-groww" src="'+logoSrc.groww+'" alt="" />',
      angel: '<img class="tok-logo tok-logo-angel" src="'+logoSrc.angel+'" alt="" />',
      fyers: '<img class="tok-logo tok-logo-fyers" src="'+logoSrc.fyers+'" alt="" />',
      kotak: '<img class="tok-logo tok-logo-kotak" src="'+logoSrc.kotak+'" alt="" />',
    };
    const face = (id) => `<span class="tok-logo-wrap">${logoOf[id]}</span><span class="tok-pick-copy"><b>${names[id]}</b><small>${statusOf[id]}</small></span>`;
    view.innerHTML = `
      <header class="page-head tok-head">
        <p class="tok-kicker">Desk access</p>
        <h3>AUTHORISATION</h3>
        <p class="sub tok-head-lead">Choose the house that punches orders. Feeder Nifty LTP can still use both.</p>
      </header>
      <div class="tok-grid tok-grid-3 tok-stage">
        <div class="card tok-card tok-card-main">
          <div class="tok-main-top">
            <h3 class="tok-title">MAIN ORDER BROKER</h3>
            <p class="tok-main-live tok-armed" id="tokArmed"><i class="tok-armed-dot" aria-hidden="true"></i>Orders go through <span class="tok-armed-house" id="tokArmedHouse"><img class="tok-armed-logo" id="tokArmedLogo" src="${logoSrc[main]}" alt="" /><b id="mainLiveLbl">${mainName}</b></span></p>
          </div>
          <p class="sub tok-main-lead">Tap a house. The token desk slides to that broker.</p>
          <div class="tok-dots" id="tokDots" hidden aria-hidden="true"><i></i><i></i><i></i></div>
          <div class="tok-drop tok-rail" id="mainBrokerPick">
            <button type="button" class="tok-drop-btn tok-sr" id="mainBrokerBtn" data-broker="${main}" aria-haspopup="listbox" aria-expanded="true" tabindex="-1">
              <span class="tok-drop-face">${face(main)}</span>
              <span class="tok-drop-caret" aria-hidden="true"></span>
            </button>
            <div class="tok-drop-menu tok-rail-menu" id="mainBrokerMenu" role="listbox">
              ${["zerodha","dhan"].map(function (id) { return '<button type="button" role="option" class="tok-drop-item tok-chip' + (main === id ? " on" : "") + '" data-broker="' + id + '" aria-selected="' + (main === id ? "true" : "false") + '"><span class="tok-drop-face">' + face(id) + '</span><span class="tok-chip-mark" aria-hidden="true"></span></button>'; }).join("")}
            </div>
          </div>
        </div>
        <div class="tok-right">
        <div class="card tok-card tok-panel tok-card-green${main === "dhan" ? " on tok-card-live" : ""}" data-panel="dhan" style="display:${main === "dhan" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title"><img class="tok-logo tok-logo-dhan" src="/static/dhan.svg" alt="" />DHAN TOKEN</h3>
          <div class="tok-meta">
            <div class="kv"><span>Status</span><b class="${d.present ? "buy" : "sell"}">${esc(healthLabel)}</b></div>
            <div class="kv"><span>Last4</span><b>${d.last4 ? "····" + esc(d.last4) : "-"}</b></div>
            <div class="kv"><span>Saved</span><b>${esc(d.saved_at || "-")}</b></div>
            <div class="kv"><span>Expires</span><b>${esc(d.jwt_exp || d.expires_at || "-")}</b></div>
            <div class="kv"><span>Age / Left</span><b>${d.age_hours == null || !d.present ? "-" : d.age_hours + "h / " + (d.expires_in_hours == null ? "-" : d.expires_in_hours + "h")}</b></div>
            <div class="kv"><span>TOTP Auto-Renew</span><b>${totp.configured ? (totp.auto_renew ? "ON" : "OFF") : "Not configured"}</b></div>
            <div class="kv"><span>TOTP Secret Due</span><b>${totp.days_left == null ? "-" : fmtDays(totp.days_left)}</b></div>
            <div class="kv"><span>Last TOTP Refresh</span><b>${esc(totp.last_refresh || "never")}</b></div>
          </div>
          ${totp.last_error ? `<p class="sub tok-err">${esc(totp.last_error)}</p>` : ""}
          <label class="muted" for="jwtBox">Paste Dhan Token</label>
          <textarea id="jwtBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste DHAN Token Here"></textarea>
          <div class="row2">
            <button type="button" class="btn btn-green" id="tokRef">REFRESH JWT</button>
            <button type="button" class="btn btn-green-outline" id="tokPaste">SAVE DHAN TOKEN</button>
          </div>
          <div class="row2 tok-actions">
            <button type="button" class="btn btn-green-outline" id="tokStat">TOKEN STATUS</button>
            <button type="button" class="btn btn-red" id="tokOff">DEACTIVATE TOKEN</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-red${main === "zerodha" ? " on tok-card-live" : ""}" data-panel="zerodha" style="display:${main === "zerodha" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title"><img class="tok-logo" src="/static/kite.svg" alt="" />KITE TOKEN</h3>
          <div class="tok-meta">
            <div class="kv"><span>Set</span><b class="${z.present ? "buy" : "sell"}">${z.present ? "Yes" : "No"}</b></div>
            <div class="kv"><span>Valid today</span><b class="${kiteValidToday(z.saved_at) ? "buy" : "sell"}">${z.present ? (kiteValidToday(z.saved_at) ? "Yes" : "No - paste a fresh token") : "-"}</b></div>
            <div class="kv"><span>Last4</span><b>${z.last4 ? "····" + esc(z.last4) : "-"}</b></div>
            <div class="kv"><span>Saved</span><b>${esc(z.saved_at || "-")}</b></div>
            <div class="kv"><span>Nifty feed</span><b>${n.ltp ? (esc(n.source || "?") + "  " + n.ltp) : "-"}</b></div>
          </div>
          <label class="muted" for="zBox">Paste Zerodha Token</label>
          <textarea id="zBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste ZERODHA Token Here"></textarea>
          <div class="row2">
            <button type="button" class="btn btn-red" id="tokZ">SAVE ZERODHA TOKEN</button>
            <button type="button" class="btn btn-red" id="tokZOff">DEACTIVATE TOKEN</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-shoonya${main === "shoonya" ? " on tok-card-live" : ""}" data-panel="shoonya" style="display:${main === "shoonya" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title">SHOONYA SESSION</h3>
          <div class="tok-meta">
            <div class="kv"><span>Set</span><b class="${s.present ? "buy" : "sell"}">${s.present ? "Yes" : "No"}</b></div>
            <div class="kv"><span>User</span><b>${esc(s.uid || "-")}</b></div>
            <div class="kv"><span>Last4</span><b>${s.last4 ? "····" + esc(s.last4) : "-"}</b></div>
            <div class="kv"><span>Saved</span><b>${esc(s.saved_at || "-")}</b></div>
          </div>
          <label class="muted" for="sUid">Shoonya user id</label>
          <input id="sUid" class="tokbox" autocomplete="off" spellcheck="false" placeholder="User id" value="${esc(s.uid || "")}" />
          <label class="muted" for="sBox">Paste Shoonya session token</label>
          <textarea id="sBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste susertoken from Shoonya OAuth login"></textarea>
          <div class="row2">
            <button type="button" class="btn" id="tokS">SAVE SHOONYA SESSION</button>
            <button type="button" class="btn btn-red" id="tokSOff">DEACTIVATE</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-groww${main === "groww" ? " on tok-card-live" : ""}" data-panel="groww" style="display:${main === "groww" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title">GROWW TOKEN</h3>
          <label class="muted" for="gBox">Paste Groww access token</label>
          <textarea id="gBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste Groww access token"></textarea>
          <div class="row2">
            <button type="button" class="btn" id="tokG">SAVE GROWW TOKEN</button>
            <button type="button" class="btn btn-red" id="tokGOff">DEACTIVATE</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-angel${main === "angel" ? " on tok-card-live" : ""}" data-panel="angel" style="display:${main === "angel" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title">ANGEL SESSION</h3>
          <input id="aClient" class="tokbox" autocomplete="off" spellcheck="false" placeholder="Client code" />
          <input id="aKey" class="tokbox" autocomplete="off" spellcheck="false" placeholder="SmartAPI key" />
          <textarea id="aBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste jwtToken"></textarea>
          <div class="row2">
            <button type="button" class="btn" id="tokA">SAVE ANGEL SESSION</button>
            <button type="button" class="btn btn-red" id="tokAOff">DEACTIVATE</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-fyers${main === "fyers" ? " on tok-card-live" : ""}" data-panel="fyers" style="display:${main === "fyers" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title">FYERS TOKEN</h3>
          <input id="fApp" class="tokbox" autocomplete="off" spellcheck="false" placeholder="App id" />
          <textarea id="fBox" class="tokbox" rows="3" autocomplete="off" spellcheck="false" placeholder="Paste access token"></textarea>
          <div class="row2">
            <button type="button" class="btn" id="tokF">SAVE FYERS TOKEN</button>
            <button type="button" class="btn btn-red" id="tokFOff">DEACTIVATE</button>
          </div>
        </div>
        <div class="card tok-card tok-panel tok-card-kotak${main === "kotak" ? " on tok-card-live" : ""}" data-panel="kotak" style="display:${main === "kotak" ? "flex" : "none"};flex-direction:column;width:100%">
          <h3 class="tok-title">KOTAK NEO</h3>
          <input id="kMobile" class="tokbox" autocomplete="off" spellcheck="false" placeholder="Mobile" />
          <input id="kUcc" class="tokbox" autocomplete="off" spellcheck="false" placeholder="UCC" />
          <textarea id="kAccess" class="tokbox" rows="2" autocomplete="off" spellcheck="false" placeholder="Dashboard access token"></textarea>
          <input id="kTrade" class="tokbox" autocomplete="off" spellcheck="false" placeholder="Trade token" />
          <input id="kSid" class="tokbox" autocomplete="off" spellcheck="false" placeholder="Sid" />
          <div class="row2">
            <button type="button" class="btn" id="tokK">SAVE KOTAK SESSION</button>
            <button type="button" class="btn btn-red" id="tokKOff">DEACTIVATE</button>
          </div>
        </div>
        </div>
      </div>
      <p class="sub" id="tokMsg"></p>`;
    wrapPageFrame(view);
  }

  async function renderTokenPage(view, msgText) {
    const t = await api("/api/token/status");
    paintTokenPage(view, t);
    if (msgText && $("tokMsg")) $("tokMsg").textContent = msgText;
    const live = view.querySelector(".tok-panel.on");
    if (live) live.classList.add("tok-panel-in");
    wireTokenHandlers(view);
  }

  
  function drawPayoffChart(svg, points, spot, opts) {
    if (!svg) return;
    const pts = Array.isArray(points) ? points : [];
    const o = opts || {};
    const step = Number(o.step) || 50;
    const W = 1000, H = 420;
    const pad = { l: 64, r: 24, t: 28, b: 52 };
    const innerW = W - pad.l - pad.r;
    const innerH = H - pad.t - pad.b;
    if (!pts.length) {
      svg.innerHTML = `<text x="500" y="210" text-anchor="middle" fill="#7A7494" font-size="18">No payoff points</text>`;
      return;
    }
    const xs = pts.map((p) => Number(p.x));
    const ys = pts.map((p) => Number(p.y));
    let xmin = Math.min(...xs), xmax = Math.max(...xs);
    let ymin = Math.min(...ys, 0), ymax = Math.max(...ys, 0);
    if (xmin === xmax) { xmin -= step; xmax += step; }
    if (ymin === ymax) { ymin -= 100; ymax += 100; }
    // Keep spot visually centered: domain already ATM±10; if spot exists, shift domain symmetrically around spot.
    if (spot != null && Number(spot) > 0) {
      const half = (xmax - xmin) / 2;
      const s = Number(spot);
      xmin = s - half;
      xmax = s + half;
    }
    const xScale = (x) => pad.l + ((x - xmin) / (xmax - xmin)) * innerW;
    const yScale = (y) => pad.t + ((ymax - y) / (ymax - ymin)) * innerH;
    const zeroY = yScale(0);

    // Split payoff into profit (green) / loss (red); interpolate at zero crossings.
    const greenSegs = [];
    const redSegs = [];
    let seg = [];
    let segSign = 0;
    const flushSeg = () => {
      if (seg.length >= 2) {
        const d = seg.map((c, i) => `${i ? "L" : "M"}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(" ");
        if (segSign >= 0) greenSegs.push(d);
        else redSegs.push(d);
      }
      seg = [];
    };
    for (let i = 0; i < pts.length; i++) {
      const x = Number(pts[i].x);
      const y = Number(pts[i].y);
      const px = xScale(x);
      const py = yScale(y);
      const sign = y > 0 ? 1 : y < 0 ? -1 : 0;
      if (i === 0) {
        seg = [[px, py]];
        segSign = sign || 1;
        continue;
      }
      const y0 = Number(pts[i - 1].y);
      const px0 = xScale(Number(pts[i - 1].x));
      if ((y0 > 0 && y < 0) || (y0 < 0 && y > 0)) {
        const t = Math.abs(y0) / (Math.abs(y0) + Math.abs(y));
        const zx = px0 + t * (px - px0);
        const zy = zeroY;
        seg.push([zx, zy]);
        flushSeg();
        seg = [[zx, zy], [px, py]];
        segSign = sign;
      } else {
        if (sign && segSign && sign !== segSign) {
          flushSeg();
          seg = [[px0, yScale(y0)], [px, py]];
          segSign = sign;
        } else {
          if (sign) segSign = sign;
          seg.push([px, py]);
        }
      }
    }
    flushSeg();

    const pathAll = pts.map((p, i) => `${i ? "L" : "M"}${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`).join(" ");
    const area = `${pathAll} L${xScale(pts[pts.length - 1].x).toFixed(1)},${zeroY.toFixed(1)} L${xScale(pts[0].x).toFixed(1)},${zeroY.toFixed(1)} Z`;
    const greenPaths = greenSegs.map((d) => `<path d="${d}" fill="none" stroke="#1ED760" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />`).join("");
    const redPaths = redSegs.map((d) => `<path d="${d}" fill="none" stroke="#FF3B5C" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />`).join("");

    let grid = "";
    const atm = o.atm != null ? Number(o.atm) : null;
    const strikeStart = Math.ceil(xmin / step) * step;
    const strikes = [];
    for (let x = strikeStart; x <= xmax + 0.01; x += step) strikes.push(Math.round(x));
    strikes.forEach((x, idx) => {
      const px = xScale(x);
      const isAtm = atm != null && Math.abs(x - atm) < 0.1;
      const showLabel = isAtm || idx === 0 || idx === strikes.length - 1 || idx % 2 === 0;
      grid += `<line x1="${px}" y1="${pad.t}" x2="${px}" y2="${H - pad.b}" stroke="${isAtm ? "rgba(43,179,192,0.35)" : "rgba(124,106,232,0.10)"}" />`;
      if (showLabel) {
        grid += `<text x="${px}" y="${H - 16}" text-anchor="middle" fill="${isAtm ? "#2BB3C0" : "#7A7494"}" font-size="${isAtm ? 13 : 11}" font-weight="${isAtm ? 700 : 500}">${x}</text>`;
      }
    });
    for (let i = 0; i <= 4; i++) {
      const y = ymin + ((ymax - ymin) * i) / 4;
      const py = yScale(y);
      grid += `<line x1="${pad.l}" y1="${py}" x2="${W - pad.r}" y2="${py}" stroke="rgba(124,106,232,0.10)" />`;
      grid += `<text x="${pad.l - 10}" y="${py + 4}" text-anchor="end" fill="#7A7494" font-size="13">${Math.round(y)}</text>`;
    }

    let peakLbl = "";
    const iMax = ys.indexOf(Math.max(...ys));
    const iMin = ys.indexOf(Math.min(...ys));
    if (iMax >= 0 && ys[iMax] > 0) {
      const mx = xScale(xs[iMax]), my = yScale(ys[iMax]);
      peakLbl += `<text x="${mx}" y="${Math.max(pad.t + 14, my - 10)}" text-anchor="middle" fill="#1ED760" font-size="12" font-weight="800">MAX +${Math.round(ys[iMax])}</text>`;
    }
    if (iMin >= 0 && ys[iMin] < 0) {
      const mx = xScale(xs[iMin]), my = yScale(ys[iMin]);
      peakLbl += `<text x="${mx}" y="${Math.min(H - pad.b - 6, my + 18)}" text-anchor="middle" fill="#FF3B5C" font-size="12" font-weight="800">MAX −${Math.round(Math.abs(ys[iMin]))}</text>`;
    }

    let spotLine = "";
    if (spot != null && Number(spot) > 0) {
      const s = Number(spot);
      const sx = xScale(s);
      let spotY = null;
      for (let i = 1; i < pts.length; i++) {
        const x0 = Number(pts[i - 1].x), x1 = Number(pts[i].x);
        if ((s >= x0 && s <= x1) || (s >= x1 && s <= x0)) {
          const t = x1 === x0 ? 0 : (s - x0) / (x1 - x0);
          spotY = Number(pts[i - 1].y) + t * (Number(pts[i].y) - Number(pts[i - 1].y));
          break;
        }
      }
      if (spotY == null) spotY = 0;
      const sy = yScale(spotY);
      const pnlCol = spotY >= 0 ? "#1ED760" : "#FF3B5C";
      const pnlTxt = (spotY >= 0 ? "+" : "") + Math.round(spotY);
      spotLine = `<line x1="${sx}" y1="${pad.t}" x2="${sx}" y2="${H - pad.b}" stroke="#7C6AE8" stroke-width="2.5" />
        <circle cx="${sx}" cy="${sy}" r="6" fill="#7C6AE8" stroke="#fff" stroke-width="1.5" />
        <text x="${sx}" y="${pad.t + 18}" text-anchor="middle" fill="#7C6AE8" font-size="14" font-weight="800">SPOT ${s.toFixed(0)}</text>
        <text x="${sx + 10}" y="${sy - 10}" text-anchor="start" fill="${pnlCol}" font-size="13" font-weight="800">PnL ${pnlTxt}</text>`;
    }

    const uid = "pf" + Math.floor(Math.random() * 1e9);
    svg.innerHTML = `
      <defs>
        <clipPath id="${uid}up"><rect x="${pad.l}" y="${pad.t}" width="${innerW}" height="${Math.max(0, zeroY - pad.t)}" /></clipPath>
        <clipPath id="${uid}dn"><rect x="${pad.l}" y="${zeroY}" width="${innerW}" height="${Math.max(0, H - pad.b - zeroY)}" /></clipPath>
      </defs>
      <rect x="0" y="0" width="${W}" height="${H}" fill="transparent" />
      ${grid}
      <line x1="${pad.l}" y1="${zeroY}" x2="${W - pad.r}" y2="${zeroY}" stroke="#2A2450" stroke-width="1.5" opacity="0.45" />
      <path d="${area}" fill="rgba(30,215,96,0.14)" clip-path="url(#${uid}up)" />
      <path d="${area}" fill="rgba(255,59,92,0.12)" clip-path="url(#${uid}dn)" />
      ${greenPaths}
      ${redPaths}
      ${peakLbl}
      ${spotLine}
    `;
  }



  function go(p) {
    if (isDirectional() && (p === "overnight" || p === "payoff")) p = "home";
    // Shoonya desk: Copy Trades UI removed for Batman and Directional.
    if (p === "copytrades") p = "home";
    page = p;
    closeMenu();
    applyStrategyChrome();
    document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("on", b.dataset.go === p));
    const tabPages = { home: 1, status: 1, ato: 1, register: 1 };
    document.querySelectorAll(".tabbar span").forEach((b) => {
      if (b.hasAttribute("data-menu")) {
        b.classList.toggle("on", !tabPages[p]);
      } else {
        b.classList.toggle("on", b.dataset.go === p);
      }
    });
    render(true);
  }

  function autoProtect(side, sellStrike, step) {
    const n = Number(sellStrike);
    if (!n) return "";
    return side === "PE" ? String(n - step) : String(n + step);
  }

  function strikeOf(list, symbol) {
    const row = (list || []).find((p) => p.symbol === symbol);
    return row ? row.strike : "";
  }

  function optionHtml(rows, selected, noneLabel) {
    const opts = [`<option value="">${esc(noneLabel)}</option>`];
    for (const p of rows || []) {
      const sel = p.symbol === selected ? " selected" : "";
      opts.push(`<option value="${esc(p.symbol)}"${sel}>${esc(p.label)}</option>`);
    }
    return opts.join("");
  }

  function qBlock(n, label, inner) {
    return `<div class="reg-q"><div class="q-lab"><span class="q-label">${esc(label)}</span></div><div class="field">${inner}</div></div>`;
  }


  function numLevel(el) {
    if (!el) return null;
    const raw = String(el.value || "").trim().replace(/,/g, "");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }

  function bufferPairIssues() {
    const issues = [];
    const ceEntry = numLevel($("ce_entry"));
    const ceExit = numLevel($("ce_retrace"));
    const peEntry = numLevel($("pe_entry"));
    const peExit = numLevel($("pe_retrace"));
    if (ceEntry != null && ceExit != null && !(ceExit < ceEntry)) {
      issues.push("CE exit must be below CE entry");
    }
    if (peEntry != null && peExit != null && !(peExit > peEntry)) {
      issues.push("PE exit must be above PE entry");
    }
    return issues;
  }

  function syncBufSaveEnabled() {
    const btn = $("bufSave");
    const msg = $("bufMsg");
    if (!btn) return;
    const issues = bufferPairIssues();
    const ok = issues.length === 0;
    btn.disabled = !ok;
    btn.setAttribute("aria-disabled", ok ? "false" : "true");
    if (msg) {
      if (!ok) {
        msg.textContent = issues.join(" · ");
        msg.classList.add("buf-hint-bad");
      } else if (msg.classList.contains("buf-hint-bad") || /must be (above|below)/i.test(msg.textContent || "")) {
        msg.textContent = "";
        msg.classList.remove("buf-hint-bad");
      }
    }
  }

  function wireAtoMgrHandlers() {
    function syncMgrAtoStrike() {
      function paint(modeEl, inp) {
        if (!modeEl || !inp) return;
        const custom = modeEl.value === "custom";
        const autoVal = String(inp.getAttribute("data-auto") || "").trim();
        inp.disabled = !custom;
        inp.readOnly = !custom;
        inp.classList.toggle("is-grey", !custom);
        if (!custom) {
          // Show recommended Auto strike (same idea as Register placeholder).
          inp.value = autoVal || "";
          inp.placeholder = autoVal ? ("Auto " + autoVal) : "Auto";
        } else if (inp.dataset.wasAuto === "1") {
          // Switching into Custom: keep last auto as starting point if empty.
          if (!String(inp.value || "").trim() && autoVal) inp.value = autoVal;
        }
        inp.dataset.wasAuto = custom ? "0" : "1";
      }
      paint($("mgrCeAtoMode"), $("mgrCeAto"));
      paint($("mgrPeAtoMode"), $("mgrPeAto"));
    }
    ["ce_entry", "ce_retrace", "pe_entry", "pe_retrace"].forEach((id) => {
      const el = $(id);
      if (!el || el.dataset.bufGateWired === "1") return;
      el.dataset.bufGateWired = "1";
      el.addEventListener("input", syncBufSaveEnabled);
      el.addEventListener("change", syncBufSaveEnabled);
    });
    ["mgrCeAtoMode", "mgrPeAtoMode"].forEach((id) => {
      const el = $(id);
      if (!el || el.dataset.atoModeWired === "1") return;
      el.dataset.atoModeWired = "1";
      el.addEventListener("change", syncMgrAtoStrike);
    });
    syncMgrAtoStrike();
    syncBufSaveEnabled();
    if ($("bufSave")) {
      $("bufSave").onclick = async () => {
        syncBufSaveEnabled();
        if ($("bufSave").disabled) return;
        const body = {
          ce_entry: $("ce_entry").value,
          pe_entry: $("pe_entry").value,
          ce_retrace: $("ce_retrace").value,
          pe_retrace: $("pe_retrace").value,
          ce_ato_mode: $("mgrCeAtoMode") ? $("mgrCeAtoMode").value : "auto",
          pe_ato_mode: $("mgrPeAtoMode") ? $("mgrPeAtoMode").value : "auto",
          ce_ato_strike: ($("mgrCeAtoMode") && $("mgrCeAtoMode").value === "custom" && $("mgrCeAto")) ? $("mgrCeAto").value : "",
          pe_ato_strike: ($("mgrPeAtoMode") && $("mgrPeAtoMode").value === "custom" && $("mgrPeAto")) ? $("mgrPeAto").value : "",
        };
        try {
          const r = await api("/api/buffer", { method: "POST", body: JSON.stringify(body) });
          const ok = !!r.ok;
          $("bufMsg").textContent = ok ? (r.text || "Saved") : (r.error || "failed");
          if ($("bufMsg")) $("bufMsg").classList.toggle("buf-hint-bad", !ok);
          toastResult(r, ok);
          if (ok) invalidatePageCache(["home", "ato"]);
        } catch (err) {
          const msg = err.message || "Save failed";
          if ($("bufMsg")) {
            $("bufMsg").textContent = msg;
            $("bufMsg").classList.add("buf-hint-bad");
          }
          toast(msg, false);
        }
      };
    }
    if ($("pollSave")) {
      $("pollSave").onclick = async () => {
        try {
          const r = await api("/api/poll", { method: "POST", body: JSON.stringify({ poll_interval: $("pollSel").value }) });
          $("pollMsg").textContent = r.ok ? (r.text || "Saved") : (r.error || "failed");
          toastResult(r, !!r.ok);
        } catch (err) {
          $("pollMsg").textContent = err.message;
          toast(err.message, false);
        }
      };
    }
    if ($("atoSyncLots")) {
      $("atoSyncLots").onclick = () => runSyncLotsFromBroker($("atoSyncLotsMsg"));
    }
  }


  function fmtPreviewLtp(v) {
    if (v == null || v === "") return "-";
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(2) : String(v);
  }

  function fmtAtoFireSize(lots, qty) {
    if (lots == null || lots === "") return "N/A";
    const n = Number(lots);
    if (!Number.isFinite(n)) return "N/A";
    const q = qty == null || qty === "" ? n * 65 : Number(qty);
    return n + " lots (" + (Number.isFinite(q) ? q : "?") + " qty)";
  }

  async function runSyncLotsFromBroker(msgEl) {
    if (!window.confirm("Update managed/ATO size from current broker qty? Symbols and buffers stay the same.")) {
      return;
    }
    try {
      setBusy(true);
      const r = await api("/api/register/sync-lots", {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      });
      if (msgEl) msgEl.textContent = r.ok ? (r.text || "Synced.") : (r.error || "Sync failed.");
      toastResult(r, !!r.ok);
      if (r.ok) {
        invalidatePageCache(["home", "ato", "register"]);
        try { await render(); } catch (_) {}
      }
    } catch (err) {
      if (msgEl) msgEl.textContent = err.message || "Sync failed.";
      toast(err.message || "Sync failed.", false);
    } finally {
      setBusy(false);
    }
  }

  function previewTableHtml(rows, opts) {
    const editable = !!(opts && opts.editableStrikes);
    const selectable = !!(opts && opts.selectableLegs);
    const prefix = (opts && opts.idPrefix) || "dep";
    const colCount = selectable ? 9 : 8;
    const body = (rows || []).length
      ? rows.map((r, idx) => {
          const rowCls = String(r.side || "").toUpperCase() === "BUY" ? "dep-buy-row" : "dep-sell-row";
          const key = String(r.key || r.type || idx);
          let strikeCell = esc(r.strike);
          if (editable) {
            const choices = Array.isArray(r.strike_choices) && r.strike_choices.length
              ? r.strike_choices
              : (opts.strikeChoices || []);
            const optsHtml = choices.map((s) => {
              const sel = Number(s) === Number(r.strike) ? " selected" : "";
              return `<option value="${esc(s)}"${sel}>${esc(s)}</option>`;
            }).join("");
            strikeCell = `<select class="dep-strike-sel" data-leg-key="${esc(key)}" data-opt="${esc(r.opt || "")}" data-view="${esc((opts && opts.view) || "")}" id="${esc(prefix)}Strike_${esc(key)}">${optsHtml}</select>`;
          }
          const selCell = selectable
            ? `<td class="dep-leg-sel-cell"><input type="checkbox" class="dep-leg-sel" data-leg-key="${esc(key)}" checked aria-label="Include ${esc(key)}"></td>`
            : "";
          return `<tr class="${rowCls}" data-leg-key="${esc(key)}">
          ${selCell}
          <td>${esc(r.sn)}</td>
          <td>${esc(r.type)}</td>
          <td>${esc(r.side)}</td>
          <td>${esc(r.opt)}</td>
          <td>${strikeCell}</td>
          <td>${esc(r.lots)}</td>
          <td>${esc(r.qty)}</td>
          <td class="dep-ltp-cell">${esc(fmtPreviewLtp(r.ltp))}</td>
        </tr>`;
        }).join("")
      : `<tr><td colspan="${colCount}">None</td></tr>`;
    const selHead = selectable ? `<th class="dep-leg-sel-cell"></th>` : "";
    return `<div class="card hist-wrap dep-preview-wrap"><table class="hist-table dep-preview-table"><thead><tr>
      ${selHead}<th>#</th><th>Type</th><th>BUY/SELL</th><th>CE/PE</th><th>Strike</th><th>Lots</th><th>Qty</th><th>LTP</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
  }


  function wireDirStrikeLtp(el, opts) {
    if (!el) return;
    const prefix = (opts && opts.idPrefix) || "";
    const expiryEl = prefix ? $(prefix + "Expiry") : null;
    el.querySelectorAll("select.dep-strike-sel").forEach((sel) => {
      if (sel.dataset.ltpWired === "1") return;
      sel.dataset.ltpWired = "1";
      sel.addEventListener("change", async () => {
        const key = String(sel.getAttribute("data-leg-key") || "");
        const opt = String(sel.getAttribute("data-opt") || "").toUpperCase();
        const strike = Number(sel.value);
        const expiry = expiryEl ? expiryEl.value : "";
        const row = sel.closest("tr");
        const ltpCell = row ? row.querySelector(".dep-ltp-cell") : null;
        if (ltpCell) ltpCell.textContent = "…";
        try {
          const r = await api("/api/deploy", {
            method: "POST",
            body: JSON.stringify({
              preview: true,
              quote_ltp: true,
              strategy: "directional",
              key: key,
              opt: opt,
              strike: strike,
              expiry: expiry,
            }),
          });
          if (ltpCell) ltpCell.textContent = r.ok ? fmtPreviewLtp(r.ltp) : "-";
          if (r.ok && window.__dirPreview) {
            const view = String(sel.getAttribute("data-view") || (opts && opts.view) || "");
            const cached = window.__dirPreview[view];
            if (cached && Array.isArray(cached.place_rows)) {
              cached.place_rows.forEach((row0) => {
                if (String(row0.key || row0.type) === key) {
                  row0.strike = strike;
                  row0.ltp = r.ltp;
                }
              });
            }
          }
          if (!r.ok && r.error) {
            // keep cell dash; soft toast for failures only
            toast(r.error, false);
          }
        } catch (err) {
          if (ltpCell) ltpCell.textContent = "-";
          toast(err.message || "LTP quote failed", false);
        }
      });
    });
  }

  function collectDirLegsFromPreview(el, fallbackRows) {
    const rows = Array.isArray(fallbackRows) ? fallbackRows : [];
    const byKey = {};
    rows.forEach((r) => { byKey[String(r.key || r.type)] = Object.assign({}, r); });
    if (el) {
      el.querySelectorAll("select.dep-strike-sel").forEach((sel) => {
        const key = String(sel.getAttribute("data-leg-key") || "");
        if (!key || !byKey[key]) return;
        const n = Number(sel.value);
        if (Number.isFinite(n)) byKey[key].strike = n;
      });
    }
    const boxes = el ? el.querySelectorAll("input.dep-leg-sel") : [];
    if (boxes && boxes.length) {
      const checked = {};
      boxes.forEach((box) => {
        const key = String(box.getAttribute("data-leg-key") || "");
        if (key && box.checked) checked[key] = true;
      });
      return Object.keys(byKey).filter((k) => checked[k]).map((k) => byKey[k]);
    }
    return Object.keys(byKey).map((k) => byKey[k]);
  }

  function renderDeployPreview(el, r, opts) {
    if (!el) return;
    if (r && r.ok && Array.isArray(r.place_rows)) {
      const nPlace = r.place_rows.length;
      const nSkip = (r.skipped_rows || []).length;
      const nPlan = nPlace + nSkip;
      const dir = !!(r.strategy === "directional" || (opts && opts.directional));
      const title = dir
        ? (`${esc(r.view_label || "Directional")} Preview - Center ${esc(r.level)}`)
        : (`Batman 2.0 Preview - Center ${esc(r.level)}`);
      let html = `<div class="dep-preview">
        <div class="dep-preview-title">${title}</div>
        <div class="dep-preview-sub">Expiry: ${esc(r.expiry_label || r.expiry || "")} | Lots: ${esc(r.lots)} | ${nPlace} Legs to place (of ${nPlan} in plan)</div>
        ${previewTableHtml(r.place_rows, {
          editableStrikes: dir,
          selectableLegs: dir,
          idPrefix: (opts && opts.idPrefix) || "dep",
          view: r.view || (opts && opts.view) || "",
          strikeChoices: r.strike_choices || [],
        })}`;
      if (nSkip) {
        html += `<div class="dep-preview-skip">Skipped (35% dyn hedge &lt; 1 lot at this size):</div>
          ${previewTableHtml(r.skipped_rows)}`;
      }
      html += dir
        ? `</div>`
        : `<p class="dep-preview-foot">Confirm to place all legs above (entry only).<br>Then Register Batman to arm Phase 1 / ATO.</p></div>`;
      el.innerHTML = html;
      if (dir) {
        if (!window.__dirPreview) window.__dirPreview = {};
        window.__dirPreview[String(r.view || (opts && opts.view) || "")] = r;
        wireDirStrikeLtp(el, opts || {});
      }
      return;
    }
    el.textContent = (r && (r.text || r.error)) || "";
  }

  function wireEmergencyExitBtn() {
    const btn = $("exitAllBtn");
    if (!btn || btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.onclick = async () => {
      if (!window.confirm("Emergency exit: close every open position on the broker now?")) return;
      try {
        setBusy(true);
        const dres = await api("/api/flatten", {
          method: "POST",
          body: JSON.stringify({ confirm: true }),
        });
        const exitMsg = $("exitMsg");
        if (exitMsg) exitMsg.textContent = dres.text || dres.error || "";
        toastResult(dres, !!dres.ok);
        if (dres.pnl_exit) live.pnl_exit = dres.pnl_exit;
        if (dres.ok) live.paused = true;
        applyChrome(live);
        if (page === "home" || page === "positions") {
          try { await render(); } catch (_) {}
        }
      } catch (err) {
        const exitMsg = $("exitMsg");
        if (exitMsg) exitMsg.textContent = err.message;
        toast(err.message, false);
      } finally {
        setBusy(false);
      }
    };
  }

  function wireDeployHandlers() {
    function wireBatmanDeploy() {
      if ($("depPrev")) {
        $("depPrev").onclick = async () => {
          try {
            const r = await api("/api/deploy", { method: "POST", body: JSON.stringify({ preview: true, level: $("depLevel").value, lots: $("depLots").value, expiry: $("depExpiry") ? $("depExpiry").value : "" }) });
            renderDeployPreview($("depOut"), r);
            $("depMsg").textContent = r.ok ? "Preview ready. Confirm only if the legs look right." : (r.error || "");
            savePageCache("deploy", $("view").innerHTML, pageCacheMeta.deploy);
          } catch (err) {
            $("depOut").textContent = err.message;
          }
        };
      }
      if ($("depGo")) {
        $("depGo").onclick = async () => {
          if (!window.confirm("Place Batman 2.0 legs now?")) return;
          try {
            const _lotsN = Number($("depLots").value || 0);
            const _maxSell = Number((window.__sub && window.__sub.max_sell_lots_batman));
            if (Number.isFinite(_maxSell) && _lotsN * 4 > _maxSell) {
              toast("Deploy blocked. Your subscription allows a maximum of " + _maxSell + " Batman SELL lots (total). Lots " + _lotsN + " needs " + (_lotsN * 4) + " SELL lots.", true);
              return;
            }
            const r = await api("/api/deploy", { method: "POST", body: JSON.stringify({ confirm: true, level: $("depLevel").value, lots: $("depLots").value, expiry: $("depExpiry") ? $("depExpiry").value : "" }) });
            $("depOut").textContent = r.text || r.error || "";
            const filled = Number((r.result && r.result.legs || []).filter(function (x) { return x && x.status === "filled"; }).length);
            const success = !!r.ok && filled > 0;
            $("depMsg").textContent = success
              ? ("Deploy sent " + filled + " leg(s) to Kite.")
              : (r.error || r.text || "No legs filled - check Kite. No orders punched.");
            toastResult(r, success);
            if (success) invalidatePageCache();
          } catch (err) {
            $("depOut").textContent = err.message;
            toast(err.message, false);
          }
        };
      }
      if ($("depGoReg")) {
        $("depGoReg").onclick = async () => {
          if (!window.confirm("Place Batman 2.0 legs now, then auto-Register if all placeable legs fill?")) return;
          try {
            const r = await api("/api/deploy-register", { method: "POST", body: JSON.stringify({ level: $("depLevel").value, lots: $("depLots").value, expiry: $("depExpiry") ? $("depExpiry").value : "" }) });
            $("depOut").textContent = r.detail_text || r.text || r.error || "";
            $("depMsg").textContent = r.ok
              ? "Deploy sent and Register armed successfully."
              : (r.error || r.text || "Deploy completed, but Register was not started.");
            toastResult(r, !!r.ok);
            if (r.ok) invalidatePageCache();
          } catch (err) {
            $("depOut").textContent = err.message;
            $("depMsg").textContent = err.message;
            toast(err.message, false);
          }
        };
      }
      wireEmergencyExitBtn();
    }

    function wireDirCard(view, prefix) {
      const prev = $(prefix + "Prev");
      const goBtn = $(prefix + "Go");
      const goReg = $(prefix + "GoReg");
      const out = $(prefix + "Out");
      const msg = $(prefix + "Msg");
      const levelEl = $(prefix + "Level");
      const lotsEl = $(prefix + "Lots");
      const expEl = $(prefix + "Expiry");
      function payloadBase(extra) {
        const body = Object.assign({
          strategy: "directional",
          view: view,
          level: levelEl ? levelEl.value : "",
          lots: lotsEl ? lotsEl.value : "",
          expiry: expEl ? expEl.value : "",
        }, extra || {});
        const cached = window.__dirPreview && window.__dirPreview[view];
        if (cached && Array.isArray(cached.place_rows)) {
          body.legs = collectDirLegsFromPreview(out, cached.place_rows).map((r) => ({
            key: r.key,
            strike: r.strike,
            side: r.side,
            opt: r.opt,
            type: r.type,
          }));
        }
        return body;
      }
      function selectedLegKeys() {
        const cached = window.__dirPreview && window.__dirPreview[view];
        if (!cached || !Array.isArray(cached.place_rows)) return [];
        return collectDirLegsFromPreview(out, cached.place_rows).map((r) => String(r.key || ""));
      }
      function gateCheckedLegs(forRegister) {
        const keys = selectedLegKeys();
        if (!keys.length) {
          toast("Select at least one leg before deploying.", false);
          if (msg) msg.textContent = "Select at least one leg before deploying.";
          return null;
        }
        if (forRegister) {
          const buyKey = view === "bearish" ? "ce_buy" : "pe_buy";
          const sellKey = view === "bearish" ? "ce_sell" : "pe_sell";
          if (keys.indexOf(buyKey) < 0 || keys.indexOf(sellKey) < 0) {
            const err = "Core Buy and Sell must be checked. Margin Hedge is optional.";
            toast(err, false);
            if (msg) msg.textContent = err;
            return null;
          }
        }
        return keys;
      }
      if (prev) {
        prev.onclick = async () => {
          try {
            const r = await api("/api/deploy", { method: "POST", body: JSON.stringify(Object.assign(payloadBase(), { preview: true })) });
            renderDeployPreview(out, r, { directional: true, idPrefix: prefix, view: view });
            if (msg) msg.textContent = r.ok ? "" : (r.error || "");
            savePageCache("deploy", $("view").innerHTML, pageCacheMeta.deploy);
          } catch (err) {
            if (out) out.textContent = err.message;
          }
        };
      }
      if (goBtn) {
        goBtn.onclick = async () => {
          if (!gateCheckedLegs(true)) return;
          const _lotsN = Number(lotsEl && lotsEl.value || 0);
          const _maxSell = Number((window.__sub && window.__sub.max_sell_lots_directional));
          const _maxAto = Number((window.__sub && window.__sub.max_ato_lots_directional));
          if (Number.isFinite(_maxSell) && _lotsN > _maxSell) {
            toast("Deploy blocked. Your subscription allows a maximum of " + _maxSell + " Directional SELL lots.", true);
            return;
          }
          if (Number.isFinite(_maxAto) && _lotsN > _maxAto) {
            toast("Deploy blocked. Your subscription allows a maximum of " + _maxAto + " Directional ATO lots.", true);
            return;
          }
          if (!window.confirm(
            "Place " + (view === "bearish" ? "Bearish View" : "Bullish View")
            + " legs, verify broker fills, then sync managed/ATO lots from the broker book?"
          )) return;
          try {
            setBusy(true);
            const r = await api("/api/deploy-then-sync", { method: "POST", body: JSON.stringify(payloadBase()) });
            if (out) out.textContent = r.detail_text || r.text || r.error || "";
            const success = !!r.ok;
            if (msg) msg.textContent = success
              ? "Deploy completed and lots synced from broker."
              : (r.error || r.text || "Deploy then sync did not complete.");
            toastResult(r, success);
            if (success) {
              invalidatePageCache(["home", "ato", "register", "deploy", "summary", "positions"]);
              try { await render(); } catch (_) {}
            }
          } catch (err) {
            if (out) out.textContent = err.message;
            toast(err.message, false);
          } finally {
            setBusy(false);
          }
        };
      }
      if (goReg) {
        goReg.onclick = async () => {
          if (!gateCheckedLegs(true)) return;
          const _lotsN2 = Number(lotsEl && lotsEl.value || 0);
          const _maxSell2 = Number((window.__sub && window.__sub.max_sell_lots_directional));
          const _maxAto2 = Number((window.__sub && window.__sub.max_ato_lots_directional));
          if (Number.isFinite(_maxSell2) && _lotsN2 > _maxSell2) {
            toast("Deploy blocked. Your subscription allows a maximum of " + _maxSell2 + " Directional SELL lots.", true);
            return;
          }
          if (Number.isFinite(_maxAto2) && _lotsN2 > _maxAto2) {
            toast("Deploy blocked. Your subscription allows a maximum of " + _maxAto2 + " Directional ATO lots.", true);
            return;
          }
          if (!window.confirm("Place legs now, then auto-Register if all placeable legs fill?")) return;
          try {
            const r = await api("/api/deploy-register", { method: "POST", body: JSON.stringify(payloadBase()) });
            if (out) out.textContent = r.detail_text || r.text || r.error || "";
            if (msg) msg.textContent = r.ok
              ? "Deploy sent and Register armed successfully."
              : (r.error || r.text || "Deploy completed, but Register was not started.");
            toastResult(r, !!r.ok);
            if (r.ok) invalidatePageCache();
          } catch (err) {
            if (out) out.textContent = err.message;
            if (msg) msg.textContent = err.message;
            toast(err.message, false);
          }
        };
      }
    }

    if (isDirectional() && ($("depBearPrev") || $("depBullPrev"))) {
      wireDirCard("bearish", "depBear");
      wireDirCard("bullish", "depBull");
      return;
    }
    wireBatmanDeploy();
  }

  function wirePayoffChart(meta) {
    drawPayoffChart($("payoffSvg"), meta.points || [], meta.spot, {
      atm: meta.atm,
      step: meta.step || 50,
    });
  }

  function wireTokenHandlers(view) {
    if (!view) return;
    async function tokenCall(fn) {
      const msg = $("tokMsg");
      const stage = view.querySelector(".tok-stage");
      const dots = $("tokDots");
      try {
        setBusy(true);
        if (stage) stage.classList.add("tok-busy");
        if (dots) dots.removeAttribute("hidden");
        const r = await fn();
        const text = r.text || r.error || (r.ok ? "Done" : "failed");
        toastResult(r, !!r.ok);
        if (r.ok) {
          invalidatePageCache("token");
          await renderTokenPage(view, text);
          const next = view.querySelector(".tok-stage");
          if (next) {
            next.classList.remove("tok-magic-ok");
            void next.offsetWidth;
            next.classList.add("tok-magic-ok");
          }
          return;
        }
        if (msg) msg.textContent = text;
        if (stage) {
          stage.classList.remove("tok-shake");
          void stage.offsetWidth;
          stage.classList.add("tok-shake");
        }
      } catch (err) {
        const text = err.message || "failed";
        if (msg) msg.textContent = text;
        toast(text, false);
        if (stage) {
          stage.classList.remove("tok-shake");
          void stage.offsetWidth;
          stage.classList.add("tok-shake");
        }
      } finally {
        setBusy(false);
        if (stage) stage.classList.remove("tok-busy");
        if (dots) dots.setAttribute("hidden", "");
      }
    }
    const paintMain = (broker) => {
      const stage = view.querySelector(".tok-stage");
      if (stage) {
        stage.classList.remove("tok-switching");
        void stage.offsetWidth;
        stage.classList.add("tok-switching");
        window.setTimeout(() => stage.classList.remove("tok-switching"), 780);
      }
      view.querySelectorAll(".tok-panel").forEach((el) => {
        const on = el.getAttribute("data-panel") === broker;
        el.classList.toggle("on", on);
        el.classList.toggle("tok-card-live", on);
        el.style.display = on ? "flex" : "none";
        if (on) {
          el.classList.remove("tok-panel-in");
          void el.offsetWidth;
          el.classList.add("tok-panel-in");
        }
      });
      view.querySelectorAll(".tok-drop-item").forEach((el) => {
        const on = el.getAttribute("data-broker") === broker;
        el.classList.toggle("on", on);
        el.setAttribute("aria-selected", on ? "true" : "false");
      });
      const btn = $("mainBrokerBtn");
      if (btn) btn.setAttribute("data-broker", broker);
      const live = $("mainLiveLbl");
      const liveNames = {zerodha:"ZERODHA",dhan:"DHAN",shoonya:"SHOONYA",groww:"GROWW",angel:"ANGEL",fyers:"FYERS",kotak:"KOTAK"};
      const liveLogos = {zerodha:"/static/kite.svg",dhan:"/static/dhan.svg",shoonya:"/static/shoonya.svg",groww:"/static/groww.png",angel:"/static/angel.png",fyers:"/static/fyers.png",kotak:"/static/kotak.svg"};
      if (live) live.textContent = liveNames[broker] || broker.toUpperCase();
      const armedLogo = $("tokArmedLogo");
      if (armedLogo) armedLogo.src = liveLogos[broker] || liveLogos.zerodha;
      const armed = $("tokArmed");
      if (armed) {
        armed.classList.remove("tok-armed-pop");
        void armed.offsetWidth;
        armed.classList.add("tok-armed-pop");
      }
    };
    let chosen = ($("mainBrokerBtn") && $("mainBrokerBtn").getAttribute("data-broker")) || "zerodha";
    const currentMain = () => chosen;
    const setMain = (broker) => {
      if (broker === chosen) return;
      chosen = broker;
      paintMain(broker);
      tokenCall(() => api("/api/token/main-broker", {
        method: "POST",
        body: JSON.stringify({ main_broker: broker }),
      }));
    };
    const brokerBtn = $("mainBrokerBtn");
    const brokerMenu = $("mainBrokerMenu");
    if (brokerBtn && brokerMenu) {
      brokerMenu.removeAttribute("hidden");
      brokerBtn.setAttribute("aria-expanded", "true");
      brokerMenu.querySelectorAll(".tok-drop-item").forEach((el) => {
        el.onclick = () => setMain(el.getAttribute("data-broker"));
      });
    }
    if ($("tokRef")) $("tokRef").onclick = () => tokenCall(() => api("/api/token/refresh", { method: "POST", body: "{}" }));
    if ($("tokOff")) $("tokOff").onclick = () => tokenCall(() => api("/api/token/deactivate", { method: "POST", body: "{}" }));
    if ($("tokStat")) $("tokStat").onclick = () => renderTokenPage(view, "Token status refreshed.");
    if ($("tokPaste")) {
      $("tokPaste").onclick = () => tokenCall(() => api("/api/token/dhan-jwt", {
        method: "POST",
        body: JSON.stringify({ token: $("jwtBox").value }),
      }));
    }
    if ($("tokZ")) {
      $("tokZ").onclick = () => tokenCall(() => api("/api/token/zerodha", {
        method: "POST",
        body: JSON.stringify({ token: $("zBox").value }),
      }));
    }
    if ($("tokZOff")) $("tokZOff").onclick = () => tokenCall(() => api("/api/token/zerodha/deactivate", { method: "POST", body: "{}" }));
    if ($("tokS")) {
      $("tokS").onclick = () => tokenCall(() => api("/api/token/shoonya", {
        method: "POST",
        body: JSON.stringify({ user_id: $("sUid").value, token: $("sBox").value }),
      }));
    }
    if ($("tokSOff")) $("tokSOff").onclick = () => tokenCall(() => api("/api/token/shoonya/deactivate", { method: "POST", body: "{}" }));
    if ($("tokG")) $("tokG").onclick = () => tokenCall(() => api("/api/token/groww", { method: "POST", body: JSON.stringify({ token: $("gBox").value }) }));
    if ($("tokGOff")) $("tokGOff").onclick = () => tokenCall(() => api("/api/token/groww/deactivate", { method: "POST", body: "{}" }));
    if ($("tokA")) $("tokA").onclick = () => tokenCall(() => api("/api/token/angel", { method: "POST", body: JSON.stringify({ client_code: $("aClient").value, api_key: $("aKey").value, token: $("aBox").value }) }));
    if ($("tokAOff")) $("tokAOff").onclick = () => tokenCall(() => api("/api/token/angel/deactivate", { method: "POST", body: "{}" }));
    if ($("tokF")) $("tokF").onclick = () => tokenCall(() => api("/api/token/fyers", { method: "POST", body: JSON.stringify({ app_id: $("fApp").value, token: $("fBox").value }) }));
    if ($("tokFOff")) $("tokFOff").onclick = () => tokenCall(() => api("/api/token/fyers/deactivate", { method: "POST", body: "{}" }));
    if ($("tokK")) $("tokK").onclick = () => tokenCall(() => api("/api/token/kotak", { method: "POST", body: JSON.stringify({ mobile: $("kMobile").value, ucc: $("kUcc").value, access_token: $("kAccess").value, trade_token: $("kTrade").value, sid: $("kSid").value }) }));
    if ($("tokKOff")) $("tokKOff").onclick = () => tokenCall(() => api("/api/token/kotak/deactivate", { method: "POST", body: "{}" }));

  }


  function wireRegisterHandlers(d) {
    const step = Number(d.ato_step || 50);

    function refillLong(sel, rows, keep, used, noneLabel) {
      const filtered = (rows || []).filter((p) => p.symbol === keep || !used.has(p.symbol));
      sel.innerHTML = optionHtml(filtered, keep, noneLabel);
    }

    function sync() {
      const scope = $("regScope").value;
      const peOn = scope !== "ce";
      const ceOn = scope !== "pe";
      $("regPeBlock").classList.toggle("q-off", !peOn);
      $("regCeBlock").classList.toggle("q-off", !ceOn);
      $("regPeBlock").querySelectorAll("input,select").forEach((el) => { el.disabled = !peOn; });
      $("regCeBlock").querySelectorAll("input,select").forEach((el) => { el.disabled = !ceOn; });

      const peUsed = new Set();
      if ($("regPeBuy").value) peUsed.add($("regPeBuy").value);
      refillLong($("regPeHedge"), d.pe_long, $("regPeHedge").value, peUsed, "None - not required");
      if ($("regPeHedge").value) peUsed.add($("regPeHedge").value);
      if ($("regPeDyn")) refillLong($("regPeDyn"), d.pe_long, $("regPeDyn").value, peUsed, "None - not required");
      const peHedgeLeft = (d.pe_long || []).filter((p) => p.symbol !== $("regPeBuy").value);
      const peDynLeft = peHedgeLeft.filter((p) => p.symbol !== ($("regPeHedge") && $("regPeHedge").value));
      $("regPeHedge").closest(".reg-q").classList.toggle("q-off", !peOn || peHedgeLeft.length === 0);
      if ($("regPeDyn")) $("regPeDyn").closest(".reg-q").classList.toggle("q-off", !peOn || peDynLeft.length === 0);
      if (peOn) {
        $("regPeHedge").disabled = peHedgeLeft.length === 0;
        if ($("regPeDyn")) $("regPeDyn").disabled = peDynLeft.length === 0;
      }

      const ceUsed = new Set();
      if ($("regCeBuy").value) ceUsed.add($("regCeBuy").value);
      refillLong($("regCeHedge"), d.ce_long, $("regCeHedge").value, ceUsed, "None - not required");
      if ($("regCeHedge").value) ceUsed.add($("regCeHedge").value);
      if ($("regCeDyn")) refillLong($("regCeDyn"), d.ce_long, $("regCeDyn").value, ceUsed, "None - not required");
      const ceHedgeLeft = (d.ce_long || []).filter((p) => p.symbol !== $("regCeBuy").value);
      const ceDynLeft = ceHedgeLeft.filter((p) => p.symbol !== ($("regCeHedge") && $("regCeHedge").value));
      $("regCeHedge").closest(".reg-q").classList.toggle("q-off", !ceOn || ceHedgeLeft.length === 0);
      if ($("regCeDyn")) $("regCeDyn").closest(".reg-q").classList.toggle("q-off", !ceOn || ceDynLeft.length === 0);
      if (ceOn) {
        $("regCeHedge").disabled = ceHedgeLeft.length === 0;
        if ($("regCeDyn")) $("regCeDyn").disabled = ceDynLeft.length === 0;
      }

      const peSell = strikeOf(d.pe_short, $("regPeSell").value);
      const ceSell = strikeOf(d.ce_short, $("regCeSell").value);
      const peBuy = strikeOf(d.pe_long, $("regPeBuy").value);
      const ceBuy = strikeOf(d.ce_long, $("regCeBuy").value);
      const peAuto = autoProtect("PE", isDirectional() ? peBuy : peSell, step);
      const ceAuto = autoProtect("CE", isDirectional() ? ceBuy : ceSell, step);
      const peCustom = $("regPeAtoMode").value === "custom";
      const ceCustom = $("regCeAtoMode").value === "custom";
      if (peOn) {
        $("regPeAto").disabled = !peCustom;
        $("regPeAto").closest(".reg-q").querySelector("input").classList.toggle("is-grey", !peCustom);
        if (!peCustom) $("regPeAto").value = "";
        $("regPeAto").placeholder = peAuto ? "Auto " + peAuto : "Custom NIFTY strike";
        if (isDirectional() && $("regPeEntry") && peAuto) {
          $("regPeEntry").placeholder = String(peAuto);
          if ($("regPeExit")) $("regPeExit").placeholder = String(Number(peAuto) + 10);
        }
      }
      if (ceOn) {
        $("regCeAto").disabled = !ceCustom;
        if (!ceCustom) $("regCeAto").value = "";
        $("regCeAto").placeholder = ceAuto ? "Auto " + ceAuto : "Custom NIFTY strike";
        if (isDirectional() && $("regCeEntry") && ceAuto) {
          $("regCeEntry").placeholder = String(ceAuto);
          if ($("regCeExit")) $("regCeExit").placeholder = String(Number(ceAuto) - 10);
        }
      }

      const atoMon = $("regAtoMon");
      if (atoMon) {
        const atoMonQ = atoMon.closest(".reg-q");
        if (peOn && ceOn) {
          atoMon.disabled = false;
          if (atoMonQ) atoMonQ.classList.remove("q-off");
        } else {
          atoMon.value = peOn ? "pe" : "ce";
          atoMon.disabled = true;
          if (atoMonQ) atoMonQ.classList.add("q-off");
        }
      }
    }

    ["regScope","regPeBuy","regPeHedge","regPeDyn","regPeSell","regPeAtoMode","regCeBuy","regCeHedge","regCeDyn","regCeSell","regCeAtoMode"].forEach((id) => {
      const el = $(id);
      if (el) el.onchange = sync;
    });
    if ($("regExpiry")) {
      $("regExpiry").onchange = async () => {
        const iso = $("regExpiry").value;
        const view = $("view");
        try {
          setBusy(true);
          const nd = await api("/api/register?expiry=" + encodeURIComponent(iso));
          renderRegister(view, nd);
        } catch (err) {
          toast(err.message || "Could not reload expiry", false);
        } finally {
          setBusy(false);
        }
      };
    }
    sync();

    if ($("regSyncLots")) {
      $("regSyncLots").onclick = () => runSyncLotsFromBroker($("regMsg"));
    }

    if ($("regGo")) {
      $("regGo").onclick = async () => {
        if (window.__subAllowed === false) {
          toast(window.__subReason || "Subscription inactive. Renew to register.", true);
          return;
        }
        const scopeVal = $("regScope").value;
        const body = {
          confirm: true,
          strategy: isDirectional() ? "directional" : "batman",
          order_mode: $("regMode").value,
          expiry: $("regExpiry") ? $("regExpiry").value : "",
          reg_scope: scopeVal,
          pe_buy: $("regPeBuy") ? $("regPeBuy").value : "",
          pe_margin_hedge: $("regPeHedge") ? $("regPeHedge").value : "",
          pe_dyn_hedge: (isDirectional() || !$("regPeDyn")) ? "" : $("regPeDyn").value,
          pe_sell: $("regPeSell") ? $("regPeSell").value : "",
          pe_ato_mode: $("regPeAtoMode") ? $("regPeAtoMode").value : "auto",
          pe_ato_strike: $("regPeAto") ? $("regPeAto").value : "",
          pe_entry: $("regPeEntry") ? $("regPeEntry").value : "",
          pe_exit: $("regPeExit") ? $("regPeExit").value : "",
          ce_buy: $("regCeBuy") ? $("regCeBuy").value : "",
          ce_margin_hedge: $("regCeHedge") ? $("regCeHedge").value : "",
          ce_dyn_hedge: (isDirectional() || !$("regCeDyn")) ? "" : $("regCeDyn").value,
          ce_sell: $("regCeSell") ? $("regCeSell").value : "",
          ce_ato_mode: $("regCeAtoMode") ? $("regCeAtoMode").value : "auto",
          ce_ato_strike: $("regCeAto") ? $("regCeAto").value : "",
          ce_entry: $("regCeEntry") ? $("regCeEntry").value : "",
          ce_exit: $("regCeExit") ? $("regCeExit").value : "",
          ato_mon: isDirectional() ? (scopeVal === "pe" ? "pe" : "ce") : ($("regAtoMon") ? $("regAtoMon").value : "both"),
        };
        if (!window.confirm("Arm Kavach with these Register answers?")) return;
        try {
          const r = await api("/api/register", { method: "POST", body: JSON.stringify(body) });
          $("regMsg").textContent = r.text || r.error || "done";
          toastResult(r, !!r.ok);
          if (r.ok) {
            playAtoClip("register");
            invalidatePageCache();
          }
        } catch (err) {
          $("regMsg").textContent = err.message;
          toast(err.message, false);
        }
      };
    }

    if ($("doneYes")) {
      $("doneYes").onclick = async () => {
        if (!window.confirm("Complete Batman now? ATO will stop and deployment will be archived.")) return;
        try {
          const dres = await api("/api/complete", {
            method: "POST",
            body: JSON.stringify({ confirm: true }),
          });
          toastResult(dres, !!dres.ok);
          if (dres.ok) {
            playAtoClip("complete");
            invalidatePageCache();
            const pnlEl = $("statPnl");
            if (pnlEl) {
              pnlEl.textContent = fmtPnl(0);
              const base = pnlEl.classList.contains("home-pnl-strip-val")
                ? "home-pnl-strip-val"
                : (pnlEl.classList.contains("home-pnl-big") ? "home-pnl-big" : "");
              pnlEl.className = base;
            }
            const batEl = $("statBatmanPnl");
            if (batEl) {
              batEl.textContent = fmtPnl(0);
              batEl.className = "home-pnl-strip-val";
            }
          }
        } catch (err) {
          toast(err.message, false);
        }
      };
    }
  }

  function renderRegister(view, d) {
    window.__regQCount = Number(d.question_count || 18);
    const step = Number(d.ato_step || 50);
    const peHint = d.auto_protect_hint && d.auto_protect_hint.pe ? d.auto_protect_hint.pe : "";
    const ceHint = d.auto_protect_hint && d.auto_protect_hint.ce ? d.auto_protect_hint.ce : "";
    const note = d.book_note
      ? `<p class="reg-note">${esc(d.book_note)}</p>`
      : "";
    const armed = d.deployed
      ? `<p class="reg-note">Batman is already armed (${esc(d.file)}). Confirm will archive it and register again.</p>`
      : "";
    view.innerHTML = `
      <header class="page-head"><h3>${isDirectional() ? "REGISTER" : "REGISTER BATMAN"}</h3></header>
      ${note}${armed}
      <div class="card add-form reg-form">
        <section class="reg-setup reg-side dep-card-blue">
          <h4 class="sec-title">SETUP</h4>
          <div class="reg-top-grid reg-top-row">
          <div class="reg-q reg-complete-q">
            <div class="q-lab"><span class="q-label">Complete Previous Registration</span></div>
            <div class="field">
              <button type="button" class="btn btn-red reg-complete-btn" id="doneYes">CONFIRM COMPLETE</button>
            </div>
          </div>
          ${qBlock(1, "Select Mode", `
            <select id="regMode">
              <option value="paper" ${d.order_mode !== "live" ? "selected" : ""}>Paper</option>
              <option value="live" ${d.order_mode === "live" ? "selected" : ""}>Live</option>
            </select>`)}
          ${qBlock(2, "Expiry week", `
            <select id="regExpiry">
              ${(d.expiry_options || []).map((o) => `<option value="${esc(o.iso)}" ${d.expiry === o.iso ? "selected" : ""}>${esc(o.label)}</option>`).join("")}
            </select>`)}
          ${qBlock(3, "Register CE/PE", `
            <select id="regScope">
              ${isDirectional() ? "" : `<option value="both" ${d.reg_scope === "both" ? "selected" : ""}>Register both CE and PE</option>`}
              <option value="ce" ${(isDirectional() ? (d.reg_scope !== "pe") : (d.reg_scope === "ce")) ? "selected" : ""}>Register CE only</option>
              <option value="pe" ${d.reg_scope === "pe" ? "selected" : ""}>Register PE only</option>
            </select>`)}
          </div>
        </section>
        <div class="reg-grid">
          <section class="reg-side reg-side-ce dep-card-green" id="regCeBlock">
            <h4 class="sec-title">CE SIDE</h4>
            ${qBlock(10, "Core BUY leg", `<select id="regCeBuy">${optionHtml(d.ce_long, "", "Core BUY")}</select>`)}
            ${qBlock(11, "Margin Hedge", `<select id="regCeHedge">${optionHtml(d.ce_long, "", "None - not required")}</select>`)}
            ${isDirectional() ? "" : qBlock(12, "35% Dynamic Hedge", `<select id="regCeDyn">${optionHtml(d.ce_long, "", "None - not required")}</select>`)}
            ${qBlock(13, "SELL leg", `<select id="regCeSell">${optionHtml(d.ce_short, "", "SELL")}</select>`)}
            ${qBlock(14, "ATO strike", `
              <div class="ato-row">
                <select id="regCeAtoMode">
                  <option value="auto" selected>Auto</option>
                  <option value="custom">Custom strike</option>
                </select>
                <input id="regCeAto" type="number" placeholder="Auto ${esc(ceHint)}" />
              </div>`)}
            ${qBlock(15, "ATO Entry Level", `<input id="regCeEntry" type="number" placeholder="${esc(d.entry_placeholder || "NIFTY level")}" />`)}
            ${qBlock(16, "ATO Exit Level (Retrace)", `<input id="regCeExit" type="number" placeholder="${esc(d.exit_placeholder || "NIFTY level")}" />`)}
          </section>
          <section class="reg-side reg-side-pe dep-card-red" id="regPeBlock">
            <h4 class="sec-title">PE SIDE</h4>
            ${qBlock(3, "Core BUY leg", `<select id="regPeBuy">${optionHtml(d.pe_long, "", "Core BUY")}</select>`)}
            ${qBlock(4, "Margin Hedge", `<select id="regPeHedge">${optionHtml(d.pe_long, "", "None - not required")}</select>`)}
            ${isDirectional() ? "" : qBlock(5, "35% Dynamic Hedge", `<select id="regPeDyn">${optionHtml(d.pe_long, "", "None - not required")}</select>`)}
            ${qBlock(6, "SELL leg", `<select id="regPeSell">${optionHtml(d.pe_short, "", "SELL")}</select>`)}
            ${qBlock(7, "ATO strike", `
              <div class="ato-row">
                <select id="regPeAtoMode">
                  <option value="auto" selected>Auto</option>
                  <option value="custom">Custom strike</option>
                </select>
                <input id="regPeAto" type="number" placeholder="Auto ${esc(peHint)}" />
              </div>`)}
            ${qBlock(8, "ATO Entry Level", `<input id="regPeEntry" type="number" placeholder="${esc(d.entry_placeholder || "NIFTY level")}" />`)}
            ${qBlock(9, "ATO Exit Level (Retrace)", `<input id="regPeExit" type="number" placeholder="${esc(d.exit_placeholder || "NIFTY level")}" />`)}
          </section>
        </div>
        <div class="reg-top-grid">
          ${isDirectional() ? "" : qBlock(17, "ATO manage CE/PE", `
            <select id="regAtoMon">
              <option value="both" ${d.ato_mon === "both" ? "selected" : ""}>Manage both CE and PE</option>
              <option value="ce" ${d.ato_mon === "ce" ? "selected" : ""}>Manage CE only</option>
              <option value="pe" ${d.ato_mon === "pe" ? "selected" : ""}>Manage PE only</option>
            </select>`)}
          ${subLotHintHtml()}
          <div class="reg-action-row${d.deployed ? " reg-action-row-dual" : ""}">
            <button type="button" class="btn gold" id="regGo">REGISTER - ARM KAVACH</button>
            ${d.deployed ? `<button type="button" class="btn gold" id="regSyncLots">SYNC LOTS FROM BROKER</button>` : ""}
          </div>
        </div>
        <p class="sub" id="regMsg"></p>
      </div>`;
    wrapPageFrame(view);
    wireRegisterHandlers(d);
  }

  function demoOvernight() {
    try {
      return new URLSearchParams(location.search).get("demo") === "hedge";
    } catch (e) {
      return false;
    }
  }
  function overnightDemoData() {
    return {
      pending: true,
      denied: false,
      active: false,
      buy_time_ist: "15:20",
      ce_symbol: "NIFTY25SEP24950CE",
      ce_qty: 650,
      pe_symbol: "NIFTY25SEP23950PE",
      pe_qty: 650,
      sides: [
        { side: "CE", zone: "White", symbol: "NIFTY25SEP24950CE", strike: 24950, qty: 650, action: "standard_break_even" },
        { side: "PE", zone: "White", symbol: "NIFTY25SEP23950PE", strike: 23950, qty: 650, action: "standard_break_even" },
      ],
      summary: { status: "pending", denied: false },
      cycles: [
        { side: "CE", symbol: "NIFTY25SEP24950CE", qty: 650, entry_time: "15:20:11", entry_premium: 42.5, exit_time: "", exit_premium: null, impact: null, status: "open" },
        { side: "PE", symbol: "NIFTY25SEP23950PE", qty: 650, entry_time: "15:20:14", entry_premium: 38.0, exit_time: "", exit_premium: null, impact: null, status: "open" },
      ],
    };
  }

  async function render() {

    const view = $("view");
    if (!view) return;
    try {
      if (restorePageFromCache(view)) return;
      const navT0 = performance.now();
      let pageMeta = null;
      setBusy(true);
      if (page === "home") {
        const [s, a, b, sum, tok] = await Promise.all([
          api("/api/state"),
          api("/api/ato-status"),
          api("/api/buffer"),
          api("/api/trade-summary"),
          api("/api/token/status"),
        ]);
        if (demoOvernight()) s.overnight = overnightDemoData();
        applyChrome(s);
        const homePnl = s.deployment ? s.day_pnl : 0;
        const batmanPnl = s.deployment ? (s.batman_pnl != null ? s.batman_pnl : homePnl) : 0;
        const pnlCls = homePnl == null || homePnl === "" ? "" : clsPnl(homePnl);
        const batmanCls = batmanPnl == null || batmanPnl === "" ? "" : clsPnl(batmanPnl);
        const mode = String(s.order_mode || "paper").toUpperCase();
        const armed = !!s.deployment;
        const dhan = (tok && tok.dhan) || {};
        const kite = (tok && tok.zerodha) || {};
        const dhanLbl = dhan.present ? cap(dhan.status || dhan.health_class || "OK") : "Missing";
        const kiteLbl = kite.present ? "Set" : "Missing";
        const open = (sum && sum.open) || [];
        const openHtml = open.length
          ? open.map((leg) => `
              <div class="kv"><span>${esc(leg.side)} ATO</span><b>${esc(leg.symbol || "-")}</b></div>
              <div class="kv"><span>Strike</span><b>${esc(leg.protect_strike || "-")}</b></div>
              <div class="kv"><span>Status</span><b class="buy">OPEN</b></div>`).join("")
          : `<p class="sub home-empty">No open ATO protect leg.</p>`;
        const lvl = (v) => (v == null || v === "" ? "-" : esc(v));
        view.innerHTML = `
          <header class="page-head page-head-home">
            <div class="home-pnl-pair page-head-pnl">
              <div class="home-pnl-strip" title="Day PnL">
                <span class="home-pnl-strip-lab">DAY PNL</span>
                <span id="statPnl" class="home-pnl-strip-val ${pnlCls}">${esc(fmtPnl(homePnl))}</span>
              </div>
              <div class="home-pnl-strip home-pnl-strip-batman" title="${esc((s.batman_pnl_detail && (s.batman_pnl_detail.asof_label + ' | day ' + s.batman_pnl_detail.day_pnl + ' + ATO ' + s.batman_pnl_detail.ato_prev_day_rupees + ' + OH ' + s.batman_pnl_detail.overnight_prev_day_rupees)) || 'Day PnL + prior-day ATO + prior-day overnight')}">
                <span class="home-pnl-strip-lab">BATMAN PNL</span>
                <span id="statBatmanPnl" class="home-pnl-strip-val ${batmanCls}">${esc(fmtPnl(batmanPnl))}</span>
              </div>
            </div>
            <h3>HOME</h3>
            <div class="page-head-home-spacer" aria-hidden="true"></div>
          </header>
          <div class="home-dash">
            ${"" /* Tejas desk: hide master/ANAND copy peer card (backend copy APIs kept) */}

            <div class="home-body-grid">
              <div class="home-left-stack">
                <div class="card home-card dep-card-blue home-open-card">
                  <h4 class="sec-title">OPEN ATO</h4>
                  <div class="home-kv-wrap">${openHtml}</div>
                  <div class="kv"><span>Manage</span><b>${esc(a.manage || "-")}</b></div>
                  <div class="kv"><span>CE protect</span><b>${esc(a.ce_protect || s.ce_symbol || "N/A")}</b></div>
                  <div class="kv"><span>CE ATO size</span><b>${esc(fmtAtoFireSize(a.ce_ato_lots, a.ce_ato_qty))}</b></div>
                  <div class="kv"><span>PE protect</span><b>${esc(a.pe_protect || s.pe_symbol || "N/A")}</b></div>
                  <div class="kv"><span>PE ATO size</span><b>${esc(fmtAtoFireSize(a.pe_ato_lots, a.pe_ato_qty))}</b></div>
                </div>
                <div class="card home-card dep-card-green">
                  <h4 class="sec-title">ATO LEVELS</h4>
                  <div class="kv"><span>CE Entry</span><b>${lvl(b.ce_entry)}</b></div>
                  <div class="kv"><span>CE Exit</span><b>${lvl(b.ce_retrace)}</b></div>
                  <div class="kv"><span>PE Entry</span><b>${lvl(b.pe_entry)}</b></div>
                  <div class="kv"><span>PE Exit</span><b>${lvl(b.pe_retrace)}</b></div>
                </div>
                <div class="card home-card" id="hedgeBoxCard">
                  <h4 class="sec-title">OVERNIGHT HEDGE</h4>
                  ${(function(){
                    const o = s.overnight || {};
                    const sides = o.sides || [];
                    const sideHtml = sides.length
                      ? sides.map((x) => `<div class="kv"><span>${esc(x.side)} ${esc(x.zone || "")}</span><b>${esc(x.symbol || x.strike || "-")} x${esc(x.qty || 0)}</b></div>`).join("")
                      : (o.active
                          ? `<div class="kv"><span>CE overnight</span><b>${esc(o.ce_symbol || "-")} x${esc(o.ce_qty || 0)}</b></div>
                             <div class="kv"><span>PE overnight</span><b>${esc(o.pe_symbol || "-")} x${esc(o.pe_qty || 0)}</b></div>`
                          : `<p class="sub home-empty">No Hedge Box plan. Auto-buy 15:20 unless Deny.</p>`);
                    const denyBtn = o.pending
                      ? `<button type="button" class="btn danger oh-deny" id="hedgeBoxDeny">DENY EVENING BUY</button>`
                      : "";
                    const status = o.denied ? "DENIED" : (o.active ? "ON - ATO OFF UNTIL 09:20" : (o.pending ? ("AUTO-BUY " + (o.buy_time_ist || "15:20")) : "IDLE"));
                    return `<div class="kv"><span>Status</span><b>${esc(status)}</b></div>${sideHtml}${denyBtn}`;
                  })()}
                </div>
                <div class="card home-card ato-ready-card">
                  <h4 class="sec-title">ATO READINESS</h4>
                  <div class="kv"><span>Status</span><b class="${(function(){ const ar=s.ato_readiness||{}; const att=ar.attention_reasons||[]; const halt=att.some(r=>r==="pe_side_halted"||r==="ce_side_halted"||r==="all_sides_halted"); if(ar.armed && !halt) return "ok"; return "bad"; })()}">${esc((s.ato_readiness && s.ato_readiness.summary_line) || "-")}</b></div>
                  <div class="kv"><span>PE side</span><b class="${(s.ato_readiness && s.ato_readiness.sides && s.ato_readiness.sides.pe && s.ato_readiness.sides.pe.halted) ? "bad" : "ok"}">${esc((s.ato_readiness && s.ato_readiness.sides && s.ato_readiness.sides.pe && s.ato_readiness.sides.pe.label) || "-")}</b></div>
                  <div class="kv"><span>CE side</span><b class="${(s.ato_readiness && s.ato_readiness.sides && s.ato_readiness.sides.ce && s.ato_readiness.sides.ce.halted) ? "bad" : "ok"}">${esc((s.ato_readiness && s.ato_readiness.sides && s.ato_readiness.sides.ce && s.ato_readiness.sides.ce.label) || "-")}</b></div>
                  <div class="kv"><span>Feed age</span><b>${esc((s.ato_readiness && s.ato_readiness.feed && s.ato_readiness.feed.age_s != null) ? (Math.round(s.ato_readiness.feed.age_s) + "s") : "-")}</b></div>
                  <div class="kv"><span>Checked</span><b>${esc((s.ato_readiness && s.ato_readiness.checked_at) || "-")}</b></div>
                </div>
              </div>

              <div class="home-right-stack">
                <div class="card hist-wrap pos-card home-pos-card">
                  <div class="home-pos-toolbar" id="homeEmergencyExit">
                    <div class="home-pos-toolbar-copy">
                      <span class="home-pos-toolbar-kicker">Broker positions</span>
                      <span class="home-pos-toolbar-note">Exits every open position on broker immediately after you confirm.</span>
                    </div>
                    <button type="button" class="btn btn-red home-emergency-exit-btn" id="exitAllBtn">EMERGENCY EXIT</button>
                  </div>
                  <div class="home-pos-inner">
                    <div class="home-pos-side" aria-label="Positions">POSITIONS</div>
                    <div class="home-pos-table-wrap">
                      <table class="hist-table pos-table"><thead><tr>
                        <th>Symbol</th><th>Type</th><th>Qty</th><th>Avg</th><th>LTP</th><th>PnL</th>
                      </tr></thead><tbody id="posBody">${posRowsHtml(s.positions)}</tbody><tfoot id="posFoot">${posFootHtml(s.positions)}</tfoot></table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>`;
        finishPageRender("home", view, null, navT0);
        // Tejas: copy peer card hidden — skip paint/refresh on home
        wireEmergencyExitBtn();
        const denyBtn = $("hedgeBoxDeny");
        if (denyBtn) denyBtn.onclick = async () => {
          const r = await api("/api/hedge-box", { method: "POST", body: JSON.stringify({ action: "deny" }) });
          toast((r && r.text) || (r && r.error) || "Deny sent", r && r.ok);
          render();
        };
        return;
      }

      if (page === "positions") {
        const s = await api("/api/state");
        applyChrome(s);
        view.innerHTML = `
          <header class="page-head"><h3>POSITIONS</h3></header>
          ${positionsCardHtml(s.positions)}`;
        finishPageRender("positions", view, null, navT0);
        wireEmergencyExitBtn();
        return;
      }
      if (page === "status") {
        const stTxt = (await api("/api/status")).text;
        let ready = {};
        try { ready = await api("/api/ato-readiness"); } catch (_) { ready = {}; }
        const hard = (ready.hard_blocked_reasons || []).map((r) => (ready.reason_labels && ready.reason_labels[r]) || r);
        const svc = ready.services || {};
        const feed = ready.feed || {};
        view.innerHTML = `<header class="page-head"><h3>KAVACH STATUS</h3></header>
          <div class="card ato-ready-card">
            <h4 class="sec-title">ATO READINESS</h4>
            <div class="kv"><span>Summary</span><b class="${(function(){ const att=ready.attention_reasons||[]; const halt=att.some(r=>r==="pe_side_halted"||r==="ce_side_halted"||r==="all_sides_halted"); if(ready.armed && !halt) return "ok"; return "bad"; })()}">${esc(ready.summary_line || "-")}</b></div>
            <div class="kv"><span>PE side</span><b class="${(ready.sides && ready.sides.pe && ready.sides.pe.halted) ? "bad" : "ok"}">${esc((ready.sides && ready.sides.pe && ready.sides.pe.label) || "-")}</b></div>
            <div class="kv"><span>CE side</span><b class="${(ready.sides && ready.sides.ce && ready.sides.ce.halted) ? "bad" : "ok"}">${esc((ready.sides && ready.sides.ce && ready.sides.ce.label) || "-")}</b></div>
            <div class="kv"><span>Checked</span><b>${esc(ready.checked_at || "-")}</b></div>
            <div class="kv"><span>Feed</span><b>${esc((feed.ltp != null ? feed.ltp : "-") + " · age " + (feed.age_s != null ? Math.round(feed.age_s) + "s" : "?") + " · " + (feed.source || "") + " · " + (feed.collector || ""))}</b></div>
            <div class="kv"><span>Datafeedbot</span><b class="${svc.datafeedbot_active ? "ok" : "bad"}">${svc.datafeedbot_active ? "active" : "down"}</b></div>
            <div class="kv"><span>Kavach2</span><b class="${svc.kavach2_active ? "ok" : "bad"}">${svc.kavach2_active ? "active" : "down"}</b></div>
            <div class="kv"><span>Block reasons</span><b>${esc(hard.length ? hard.join("; ") : "none")}</b></div>
          </div>` + pre(stTxt);

      } else if (page === "overnight") {
        const o = demoOvernight() ? overnightDemoData() : ((await api("/api/hedge-box")) || {});
        const sum = o.summary || {};
        const sides = o.sides && o.sides.length ? o.sides : (sum.sides || []);
        const sideRows = sides.length
          ? sides.map((x, i) => `<tr>
              <td>${i + 1}</td>
              <td>${esc(x.side)}</td>
              <td>${esc(x.zone || "-")}</td>
              <td>${esc(x.symbol || x.strike || "-")}</td>
              <td>${esc(x.qty || 0)}</td>
              <td>${esc(x.action || "-")}</td>
            </tr>`).join("")
          : `<tr><td colspan="6">No evening plan yet. Kavach posts CE + PE here at 15:15.</td></tr>`;
        const status = o.denied || sum.denied ? "DENIED" : (o.active ? "HOLDING - ATO OFF UNTIL 09:20" : (o.pending ? ("PENDING - AUTO-BUY " + (o.buy_time_ist || "15:20")) : (sum.status || "IDLE")));
        const morning = sum.morning_kind
          ? (sum.morning_kind === "inside"
              ? "Open inside box - hedges exited - ATO on"
              : sum.morning_kind === "ce_out"
                ? "Open outside CE - CE ATO first - hedges exited"
                : sum.morning_kind === "pe_out"
                  ? "Open outside PE - PE ATO first - hedges exited"
                  : "Open outside both - ATO both sides - hedges exited")
          : "Waiting for 09:20";
        const denyBtn = o.pending
          ? `<button type="button" class="btn danger oh-deny" id="hedgeBoxDeny">DENY EVENING BUY</button>`
          : "";
        const cycles = o.cycles || [];
        let cycTotal = 0;
        let cycRs = 0;
        let cycN = 0;
        const sess = o.session_totals || {};
        const cycRows = cycles.map((b, i) => {
          const impact = b.impact == null || b.impact === "" ? null : Number(b.impact);
          if (impact != null && Number.isFinite(impact)) {
            cycTotal += impact;
            cycRs += Number(b.impact_rupees || 0);
            cycN += 1;
          }
          const num = b.session_cycle != null ? b.session_cycle : (i + 1);
          const when = b.entry_time || "-";
          return `<tr class="hist-buy">
            <td>${num}</td>
            <td>${esc(b.side)}</td>
            <td>${esc(b.symbol)}</td>
            <td>${esc(when)}</td>
            <td>${b.entry_premium != null && b.entry_premium !== "" ? fmt(b.entry_premium) : "-"}</td>
            <td>${esc(b.exit_time || "-")}</td>
            <td>${b.exit_premium != null && b.exit_premium !== "" ? fmt(b.exit_premium) : "-"}</td>
            <td class="${impact == null ? "" : clsPnl(impact)}">${impact == null ? (b.converted_to_ato ? "→ ATO" : "-") : ((impact >= 0 ? "+" : "") + fmt(impact))}</td>
          </tr>`;
        }).join("");
        const footImpact = sess.total_impact != null ? Number(sess.total_impact) : cycTotal;
        const footRs = sess.total_rupees != null ? Number(sess.total_rupees) : cycRs;
        const footN = sess.closed_count != null ? Number(sess.closed_count) : cycN;
        const cycFoot = (cycles.length || footN)
          ? `<tfoot><tr class="hist-total">
              <td colspan="7"><b>Session total since Register - ${footN} closed hedge cycle${footN === 1 ? "" : "s"} · ₹ ${fmt(footRs)}</b></td>
              <td class="${clsPnl(footImpact)}"><b>${(footImpact >= 0 ? "+" : "") + fmt(footImpact)}</b></td>
            </tr></tfoot>`
          : "";
        view.innerHTML = `
          <header class="page-head"><h3>OVERNIGHT HEDGE</h3><p class="sub">Right card keeps every buy/sell from Register until Batman Complete.</p></header>
          <div class="oh-page-grid">
            <div class="oh-left-stack">
              <div class="card home-card dep-card-blue">
                <h4 class="sec-title">TODAY</h4>
                <div class="kv"><span>Status</span><b>${esc(status)}</b></div>
                <div class="kv"><span>CE fill</span><b>${esc(o.ce_symbol || sum.ce_symbol || "-")} x${esc(o.ce_qty || sum.ce_qty || 0)}</b></div>
                <div class="kv"><span>PE fill</span><b>${esc(o.pe_symbol || sum.pe_symbol || "-")} x${esc(o.pe_qty || sum.pe_qty || 0)}</b></div>
                <div class="kv"><span>Morning</span><b>${esc(morning)}</b></div>
                <p class="sub">Kavach buys at 15:20 unless you Deny. Morning SELL is the evening fill only - extra lots and 35% dyn hedge stay.</p>
                ${denyBtn}
              </div>
              <div class="card hist-wrap">
                <table class="hist-table"><thead><tr>
                  <th>#</th><th>Side</th><th>Zone</th><th>Hedge</th><th>Qty</th><th>Action</th>
                </tr></thead><tbody>${sideRows}</tbody></table>
              </div>
            </div>
            <div class="card hist-wrap oh-summary-card">
              <table class="hist-table"><thead><tr>
                <th>#</th><th>Side</th><th>Hedge</th><th>Entry Time</th><th>Entry Px</th>
                <th>Exit Time</th><th>Exit Px</th><th>Impact</th>
              </tr></thead><tbody>${cycRows || '<tr><td colspan="8">No overnight hedge cycles since Register.</td></tr>'}</tbody>${cycFoot}</table>
            </div>
          </div>`;
        const denyBtnEl = $("hedgeBoxDeny");
        if (denyBtnEl) denyBtnEl.onclick = async () => {
          const r = await api("/api/hedge-box", { method: "POST", body: JSON.stringify({ action: "deny" }) });
          toast((r && r.text) || (r && r.error) || "Deny sent", r && r.ok);
          render();
        };

      } else if (page === "summary") {
        const d = await api("/api/trade-summary");
        const closed = d.closed || [];
        let total = 0;
        let totalRs = 0;
        const rows = closed
          .map((b, i) => {
            const impact = Number(b.impact || 0);
            total += impact;
            totalRs += Number(b.impact_rupees || 0);
            const num = b.session_cycle != null ? b.session_cycle : (i + 1);
            return `<tr class="hist-buy">
            <td>${num}</td>
            <td>${esc(b.side)}</td>
            <td>${esc(b.symbol)}</td>
            <td>${esc(b.entry_time)}</td>
            <td>${fmt(b.entry_premium)}</td>
            <td>${esc(b.exit_time)}</td>
            <td>${fmt(b.exit_premium)}</td>
            <td class="${clsPnl(impact)}">${(impact >= 0 ? "+" : "") + fmt(impact)}</td>
          </tr>`;
          })
          .join("");
        const n = closed.length;
        const foot = n
          ? `<tfoot><tr class="hist-total">
              <td colspan="7"><b>Session total since Register - ${n} ATO cycle${n === 1 ? "" : "s"} · ₹ ${fmt(d.total_rupees != null ? d.total_rupees : totalRs)}</b></td>
              <td class="${clsPnl(d.total_impact != null ? d.total_impact : total)}"><b>${((d.total_impact != null ? d.total_impact : total) >= 0 ? "+" : "") + fmt(d.total_impact != null ? d.total_impact : total)}</b></td>
            </tr></tfoot>`
          : "";
        view.innerHTML = `
          <header class="page-head"><h3>ATO SUMMARY</h3>
            <p class="sub">Cycles keep numbering from Register until Batman Complete (not reset each day).</p>
          </header>
          <div class="card hist-wrap">
            <table class="hist-table"><thead><tr>
              <th>#</th><th>Side</th><th>Protect</th><th>Entry Time</th><th>Entry Px</th>
              <th>Exit Time</th><th>Exit Px</th><th>Impact</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="8">No completed ATO cycles since Register.</td></tr>'}</tbody>${foot}</table>
          </div>`;


      } else if (page === "ato" || page === "buffer") {
        const [a, b, sum, p] = await Promise.all([
          api("/api/ato-status"),
          api("/api/buffer"),
          api("/api/trade-summary"),
          api("/api/poll"),
        ]);
        const open = sum.open || [];
        const openHtml = open.length
          ? open
              .map(
                (leg) => `<div class="ato-open-block">
              <div class="kv"><span>Side</span><b>${esc(leg.side)} ATO</b></div>
              <div class="kv"><span>Protect</span><b>${esc(leg.symbol)}</b></div>
              <div class="kv"><span>Strike</span><b>${esc(leg.protect_strike)}</b></div>
              <div class="kv"><span>Order</span><b>${esc(leg.order_id || "-")}</b></div>
              <div class="kv"><span>Status</span><b class="buy">OPEN</b></div>
            </div>`
              )
              .join("")
          : `<p class="sub ato-open-empty">No open ATO protect leg.</p>`;
        const pollOpts = (p.poll_options || [0, 1, 2, 3, 4, 5, 10, 15])
          .map((v) => `<option value="${v}" ${Number(p.poll_interval) === Number(v) ? "selected" : ""}>${v}s</option>`)
          .join("");
        const statusHtml = a.deployed === false
          ? `<p class="sub ato-open-empty">${esc(a.text || "No active deployment.")}</p>`
          : `<div class="ato-open-block ato-status-block">
              <div class="kv"><span>CE protect</span><b>${esc(a.ce_protect || "N/A")}</b></div>
              <div class="kv"><span>CE ATO size</span><b>${esc(fmtAtoFireSize(a.ce_ato_lots, a.ce_ato_qty))}</b></div>
              <div class="kv"><span>PE protect</span><b>${esc(a.pe_protect || "N/A")}</b></div>
              <div class="kv"><span>PE ATO size</span><b>${esc(fmtAtoFireSize(a.pe_ato_lots, a.pe_ato_qty))}</b></div>
              <div class="kv"><span>Manage</span><b>${esc(a.manage || "-")}</b></div>
              <button type="button" class="btn gold" id="atoSyncLots">SYNC LOTS FROM BROKER</button>
              <p class="sub" id="atoSyncLotsMsg"></p>
            </div>`;
        view.innerHTML = `
          <header class="page-head"><h3>ATO MANAGER</h3></header>
          <div class="ato-mgr">
            <div class="card add-form ato-mgr-buf dep-card-green">
              <h4 class="sec-title">BUFFER MANAGER</h4>
              <div class="ato-buf-fields">
                <div class="field"><label class="ato-lab" for="ce_entry">CE ENTRY</label><input id="ce_entry" type="number" inputmode="decimal" autocomplete="off" name="ato-ce-entry" placeholder="${esc(b.ce_entry_placeholder || "Nifty Level")}" value="${esc(b.ce_entry)}" /></div>
                <div class="field"><label class="ato-lab" for="ce_retrace">CE EXIT</label><input id="ce_retrace" type="number" inputmode="decimal" autocomplete="off" name="ato-ce-exit" placeholder="${esc(b.ce_retrace_placeholder || "Nifty Level")}" value="${esc(b.ce_retrace)}" /></div>
                <div class="field"><label class="ato-lab" for="pe_entry">PE ENTRY</label><input id="pe_entry" type="number" inputmode="decimal" autocomplete="off" name="ato-pe-entry" placeholder="${esc(b.pe_entry_placeholder || "Nifty Level")}" value="${esc(b.pe_entry)}" /></div>
                <div class="field"><label class="ato-lab" for="pe_retrace">PE EXIT</label><input id="pe_retrace" type="number" inputmode="decimal" autocomplete="off" name="ato-pe-exit" placeholder="${esc(b.pe_retrace_placeholder || "Nifty Level")}" value="${esc(b.pe_retrace)}" /></div>
              </div>
              <h4 class="sec-title ato-strike-head">ATO STRIKE</h4>
              <div class="ato-buf-fields ato-strike-fields">
                <div class="field">
                  <label class="ato-lab">CE ATO STRIKE</label>
                  <div class="ato-row ato-mgr-strike-row">
                    <select id="mgrCeAtoMode" title="CE ATO mode">
                      <option value="auto" ${String(b.ce_ato_mode || "auto") !== "custom" ? "selected" : ""}>Auto</option>
                      <option value="custom" ${String(b.ce_ato_mode || "") === "custom" ? "selected" : ""}>Custom strike</option>
                    </select>
                    <input id="mgrCeAto" type="number" data-auto="${esc(b.ce_ato_auto != null ? b.ce_ato_auto : "")}" placeholder="Auto ${esc(b.ce_ato_auto != null ? b.ce_ato_auto : "")}" value="${esc(b.ce_ato_strike || "")}" />
                  </div>
                </div>
                <div class="field">
                  <label class="ato-lab">PE ATO STRIKE</label>
                  <div class="ato-row ato-mgr-strike-row">
                    <select id="mgrPeAtoMode" title="PE ATO mode">
                      <option value="auto" ${String(b.pe_ato_mode || "auto") !== "custom" ? "selected" : ""}>Auto</option>
                      <option value="custom" ${String(b.pe_ato_mode || "") === "custom" ? "selected" : ""}>Custom strike</option>
                    </select>
                    <input id="mgrPeAto" type="number" data-auto="${esc(b.pe_ato_auto != null ? b.pe_ato_auto : "")}" placeholder="Auto ${esc(b.pe_ato_auto != null ? b.pe_ato_auto : "")}" value="${esc(b.pe_ato_strike || "")}" />
                  </div>
                </div>
              </div>
              <button type="button" class="btn btn-green" id="bufSave">SAVE</button>
              <p class="sub" id="bufMsg"></p>
            </div>
            <div class="card ato-mgr-status dep-card-blue">
              <h4 class="sec-title">ATO STATUS</h4>
              <div class="ato-status-wrap">${statusHtml}</div>
              <h4 class="sec-title ato-open-title">OPEN ATO</h4>
              <div class="ato-open-wrap">${openHtml}</div>
            </div>
            <div class="card add-form ato-mgr-poll dep-card-red">
              <h4 class="sec-title">POLL INTERVAL</h4>
              <div class="field"><select id="pollSel">${pollOpts}</select></div>
              <button type="button" class="btn btn-red" id="pollSave">SAVE</button>
              <p class="sub" id="pollMsg"></p>
            </div>
          </div>`;
        wireAtoMgrHandlers();
      } else if (page === "legs") {
        page = "home";
        document.querySelectorAll("[data-go]").forEach((el) => {
          el.classList.toggle("on", el.getAttribute("data-go") === "home");
        });
        return render();
      } else if (page === "poll") {
        page = "ato";
        document.querySelectorAll("[data-go]").forEach((el) => {
          el.classList.toggle("on", el.getAttribute("data-go") === "ato");
        });
        return render();

      } else if (page === "register") {
        const d = await api("/api/register");
        renderRegister(view, d);
        finishPageRender("register", view, { register: d }, navT0);
        return;
      } else if (page === "deploy" || page === "complete") {
        if (page === "complete") {
          page = "deploy";
          document.querySelectorAll("[data-go]").forEach((el) => {
            el.classList.toggle("on", el.getAttribute("data-go") === "deploy");
          });
        }
        const d = await api("/api/deploy");
        const expOpts = (d.expiry_options || []).map((o) => `<option value="${esc(o.iso)}" ${d.expiry === o.iso ? "selected" : ""}>${esc(o.label)}</option>`).join("");
        if (isDirectional()) {
          const cardHtml = (title, prefix, tone) => `
            <div class="card add-form ${tone} ${prefix === "depBull" ? "dep-exit-card dep-bull-card" : "dep-bear-card"}">
              <h4 class="sec-title">${title}</h4>
              <div class="dep-top-row">
                <div class="field dep-top-field"><label class="ato-lab">EXPIRY WEEK</label>
                  <select id="${prefix}Expiry">${expOpts}</select>
                </div>
                <div class="field dep-top-field"><label class="ato-lab">NIFTY CENTER</label><input id="${prefix}Level" type="number" placeholder="${esc(d.placeholder_level || "NIFTY level")}" value="${esc(d.level || "")}" /></div>
                <div class="field dep-top-field"><label class="ato-lab">LOTS</label><input id="${prefix}Lots" type="number" placeholder="Lots" value="${esc(d.lots || 1)}" /></div>
                <div class="dep-top-action">
                  <label class="ato-lab dep-top-action-label">&nbsp;</label>
                  <button type="button" class="btn ${prefix === "depBull" ? "btn-green-outline" : "btn-red"}" id="${prefix}Prev" style="${prefix === "depBull" ? "" : "width:auto"}">PREVIEW LEGS</button>
                </div>
              </div>
              <div class="dep-out" id="${prefix}Out"></div>
              <div class="row2 dep-confirm-row">
                <button type="button" class="btn ${prefix === "depBull" ? "btn-green" : "btn-red"}" id="${prefix}Go">CONFIRM THEN SYNC</button>
                <button type="button" class="btn ${prefix === "depBull" ? "btn-green" : "btn-red"}" id="${prefix}GoReg">CONFIRM DEPLOY AND REGISTER</button>
              </div>
              <p class="sub" id="${prefix}Msg"></p>
            </div>`;
          view.innerHTML = `
            <header class="page-head"><h3>DEPLOY</h3></header>
            <div class="dep-complete dep-complete-dir">
              ${cardHtml("Bullish View", "depBull", "dep-card-green")}
              ${cardHtml("Bearish View", "depBear", "dep-card-red")}
            </div>`;
        } else {
          view.innerHTML = `
            <header class="page-head"><h3>DEPLOY BATMAN 2.0</h3></header>
            <div class="dep-complete">
              <div class="card add-form dep-card-green">
                <h4 class="sec-title">DEPLOY BATMAN 2.0</h4>
                <p class="sub dep-card-sub">Preview the legs below, then confirm to place them.</p>
                <div class="dep-top-row">
                  <div class="field dep-top-field"><label class="ato-lab">EXPIRY WEEK</label>
                    <select id="depExpiry">${expOpts}</select>
                  </div>
                  <div class="field dep-top-field"><label class="ato-lab">NIFTY CENTER</label><input id="depLevel" type="number" placeholder="${esc(d.placeholder_level || "NIFTY level")}" value="${esc(d.level || "")}" /></div>
                  <div class="field dep-top-field"><label class="ato-lab">LOTS</label><input id="depLots" type="number" placeholder="Lots" value="${esc(d.lots || 1)}" /></div>
                  <div class="dep-top-action">
                    <label class="ato-lab dep-top-action-label">&nbsp;</label>
                    <button type="button" class="btn btn-green-outline" id="depPrev">PREVIEW LEGS</button>
                  </div>
                  ${subLotHintHtml()}
                </div>
                <div class="dep-out" id="depOut"></div>
                <div class="row2 dep-confirm-row">
                  <button type="button" class="btn btn-green" id="depGo">CONFIRM DEPLOY ONLY</button>
                  <button type="button" class="btn btn-green" id="depGoReg">CONFIRM DEPLOY AND REGISTER</button>
                </div>
                <p class="sub" id="depMsg"></p>
              </div>
              <div class="card add-form dep-card-red dep-exit-card">
                <h4 class="sec-title">EMERGENCY EXIT ALL POSITIONS</h4>
                <div class="complete-pre" id="exitMsg">Exits every open position on broker immediately after you confirm.</div>
                <button type="button" class="btn btn-red" id="exitAllBtn">CONFIRM EXIT</button>
              </div>
            </div>`;
        }
        wireDeployHandlers();
      } else if (page === "payoff") {
        const d = await api("/api/payoff");
        const legs = d.legs || [];
        const pts = d.points || [];
        const spot = d.spot;
        const be = (d.breakevens || []).map((x) => Number(x).toFixed(0)).join(" · ") || "-";
        const maxP = d.max_profit == null ? "-" : fmtPnl(d.max_profit);
        const maxL = d.max_loss == null ? "-" : fmtPnl(d.max_loss);
        const atm = d.atm;
        view.innerHTML = `
          <header class="page-head payoff-head"><h3>PAYOFF</h3></header>
          <div class="payoff-page">
            <div class="payoff-stats">
              <div class="card home-chip-card"><kbd>SPOT</kbd><b>${spot == null ? "-" : Number(spot).toFixed(2)}</b></div>
              <div class="card home-chip-card"><kbd>ATM</kbd><b>${atm == null ? "-" : Number(atm).toFixed(0)}</b></div>
              <div class="card home-chip-card"><kbd>MAX PROFIT*</kbd><b class="buy">${esc(maxP)}</b></div>
              <div class="card home-chip-card"><kbd>MAX LOSS*</kbd><b class="sell">${esc(maxL)}</b></div>
              <div class="card home-chip-card payoff-be"><kbd>BREAKEVENS*</kbd><b>${esc(be)}</b></div>
            </div>
            <div class="card payoff-chart-card">
              <div class="payoff-chart-top">
                <h4 class="sec-title">EXPIRY PAYOFF</h4>
                <span class="sub payoff-sub">ATM ± 10 strikes · spot at center</span>
              </div>
              <div class="payoff-chart-wrap">
                <svg id="payoffSvg" class="payoff-svg" viewBox="0 0 1000 420" preserveAspectRatio="none" role="img" aria-label="Payoff chart"></svg>
              </div>
            </div>
          </div>`;
        pageMeta = { points: pts, spot: spot, atm: atm, step: d.step || 50 };
        wirePayoffChart(pageMeta);

      } else if (page === "copytrades") {
        // Tejas desk: COPY TRADES logs hidden in UI; backend /api/copy/* unchanged
        page = "home";
        setBusy(false);
        return render();
      } else if (page === "alerts") {
        const d = await api("/api/alerts?limit=200");
        const rows = (d.alerts || []).slice().reverse();
        const body = rows.length
          ? rows.map((a) => {
              const sev = String(a.severity || "orange");
              return `<tr>
                <td>${esc(a.ts || "")}</td>
                <td class="sev-${esc(sev)}">${esc(a.category || "")}</td>
                <td>${esc(a.alert || "")}</td>
                <td class="alert-log">${esc(a.log || "")}</td>
              </tr>`;
            }).join("")
          : `<tr><td colspan="4">No alerts yet today.</td></tr>`;
        view.innerHTML = `
          <header class="page-head"><h3>ALERT LOGS</h3></header>
          <div class="card hist-wrap">
            <table class="hist-table"><thead><tr>
              <th>Time</th><th>Category</th><th>Alert</th><th>Full log</th>
            </tr></thead><tbody>${body}</tbody></table>
          </div>`;
      } else if (page === "token") {
        await renderTokenPage(view);
      }
      if (page === "ato" || page === "buffer") page = "ato";
      if (page === "status" || page === "summary" || page === "overnight" || page === "ato" || page === "deploy" || page === "payoff" || page === "token" || page === "alerts" || page === "copytrades" || page === "positions") {
        finishPageRender(page, view, pageMeta, navT0);
      } else {
        playViewIn(view);
      }
    } catch (err) {
      view.innerHTML = `<p class="sub" style="color:var(--sell)">${esc(err.message)}</p>`;
    } finally {
      setBusy(false);
    }
  }

  function closeMenu() {
    const side = $("sideNav") || document.querySelector(".side");
    const scrim = $("navScrim");
    const btn = $("menuBtn");
    if (side) side.classList.remove("open");
    if (scrim) scrim.classList.remove("on");
    document.body.classList.remove("nav-open");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function openMenu() {
    const side = $("sideNav") || document.querySelector(".side");
    const scrim = $("navScrim");
    const btn = $("menuBtn");
    if (side) side.classList.add("open");
    if (scrim) scrim.classList.add("on");
    document.body.classList.add("nav-open");
    if (btn) btn.setAttribute("aria-expanded", "true");
  }

  function toggleMenu() {
    const side = $("sideNav") || document.querySelector(".side");
    if (side && side.classList.contains("open")) closeMenu();
    else openMenu();
  }

  function bindGo(root) {
    root.querySelectorAll("[data-go]").forEach((el) => {
      el.addEventListener("click", () => go(el.dataset.go));
    });
  }

  let brokerAcctPainted = false;


  function syncBrokerSubStatus(d) {
    const statusEl = $("brokerAcctStatus");
    if (!statusEl) return;
    const raw = String((d && (d.status || d.ui_status)) || "").toUpperCase().trim();
    statusEl.classList.remove("is-active", "is-inactive", "is-sub-active", "is-sub-blocked", "is-sub-warn");
    if (raw === "ACTIVE") {
      statusEl.classList.add("is-sub-active");
      statusEl.title = "Subscription ACTIVE";
      statusEl.setAttribute("aria-label", "Subscription active");
    } else if (raw === "BLOCKED" || raw === "NOT_AUTHORIZED" || raw === "SERVER_NOT_AUTHORIZED" || raw === "USER_NOT_REGISTERED") {
      statusEl.classList.add("is-sub-blocked");
      statusEl.title = "Subscription BLOCKED";
      statusEl.setAttribute("aria-label", "Subscription blocked");
    } else if (raw === "EXPIRED" || raw === "EXPIRING_SOON" || raw.includes("EXPIR")) {
      statusEl.classList.add("is-sub-warn");
      statusEl.title = "Subscription " + raw.replace(/_/g, " ");
      statusEl.setAttribute("aria-label", "Subscription warning");
    } else if (raw) {
      statusEl.classList.add("is-sub-blocked");
      statusEl.title = "Subscription " + raw.replace(/_/g, " ");
      statusEl.setAttribute("aria-label", "Subscription blocked");
    } else {
      statusEl.classList.add("is-sub-warn");
      statusEl.title = "Subscription status unknown";
      statusEl.setAttribute("aria-label", "Subscription status unknown");
    }
  }

  function wireBrokerProfile() {
    const profileBtn = $("brokerAcctProfile");
    if (!profileBtn || profileBtn.dataset.wired === "1") return;
    profileBtn.dataset.wired = "1";
    profileBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openUserDetailPanel();
    });
  }

  function paintBrokerAcct(me) {
    const z = (me && me.zerodha) || {};
    const d = (me && me.dhan) || {};
    const kiteId = String(z.user_id || "").trim();
    const kiteName = String(z.name || "").trim();
    const kiteOn = !!(z.present || kiteId || kiteName);
    const dhanId = String(d.client_id || d.client_code || "").trim();
    const dhanName = String(d.name || "").trim();
    const dhanOn = !!d.present || !!dhanId;
    const main = String((me && me.main_broker) || "").toLowerCase();
    const useKite = main === "zerodha" || (main !== "dhan" && (kiteOn || !dhanOn));
    const broker = useKite ? "Kite" : "Dhan";
    const name = useKite ? kiteName : dhanName;
    const uid = useKite ? (kiteId || "STE992") : "";
    const active = useKite ? kiteOn : dhanOn;
    const dir = isDirectional();
    const wrap = $("brokerAcct");
    const nameEl = $("brokerAcctName");
    const brokerEl = $("brokerAcctBroker");
    const idEl = $("brokerAcctId");
    const sep1 = $("brokerAcctSep1");
    const sep2 = $("brokerAcctSep2");
    const statusEl = $("brokerAcctStatus");
    if (!wrap || !nameEl || !idEl) return;
    // Directional: logo → name → | → subscription glow (no broker / client id).
    // Batman: keep logo → status → broker → name → || → client id.
    nameEl.textContent = name || (dir ? broker : "");
    nameEl.style.display = (name || dir) ? "" : "none";
    if (brokerEl) {
      brokerEl.textContent = broker;
      brokerEl.style.display = dir ? "none" : "";
    }
    idEl.textContent = uid || "-";
    idEl.style.display = dir ? "none" : (uid ? "" : "none");
    if (sep1) sep1.style.display = dir ? (name || broker ? "" : "none") : (name && broker ? "" : "none");
    if (sep2) sep2.style.display = dir ? "none" : (broker && uid ? "" : "none");
    if (statusEl) {
      statusEl.style.display = "";
      // Subscription pulse owned by syncBrokerSubStatus(); broker connect is fallback only.
      if (!(window.__sub && (window.__sub.status || window.__sub.ui_status))) {
        statusEl.classList.remove("is-sub-active", "is-sub-blocked", "is-sub-warn");
        statusEl.classList.toggle("is-active", active);
        statusEl.classList.toggle("is-inactive", !active);
        statusEl.title = active ? (broker + " connected") : (broker + " off");
        statusEl.setAttribute("aria-label", active ? "Connected" : "Disconnected");
      }
    }
    if (window.__sub) syncBrokerSubStatus(window.__sub);
    wireBrokerProfile();
    wrap.hidden = false;
    wrap.classList.toggle("broker-acct-dir", !!dir);
    brokerAcctPainted = true;
  }

  async function loadBrokerAcct() {
    try {
      const me = await api("/api/me");
      paintBrokerAcct(me);
    } catch (_) {}
  }

  function showDesk() {
    $("login").classList.add("hidden");
    applyStrategyChrome();
    wireUserDetailUi();
    refreshSubscriptionChip();
    $("desk").classList.remove("hidden");
    $("desk").classList.remove("desk-in");
    void $("desk").offsetWidth;
    $("desk").classList.add("desk-in");
    $("view").innerHTML = skeleton();
    loadBrokerAcct();
    go("home");
    startWs();
    if (!clockTimer) {
      tickClock();
      clockTimer = setInterval(tickClock, 1000);
    }
  }


  function applyTheme(mode) {
    const t = mode === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("kavach-theme", t); } catch (_) {}
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "dark" ? "#0F172A" : "#7C6AE8");
    document.querySelectorAll(".theme-switch input").forEach((inp) => {
      inp.checked = t === "dark";
      const lab = inp.closest(".theme-switch");
      if (!lab) return;
      lab.setAttribute("aria-checked", t === "dark" ? "true" : "false");
      lab.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
      lab.title = t === "dark" ? "Dark theme" : "Light theme";
    });
    try {
      if (window.StratGlobe && typeof window.StratGlobe.applyTheme === "function") {
        window.StratGlobe.applyTheme(t);
      }
    } catch (_) {}
  }
  function bindThemeToggles() {
    document.querySelectorAll(".theme-switch input").forEach((inp) => {
      if (inp.dataset.bound) return;
      inp.dataset.bound = "1";
      inp.addEventListener("change", () => {
        applyTheme(inp.checked ? "dark" : "light");
      });
    });
    let start = document.documentElement.getAttribute("data-theme") || "light";
    try {
      const saved = localStorage.getItem("kavach-theme");
      if (saved === "dark" || saved === "light") start = saved;
    } catch (_) {}
    applyTheme(start);
  }
  bindThemeToggles();

  function playLockThenLogin() {
    $("desk").classList.add("hidden");
    const scene = $("lockScene");
    const logo = document.querySelector(".logo-hero");
    if (logo) logo.classList.remove("drop-in");
    scene.classList.remove("is-on");
    void scene.offsetWidth;
    scene.classList.add("is-on");
    scene.setAttribute("aria-hidden", "false");
    const wait = reduceMotion() ? 700 : 3400;
    setTimeout(() => {
      scene.classList.remove("is-on");
      scene.setAttribute("aria-hidden", "true");
      if (logo) {
        void logo.offsetWidth;
        logo.classList.add("drop-in");
      }
      $("login").classList.remove("hidden");
      $("pw").value = "";
      $("loginErr").textContent = "";
    }, wait);
  }

  function startWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws;
    const connect = () => {
      try { ws = new WebSocket(proto + "://" + location.host + "/ws/state"); }
      catch (_) { setTimeout(connect, 2000); return; }
      ws.onmessage = (ev) => {
        try { applyChrome(JSON.parse(ev.data)); } catch (_) {}
      };
      ws.onclose = () => setTimeout(connect, 2000);
      ws.onerror = () => { try { ws.close(); } catch (_) {} };
    };
    connect();
  }

  document.querySelectorAll(".nav, .tabbar span").forEach((el) => {
    if (el.id === "logoutBtn") return;
    if (el.hasAttribute("data-menu")) {
      el.addEventListener("click", () => toggleMenu());
      return;
    }
    el.addEventListener("click", () => go(el.dataset.go));
  });
  const menuBtn = $("menuBtn");
  if (menuBtn) menuBtn.addEventListener("click", () => toggleMenu());
  const navScrim = $("navScrim");
  if (navScrim) navScrim.addEventListener("click", () => closeMenu());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMenu();
  });


  async function flipRun(wantResume) {
    toggling = true;
    try {
      setBusy(true);
      const r = await api(wantResume ? "/api/resume" : "/api/pause", { method: "POST", body: "{}" });
      if (!r.ok) throw new Error(r.error || "failed");
      live.paused = !wantResume;
      applyChrome(live);
      if (wantResume) setGoNotice("WEAPONS HOT", "ok");
      else setGoNotice("WEAPONS COLD", "paused");
    } catch (err) {
      const run = $("runToggle");
      if (run) run.checked = !wantResume;
      applyChrome(live);
      toast(err.message, false);
    } finally {
      toggling = false;
      setBusy(false);
    }
  }

  async function flipHedge(on) {
    toggling = true;
    try {
      setBusy(true);
      const r = await api("/api/dyn-hedge", { method: "POST", body: JSON.stringify({ enabled: on }) });
      if (r.ok === false) throw new Error(r.error || "failed");
      live.dyn_hedge = on;
      applyChrome(live);
      toastResult(r, true);
    } catch (err) {
      const h = $("hedgeToggle");
      if (h) h.checked = !on;
      applyChrome(live);
      toast(err.message, false);
    } finally {
      toggling = false;
      setBusy(false);
    }
  }

  function paintCopyChrome(d) {
    if (!d) return;
    const role = String(d.role || "");
    // Master needs board allow_copy and the AMAN channel. Child only needs allow_copy.
    const boardOn = !!d.allow_copy;
    const channelOn = role === "master" ? boardOn && d.channel_on !== false : boardOn;
    const tog = $("copyToggle");
    const lbl = $("copyLbl");
    if (!copyUiBusy && tog && document.activeElement !== tog) tog.checked = boardOn;
    if (lbl) lbl.textContent = boardOn ? "COPY ON" : "COPY OFF";
    const dot = $("copyLinkDot");
    const name = $("copyPeerName");
    if (name && d.peer_name) name.textContent = d.peer_name;
    if (dot) {
      dot.className = "broker-acct-status " + (d.link_ok ? "is-active" : "is-inactive");
    }
    const channelTog = $("copyChannelToggle");
    const channelLbl = $("copyChannelLbl");
    const channelLabel = $("copyChannelLabel");
    if (!copyUiBusy && channelTog && document.activeElement !== channelTog) channelTog.checked = channelOn;
    if (channelLbl) channelLbl.textContent = channelOn ? "ON" : "OFF";
    if (channelLabel) {
      channelLabel.classList.toggle("is-on", channelOn);
      channelLabel.classList.toggle("is-off", !channelOn);
    }
    const card = $("copyLinkCard");
    if (card) {
      card.classList.toggle("is-on", channelOn);
      card.classList.toggle("is-off", !channelOn);
    }
    const qtyRow = $("copyQtyRow");
    const qty = $("copyQty");
    const qtyLabel = $("copyQtyLabel");
    if (qtyRow) qtyRow.hidden = false;
    if (qtyLabel) qtyLabel.textContent = "Allowed lots";
    if (!copyUiBusy && qty && document.activeElement !== qty && !qty.dataset.dirty) {
      qty.value = d.qty_mode === "override" && d.qty_override ? String(d.qty_override) : "";
    }
    const body = $("copyLogBody");
    if (body && page === "copytrades") {
      const rows = (d.logs || []).slice().reverse();
      body.innerHTML = rows.length
        ? rows.map((row) => `<tr>
            <td>${esc(row.ts || "")}</td>
            <td>${esc(row.action || "")}</td>
            <td>${esc(row.summary || "")}</td>
            <td>${esc(row.strategy || "")}</td>
            <td>${esc(row.qty == null ? "" : row.qty)}</td>
            <td>${row.accepted ? "Yes" : "No"}</td>
            <td>${esc(row.detail || "")}</td>
          </tr>`).join("")
        : `<tr><td colspan="7">No copy rows yet.</td></tr>`;
    }
  }

  async function saveCopyQtyFromInput() {
    const qty = $("copyQty");
    if (!qty) return;
    const raw = String(qty.value || "").trim();
    const n = raw === "" ? 0 : Number(raw);
    if (raw !== "" && (!Number.isFinite(n) || n < 1)) {
      toast("Enter a whole number of lots, or clear for Same", false);
      return;
    }
    const body = raw
      ? { qty_mode: "override", qty_override: Math.floor(n) }
      : { qty_mode: "same", qty_override: 0 };
    const master = String((window.__copyDesk || {}).role || "") === "master";
    copyUiBusy = true;
    try {
      window.__copyDesk = await api("/api/copy/desk", { method: "POST", body: JSON.stringify(body) });
      qty.dataset.dirty = "";
      paintCopyChrome(window.__copyDesk);
      if (!raw) toast(master ? "Allowed lots follow the trade" : "Child lots follow the allowed amount", true);
      else toast((master ? "Allowed lots set to " : "Child lots set to ") + Math.floor(n), true);
    } catch (err) {
      toast(err.message, false);
    } finally {
      copyUiBusy = false;
    }
  }

  function paintCopyCard(d) {
    paintCopyChrome(d);
    const qty = $("copyQty");
    if (qty && qty.dataset.wired !== "1") {
      qty.dataset.wired = "1";
      qty.addEventListener("input", () => {
        qty.dataset.dirty = "1";
      });
      qty.addEventListener("change", () => {
        void saveCopyQtyFromInput();
      });
      qty.addEventListener("blur", () => {
        if (qty.dataset.dirty === "1") void saveCopyQtyFromInput();
      });
    }
    const channel = $("copyChannelToggle");
    if (channel && channel.dataset.wired !== "1") {
      channel.dataset.wired = "1";
      channel.addEventListener("change", async () => {
        const on = !!channel.checked;
        const master = String((window.__copyDesk || {}).role || "") === "master";
        // Master card arms both the board and this child so poll/deploy actually copy.
        const body = master ? { allow_copy: on, channel_on: on } : { allow_copy: on };
        copyUiBusy = true;
        try {
          window.__copyDesk = await api("/api/copy/desk", { method: "POST", body: JSON.stringify(body) });
          paintCopyChrome(window.__copyDesk);
          const who = master ? "Aman" : "Copy";
          toast(on ? who + " copy on" : who + " copy off", true);
        } catch (err) {
          channel.checked = !on;
          toast(err.message, false);
        } finally {
          copyUiBusy = false;
        }
      });
    }
  }

  async function refreshCopyDesk() {
    if (copyUiBusy) return;
    const qty = $("copyQty");
    if (qty && (document.activeElement === qty || qty.dataset.dirty === "1")) return;
    const channel = $("copyChannelToggle");
    const header = $("copyToggle");
    if (document.activeElement === channel || document.activeElement === header) return;
    try {
      const d = await api("/api/copy/desk");
      if (copyUiBusy) return;
      if (qty && (document.activeElement === qty || qty.dataset.dirty === "1")) return;
      window.__copyDesk = d;
      paintCopyChrome(d);
    } catch (_) {}
  }

  async function flipCopy(on) {
    const tog = $("copyToggle");
    copyUiBusy = true;
    try {
      // Keep the AMAN channel armed with the header board switch.
      window.__copyDesk = await api("/api/copy/desk", {
        method: "POST",
        body: JSON.stringify(on ? { allow_copy: true, channel_on: true } : { allow_copy: false }),
      });
      paintCopyChrome(window.__copyDesk);
      toast(on ? "Copy on" : "Copy off", true);
    } catch (err) {
      if (tog) tog.checked = !on;
      toast(err.message, false);
    } finally {
      copyUiBusy = false;
    }
  }

  $("runToggle").addEventListener("change", (e) => {
    flipRun(!!e.target.checked);
  });
  $("hedgeToggle").addEventListener("change", (e) => {
    flipHedge(!!e.target.checked);
  });
  const copyToggle = $("copyToggle");
  if (copyToggle) {
    copyToggle.addEventListener("change", (e) => {
      flipCopy(!!e.target.checked);
    });
  }
  refreshCopyDesk();
  setInterval(refreshCopyDesk, 3000);
  bindPnlExitControls();

  $("logoutBtn").addEventListener("click", async () => {
    closeMenu();
    invalidatePageCache();
    setStrategyMode("");
    hideStrategyPick();
    try {
      await api("/api/logout", { method: "POST", body: "{}" });
    } catch (_) {}
    playLockThenLogin();
  });

  $("loginBtn").addEventListener("click", async () => {
    toast("");
    $("loginErr").textContent = "";
    try {
      setBusy(true);
      await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ password: $("pw").value }),
      });
      unlockAudio();
      if (!_welcomePlayed) { _welcomePlayed = true; playAtoClip("welcome"); }
      showStrategyPick();
    } catch (e) {
      $("loginErr").textContent = e.message;
    } finally {
      setBusy(false);
    }
  });
  $("pw").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("loginBtn").click();
  });

  const viewEl = $("view");
  if (viewEl) {
    viewEl.addEventListener(
      "scroll",
      () => {
        const main = document.querySelector(".main");
        if (!main || reduceMotion()) return;
        main.style.setProperty("--paray", viewEl.scrollTop * 0.18 + "px");
      },
      { passive: true }
    );
  }

  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches
      || window.navigator.standalone === true;
  }

  function isIos() {
    const ua = navigator.userAgent || "";
    return /iPhone|iPad|iPod/i.test(ua)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function setInstallHint(text) {
    const el = $("installHint");
    if (el) el.textContent = text || "";
  }

  if (isStandalone()) {
    document.body.classList.add("standalone");
    const hint = $("installHint");
    const btn = $("installBtn");
    if (hint) hint.textContent = "Running as app";
    if (btn) btn.classList.add("hidden");
  } else if (isIos()) {
    setInstallHint("iPhone / iPad: tap Share, then Add to Home Screen. Open that icon to hide the address bar.");
  } else if (location.protocol !== "https:") {
    setInstallHint("Open the HTTPS link once, then tap Install as app. That opens Kavach with no address bar.");
  } else {
    setInstallHint("Install as app to hide the address bar. Chrome menu → Install Kavach, or tap the button below.");
    const btn = $("installBtn");
    if (btn) btn.classList.remove("hidden");
  }

  if (location.protocol === "https:" && "serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }

  let deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    const btn = $("installBtn");
    if (btn && !isStandalone()) btn.classList.remove("hidden");
    setInstallHint("Tap Install as app. Kavach will open in its own window with no address bar.");
  });

  const installBtn = $("installBtn");
  if (installBtn) {
    installBtn.addEventListener("click", async () => {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        try { await deferredPrompt.userChoice; } catch (e) {}
        deferredPrompt = null;
        installBtn.classList.add("hidden");
        return;
      }
      if (isIos()) {
        setInstallHint("Safari only: tap the Share button, then Add to Home Screen.");
        return;
      }
      setInstallHint("Use the Chrome menu (three dots) → Install Kavach / Add to Home Screen.");
    });
  }

  window.addEventListener("appinstalled", () => {
    const btn = $("installBtn");
    if (btn) btn.classList.add("hidden");
    setInstallHint("Installed. Open Kavach from the app icon - no address bar.");
  });

  api("/api/me")
    .then((me) => {
      paintBrokerAcct(me);
      let prior = "";
      try {
        prior = String(sessionStorage.getItem("kavach-strategy") || "");
      } catch (_) {}
      if (prior === "batman" || prior === "directional") {
        setStrategyMode(prior);
        showDesk();
      } else {
        setStrategyMode("");
        showStrategyPick();
      }
    })
    .catch(() => {});
})();
