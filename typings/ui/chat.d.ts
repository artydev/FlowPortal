
/**
 * FlowPortal — interface conversationnelle (chat.html).
 *
 * Ce module est chargé après core.js, html-template.js, state.js et engine-events.js.
 * Il redéfinit les fonctions que engine-events.js appelle (`renderLiveRun`,
 * `finishLiveRun`, `reloadWorkflowList`, `renderEnvChips`, `handleAssistantEvent`)
 * pour les adapter au contexte conversationnel ; les autres modules du portail
 * (workflow-panel, assistant, run-result…) ne sont pas chargés ici.
 */

/* ── Références DOM ─────────────────────────────────────────────────────── */
const messagesContainer     = document.getElementById("chat-messages");
const scrollAnchorElement   = document.getElementById("chat-scroll-anchor");
const inputTextarea         = document.getElementById("chat-input-textarea");
const sendButton            = document.getElementById("chat-send-button");

/* ── État spécifique au chat ────────────────────────────────────────────── */
// Élément DOM .wf-card actuellement affiché dans la conversation (dernier en date).
let activeWorkflowCardElement = null;
// Nom du fichier en cours de modification via CSAgent (mémorisé à l'envoi pour
// être disponible quand assist_done arrive de façon asynchrone).
let pendingAssistFilename = null;

/* ── Vue simple / développeur ───────────────────────────────────────────── */
/** « simple » : langage courant, sans types ni identifiants. « dev » : tout est visible (types, ids, YAML). */
function applyViewMode(mode) {
  const next = mode === "dev" ? "dev" : "simple";
  document.body.dataset.view = next;
  try { localStorage.setItem("flowportal.chatView", next); } catch (_) { /* stockage indisponible : sans importance */ }
  const button = document.getElementById("view-toggle-button");
  if (button) button.textContent = next === "simple" ? "Vue développeur" : "Vue simple";
}
function initViewMode() {
  let saved = "simple";
  try { saved = localStorage.getItem("flowportal.chatView") || "simple"; } catch (_) { /* idem */ }
  applyViewMode(saved);
  const button = document.getElementById("view-toggle-button");
  if (button) button.onclick = () => applyViewMode(document.body.dataset.view === "simple" ? "dev" : "simple");
}
initViewMode();

/* ── Démarrage ──────────────────────────────────────────────────────────── */
/**
 * Démarre l'interface chat : charge les workflows depuis le backend.
 * Réessaie toutes les 250 ms pendant 10 s au plus (le serveur Python peut
 * mettre un instant à répondre après le lancement de l'exe).
 */
