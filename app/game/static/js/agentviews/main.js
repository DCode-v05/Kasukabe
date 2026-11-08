import { checkWorldHealth, getWorldBaseUrl, setWorldBaseUrl } from "./api.js";
import { el } from "./dom.js";
import { initFinder, loadFinder } from "./finder.js";
import { initActivity, loadActivity } from "./activity.js";
import { loadReasoning } from "./reasoning.js";
import { getSelectedAgent, onAgentSelected } from "./state.js";

function activateTab(name) {
  if ((name === "activity" || name === "reasoning") && !getSelectedAgent()) {
    name = "finder";
  }
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "finder") loadFinder();
  if (name === "activity") loadActivity();
  if (name === "reasoning") loadReasoning();
}

function wireTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

function updateAgentStrip() {
  const selected = getSelectedAgent();
  const strip = el("agent-strip");
  const activityTabBtn = document.querySelector('.tab-btn[data-tab="activity"]');
  const reasoningTabBtn = document.querySelector('.tab-btn[data-tab="reasoning"]');
  if (!selected) {
    strip.className = "agent-strip empty";
    strip.textContent = "No agent selected — pick one in Finder.";
    activityTabBtn.disabled = true;
    reasoningTabBtn.disabled = true;
    return;
  }
  strip.className = "agent-strip";
  strip.innerHTML = `
    <span class="agent-strip-name">${selected.name}</span>
    <span class="agent-strip-id mono">${selected.agentId}</span>
    <button id="agent-strip-change" class="btn btn-ghost btn-sm">Change agent</button>`;
  activityTabBtn.disabled = false;
  reasoningTabBtn.disabled = false;
  el("agent-strip-change").addEventListener("click", () => activateTab("finder"));
}

async function refreshConnectionDot() {
  const dot = el("api-dot");
  try {
    await checkWorldHealth();
    dot.className = "dot dot-ok";
    dot.title = `connected to ${getWorldBaseUrl() || "(same origin)"}`;
  } catch (err) {
    dot.className = "dot dot-bad";
    dot.title = err.message;
  }
}

function wireApiBox() {
  const input = el("api-base-url");
  input.value = getWorldBaseUrl();
  input.placeholder = "http://localhost:8100";
  el("api-save").addEventListener("click", () => {
    setWorldBaseUrl(input.value);
    refreshConnectionDot();
    activateTab(currentTab());
  });
}

function currentTab() {
  const active = document.querySelector(".tab-btn.active");
  return active ? active.dataset.tab : "finder";
}

onAgentSelected(() => updateAgentStrip());

wireTabs();
wireApiBox();
initFinder(() => activateTab("activity"));
initActivity(() => activateTab("reasoning"));
updateAgentStrip();
refreshConnectionDot();
activateTab("finder");
setInterval(refreshConnectionDot, 20_000);
