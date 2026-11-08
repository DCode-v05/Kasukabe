const TOKEN_KEY = "kasukabe_operator_token";
const REFRESH_INTERVAL_MS = 8000;

function esc(value) {
  const div = document.createElement("div");
  div.textContent = value === null || value === undefined ? "" : String(value);
  return div.innerHTML;
}

function splitNames(value) {
  const names = String(value || "")
    .split(",")
    .map((name) => name.trim())
    .filter((name) => name.length > 0);
  return names.length ? names : null;
}

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

function setConnectionDot(state) {
  const dot = document.getElementById("connection-dot");
  dot.className = "dot dot-" + state;
}

function showBanner(message, kind) {
  const region = document.getElementById("banner-region");
  const banner = document.createElement("div");
  banner.className = "banner banner-" + (kind || "error");
  banner.textContent = message;
  region.innerHTML = "";
  region.appendChild(banner);
  window.setTimeout(() => {
    if (region.contains(banner)) region.removeChild(banner);
  }, 6000);
}

async function apiFetch(path, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers, {
    Authorization: "Bearer " + getToken(),
  });
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (err) {
    setConnectionDot("bad");
    showBanner("Console unreachable: " + err.message);
    throw err;
  }

  if (response.status === 401) {
    setConnectionDot("bad");
    showBanner("Operator token rejected - check the token above.");
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    setConnectionDot(response.status === 503 ? "bad" : "ok");
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) {
    }
    showBanner("Request failed (" + response.status + "): " + detail);
    throw new Error(detail);
  }

  setConnectionDot("ok");
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

function listItem(innerHtml) {
  const div = document.createElement("div");
  div.className = "list-item";
  div.innerHTML = innerHtml;
  return div;
}

function renderList(containerId, items, renderFn, emptyMessage) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (!items || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = emptyMessage || "nothing pending";
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => container.appendChild(renderFn(item)));
}

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    });
  });
}

function initTokenBox() {
  const input = document.getElementById("operator-token");
  input.value = getToken();
  document.getElementById("token-save").addEventListener("click", () => {
    setToken(input.value.trim());
    showBanner("Token saved for this browser session.", "ok");
    refreshAll();
  });
}

const ARM_TIMEOUT_MS = 5000;

function confirmable(button, run) {
  let armed = false;
  let timer = null;
  const resting = button.textContent;

  function disarm() {
    armed = false;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    button.textContent = resting;
    button.classList.remove("armed");
  }

  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = "Confirm: " + resting;
      button.classList.add("armed");
      timer = window.setTimeout(disarm, ARM_TIMEOUT_MS);
      return;
    }
    disarm();
    await run();
  });
}

