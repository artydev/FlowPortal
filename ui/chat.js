"use strict";
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
  if (typeof renderChatInputControls === "function" && typeof sendButton !== "undefined") renderChatInputControls();
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
/* ── Écran d'accueil : suggestions cliquables ───────────────────────────── */
const STARTER_SUGGESTIONS = [
  "Résume un document que je vais te donner",
  "Prépare le compte rendu d'une réunion",
  "Surveille une page web et prévient-moi si elle change",
];

/** Puces sous le message d'accueil : idées de demandes, puis quelques workflows existants à lancer. */
function renderWelcomeSuggestions() {
  removeElementById("chat-suggestions");
  const welcome = document.getElementById("chat-welcome-message");
  if (!welcome) return;
  const container = document.createElement("div");
  container.id = "chat-suggestions";
  container.className = "chat-suggestions";
  const existing = (appState.workflows || []).slice(0, 3);
  container.innerHTML = html`
    <div class="suggestions-title">Essayez par exemple</div>
    <div class="suggestions-row">${STARTER_SUGGESTIONS.map(text => html`<button class="suggestion-chip" data-text="${text}">${text}</button>`)}</div>
    ${existing.length ? html`
      <div class="suggestions-title">Ou lancez un de vos workflows</div>
      <div class="suggestions-row">${existing.map(workflow => html`<button class="suggestion-chip" data-open="${workflow.filename}">▶ ${friendlyWorkflowTitle(workflow)}</button>`)}</div>` : ""}`;
  container.onclick = event => {
    const chip = event.target.closest(".suggestion-chip");
    if (!chip) return;
    if (chip.dataset.open) { removeElementById("chat-suggestions"); openWorkflowCard(chip.dataset.open); return; }
    inputTextarea.value = chip.dataset.text;
    sendChatMessage();
  };
  welcome.after(container);
}

