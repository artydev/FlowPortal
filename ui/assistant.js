"use strict";
/**
 * Assistant CSAgent de l'éditeur plein écran : conversation, propositions à
 * relire, accords demandés par l'agent et dictée vers l'assistant.
 */
/* ── assistant CSAgent ─────────────────────────────────────────────── */
/** Bascule le panneau latéral de l'éditeur : "p" = Problèmes, "a" = Assistant CSAgent. */
function selectSideTab(tabKey) {
  const isAssistantTab = tabKey == "a";
  $("#fullscreen-tab-issues").setAttribute("aria-selected", !isAssistantTab);
  $("#fullscreen-tab-assistant").setAttribute("aria-selected", isAssistantTab);
  $("#fullscreen-issue-list").hidden = isAssistantTab;
  $("#assistant-pane").hidden = !isAssistantTab;
  renderAssistant();
  if (isAssistantTab) {
    renderAssistantControls();
    $("#assistant-textarea").focus();
  }
}
$("#fullscreen-tab-issues").onclick = () => selectSideTab("p");
$("#fullscreen-tab-assistant").onclick = () => selectSideTab("a");
/**
 * Redessine l'assistant : journal, étape en cours, proposition (diff + problèmes
 * restants) et éventuelle demande d'accord de l'agent. Une proposition n'est
 * JAMAIS appliquée seule : l'utilisateur la relit, puis « Appliquer dans
 * l'éditeur » (voir `applyAssistantProposal`) ; elle modifie le brouillon sans enregistrer.
 */
function renderAssistant() {
  const assistant = appState.assistant;
  if (!assistant || !$("#assistant-messages")) return;
  $("#fullscreen-tab-assistant").textContent =
    `Assistant${assistant.busy || assistant.proposal ? " ●" : ""}`;
  const logElement = $("#assistant-messages");
  const introHtml =
    assistant.messages.length || assistant.busy
      ? ""
      : html`<p class="hint-text">Décrivez en une ou deux phrases ce que doit faire le workflow, ou ce qu'il faut changer. CSAgent prépare une proposition que vous relisez avant de l'appliquer ; rien n'est enregistré sans votre accord.</p>`;
  const messagesHtml = assistant.messages.map(
    (message) =>
      html`<div class="assistant-message ${message.role}">${message.text}</div>`,
  );
  const statusHtml = assistant.busy
    ? html`<div class="assistant-message is-status" role="status">${assistant.status || "CSAgent travaille…"}</div>`
    : "";
  logElement.innerHTML = html`${introHtml}${messagesHtml}${statusHtml}`;
  logElement.scrollTop = logElement.scrollHeight;
  const proposal = assistant.proposal,
    proposalElement = $("#assistant-proposal");
  if (!proposal) proposalElement.innerHTML = "";
  else {
    const errorCount = proposal.issues.filter((i) => i.level == "error").length;
    const issuesHtml = proposal.issues.length
      ? html`<div class="message-box">${errorCount ? html`<b>${errorCount} erreur${errorCount > 1 ? "s" : ""} de validation restante${errorCount > 1 ? "s" : ""}</b> : vous pourrez les corriger dans l'éditeur.` : html`<b>Avertissements</b>`}<ul>${proposal.issues.slice(0, 6).map((i) => html`<li>${i.line ? `L${i.line} · ` : ""}${i.message}</li>`)}</ul></div>`
      : "";
    const overwriteNoteHtml = assistant.overwriteArmed
      ? html`<p class="hint-text">Vous avez modifié le texte depuis la demande : l'appliquer remplace ces modifications.</p>`
      : html`<p class="hint-text">L'application modifie seulement l'éditeur ; enregistrez ensuite comme d'habitude.</p>`;
    proposalElement.innerHTML = html`
      <div class="proposal">
        <b>Proposition de CSAgent</b>
        ${proposal.answer ? html`<span>${proposal.answer}</span>` : ""}
        <div class="diff" tabindex="0" aria-label="Différences proposées">${proposal.diff.map(
          (row) =>
            html`<div class="diff-line ${row.op}"><i>${
              {
                add: "+",
                del: "−",
              }[row.op] || " "
            }</i><span>${row.text || ""}</span></div>`,
        )}</div>
        ${issuesHtml}
        <div class="button-row">
          <button class="button-primary" id="proposal-apply-button">${assistant.overwriteArmed ? "Appliquer quand même" : "Appliquer dans l'éditeur"}</button>
          <button class="button-primary is-ghost" id="proposal-reject-button">Rejeter</button>
        </div>
        ${overwriteNoteHtml}
      </div>
    `;
    $("#proposal-apply-button").onclick = applyAssistantProposal;
    $("#proposal-reject-button").onclick = () => {
      appState.assistant = rejectAssistantProposal(appState.assistant);
      renderAssistant();
    };
  }
  const gate = assistant.gate,
    gateContainer = $("#assistant-approval-gate");
  if (assistant.renderedGate !== gate) {
    assistant.renderedGate = gate;
    gateContainer.innerHTML = gate
      ? html`
        <div class="decision-gate">
          <b>CSAgent demande votre accord</b>
          <span>${gate.message}</span>
          <div class="button-row">
            <button class="button-primary" id="assistant-approve-button">Approuver</button>
            <button class="button-primary is-ghost" id="assistant-refuse-button">Refuser</button>
          </div>
        </div>
      `
      : "";
    if (gate) {
      $("#assistant-approve-button").onclick = () =>
        answerAssistantApproval("approved");
      $("#assistant-refuse-button").onclick = () =>
        answerAssistantApproval("rejected");
      $("#assistant-approve-button").focus();
    }
  }
  renderAssistantControls();
}
/** Réponse de l'utilisateur à une demande d'accord de l'agent. */
async function answerAssistantApproval(decision) {
  appState.assistant = closeAssistantGate(appState.assistant);
  renderAssistant();
  await callBackend("resolve_approval", decision);
}
/** État des boutons de l'assistant (Envoyer, Arrêter, Dicter) et messages de dictée. */
function renderAssistantControls() {
  const assistant = appState.assistant,
    textarea = $("#assistant-textarea");
  if (!assistant || !textarea) return;
  const voice =
      appState.voice && appState.voice.target == "asst" ? appState.voice : null,
    isRecording = !!voice && voice.state == "recording",
    isTranscribing = !!voice && voice.state == "transcribing";
  $("#assistant-send-button").disabled =
    assistant.busy || isRecording || isTranscribing || !textarea.value.trim();
  $("#assistant-stop-button").hidden = !assistant.busy;
  $("#assistant-stop-button").disabled = assistant.status == "Arrêt en cours…";
  const micButton = $("#assistant-mic-button");
  micButton.className = `button-primary ${isRecording ? "is-danger" : "is-ghost"}`;
  micButton.setAttribute("aria-pressed", isRecording);
  micButton.disabled = isTranscribing || assistant.busy;
  micButton.textContent = isRecording
    ? `⏹ Arrêter (${formatClock(voice.elapsedSeconds)})`
    : isTranscribing
      ? "… Transcription"
      : "🎙 Dicter";
  $("#assistant-voice-status").textContent = isRecording
    ? "Enregistrement en cours… parlez, puis cliquez sur Arrêter."
    : voice
      ? voice.message
      : "";
  const errorBadge = $("#assistant-voice-error");
  errorBadge.hidden = !(voice && voice.errorMessage);
  errorBadge.textContent = (voice && voice.errorMessage) || "";
}
/**
 * Envoie la demande à CSAgent avec le YAML courant comme base (`assistant.baseYaml`). La
 * suite arrive par événements `assist_*` (voir `handleAssistantEvent`). Une éventuelle
 * proposition précédente est écartée.
 */