async function bootChatApp() {
  for (let attempt = 0; attempt < 40; attempt++) {
    const result = await callBackend("portal_list");
    if (result && result.cards) {
      appState.workflows   = result.cards;
      appState.recentRuns  = result.recent || [];
      appState.busy        = !!result.busy;
      renderWorkflowList();
      updateEngineStatusChip();
      renderChatInputControls();
      inputTextarea.focus();
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  // Après 10 s sans réponse : message d'erreur dans la sidebar.
  const listElement = document.getElementById("chat-workflow-list");
  if (listElement) {
    listElement.innerHTML = html`<p class="hint-text" style="padding:12px 16px;color:var(--bad)">Impossible de joindre FlowPortal.</p>`;
  }
}

/* ── Puce de statut du moteur ───────────────────────────────────────────── */
function updateEngineStatusChip() {
  const chip = document.getElementById("chat-engine-status-chip");
  if (!chip) return;
  chip.innerHTML = appState.busy
    ? `<span class="status-dot" style="background:var(--human)"></span>Moteur occupé`
    : `<span class="status-dot"></span>Moteur actif`;
}

/* ── Barre latérale : onglets ───────────────────────────────────────────── */
function switchSidebarTab(tabKey) {
  document.getElementById("sidebar-tab-conversations").classList.toggle("active", tabKey === "conversations");
  document.getElementById("sidebar-tab-workflows").classList.toggle("active",     tabKey === "workflows");
  document.getElementById("sidebar-panel-conversations").classList.toggle("active", tabKey === "conversations");
  document.getElementById("sidebar-panel-workflows").classList.toggle("active",     tabKey === "workflows");
}

/* ── Barre latérale : liste de workflows ───────────────────────────────── */
function renderWorkflowList() {
  const listElement = document.getElementById("chat-workflow-list");
  if (!listElement) return;
  if (!appState.workflows.length) {
    listElement.innerHTML = html`<p class="hint-text" style="padding:12px 16px">Aucun workflow.</p>`;
    return;
  }
  listElement.innerHTML = appState.workflows.map(workflow => html`
    <div class="wf-list-item" data-filename="${workflow.filename}"
         onclick="openWorkflowCard('${workflow.filename}')">
      <span class="wf-list-name">${workflow.title}</span>
      <div class="wf-type-dot ${dominantStepType(workflow)}"
           title="${dominantStepType(workflow)}"></div>
    </div>
  `).join("");
}

/** Recharge la liste depuis le backend et la re-dessine. */
async function reloadWorkflowList() {
  const result = await callBackend("portal_list");
  if (result && result.cards) {
    appState.workflows  = result.cards;
    appState.recentRuns = result.recent || [];
    appState.busy       = !!result.busy;
  }
  renderWorkflowList();
}

/** Type d'étape dominant d'un workflow (détermine la couleur du point). */
function dominantStepType(workflow) {
  const steps = workflow.steps || [];
  if (steps.some(s => s.type === "agent"))      return "agent";
  if (steps.some(s => s.type === "shell"))      return "shell";
  if (steps.some(s => s.type === "python"))     return "python";
  return "file_write";
}

/** Filtre les items de la liste par texte de recherche. */
function filterWorkflowList(query) {
  const lowerQuery = query.toLowerCase();
  document.querySelectorAll("#chat-workflow-list .wf-list-item").forEach(item => {
    item.style.display = item.textContent.toLowerCase().includes(lowerQuery) ? "" : "none";
  });
}

/* ── Conversation ───────────────────────────────────────────────────────── */
function startNewConversation() {
  [...messagesContainer.querySelectorAll(".chat-message:not(#chat-welcome-message)")].forEach(el => el.remove());
  inputTextarea.value       = "";
  inputTextarea.style.height = "";
  activeWorkflowCardElement = null;
  pendingAssistFilename     = null;
  appState.busy = false;
  renderChatInputControls();
  inputTextarea.focus();
}

/* ── Ajout de messages dans la conversation ─────────────────────────────── */
function appendUserMessage(text) {
  const messageElement = document.createElement("div");
  messageElement.className = "chat-message user";
  messageElement.innerHTML = html`
    <div class="chat-avatar user-avatar">SD</div>
    <div class="chat-bubble">${text}</div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
}

function appendAssistantMessage(htmlContent) {
  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.innerHTML = `
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="chat-bubble">${htmlContent}</div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
  return messageElement;
}

/** Affiche un indicateur « ... » le temps que l'assistant réponde. Renvoie l'id de l'élément. */
function showTypingIndicator() {
  const typingMessageId = "chat-typing-" + Date.now();
  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.id = typingMessageId;
  messageElement.innerHTML = `
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="chat-bubble">
      <div class="typing-indicator"><span></span><span></span><span></span></div>
    </div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
  scrollMessagesToBottom();
  return typingMessageId;
}

function removeElementById(id) {
  const element = document.getElementById(id);
  if (element) element.remove();
}

/* ── Carte de workflow dans la conversation ─────────────────────────────── */
/** Ouvre la carte d'un workflow dans la conversation (depuis la barre latérale). */
function openWorkflowCard(filename) {
  const workflow = findCard(filename);
  if (!workflow) return;
  switchSidebarTab("conversations");
  const typingMessageId = showTypingIndicator();
  setTimeout(() => {
    removeElementById(typingMessageId);
    appendWorkflowCardMessage(workflow);
    scrollMessagesToBottom();
    renderChatInputControls(); // affiche le chip de contexte
    inputTextarea.focus();
  }, 400);
}

/** Ajoute une carte de workflow dans la conversation. */
function appendWorkflowCardMessage(workflow) {
  const steps = workflow.steps || [];
  const stepTimelineHtml = steps.map((step, index) => {
    const connector = index < steps.length - 1 ? `<div class="step-connector"></div>` : "";
    return `<div class="step-dot ${step.type}"></div>${connector}`;
  }).join("") || `<div class="step-dot"></div>`;

  const stepCount = steps.length;
  const tagsHtml = Object.keys(workflow.tags || {}).map(key =>
    html`<span class="tag tag-${stepTypeToTagClass(key)}">${key}</span>`
  ).join("") + html`<span class="tag">${stepCount} étape${stepCount !== 1 ? "s" : ""}</span>`;

  // Vue simple : les premières étapes en clair (icône + titre), sans types ni identifiants.
  const SIMPLE_STEPS_SHOWN = 6;
  const simpleStepsHtml = steps.slice(0, SIMPLE_STEPS_SHOWN).map(step => html`
      <li class="sc-step"><span class="sc-ico">${friendlyStepIcon(step.type)}</span><span class="sc-step-main"><span>${step.label || step.id}</span></span></li>`).join("")
    + (steps.length > SIMPLE_STEPS_SHOWN ? html`<li class="sc-step" style="color:var(--mute)">… et ${steps.length - SIMPLE_STEPS_SHOWN} autre${steps.length - SIMPLE_STEPS_SHOWN > 1 ? "s" : ""} étape${steps.length - SIMPLE_STEPS_SHOWN > 1 ? "s" : ""}</li>` : "");

  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.innerHTML = `
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="wf-card" data-filename="${workflow.filename}">
      <div class="wf-header">
        <div class="wf-name">${workflow.title}</div>
        <div class="wf-desc">${workflow.summary || ""}</div>
      </div>
      <div class="tag-list dev-only" style="padding:0 16px 10px">${tagsHtml}</div>
      <div class="wf-step-tl dev-only">${stepTimelineHtml}</div>
      <ol class="sc-steps simple-only">${simpleStepsHtml}</ol>
      <div class="card-footer" style="padding:10px 16px;border-top:1px solid var(--line);display:flex;gap:7px">
        <button class="button-primary"
                onclick="launchWorkflowFromChat('${workflow.filename}', this)">▶ Lancer</button>
        <button class="button-primary is-ghost simple-only"
                onclick="askModificationFromChat(this)">✏ Modifier</button>
        <button class="button-primary is-ghost dev-only"
                onclick="openYamlPanel('${workflow.filename}', true)">✏ Modifier</button>
        <button class="button-primary is-ghost dev-only"
                onclick="openYamlPanel('${workflow.filename}', false)">⌄ YAML</button>
      </div>
    </div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
  activeWorkflowCardElement = messageElement.querySelector(".wf-card");
}

/** Vue simple : « Modifier » ne montre pas le YAML, il fait de cette carte le contexte du chat et donne la main à la saisie. */
function askModificationFromChat(button) {
  activeWorkflowCardElement = button.closest(".wf-card");
  renderChatInputControls();
  inputTextarea.focus();
}

/** Convertit un type d'étape en suffixe de classe CSS pour les tags. */
function stepTypeToTagClass(stepType) {
  declare interface tagClassesType {}

/**
 * Appelé par receiveEvent à chaque événement step_* / workflow_*.
 * Met à jour la puce de statut et affiche la porte (saisie / validation)
 * sous forme de bulle dans la conversation.
 */
function renderLiveRun() {
  updateEngineStatusChip();
  renderChatGate();
}

/**
 * Affiche la porte active du lancement (user_input ou human_approval) comme
 * bulle dans la conversation ; retire l'ancienne bulle si la porte a changé.
 * Mutation de `liveRun.renderedGate` pour éviter les doubles rendus (même
 * convention que workflow-panel.js).
 */
function renderChatGate() {
  const liveRun = appState.liveRun;
  const gate = liveRun && liveRun.gate;

  // Pas de porte ou porte fermée : on retire l'éventuelle bulle existante.
  if (!gate) {
    removeElementById("chat-live-gate");
    return;
  }
  // Même objet gate qu'au dernier rendu : rien à refaire.
  if (liveRun.renderedGate === gate) return;
  liveRun.renderedGate = gate;

  // Retire l'ancienne bulle (si porte différente) avant d'en créer une nouvelle.
  removeElementById("chat-live-gate");

  const gateElement = document.createElement("div");
  gateElement.className = "chat-message assistant";
  gateElement.id = "chat-live-gate";

  if (gate.kind === "approval") {
    gateElement.innerHTML = html`
      <div class="chat-avatar assistant-avatar">⚡</div>
      <div class="chat-bubble" style="border-color:var(--human)">
        <b>Votre décision est attendue</b><br>
        <span style="color:var(--mute);font-size:13px">${gate.message}</span>
        <div class="button-row" style="margin-top:10px">
          <button class="button-primary" id="chat-gate-approve">✓ Approuver</button>
          <button class="button-primary is-ghost" id="chat-gate-reject">✕ Refuser</button>
        </div>
      </div>
    `;
    messagesContainer.insertBefore(gateElement, scrollAnchorElement);
    scrollMessagesToBottom();

    document.getElementById("chat-gate-approve").onclick = async () => {
      gateElement.remove();
      appState.liveRun = closeGate(appState.liveRun);
      await callBackend("resolve_approval", "approved");
    };
    document.getElementById("chat-gate-reject").onclick = async () => {
      gateElement.remove();
      appState.liveRun = closeGate(appState.liveRun);
      await callBackend("resolve_approval", "rejected");
    };
    document.getElementById("chat-gate-approve").focus();
  }

  if (gate.kind === "input") {
    gateElement.innerHTML = html`
      <div class="chat-avatar assistant-avatar">⚡</div>
      <div class="chat-bubble" style="border-color:var(--accent)">
        <b>${gate.prompt}</b>
        <textarea id="chat-gate-textarea" rows="3"
          style="display:block;width:100%;margin-top:8px;padding:8px 10px;
                 border:1px solid var(--line);border-radius:8px;
                 background:var(--bg);color:var(--ink);
                 font:14px/1.5 var(--font-body);resize:vertical"></textarea>
        <div class="button-row" style="margin-top:8px">
          <button class="button-primary" id="chat-gate-submit">Valider</button>
          <button class="button-primary is-ghost" id="chat-gate-cancel">Annuler</button>
        </div>
      </div>
    `;
    messagesContainer.insertBefore(gateElement, scrollAnchorElement);

    // Valeur par défaut après insertion dans le DOM (innerHTML ne gère pas .value).
    const gateTextarea = document.getElementById("chat-gate-textarea");
    gateTextarea.value = gate.def || "";
    gateTextarea.focus();
    scrollMessagesToBottom();

    // Entrée = Valider (Shift+Entrée = saut de ligne).
    gateTextarea.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        document.getElementById("chat-gate-submit")?.click();
      }
    });

    document.getElementById("chat-gate-submit").onclick = async () => {
      const inputText = gateTextarea.value;
      gateElement.remove();
      appState.liveRun = closeInputGate(appState.liveRun);
      await callBackend("submit_input", inputText, false);
    };
    document.getElementById("chat-gate-cancel").onclick = async () => {
      gateElement.remove();
      appState.liveRun = closeInputGate(appState.liveRun);
      await callBackend("submit_input", "", true);
    };
  }
}

