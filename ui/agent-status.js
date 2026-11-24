/* ════════════════════════════════════════════════════════════════════════════
 *  agent-status.js — voyant de CSAgent, commun au chat et au portail
 * ────────────────────────────────────────────────────────────────────────────
 *  Le voyant est la pastille `#chat-engine-status-chip` (chat) ou `#status-chip-agent` (portail).
 *  États (`appState.agentState`, rendus par `portal_agent_status`) :
 *    starting · ready · external · missing · failed · stopped
 *  Quand FlowPortal a lancé CSAgent lui-même (`appState.agentManaged`), un clic sur le voyant le relance
 *  s'il est arrêté ou en échec.
 * ════════════════════════════════════════════════════════════════════════════ */

function agentStatusChip() {
  return document.getElementById("chat-engine-status-chip") || document.getElementById("status-chip-agent");
}

function updateEngineStatusChip() {
  const chip = agentStatusChip();
  if (!chip) return;
  chip.title = "";
  chip.style.cursor = "";
  chip.onclick = null;
  const dot = color => `<span class="status-dot" style="background:var(--${color})"></span>`;
  const state = appState.agentState;
  if (state === "starting") {
    chip.title = appState.agentMessage || "CSAgent démarre.";
    chip.innerHTML = `${dot("human")}CSAgent : démarrage…`;
    return;
  }
  const managedProblem = appState.agentManaged && (state === "failed" || state === "stopped");
  if (state === "missing" || managedProblem) {
    chip.title = `${appState.agentMessage || "CSAgent n'est pas disponible."}${managedProblem ? "\n\nCliquez ici pour le relancer." : ""}`;
    chip.innerHTML = `${dot("bad")}CSAgent indisponible`;
    if (managedProblem) { chip.style.cursor = "pointer"; chip.onclick = restartAgentFromChip; }
    return;
  }
  if (appState.agentUp === false && appState.agentManaged) {
    chip.title = "CSAgent, lancé par FlowPortal, ne répond plus.\n\nCliquez ici pour le relancer.";
    chip.innerHTML = `${dot("bad")}CSAgent arrêté`;
    chip.style.cursor = "pointer";
    chip.onclick = restartAgentFromChip;
    return;
  }
  if (appState.agentUp === false) {
    chip.title = `CSAgent ne répond pas à l'adresse ${appState.agentUrl || "http://localhost:8080"} : démarrez-le (start_agent) ou indiquez sa vraie adresse avec la variable CSAGENT_URL. Les workflows avec une étape IA échouent tant qu'il est arrêté.`;
    chip.innerHTML = `${dot("bad")}CSAgent arrêté`;
    return;
  }
  const busy = appState.busy || (appState.liveRun && appState.liveRun.running);
  chip.innerHTML = busy ? `${dot("human")}CSAgent occupé` : `<span class="status-dot"></span>CSAgent actif`;
}

/** Interroge CSAgent et met le voyant à jour ; rappelé toutes les 15 s (toutes les 1,5 s pendant son démarrage) et après chaque échec. */
async function refreshAgentStatus() {
  try {
    const status = await callBackend("portal_agent_status");
    appState.agentUp = !!(status && status.reachable);
    appState.agentUrl = (status && status.url) || appState.agentUrl;
    appState.agentState = (status && status.state) || "external";
    appState.agentMessage = (status && status.message) || "";
    appState.agentManaged = !!(status && status.managed);
  } catch (error) { /* backend indisponible : on garde l'état précédent */ }
  updateEngineStatusChip();
  clearTimeout(refreshAgentStatus.timer);
  if (appState.agentState === "starting") refreshAgentStatus.timer = setTimeout(refreshAgentStatus, 1500);
}

/** Clic sur le voyant quand CSAgent, lancé par FlowPortal, s'est arrêté : le relance. */
async function restartAgentFromChip() {
  appState.agentState = "starting";
  appState.agentMessage = "Démarrage de CSAgent…";
  updateEngineStatusChip();
  try { await callBackend("portal_agent_restart"); } catch (error) { /* le prochain statut dira ce qui s'est passé */ }
  refreshAgentStatus();
}

/** À appeler une fois par page : premier test tout de suite, puis toutes les 15 s. */
function startAgentStatus() {
  refreshAgentStatus();
  setInterval(refreshAgentStatus, 15000);
}