async function sendToAssistant() {
  const textarea = $("#assistant-textarea"),
    text = textarea.value.trim();
  if (!text || appState.assistant.busy) return;
  textarea.value = "";
  appState.assistant = startAssistantRequest(
    appState.assistant,
    text,
    currentYaml(),
  );
  renderAssistant();
  const result = await callBackend(
    "portal_assist",
    appState.assistant.baseYaml,
    text,
  );
  if (!result.ok) {
    appState.assistant = failAssistantRequest(appState.assistant, result.error);
    renderAssistant();
  }
}
/**
 * Applique la proposition dans le brouillon (aucun enregistrement). Si le texte a
 * changé depuis l'envoi de la demande, un premier clic arme seulement la
 * confirmation (`assistant.overwriteArmed`, bouton « Appliquer quand même ») : l'application
 * écraserait ces modifications.
 */
function applyAssistantProposal() {
  const assistant = appState.assistant,
    proposal = assistant.proposal;
  if (!proposal || !appState.currentWorkflow) return;
  if (currentYaml() !== proposal.baseYaml && !assistant.overwriteArmed) {
    appState.assistant = armOverwrite(assistant);
    renderAssistant();
    return;
  }
  const filename = appState.currentWorkflow.filename;
  appState.drafts[filename] = proposal.draft;
  appState.currentWorkflow.justSaved = false;
  if ($("#fullscreen-editor-textarea"))
    $("#fullscreen-editor-textarea").value = proposal.draft;
  if ($("#panel-yaml-textarea"))
    $("#panel-yaml-textarea").value = proposal.draft;
  appState.assistant = markProposalApplied(assistant);
  validateYaml();
  renderAssistant();
  $("#fullscreen-editor-textarea").focus();
}
/**
 * Événements de l'assistant, ignorés si aucune demande n'est en cours :
 *  - assist_status : texte de progression
 *  - assist_event  : événement de l'agent (réflexion, appel d'outil, demande d'accord)
 *  - assist_done   : fin ; échec, « rien modifié », ou proposition à relire
 */
function handleAssistantEvent(event) {
  const updated = reduceAssistantEvent(appState.assistant, event);
  if (updated === appState.assistant) return;
  appState.assistant = updated;
  renderAssistant();
}
$("#assistant-send-button").onclick = sendToAssistant;
$("#assistant-stop-button").onclick = async () => {
  appState.assistant = markAssistantStopping(appState.assistant);
  renderAssistant();
  await callBackend("portal_stop");
};
$("#assistant-textarea").oninput = renderAssistantControls;
$("#assistant-textarea").onkeydown = (event) => {
  if (event.key == "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    if (!$("#assistant-send-button").disabled) sendToAssistant();
  }
};
// Dictée vers l'assistant : on prévient d'abord Python (`portal_assist_voice`) puis on enregistre.
$("#assistant-mic-button").onclick = async () => {
  const voice = appState.voice;
  if (voice && voice.target == "asst" && voice.state == "recording") {
    VoiceRecorder.stop();
    return;
  }
  appState.voice = {
    state: "idle",
    message: "",
    errorMessage: "",
    elapsedSeconds: 0,
    target: "asst",
  };
  const result = await callBackend("portal_assist_voice", true);
  if (!result.ok) {
    appState.voice.errorMessage = result.error || "Dictée indisponible.";
    renderVoiceControls();
    return;
  }
  VoiceRecorder.start();
};