function renderPetition(p) {
  const item = listItem(`
    <div class="list-item-head">
      <span class="list-item-title">${esc(p.title)}</span>
      <span class="badge badge-pending">${esc(p.status)}</span>
    </div>
    <div class="list-item-meta">filed by ${esc(p.filed_by_agent_id)} · ${esc(p.tile_count_requested)} tiles</div>
    <div class="list-item-body">${esc(p.description)}</div>
    <div class="form-row">
      <select data-role="edge">
        <option value="south">south</option>
        <option value="east">east</option>
      </select>
      <input data-role="depth" type="number" min="1" value="${esc(p.tile_count_requested) || 1}" title="depth" />
    </div>
    <input data-role="sector" placeholder="new sector name (optional)" />
    <!-- UI-31: arena_names has been in ApprovePetitionRequest
         (console/schemas.py) and in world's own admin API since
         expansion.py grew single_arena_rect_bands, and this console never
         sent it - so every approval carved the new strip into one
         undivided arena because the only caller had no field for it. -->
    <input data-role="arenas" placeholder="arena names inside it, comma separated (optional)" />
    <input data-role="note" placeholder="decision note" />
    <div class="list-item-actions">
      <button class="btn btn-ok" data-action="approve">Approve → grow map</button>
      <button class="btn btn-danger" data-action="deny">Deny</button>
    </div>
  `);
  confirmable(item.querySelector('[data-action="approve"]'), async () => {
    const edge = item.querySelector('[data-role="edge"]').value;
    const depth = parseInt(item.querySelector('[data-role="depth"]').value, 10) || 1;
    const sector = item.querySelector('[data-role="sector"]').value.trim() || null;
    const arenas = splitNames(item.querySelector('[data-role="arenas"]').value);
    const note = item.querySelector('[data-role="note"]').value;
    await apiFetch(`/api/president/petitions/${encodeURIComponent(p.petition_id)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        decision_note: note,
        edge,
        depth,
        new_sector_name: sector,
        arena_names: arenas,
      }),
    });
    showBanner(
      `Petition approved, map expanded ${depth} tiles ${edge}` +
        (arenas ? ` in ${arenas.length} arenas.` : "."),
      "ok"
    );
    refreshPresident();
  });
  confirmable(item.querySelector('[data-action="deny"]'), async () => {
    const note = item.querySelector('[data-role="note"]').value;
    await apiFetch(`/api/president/petitions/${encodeURIComponent(p.petition_id)}/deny`, {
      method: "POST",
      body: JSON.stringify({ decision_note: note }),
    });
    showBanner("Petition denied.", "ok");
    refreshPresident();
  });
  return item;
}

function renderAmendment(a) {
  const item = listItem(`
    <div class="list-item-head">
      <span class="list-item-title">${esc(a.title)}</span>
      <span class="badge badge-pending">${esc(a.status)}</span>
    </div>
    <div class="list-item-meta">field: ${esc(a.field_name)} → ${esc(JSON.stringify(a.new_value))}</div>
    <div class="list-item-body">${esc(a.rationale)}</div>
    <input data-role="veto-note" placeholder="veto note" />
    <div class="list-item-actions">
      <button class="btn btn-ok" data-action="sign">Sign</button>
      <button class="btn btn-danger" data-action="veto">Veto</button>
    </div>
  `);
  confirmable(item.querySelector('[data-action="sign"]'), async () => {
    await apiFetch(`/api/president/amendments/${encodeURIComponent(a.proposal_id)}/sign`, {
      method: "POST",
    });
    showBanner("Amendment signed into the charter.", "ok");
    refreshPresident();
  });
  confirmable(item.querySelector('[data-action="veto"]'), async () => {
    const note = item.querySelector('[data-role="veto-note"]').value;
    await apiFetch(`/api/president/amendments/${encodeURIComponent(a.proposal_id)}/veto`, {
      method: "POST",
      body: JSON.stringify({ veto_note: note }),
    });
    showBanner("Amendment vetoed.", "ok");
    refreshPresident();
  });
  return item;
}

function renderBroadcast(b) {
  return listItem(
    `<div class="list-item-meta">tick ${esc(b.issued_tick)}</div><div class="list-item-body">${esc(b.message)}</div>`
  );
}

function renderHonor(h) {
  return listItem(
    `<div class="list-item-head"><span class="list-item-title">${esc(h.title)}</span><span class="list-item-meta">${esc(h.agent_id)}</span></div>` +
      `<div class="list-item-body">${esc(h.citation)}</div>`
  );
}

async function refreshPresident() {
  const [petitions, amendments, broadcasts, honors] = await Promise.all([
    apiFetch("/api/president/petitions"),
    apiFetch("/api/president/amendments"),
    apiFetch("/api/president/broadcasts"),
    apiFetch("/api/president/honors"),
  ]);
  renderList("petitions-list", petitions, renderPetition, "no pending land petitions");
  renderList("amendments-list", amendments, renderAmendment, "no amendments awaiting signature");
  renderList("broadcasts-list", broadcasts, renderBroadcast, "no broadcasts yet");
  renderList("honors-list", honors, renderHonor, "no honors granted yet");
}

function initPresidentActions() {
  document.getElementById("btn-broadcast").addEventListener("click", async () => {
    const textarea = document.getElementById("broadcast-message");
    const message = textarea.value.trim();
    if (!message) return;
    await apiFetch("/api/president/broadcast", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    textarea.value = "";
    showBanner("Address to the nation broadcast.", "ok");
    refreshPresident();
  });

  document.getElementById("btn-grant-honor").addEventListener("click", async () => {
    const agent = document.getElementById("honor-agent").value.trim();
    const title = document.getElementById("honor-title").value.trim();
    const citation = document.getElementById("honor-citation").value.trim();
    if (!agent || !title) {
      showBanner("Honor needs an agent id and a title.");
      return;
    }
    await apiFetch("/api/president/honors", {
      method: "POST",
      body: JSON.stringify({ agent_id: agent, title, citation }),
    });
    document.getElementById("honor-agent").value = "";
    document.getElementById("honor-title").value = "";
    document.getElementById("honor-citation").value = "";
    showBanner("Honor granted.", "ok");
    refreshPresident();
  });
}

function renderPrayer(p) {
  const item = listItem(`
    <div class="list-item-head">
      <span class="list-item-title">${esc(p.agent_id)}</span>
      <span class="list-item-meta">tick ${esc(p.offered_tick)}</span>
    </div>
    <div class="list-item-body">${esc(p.content)}</div>
    <input data-role="response" placeholder="response note (visible on grant only)" />
    <div class="list-item-actions">
      <button class="btn btn-ok" data-action="grant">Grant</button>
      <button class="btn btn-ghost" data-action="silent-deny">Silent deny (no trace)</button>
    </div>
  `);
  confirmable(item.querySelector('[data-action="grant"]'), async () => {
    const note = item.querySelector('[data-role="response"]').value;
    await apiFetch(`/api/god/prayers/${encodeURIComponent(p.prayer_id)}/grant`, {
      method: "POST",
      body: JSON.stringify({ response_note: note }),
    });
    showBanner("Prayer granted.", "ok");
    refreshGod();
  });
  confirmable(item.querySelector('[data-action="silent-deny"]'), async () => {
    await apiFetch(`/api/god/prayers/${encodeURIComponent(p.prayer_id)}/silent-deny`, {
      method: "POST",
    });
    showBanner("Prayer silently denied - no trace left for the agent.", "ok");
    refreshGod();
  });
  return item;
}

function renderBlessing(b) {
  const item = listItem(`
    <div class="list-item-head">
      <span class="list-item-title">household ${esc(b.household_id)}</span>
      <span class="list-item-meta">sponsor ${esc(b.sponsor_parent_id)}</span>
    </div>
    <div class="list-item-body">parents: ${esc(b.parent_ids.join(", "))}</div>
    <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.8rem;color:var(--text-dim);">
      <input data-role="silent" type="checkbox" style="width:auto;margin:0;" /> deny silently
    </label>
    <div class="list-item-actions">
      <button class="btn btn-ok" data-action="grant">Grant blessing</button>
      <button class="btn btn-danger" data-action="deny">Deny</button>
    </div>
  `);
  confirmable(item.querySelector('[data-action="grant"]'), async () => {
    await apiFetch(`/api/god/blessings/${encodeURIComponent(b.request_id)}/grant`, {
      method: "POST",
    });
    showBanner("Baby blessing granted.", "ok");
    refreshGod();
  });
  confirmable(item.querySelector('[data-action="deny"]'), async () => {
    const silent = item.querySelector('[data-role="silent"]').checked;
    await apiFetch(`/api/god/blessings/${encodeURIComponent(b.request_id)}/deny`, {
      method: "POST",
      body: JSON.stringify({ silent }),
    });
    showBanner(silent ? "Blessing silently denied." : "Blessing denied.", "ok");
    refreshGod();
  });
  return item;
}

function renderDisaster(d) {
  return listItem(
    `<div class="list-item-head"><span class="list-item-title">${esc(d.disaster_type)}</span>` +
      `<span class="list-item-meta">tick ${esc(d.triggered_tick)}</span></div>` +
      `<div class="list-item-meta">scope: ${esc(d.scope.scope_type)}${d.scope.target ? " · " + esc(d.scope.target) : ""} · magnitude ${esc(d.magnitude)}</div>`
  );
}

function initDisasterAction() {
  confirmable(document.getElementById("btn-disaster"), async () => {
    const disasterType = document.getElementById("disaster-type").value;
    const scopeType = document.getElementById("disaster-scope-type").value;
    const target = document.getElementById("disaster-target").value.trim() || null;
    const magnitude = parseFloat(document.getElementById("disaster-magnitude").value) || 1.0;
    const note = document.getElementById("disaster-note").value;
    if (scopeType !== "town" && !target) {
      showBanner("District/zone scope needs a target id.");
      return;
    }
    await apiFetch("/api/god/disasters", {
      method: "POST",
      body: JSON.stringify({
        disaster_type: disasterType,
        scope: { scope_type: scopeType, target },
        magnitude,
        note,
      }),
    });
    showBanner(`${disasterType} unleashed (${scopeType}).`, "ok");
    refreshGod();
  });

  confirmable(document.getElementById("btn-miracle"), async () => {
    const agent = document.getElementById("mm-agent").value.trim();
    const description = document.getElementById("mm-description").value.trim();
    if (!agent || !description) {
      showBanner("Miracle needs a target agent id and a description.");
      return;
    }
    await apiFetch("/api/god/miracles", {
      method: "POST",
      body: JSON.stringify({ agent_id: agent, description }),
    });
    showBanner("Miracle cast.", "ok");
  });

  confirmable(document.getElementById("btn-misfortune"), async () => {
    const agent = document.getElementById("mm-agent").value.trim();
    const description = document.getElementById("mm-description").value.trim();
    if (!agent || !description) {
      showBanner("Misfortune needs a target agent id and a description.");
      return;
    }
    await apiFetch("/api/god/misfortunes", {
      method: "POST",
      body: JSON.stringify({ agent_id: agent, description }),
    });
    showBanner("Misfortune cast.", "ok");
  });
}

async function refreshDials() {
  const dials = await apiFetch("/api/god/population-dials");
  document.getElementById("dial-min").value = dials.target_band_min;
  document.getElementById("dial-max").value = dials.target_band_max;
  document.getElementById("dial-death").value = dials.death_hazard_multiplier;
  document.getElementById("dial-throttle").value = dials.blessing_throttle;
  document.getElementById("dials-current").textContent =
    `current: band ${dials.target_band_min}-${dials.target_band_max}, ` +
    `death×${dials.death_hazard_multiplier}, throttle ${dials.blessing_throttle}`;
}

function initDialsAction() {
  confirmable(document.getElementById("btn-update-dials"), async () => {
    const dials = await apiFetch("/api/god/population-dials", {
      method: "PUT",
      body: JSON.stringify({
        target_band_min: parseInt(document.getElementById("dial-min").value, 10),
        target_band_max: parseInt(document.getElementById("dial-max").value, 10),
        death_hazard_multiplier: parseFloat(document.getElementById("dial-death").value),
        blessing_throttle: parseFloat(document.getElementById("dial-throttle").value),
      }),
    });
    showBanner("Population dials updated.", "ok");
    document.getElementById("dials-current").textContent =
      `current: band ${dials.target_band_min}-${dials.target_band_max}, ` +
      `death×${dials.death_hazard_multiplier}, throttle ${dials.blessing_throttle}`;
  });
}

async function refreshGod() {
  const [prayers, blessings, disasters] = await Promise.all([
    apiFetch("/api/god/prayers"),
    apiFetch("/api/god/blessings"),
    apiFetch("/api/god/disasters"),
  ]);
  renderList("prayers-list", prayers, renderPrayer, "no prayers waiting");
  renderList("blessings-list", blessings, renderBlessing, "no blessing requests waiting");
  renderList("disasters-list", disasters, renderDisaster, "no disasters yet");
  await refreshDials();
}

const RUN_BUDGET_CAP_FALLBACK_CENTS = 40000;

function runCapCents(status) {
  const cap = status && status.run_cap_usd_cents;
  return (typeof cap === "number" && cap > 0) ? cap : RUN_BUDGET_CAP_FALLBACK_CENTS;
}

let lastSpendSample = null;

function usdFromCents(cents) {
  return (cents / 100).toFixed(2);
}

function renderSpend(status) {
  const parsedAtMs = Date.parse(status.sampled_at);
  const sampledAtMs = Number.isNaN(parsedAtMs) ? Date.now() : parsedAtMs;

  let rateText = "warming up…";
  if (lastSpendSample && sampledAtMs > lastSpendSample.atMs) {
    const deltaCents = status.burned_usd_cents - lastSpendSample.burnedCents;
    const deltaHours = (sampledAtMs - lastSpendSample.atMs) / 3600000;
    if (deltaHours > 0) {
      const ratePerHour = Math.max(0, deltaCents / 100 / deltaHours);
      rateText = `$${ratePerHour.toFixed(2)}/hr`;
    }
  }
  lastSpendSample = { burnedCents: status.burned_usd_cents, atMs: sampledAtMs };

  document.getElementById("spend-burned").textContent = `$${usdFromCents(status.burned_usd_cents)} burned`;
  document.getElementById("spend-rate").textContent = rateText;

  const capCents = runCapCents(status);
  const pctOfCap = Math.min(1, status.burned_usd_cents / capCents);
  const fill = document.getElementById("spend-bar-fill");
  fill.style.width = (pctOfCap * 100).toFixed(1) + "%";
  fill.className =
    "spend-bar-fill " + (pctOfCap >= 0.9 ? "spend-bar-danger" : pctOfCap >= 0.7 ? "spend-bar-warn" : "spend-bar-ok");

  document.getElementById("spend-cap-note").textContent =
    `of $${(capCents / 100).toFixed(2)} cap`
    + (status.run_cap_usd_cents ? "" : " (not reported — assumed)")
    + ` · $${usdFromCents(status.budget_total_usd_cents)} ledger pool`;
}

async function refreshSpend() {
  const status = await apiFetch("/api/spend/status");
  renderSpend(status);
}

function renderAlert(a) {
  return listItem(
    `<div class="list-item-head"><span class="badge">${esc(a.category)}</span>` +
      `<span class="list-item-meta">tick ${esc(a.raised_tick)}</span></div>` +
      `<div class="list-item-body">${esc(a.summary)}</div>`
  );
}

async function refreshAlerts() {
  const alerts = await apiFetch("/api/alerts?limit=50");
  renderList("alerts-list", alerts, renderAlert, "no alerts");
}

function renderStatus(status) {
  document.getElementById("status-readout").textContent =
    `tick ${status.tick_index} · day ${status.game_day} · ` +
    `${status.paused ? "PAUSED" : "running"} · pacing ${status.pacing_mode}` +
    (status.pacing_mode === "continuous" ? ` @ ${status.continuous_interval_s}s` : "");
  document.getElementById("pacing-mode").value = status.pacing_mode;
  document.getElementById("pacing-interval").value = status.continuous_interval_s;
}

async function refreshControl() {
  const status = await apiFetch("/api/control/status");
  renderStatus(status);
}

function initControlActions() {
  document.getElementById("btn-pause").addEventListener("click", async () => {
    renderStatus(await apiFetch("/api/control/pause", { method: "POST" }));
    showBanner("Simulation paused.", "ok");
  });
  document.getElementById("btn-resume").addEventListener("click", async () => {
    renderStatus(await apiFetch("/api/control/resume", { method: "POST" }));
    showBanner("Simulation resumed.", "ok");
  });
  document.getElementById("btn-apply-pacing").addEventListener("click", async () => {
    const pacingMode = document.getElementById("pacing-mode").value;
    const interval = parseFloat(document.getElementById("pacing-interval").value) || 0;
    renderStatus(
      await apiFetch("/api/control/tick-speed", {
        method: "PUT",
        body: JSON.stringify({ pacing_mode: pacingMode, continuous_interval_s: interval }),
      })
    );
    showBanner("Tick speed updated.", "ok");
  });
  document.getElementById("btn-checkpoint").addEventListener("click", async () => {
    const summary = await apiFetch("/api/control/checkpoint-now", { method: "POST" });
    showBanner(`Checkpoint written for game day ${summary.game_day} (tick ${summary.tick_index}).`, "ok");
  });
}

function confidenceClass(confidence) {
  if (!confidence) return "none";
  if (confidence.indexOf("moderately") === 0) return "moderate";
  if (confidence.indexOf("high") === 0) return "high";
  if (confidence.indexOf("medium") === 0) return "medium";
  if (confidence.indexOf("low") === 0) return "low";
  return "none";
}

function renderFramed(f) {
  const el = document.createElement("div");
  el.className = "framed " + (f.severity || "info");

  const q = document.createElement("div");
  q.className = "q";
  q.textContent = f.question;

  const a = document.createElement("div");
  a.className = "a";
  a.textContent = f.answer;

  const meta = document.createElement("div");
  meta.className = "meta";
  const chip = document.createElement("span");
  chip.className = "chip " + confidenceClass(f.confidence);
  chip.textContent = f.confidence;
  const basis = document.createElement("span");
  basis.className = "basis";
  basis.textContent = f.basis || "";
  meta.appendChild(chip);
  meta.appendChild(basis);

  el.appendChild(q);
  el.appendChild(a);
  el.appendChild(meta);

  if (f.breakdown && f.breakdown.length) {
    const bd = document.createElement("div");
    bd.className = "breakdown";
    f.breakdown.forEach((item) => {
      const span = document.createElement("span");
      const label = document.createTextNode(item.label + " ");
      const value = document.createElement("b");
      value.textContent = String(item.value);
      span.appendChild(label);
      span.appendChild(value);
      bd.appendChild(span);
    });
    el.appendChild(bd);
  }
  return el;
}

function renderTerm(t) {
  const el = document.createElement("div");
  el.className = "framed " + (t.is_current ? "info" : "");
  const q = document.createElement("div");
  q.className = "q";
  q.textContent = (t.pm_name || "no prime minister") + (t.is_current ? " — in office now" : "");
  const a = document.createElement("div");
  a.className = "a";
  a.textContent = t.amendment_titles && t.amendment_titles.length
    ? "Enacted under this term: " + t.amendment_titles.join("; ")
    : "No policy changes were enacted under this term.";
  el.appendChild(q);
  el.appendChild(a);
  return el;
}

async function refreshTown() {
  const body = await apiFetch("/api/town/overview");
  const observed = document.getElementById("town-observed");
  if (observed) {
    observed.textContent =
      "Everything below is judged on " + body.days_observed +
      " day" + (body.days_observed === 1 ? "" : "s") + " of recorded history.";
  }
  fillList("town-headlines", [body.headline, body.treasury, body.skills], renderFramed);
  fillList("town-at-risk", body.at_risk || [], renderFramed);
  fillList("town-businesses", body.businesses || [], renderFramed);
  const terms = await apiFetch("/api/town/terms");
  fillList("town-terms", terms || [], renderTerm);
}

async function refreshAll() {
  try {
    await Promise.all([
      refreshControl(),
      refreshPresident(),
      refreshGod(),
      refreshSpend(),
      refreshAlerts(),
      refreshTown(),
    ]);
  } catch (err) {
  }
}

function init() {
  initTabs();
  initTokenBox();
  initPresidentActions();
  initDisasterAction();
  initDialsAction();
  initControlActions();
  refreshAll();
  window.setInterval(refreshAll, REFRESH_INTERVAL_MS);
}

document.addEventListener("DOMContentLoaded", init);