async function bootChatApp() {
  for (let attempt = 0; attempt < 40; attempt++) {
    const result = await callBackend("portal_list");
    if (result && result.cards) {
      appState.workflows   = result.cards;
      appState.recentRuns  = result.recent || [];
      appState.busy        = !!result.busy;
      renderWorkflowList();
      renderWelcomeSuggestions();
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

/* ── Barre latérale : liste de workflows ───────────────────────────────── */
function renderWorkflowList() {
  const listElement = document.getElementById("chat-workflow-list");
  if (!listElement) return;
  if (!appState.workflows.length) {
    listElement.innerHTML = html`<p class="hint-text" style="padding:12px 16px">Aucun workflow.</p>`;
    return;
  }
  const collapsed = new Set(appState.collapsedCategories);
  const itemHtml = workflow => html`
    <div class="wf-list-item" data-filename="${workflow.filename}" data-category="${workflow.category || ""}"
         onclick="openWorkflowCard('${workflow.filename}')">
      <div class="wf-list-title"><span class="wf-list-ico">${workflowIcon(workflow)}</span><span class="wf-list-name">${friendlyWorkflowTitle(workflow)}</span>${workflow.status && workflow.status !== "active" ? html`<span class="status-badge is-${workflow.status}">${workflow.status === "draft" ? "Brouillon" : "À valider"}</span>` : ""}</div>
      ${workflow.summary ? html`<div class="wf-list-desc">${workflow.summary}</div>` : ""}
    </div>`;
  // Aucune catégorie : liste à plat. Sinon, regroupée comme dans le portail (en-tête teinté + effectif).
  if (!appState.workflows.some(workflow => workflow.category)) {
    listElement.innerHTML = appState.workflows.map(itemHtml).join("");
    return;
  }
  listElement.innerHTML = groupByCategory(appState.workflows).map(group => html`
    <div class="wf-list-group ${collapsed.has(group.name) ? "is-collapsed" : ""}" style="--cat-hue:${categoryHue(group.name)}">
      <button type="button" class="wf-list-group-head" data-toggle-category="${group.name}" aria-expanded="${!collapsed.has(group.name)}">
        <span class="wf-list-group-caret" aria-hidden="true">▾</span><i class="wf-list-group-dot"></i>${group.name || NO_CATEGORY_LABEL}<span class="wf-list-group-count">${group.workflows.length}</span>
      </button>
      ${group.workflows.map(itemHtml)}
    </div>`).join("");
  const searchInput = document.querySelector(".workflow-search-wrap input");
  if (searchInput && searchInput.value) filterWorkflowList(searchInput.value);
}

/* Catégories repliables : même état (et même mémoire locale) que le portail. */
function loadCollapsedCategories() {
  try {
    const saved = JSON.parse(localStorage.getItem("fp-collapsed-categories") || "[]");
    if (Array.isArray(saved)) appState.collapsedCategories = saved.map(String);
  } catch (error) { /* mémoire locale indisponible : tout reste déplié */ }
}

function toggleCategoryFromChat(name) {
  const collapsed = new Set(appState.collapsedCategories);
  collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name);
  appState.collapsedCategories = [...collapsed];
  try { localStorage.setItem("fp-collapsed-categories", JSON.stringify(appState.collapsedCategories)); } catch (error) { /* ignoré */ }
  renderWorkflowList();
}

document.addEventListener("click", event => {
  const head = event.target.closest("#chat-workflow-list [data-toggle-category]");
  if (head) toggleCategoryFromChat(head.dataset.toggleCategory);
});
loadCollapsedCategories();

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

/** Icône d'un workflow : celle de son étape la plus parlante (l'IA d'abord, sinon la première étape utile). */
function workflowIcon(workflow) {
  const steps = workflow.steps || [];
  const pick = steps.find(step => step.type === "agent")
            || steps.find(step => !["log", "end", "transform"].includes(step.type))
            || steps[0];
  return friendlyStepIcon(pick);
}

/** Filtre les items de la liste par texte de recherche. */
function filterWorkflowList(query) {
  const lowerQuery = query.toLowerCase();
  // Pendant une recherche, les catégories repliées s'ouvrent pour montrer les résultats.
  document.getElementById("chat-workflow-list").classList.toggle("is-searching", !!query);
  document.querySelectorAll("#chat-workflow-list .wf-list-item").forEach(item => {
    const text = `${item.textContent} ${item.dataset.category || ""}`.toLowerCase();
    item.style.display = text.includes(lowerQuery) ? "" : "none";
  });
  // Un groupe dont tous les workflows sont masqués disparaît aussi.
  document.querySelectorAll("#chat-workflow-list .wf-list-group").forEach(group => {
    group.style.display = [...group.querySelectorAll(".wf-list-item")].some(item => item.style.display !== "none") ? "" : "none";
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
  renderWelcomeSuggestions();
  renderChatInputControls();
  inputTextarea.focus();
}

/* ── Ajout de messages dans la conversation ─────────────────────────────── */
function appendUserMessage(text) {
  removeElementById("chat-suggestions");        // les suggestions ne servent qu'au début de la conversation
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
/** Pastille de catégorie (rangement du portail) ; vide si le workflow n'en a pas. Teinte stable calculée depuis le nom. */
function cardCategoryHtml(workflow) {
  return workflow.category
    ? html`<span class="wf-cat" style="--cat-hue:${categoryHue(workflow.category)}">${workflow.category}</span>`
    : "";
}

function appendWorkflowCardMessage(workflow) {
  const steps = workflow.steps || [];
  const stepTimelineHtml = steps.map((step, index) => html`
    <div class="step-dot ${step.type}"></div>${index < steps.length - 1 ? html`<div class="step-connector"></div>` : ""}`)
    .concat(steps.length ? [] : [html`<div class="step-dot"></div>`]);

  const stepCount = steps.length;
  const tagsHtml = Object.keys(workflow.tags || {}).map(key =>
    html`<span class="tag tag-${stepTypeToTagClass(key)}">${key}</span>`
  ).concat([html`<span class="tag">${stepCount} étape${stepCount !== 1 ? "s" : ""}</span>`]);

  // Vue simple : les premières étapes en clair (icône + titre), sans types ni identifiants.
  const SIMPLE_STEPS_SHOWN = 6;
  const hiddenCount = steps.length - SIMPLE_STEPS_SHOWN;
  const simpleStepsHtml = steps.slice(0, SIMPLE_STEPS_SHOWN).map(step => html`
      <li class="sc-step"><span class="sc-ico">${friendlyStepIcon(step)}</span><span class="sc-step-main"><span>${friendlyStepTitle(step)}</span></span></li>`)
    .concat(hiddenCount > 0 ? [html`<li class="sc-step" style="color:var(--mute)">… et ${hiddenCount} autre${hiddenCount > 1 ? "s" : ""} étape${hiddenCount > 1 ? "s" : ""}</li>`] : []);

  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.innerHTML = html`
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="wf-card" data-filename="${workflow.filename}">
      <div class="wf-header">
        <div class="wf-name"><span class="simple-only">${friendlyWorkflowTitle(workflow)}</span><span class="dev-only">${workflow.title}</span> <span class="wf-status">${cardStatusBadgeHtml(workflow)}</span> <span class="wf-cat-slot">${cardCategoryHtml(workflow)}</span></div>
        <div class="wf-desc">${workflow.summary || ""}</div>
      </div>
      <div class="tag-list dev-only" style="padding:0 16px 10px">${tagsHtml}</div>
      <div class="wf-step-tl dev-only">${stepTimelineHtml}</div>
      <ol class="sc-steps simple-only">${simpleStepsHtml}</ol>
      <div class="card-footer" style="padding:10px 16px;border-top:1px solid var(--line);display:flex;gap:7px;flex-wrap:wrap">${cardFooterHtml(workflow)}</div>
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
  const tagClasses = { file_write: "fw", python: "py", shell: "sh", agent: "ai" };
  return tagClasses[stepType] || "";
}

/* ── Statut d'un workflow écrit depuis le chat : brouillon → testé → validé ──────────────────────────────── */
/** Pastille de statut ; rien pour un workflow actif. */
function cardStatusBadgeHtml(workflow) {
  if (workflow.status === "draft")  return html`<span class="status-badge is-draft" title="À tester avant utilisation">Brouillon</span>`;
  if (workflow.status === "tested") return html`<span class="status-badge is-tested" title="Test réussi : il reste à le valider">Testé, à valider</span>`;
  return "";
}

/** Boutons de la carte selon le statut : Tester (brouillon), Valider (testé), Lancer (actif). */
function cardFooterHtml(workflow) {
  const filename = workflow.filename;
  const run = (label, primary = true) => html`<button class="button-primary ${primary ? "" : "is-ghost"}" data-filename="${filename}"
        onclick="launchWorkflowFromChat(this.dataset.filename, this)">${label}</button>`;
  const lead = workflow.status === "draft" ? run("▶ Tester")
    : workflow.status === "tested" ? html`<button class="button-primary" data-filename="${filename}" onclick="validateWorkflowFromChat(this)">✓ Valider ce workflow</button>${run("↻ Tester à nouveau", false)}`
    : run("▶ Lancer");
  return html`${lead}
        <button class="button-primary is-ghost simple-only" onclick="askModificationFromChat(this)">✏ Modifier</button>
        <button class="button-primary is-ghost dev-only" data-filename="${filename}" onclick="openYamlPanel(this.dataset.filename, true)">✏ Modifier</button>
        <button class="button-primary is-ghost dev-only" data-filename="${filename}" onclick="openYamlPanel(this.dataset.filename, false)">⌄ YAML</button>`;
}

/** Remet à jour badge et boutons de toutes les cartes affichées pour ce workflow (le statut vient de changer). */
function refreshWorkflowCards(filename) {
  const workflow = findCard(filename);
  if (!workflow) return;
  document.querySelectorAll(".wf-card").forEach(card => {
    if (card.dataset.filename !== filename) return;
    card.querySelector(".wf-status").innerHTML = cardStatusBadgeHtml(workflow);
    card.querySelector(".wf-cat-slot").innerHTML = cardCategoryHtml(workflow);
    card.querySelector(".card-footer").innerHTML = cardFooterHtml(workflow);
  });
  renderWorkflowList();
}

/** L'utilisateur valide un brouillon dont le test s'est bien passé. */
async function validateWorkflowFromChat(button) {
  const filename = button.dataset.filename;
  button.disabled = true;
  const result = await callBackend("portal_activate", filename);
  if (!result || !result.ok) {
    button.disabled = false;
    appendAssistantMessage(html`<span style="color:var(--bad)">${(result && result.error) || "Validation impossible."}</span>`);
    scrollMessagesToBottom();
    return;
  }
  await reloadWorkflowList();
  refreshWorkflowCards(filename);
  document.querySelectorAll(".validation-banner").forEach(banner => {
    if (banner.dataset.filename === filename) banner.innerHTML = html`<span style="color:var(--ok)">✓ Workflow validé. Vous pouvez le lancer quand vous voulez.</span>`;
  });
  appendAssistantMessage(html`<span style="color:var(--ok)">✓ Workflow validé : vous pouvez maintenant le lancer.</span>`);
  scrollMessagesToBottom();
}

/* ── Lancement d'un workflow ────────────────────────────────────────────── */
async function launchWorkflowFromChat(filename, launchButton) {
  if (appState.busy) return;
  launchButton.disabled = true;
  launchButton.innerHTML = `<span class="spin">⟳</span> En cours…`;
  appState.busy = true;
  appState.liveRun = {
    filename,
    running: true,
    stopping: false,
    stepStates: {},
    warnings: [],
    runId: null,
  };
  updateEngineStatusChip();
  renderLiveRunCard();

  const result = await callBackend("portal_run", filename, {});
  if (!result.ok) {
    launchButton.disabled = false;
    refreshWorkflowCards(filename);
    appState.busy = false;
    appState.liveRun = null;
    removeElementById("chat-live-run");
    updateEngineStatusChip();
    renderChatInputControls();
    appendAssistantMessage(html`<span style="color:var(--bad)">Échec du lancement : ${result.error}</span>`);
    scrollMessagesToBottom();
  }
  // La suite arrive via receiveEvent → finishLiveRun (défini ci-dessous).
}

/* ── Rendu du lancement en direct (surchargé depuis engine-events.js) ───── */
/**
 * Appelé par receiveEvent à chaque événement step_* / workflow_*.
 * Met à jour la puce de statut et affiche la porte (saisie / validation)
 * sous forme de bulle dans la conversation.
 */
function renderLiveRun() {
  updateEngineStatusChip();
  renderLiveRunCard();
  renderChatGate();
}

/**
 * Carte de suivi du lancement, dans la conversation : barre de progression, une ligne par étape
 * (faite / en cours / à venir) et bouton Arrêter. Vue simple : icône + titre ; vue développeur : type + identifiant.
 * Mise à jour en place à chaque événement ; elle disparaît quand le bilan du lancement arrive.
 */
function renderLiveRunCard() {
  const liveRun = appState.liveRun;
  if (!liveRun || !liveRun.running) { removeElementById("chat-live-run"); return; }
  const workflow = findCard(liveRun.filename);
  const steps = (workflow && workflow.steps) || [];
  const states = liveRun.stepStates || {};
  const isDone = step => states[step.id] === "ok" || states[step.id] === "skip";
  const doneCount = steps.filter(isDone).length;
  const percent = steps.length ? Math.round(100 * doneCount / steps.length) : 0;
  const waiting = !!liveRun.gate;
  const statusLabel = liveRun.stopping ? "Arrêt…" : waiting ? "Attend votre réponse" : "En cours";
  const statusClass = waiting ? "wait" : "running";

  const rowsHtml = steps.map(step => {
    const state = states[step.id];
    const rowClass = isDone(step) ? "done" : state === "run" ? "active" : state === "err" ? "err" : state === "stop" ? "err" : "pending";
    const mark = state === "ok" ? "✓" : state === "skip" ? "↷" : state === "err" ? "✕" : state === "stop" ? "⏹" : "";
    return html`
      <li class="sc-step ${rowClass}">
        <span class="sc-ico simple-only">${friendlyStepIcon(step)}</span>
        <span class="tag tag-${step.type} dev-only">${STEP_TYPE_LABELS[step.type] || step.type || "?"}</span>
        <span class="sc-step-main"><span class="simple-only">${friendlyStepTitle(step)}</span><span class="dev-only">${step.label || step.id}</span></span>
        <small class="dev-only" style="color:var(--mute);font-family:var(--font-mono)">${step.id}</small>
        <span class="sc-check">${mark}</span>
      </li>`;
  });

  let element = document.getElementById("chat-live-run");
  if (!element) {
    element = document.createElement("div");
    element.className = "chat-message assistant";
    element.id = "chat-live-run";
    messagesContainer.insertBefore(element, scrollAnchorElement);
  }
  element.innerHTML = html`
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="live-run-card">
      <div class="live-run-head">
        <b>${workflow ? friendlyWorkflowTitle(workflow) || workflow.filename : liveRun.filename}</b>
        <span class="sc-status ${statusClass}">${statusLabel}</span>
      </div>
      <div class="sc-progress"><div class="sc-bar"><div class="fill" style="width:${percent}%"></div></div><span class="sc-count">${doneCount}/${steps.length}</span></div>
      <ol class="sc-steps" style="padding:0">${rowsHtml}</ol>
      <div class="button-row"><button class="button-primary is-ghost" onclick="stopRunFromChat(this)" ${liveRun.stopping ? "disabled" : ""}>⏹ Arrêter</button></div>
    </div>`;
  scrollMessagesToBottom();
}

/** Arrête le lancement en cours (une étape CSAgent ou un script est interrompu ; sinon le workflow s'arrête après l'étape). */
async function stopRunFromChat(button) {
  button.disabled = true;
  if (appState.liveRun) appState.liveRun = { ...appState.liveRun, stopping: true };
  renderLiveRunCard();
  await callBackend("portal_stop");
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
  const liveRunFilename = appState.liveRun && appState.liveRun.filename;
  removeElementById("chat-live-run");    // le bilan ci-dessous remplace la carte de suivi
  appState.busy = false;
  updateEngineStatusChip();
  renderChatInputControls();

  await reloadWorkflowList();
  if (liveRunFilename) refreshWorkflowCards(liveRunFilename);       // remet les boutons de la carte (le statut a pu changer)

  const runDetailResult = await callBackend("portal_run_detail", savedEvent.run_id);
  if (!runDetailResult.ok) {
    appendAssistantMessage(html`<span style="color:var(--bad)">Lancement terminé · résultat indisponible.</span>`);
    scrollMessagesToBottom();
    return;
  }
  appendRunSummaryMessage(runDetailResult.run, liveRunFilename);
  scrollMessagesToBottom();
}

/** Ajoute le bilan d'un lancement dans la conversation. */
function appendRunSummaryMessage(run, filename) {
  const isSuccess  = run.status === "ok";
  const statusLabel = RUN_STATUS_LABELS[run.status] || run.status;
  const toneClass   = isSuccess ? "" : "is-error";
  const failedStep  = (run.steps || []).find(step => ["error", "rejected", "cancelled"].includes(step.status));
  const workflowStatus = filename && findCard(filename) ? findCard(filename).status : "active";
  const reason = failedStep && String(failedStep.output || "").startsWith("Accès refusé") ? failedStep.output : "";

  const stepsHtml = (run.steps || [])
    .filter(step => step.status !== "notrun")
    .map(step => html`
      <li class="${step.status === "ok" ? "ok" : ""}">
        <span class="step-icon">${step.status === "ok" ? "✓" : step.status === "skipped" ? "↷" : "✕"}</span>
        <div>
          <span class="simple-only">${friendlyStepTitle(step)}</span><span class="dev-only">${step.label || step.id}</span>
          <small class="dev-only">${step.id} · ${formatDuration(step.duration)}</small>
        </div>
      </li>
    `);

  const filesHtml = (run.files || []).map(file => html`
    <div class="file-pill" onclick="openRunFileFromChat('${file.path}')" style="cursor:pointer">
      📄 ${file.path.split("/").pop()}
    </div>
  `);

  const messageElement = document.createElement("div");
  messageElement.className = "chat-message assistant";
  messageElement.innerHTML = html`
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="run-summary ${toneClass}">
      <div class="run-summary-header">
        ${isSuccess ? "✓" : "✕"} ${statusLabel}
        ${isSuccess ? "" : html`
          <span class="simple-only" style="font-weight:400;font-size:13px">${failedStep ? html` · L'étape « ${failedStep.label || failedStep.id} » n'a pas abouti.${reason ? html` ${reason}` : ""}` : ""}</span>
          <span class="dev-only" style="font-weight:400;font-size:13px">${run.error ? html` · ${run.error}` : ""}</span>`}
        <span class="run-id-badge dev-only">${run.id}</span>
      </div>
      <ol class="step-list">${stepsHtml}</ol>
      ${isSuccess && workflowStatus === "tested" ? html`
        <div class="validation-banner" data-filename="${filename}" style="margin-top:10px">
          <span>✓ Le test s'est bien passé. Si le résultat vous convient, validez ce workflow pour l'utiliser.</span>
          <div class="button-row" style="margin-top:8px"><button class="button-primary" data-filename="${filename}" onclick="validateWorkflowFromChat(this)">✓ Valider ce workflow</button></div>
        </div>` : ""}
      ${filesHtml.length ? html`<div class="run-files" style="margin-top:8px">${filesHtml}</div>` : ""}
      <div class="button-row" style="margin-top:10px">
        <button class="button-primary is-ghost" style="font-size:12px"
                onclick="openRunResultFromChat('${run.id}')">Voir le résultat complet</button>
        ${!isSuccess && filename ? html`
          <button class="button-primary" style="font-size:12px" data-filename="${filename}"
                  onclick="launchWorkflowFromChat(this.dataset.filename, this)">${workflowStatus === "active" ? "↻ Relancer" : "↻ Tester à nouveau"}</button>
          <button class="button-primary is-ghost" style="font-size:12px" data-filename="${filename}"
                  data-step="${failedStep ? (failedStep.label || failedStep.id) : ""}" data-error="${run.error || ""}"
                  onclick="askFixAfterFailure(this)">🛠 Demander à l'assistant de corriger</button>` : ""}
      </div>
    </div>
  `;
  messagesContainer.insertBefore(messageElement, scrollAnchorElement);
}

/** Après un échec : met le workflow en contexte et pré-remplit la demande de correction (l'utilisateur l'envoie). */
function askFixAfterFailure(button) {
  const { filename, step, error } = button.dataset;
  openWorkflowCard(filename);
  inputTextarea.value = `Le lancement a échoué${step ? ` à l'étape « ${step} »` : ""}${error ? ` (${error})` : ""}. Peux-tu corriger le workflow ?`;
  autoResizeTextarea(inputTextarea);
  renderChatInputControls();
  inputTextarea.focus();
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

/* ── Vocabulaire du chat : les libellés viennent du serveur (friendly.py : icône, verbe, titre, messages) ── */
// Replis pour un backend plus ancien qui n'enverrait pas ces champs.
const friendlyStepIcon  = step => (step && step.icon) || "▫️";
const friendlyStepKind  = step => (step && step.kind) || "Étape";
/** Titre d'une étape en vue simple : sa description, sinon son verbe ; jamais son identifiant. */
const friendlyStepTitle = step => (step && (step.label !== step.id && step.label || step.friendly)) || friendlyStepKind(step);
const friendlyWorkflowTitle = workflow => (workflow && (workflow.friendly_title || workflow.title)) || "";

/** Phrase de validation en langage courant (calculée par le serveur) ; vide = rien à montrer. */
function friendlyProblem(problem, step) {
  if ("friendly" in problem) return problem.friendly || "";
  return `« ${step.label || step.title} » est à revoir. Demandez à l'assistant de la corriger.`;
}

/* ── Proposition : étapes touchées (réponse structurée de assist.py) ────── */
/** Au-delà de ce nombre d'étapes, seules les étapes touchées sont montrées (les autres se déplient). */
const PROPOSAL_COMPACT_FROM = 8;

/**
 * Corps de la proposition à partir de `event.view` (workflow résumé) et `event.changes` (ce qui a bougé),
 * ou "" si le backend ne les fournit pas (ancienne version, YAML illisible) : l'appelant se rabat alors
 * sur le simple décompte de lignes.
 */
function proposalChangesHtml(event) {
  const view = event.view;
  const changes = event.changes;
  if (!view || !changes || !(view.steps || []).length) return "";

  const created     = !!changes.created;                                 // point de départ vide : tout est nouveau
  const addedIds    = new Set(changes.added || []);
  const changedKeys = new Map((changes.changed || []).map(entry => [entry.id, entry.keys || []]));
  const removed     = changes.removed || [];
  const steps       = view.steps;

  let touchedCount = 0;
  const rowsHtml = steps.map((step, index) => {
    const isNew = !created && addedIds.has(step.id);
    const isMod = !created && !isNew && changedKeys.has(step.id);
    if (isNew || isMod) touchedCount++;
    const state    = isNew ? "is-new" : isMod ? "is-mod" : "is-same";
    const problems = (step.problems || []).filter(problem => problem.level === "error" || problem.level === "warn");
    const badges = html`
        ${isNew ? html`<span class="proposal-badge new">Nouveau</span>` : ""}
        ${isMod ? html`<span class="proposal-badge mod" title="${"Modifié : " + changedKeys.get(step.id).join(", ")}">Modifié</span>` : ""}
        ${step.autonomous ? html`<span class="proposal-badge warn" title="L'assistant décide seul de la marche à suivre">Décide seul</span>` : ""}
        ${step.conditional ? html`<span class="proposal-badge" title="Cette étape n'a lieu que dans certains cas">Selon le cas</span>` : ""}`;
    // Plusieurs messages techniques peuvent se traduire par la même phrase : on ne la répète pas.
    const problemsFor = render => {
      const seen = new Set();
      return problems.filter(problem => {
        const text = render(problem);
        const key = problem.level + text;
        return !text || seen.has(key) ? false : (seen.add(key), true);
      }).map(problem =>
        html`<span class="proposal-problem ${problem.level}">${problem.level === "error" ? "✕" : "⚠"} ${render(problem)}</span>`);
    };
    // Vue simple : icône, titre, verbe ; erreurs reformulées.  Vue développeur : type, titre, id ; messages bruts.
    return html`
      <li class="proposal-step ${state} simple-only">
        <span class="sc-ico">${friendlyStepIcon(step)}</span>
        <span class="sc-step-main"><span class="proposal-step-title">${step.label || step.title}</span>${(step.label || step.title) === friendlyStepKind(step) ? "" : html`<span class="sc-step-kind">${friendlyStepKind(step)}</span>`}</span>
        <span class="sc-step-badges">${badges}</span>
        <span class="sc-step-problems">${problemsFor(problem => friendlyProblem(problem, step))}</span>
      </li>
      <li class="proposal-step ${state} dev-only">
        <span class="proposal-step-num">${index + 1}</span>
        <span class="tag tag-${step.type}">${STEP_TYPE_LABELS[step.type] || step.type || "?"}</span>
        <span class="proposal-step-title">${step.title} <small style="color:var(--mute);font-family:var(--font-mono)">${step.id}</small></span>
        ${badges}
        ${problemsFor(problem => problem.message)}
      </li>
    `;
  });

  const compact     = !created && steps.length > PROPOSAL_COMPACT_FROM && touchedCount > 0;
  const hiddenCount = steps.length - touchedCount;
  const categoryChange = changes.category;                                     // { from, to } ou null ("" = aucune)
  const nothingTouched = !created && !touchedCount && !removed.length && !changes.reordered && !categoryChange;

  return html`
    <div class="proposal-steps-wrap">
      <ol class="proposal-steps ${compact ? "is-compact" : ""}">${rowsHtml}</ol>
      ${compact ? html`<button type="button" class="proposal-toggle"
          onclick="this.closest('.proposal-steps-wrap').classList.toggle('show-all')">Voir ou masquer les ${hiddenCount} étapes qui ne changent pas</button>` : ""}
      ${removed.length ? html`<p class="proposal-removed">Retiré : ${joinHtml(removed.map(entry => html`<s>${entry.title}</s>`), ", ")}</p>` : ""}
      ${changes.reordered ? html`<p class="proposal-note">L'ordre des étapes a changé.</p>` : ""}
      ${categoryChange ? html`<p class="proposal-note">Catégorie : ${categoryChange.from ? html`<s>${categoryChange.from}</s> → ` : ""}${categoryChange.to ? html`<b>${categoryChange.to}</b>` : "aucune"}</p>` : ""}
      ${nothingTouched ? html`<p class="proposal-note">Les étapes restent identiques : seul le descriptif a changé.</p>` : ""}
      ${view.branches ? html`<p class="proposal-note warn">Ces étapes ne se suivent pas toujours dans cet ordre : certaines dépendent de vos réponses.</p>` : ""}
    </div>
  `;
}

/** Nom de fichier libre (« mon-rapport.yaml », puis « mon-rapport-2.yaml »…) déduit du titre du workflow. */
function freeWorkflowFilename(title) {
  const base = String(title || "nouveau-workflow").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50) || "nouveau-workflow";
  let candidate = base + ".yaml";
  for (let n = 2; findCard(candidate); n++) candidate = `${base}-${n}.yaml`;
  return candidate;
}

/**
 * Ajoute une bulle de proposition dans la conversation.
 * L'utilisateur peut Appliquer (sauvegarde directe) ou Rejeter, sans jamais
 * voir le YAML brut.
 */
function appendYamlProposalMessage(event) {
  const filename = pendingAssistFilename || event.workflow || "";

  // Résumé compact des modifications : nombre de lignes ajoutées / supprimées.
  const diff = event.diff || [];
  const addCount = diff.filter(row => row.op === "add").length;
  const delCount = diff.filter(row => row.op === "del").length;
  const changeSummary = diff.length
    ? `${addCount > 0 ? `+${addCount}` : ""}${addCount > 0 && delCount > 0 ? " / " : ""}${delCount > 0 ? `−${delCount}` : ""} ligne${diff.length > 1 ? "s" : ""}`
    : "";

  // Réponse structurée (étapes touchées) si le backend la fournit ; sinon le décompte de lignes ci-dessus.
  const structuredChangesHtml = proposalChangesHtml(event);

  const issuesHtml = (event.issues || []).filter(i => i.level === "error").length
    ? html`<p style="color:var(--bad);font-size:13px;margin:6px 0 0">⚠ Cette version ne peut pas encore être enregistrée. Demandez-moi de la corriger.</p>`
    : "";

  const proposalElement = document.createElement("div");
  proposalElement.className = "chat-message assistant";
  proposalElement.id = "chat-proposal-" + Date.now();
  proposalElement.innerHTML = html`
    <div class="chat-avatar assistant-avatar">⚡</div>
    <div class="chat-bubble" style="border-color:var(--accent)">
      <b style="display:block;margin-bottom:5px">${event.changes?.created ? "Voici ce que je vous propose" : "Voici ce que je changerais"}${filename ? html` · <span style="font-weight:400;font-size:12px;color:var(--mute)">${filename}</span>` : ""}</b>
      ${event.answer ? html`<p style="margin:0 0 8px">${event.answer}</p>` : ""}
      ${structuredChangesHtml || (changeSummary ? html`<p style="margin:0 0 10px;font-size:12px;color:var(--mute);font-family:var(--font-mono)">${changeSummary}</p>` : "")}
      ${issuesHtml}
      <div class="button-row" style="margin-top:10px">
        <button class="button-primary" id="${proposalElement.id}-apply">✓ Appliquer</button>
        <button class="button-primary is-ghost" id="${proposalElement.id}-reject">✕ Rejeter</button>
      </div>
    </div>
  `;
  messagesContainer.insertBefore(proposalElement, scrollAnchorElement);

  const applyButton  = document.getElementById(proposalElement.id + "-apply");
  const rejectButton = document.getElementById(proposalElement.id + "-reject");

  applyButton.onclick = async () => {
    // Pas de workflow ouvert : c'est une création, le nom du fichier est déduit du titre.
    const isCreation = !filename;
    let targetName = filename;
    if (isCreation) targetName = freeWorkflowFilename(event.view?.name);
    applyButton.disabled = true;
    applyButton.textContent = "⟳ Enregistrement…";
    rejectButton.disabled = true;

    const saveResult = isCreation
      ? await callBackend("portal_create", targetName, event.draft, true)     // true : brouillon, à tester puis valider
      : await callBackend("portal_save", filename, event.draft, true);
    if (saveResult.ok) {
      // Remplace la bulle de proposition par un accusé de réception.
      proposalElement.querySelector(".chat-bubble").innerHTML = html`
        <span style="color:var(--ok)">✓ ${isCreation ? "Workflow créé en brouillon" : "Modifications enregistrées"} :&nbsp;
          <b class="simple-only">${event.view?.title || targetName}</b><b class="dev-only">${targetName}</b></span><br>
        <span style="color:var(--mute);font-size:13px">Testez-le avant de l'utiliser : cliquez sur « Tester » sur la carte ci-dessous.</span>
      `;
      await reloadWorkflowList();
      // Recharge la carte active si elle correspond au fichier modifié.
      if (isCreation || activeWorkflowCardElement?.dataset.filename === targetName) {
        const updatedWorkflow = findCard(targetName);
        if (updatedWorkflow) appendWorkflowCardMessage(updatedWorkflow);
      }
    } else {
      proposalElement.querySelector(".chat-bubble").innerHTML = html`
        <span style="color:var(--bad)">✕ Impossible d'enregistrer : ${saveResult.error}</span>
      `;
    }
  };

  rejectButton.onclick = () => {
    proposalElement.querySelector(".chat-bubble").innerHTML = html`
      <span style="color:var(--mute)">Proposition rejetée.</span>
    `;
  };
}

/* ── Envoi d'un message à CSAgent ───────────────────────────────────────── */
async function sendChatMessage() {
  const text = inputTextarea.value.trim();
  if (!text || appState.busy) return;

  inputTextarea.value = "";
  inputTextarea.style.height = "";
  appState.busy = true;
  renderChatInputControls();

  appendUserMessage(text);
  scrollMessagesToBottom();

  const typingMessageId = showTypingIndicator();

  // Contexte YAML : si un workflow est affiché dans la conversation, on le charge
  // pour que CSAgent puisse s'en servir comme base. Sinon, chaîne vide.
  let baseYaml = "";
  pendingAssistFilename = null;
  if (activeWorkflowCardElement) {
    const activeFilename = activeWorkflowCardElement.dataset.filename;
    if (activeFilename) {
      const yamlResult = await callBackend("portal_read", activeFilename);
      if (yamlResult && yamlResult.ok) {
        baseYaml = yamlResult.yaml || "";
        pendingAssistFilename = activeFilename;
      }
    }
  }

  const result = await callBackend("portal_assist", baseYaml, text, true);   // true : bac à sable (output/ et documents/)
  removeElementById(typingMessageId);

  if (!result.ok) {
    appState.busy = false;
    renderChatInputControls();
    appendAssistantMessage(html`<span style="color:var(--bad)">Erreur : ${result.error}</span>`);
    scrollMessagesToBottom();
  }
  // Si result.ok, la suite arrive via receiveEvent → handleAssistantEvent(assist_done).
}

/* ── Zone de saisie ─────────────────────────────────────────────────────── */
/**
 * Met à jour l'état du bouton Envoyer et le chip de contexte workflow.
 * Le chip apparaît quand un workflow est actif, avec son nom et un × pour l'effacer.
 * Le placeholder s'adapte au contexte courant.
 */
function renderChatInputControls() {
  sendButton.disabled = appState.busy || !inputTextarea.value.trim();

  const contextBar      = document.getElementById("chat-context-bar");
  const contextFilename = document.getElementById("chat-context-filename");
  const filename        = activeWorkflowCardElement?.dataset.filename;

  if (filename) {
    // Vue simple : le titre lisible du workflow ; vue développeur : le nom du fichier.
    const shownName = document.body.dataset.view === "dev" ? filename : friendlyWorkflowTitle(findCard(filename)) || filename;
    contextFilename.textContent = shownName;
    contextBar.classList.add("visible");
    inputTextarea.placeholder = `Décrivez ce que vous voulez changer dans « ${shownName} »…`;
  } else {
    contextBar.classList.remove("visible");
    inputTextarea.placeholder = "Décrivez votre workflow, ou demandez à lancer un existant…";
  }
}

function autoResizeTextarea(element) {
  element.style.height = "auto";
  element.style.height = Math.min(element.scrollHeight, 180) + "px";
}

function handleInputKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!sendButton.disabled) sendChatMessage();
  }
}

/* ── Panneau YAML ───────────────────────────────────────────────────────── */
/**
 * Ouvre le panneau latéral YAML.
 * @param {string}  filename  - nom du fichier workflow
 * @param {boolean} editMode  - true = édition + bouton Enregistrer ; false = lecture seule
 */
function openYamlPanel(filename, editMode = false) {
  const textarea    = document.getElementById("yaml-panel-textarea");
  const saveButton  = document.getElementById("yaml-save-button");
  const subtitle    = document.getElementById("yaml-panel-subtitle");
  const statusSpan  = document.getElementById("yaml-panel-status");

  document.getElementById("yaml-panel-title").textContent    = filename;
  document.getElementById("yaml-panel-filename").textContent = filename;
  textarea.value       = "# Chargement…";
  statusSpan.textContent = "";

  textarea.readOnly    = !editMode;
  saveButton.hidden    = !editMode;
  subtitle.textContent = editMode
    ? "Modifiez puis cliquez Enregistrer"
    : "Lecture seule · utilisez ✏ Modifier pour éditer";

  document.getElementById("yaml-panel-backdrop").classList.add("open");
  document.getElementById("yaml-panel-backdrop").dataset.filename = filename;

  callBackend("portal_read", filename).then(result => {
    if (result.ok) {
      textarea.value = result.yaml;
      if (editMode) textarea.focus();
    }
  });
}

function closeYamlPanel() {
  document.getElementById("yaml-panel-backdrop").classList.remove("open");
  document.getElementById("yaml-panel-status").textContent = "";
}

/** Enregistre le contenu du panneau YAML sur disque via portal_save. */
async function saveYamlFromPanel() {
  const filename   = document.getElementById("yaml-panel-backdrop").dataset.filename;
  const yamlText   = document.getElementById("yaml-panel-textarea").value;
  const saveButton = document.getElementById("yaml-save-button");
  const statusSpan = document.getElementById("yaml-panel-status");

  if (!filename) return;
  saveButton.disabled = true;
  saveButton.textContent = "⟳ Enregistrement…";
  statusSpan.textContent = "";

  const result = await callBackend("portal_save", filename, yamlText);

  saveButton.disabled = false;
  saveButton.textContent = "💾 Enregistrer";

  if (result.ok) {
    statusSpan.textContent = "✓ Enregistré";
    statusSpan.style.color = "var(--ok)";
    await reloadWorkflowList();
    // Met à jour la carte active dans la conversation si elle correspond.
    if (activeWorkflowCardElement?.dataset.filename === filename) {
      const updatedWorkflow = findCard(filename);
      if (updatedWorkflow) {
        // Recharge la frise d'étapes dans la carte existante.
        const stepTimeline = activeWorkflowCardElement.querySelector(".wf-step-tl");
        if (stepTimeline) {
          const steps = updatedWorkflow.steps || [];
          stepTimeline.innerHTML = steps.map((step, i) =>
            `<div class="step-dot ${step.type}"></div>` +
            (i < steps.length - 1 ? `<div class="step-connector"></div>` : "")
          ).join("") || `<div class="step-dot"></div>`;
        }
      }
    }
    setTimeout(() => { statusSpan.textContent = ""; }, 3000);
  } else {
    statusSpan.textContent = "✕ " + (result.error || "Échec");
    statusSpan.style.color = "var(--bad)";
  }
}

function copyYamlToClipboard() {
  const yamlText   = document.getElementById("yaml-panel-textarea").value;
  const copyButton = document.getElementById("yaml-copy-button");
  navigator.clipboard?.writeText(yamlText).then(() => {
    copyButton.textContent = "✓ Copié";
    setTimeout(() => { copyButton.textContent = "📋 Copier"; }, 1500);
  }).catch(() => {});
}

/* ── Défilement ─────────────────────────────────────────────────────────── */
function scrollMessagesToBottom() {
  scrollAnchorElement.scrollIntoView({ behavior: "smooth" });
}

/* ── Compatibilité avec engine-events.js ────────────────────────────────── */
// Ces fonctions sont appelées par engine-events.js mais sans objet dans l'interface chat.
function renderEnvChips() {}

/* ── Câblage des événements ─────────────────────────────────────────────── */
sendButton.onclick = sendChatMessage;

// × dans le chip de contexte : désactive le workflow actif sans toucher à la conversation.
document.getElementById("chat-context-clear").onclick = () => {
  activeWorkflowCardElement = null;
  pendingAssistFilename     = null;
  renderChatInputControls();
  inputTextarea.focus();
};

inputTextarea.oninput = () => {
  autoResizeTextarea(inputTextarea);
  renderChatInputControls();
};
inputTextarea.onkeydown = handleInputKeydown;

document.getElementById("yaml-panel-backdrop").onclick = event => {
  if (event.target === document.getElementById("yaml-panel-backdrop")) closeYamlPanel();
};

// Thèmes : mêmes identifiants et même clé de stockage que le portail (ui/main.js).
const CHAT_THEMES = [["light", "Clair"], ["dark", "Sombre"], ["dracula", "Dracula"], ["yaru", "Yaru"], ["nocturne", "Nocturne"]];
function applyChatTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const label = (CHAT_THEMES.find(([id]) => id === theme) || CHAT_THEMES[0])[1];
  document.getElementById("theme-toggle-button").textContent = "Thème : " + label;
}
document.getElementById("theme-toggle-button").onclick = () => {
  const current = document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light");
  const next = CHAT_THEMES[(CHAT_THEMES.findIndex(([id]) => id === current) + 1) % CHAT_THEMES.length][0];
  applyChatTheme(next);
  try { localStorage.setItem("fp-theme", next); } catch (error) {}
};
try {
  const savedTheme = localStorage.getItem("fp-theme");
  if (CHAT_THEMES.some(([id]) => id === savedTheme)) applyChatTheme(savedTheme);
} catch (error) {}

/* ── Lancement ──────────────────────────────────────────────────────────── */
bootChatApp();