/* ── Fin de lancement (surchargé depuis engine-events.js) ───────────────── */
/**
 * Appelé par receiveEvent sur portal_run_saved.
 * Recharge la liste, récupère le détail du lancement et affiche le résumé.
 */
async function finishLiveRun(savedEvent) {
  appState.busy = false;
  updateEngineStatusChip();
  renderChatInputControls();

  // Réactive le bouton Lancer de la carte active
  if (activeWorkflowCardElement) {
    const launchButton = activeWorkflowCardElement.querySelector(".button-primary");
    if (launchButton) {
      launchButton.disabled = false;
      launchButton.innerHTML = "▶ Relancer";
    }
  }

  await reloadWorkflowList();

  const runDetailResult = await callBackend("portal_run_detail", savedEvent.run_id);
  if (!runDetailResult.ok) {
    appendAssistantMessage(html`<span style="color:var(--bad)">Lancement terminé · résultat indisponible.</span>`);
    scrollMessagesToBottom();
    return;
  }
  appendRunSummaryMessage(runDetailResult.run);
  scrollMessagesToBottom();
}

/** Ajoute le bilan d'un lancement dans la conversation. */
function appendRunSummaryMessage(run) {
  const isSuccess  = run.status === "ok";
  const statusLabel = RUN_STATUS_LABELS[run.status] || run.status;
  const toneClass   = isSuccess ? "" : "is-error";

  const stepsHtml = (run.steps || [])
    .filter(step => step.status !== "notrun")
    .map(step => html`
      <li class="${step.status === "ok" ? "ok" : ""}">
        <span class="step-icon">${step.status === "ok" ? "✓" : "✕"}</span>
        <div>
          <span>${step.label || step.id}</span>
          <small>${step.id} · ${formatDuration(step.duration)}</small>
        </div>
      </li>
    `).join("");

  const filesHtml = (run.files || []).map(file => html`
    <div class="file-pill" onclick="openRunFileFromChat('${file.path}')" style="cursor:pointer">
      📄 ${file.path.split("/").pop()}
    </div>
  `).join("");

  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.innerHTML = `
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="run-summary ${toneClass}">
      <div class="run-summary-header">
        ${isSuccess ? "✓" : "✕"} ${statusLabel}
        ${run.error ? html` · <span style="font-weight:400;font-size:13px">${run.error}</span>` : ""}
        <span class="run-id-badge">${run.id}</span>
      </div>
      <ol class="step-list">${stepsHtml}</ol>
      ${filesHtml ? `<div class="run-files" style="margin-top:8px">${filesHtml}</div>` : ""}
      <div class="button-row" style="margin-top:10px">
        <button class="button-primary is-ghost" style="font-size:12px"
                onclick="openRunResultFromChat('${run.id}')">Voir le résultat complet</button>
      </div>
    </div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
}

async function openRunFileFromChat(filePath) {
  await callBackend("portal_open_file", filePath);
}

async function openRunResultFromChat(runId) {
  // Ouvre le résultat complet si run-result.js est chargé (dans une prochaine version).
  if (typeof openRunResult === "function") {
    openRunResult(runId);
  }
}

/* ── Événements de l'assistant (surchargé depuis engine-events.js) ──────── */
/**
 * Gère les événements assist_* dans le contexte chat.
 * Les messages sont affichés dans la conversation plutôt que dans un panneau latéral.
 *
 * Flux de modification sans YAML visible :
 *   assist_status → bulle de statut (mise à jour en temps réel)
 *   assist_done   → si changed && draft : bulle de proposition (texte + Appliquer/Rejeter)
 *                   sinon              : bulle de réponse texte simple
 */
/** L'agent demande l'accord de l'utilisateur (ex. avant d'écrire) : bulle Approuver / Refuser. */
function showAssistGate(message) {
  removeElementById("chat-assist-gate");
  const gateElement = document.createElement("div");
  gateElement.className = "chat-message assistant";
  gateElement.id = "chat-assist-gate";
  gateElement.innerHTML = html`
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="chat-bubble" style="border-color:var(--human)">
      <b>L'assistant demande votre accord</b><br>
      <span style="color:var(--mute);font-size:13px">${message || "Approuver ?"}</span>
      <div class="button-row" style="margin-top:10px">
        <button class="button-primary" id="chat-assist-approve">✓ Approuver</button>
        <button class="button-primary is-ghost" id="chat-assist-reject">✕ Refuser</button>
      </div>
    </div>`;
  messagesContainer.insertBefore(gateElement, scrollAnchorElement);
  scrollMessagesToBottom();
  const answer = async result => { gateElement.remove(); await callBackend("resolve_approval", result); };
  document.getElementById("chat-assist-approve").onclick = () => answer("approved");
  document.getElementById("chat-assist-reject").onclick  = () => answer("rejected");
  document.getElementById("chat-assist-approve").focus();
}

/** Interrompt la demande en cours auprès de l'assistant (lève aussi une attente d'accord). */
async function stopAssistFromChat(button) {
  button.disabled = true;
  button.textContent = "Arrêt…";
  await callBackend("portal_stop");
}

function handleAssistantEvent(event) {
  if (event.type === "assist_status") {
    let statusElement = document.getElementById("chat-assist-status");
    if (!statusElement) {
      statusElement = document.createElement("div");
      statusElement.className = "chat-message assistant";
      statusElement.id = "chat-assist-status";
      statusElement.innerHTML = `
        <div class="chat-avatar assistant-avatar">⚡</div>
        <div class="chat-bubble" style="color:var(--mute)"><i class="chat-assist-status-text"></i>
          <button class="button-primary is-ghost" style="margin-left:10px;padding:2px 10px" onclick="stopAssistFromChat(this)">⏹ Arrêter</button></div>
      `;
      messagesContainer.insertBefore(statusElement, scrollAnchorElement);
    }
    statusElement.querySelector(".chat-assist-status-text").textContent =
      event.text || event.message || "CSAgent travaille…";
    scrollMessagesToBottom();
    return;
  }

  // Événements de l'agent pendant son travail : progression, ou demande d'accord (sans réponse, l'agent attend 10 min).
  if (event.type === "assist_event") {
    const agentEvent = event.event || {};
    if (agentEvent.type === "human_approval_required") {
      showAssistGate(agentEvent.message);
      return;
    }
    const progressText = agentEvent.type === "agent_thought" ? agentDataToText(agentEvent.data).replace(/\s+/g, " ").trim().slice(0, 160)
      : agentEvent.type === "agent_call" ? agentDataToText(agentEvent.data).slice(0, 80) : "";
    if (progressText) handleAssistantEvent({ type: "assist_status", text: progressText });
    return;
  }

  if (event.type === "assist_done") {
    removeElementById("chat-assist-gate");
    removeElementById("chat-assist-status");
    appState.busy = false;
    updateEngineStatusChip();
    renderChatInputControls();

    if (!event.ok) {
      appendAssistantMessage(html`<span style="color:var(--bad)">CSAgent a rencontré une erreur : ${event.error || "échec inconnu"}</span>`);
      scrollMessagesToBottom();
      return;
    }

    // CSAgent a généré un workflow modifié → proposition sans YAML visible.
    if (event.changed && event.draft) {
      appendYamlProposalMessage(event);
      scrollMessagesToBottom();
      return;
    }

    // CSAgent a répondu sans modifier de YAML (question, conseils…).
    if (event.answer) appendAssistantMessage(event.answer);

    // CSAgent a créé ou désigné un workflow → affiche sa carte.
    if (event.workflow) {
      const workflow = findCard(event.workflow);
      if (workflow) appendWorkflowCardMessage(workflow);
    }

    scrollMessagesToBottom();
  }
}

/* ── Vocabulaire du chat : langage courant (le mode avancé garde STEP_TYPE_LABELS) ── */
declare interface FRIENDLY_STEP_LABELSType {}

declare interface FRIENDLY_STEP_ICONSType {}
