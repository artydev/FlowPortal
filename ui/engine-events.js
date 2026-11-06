"use strict";
/**
 * Événements poussés par Python (`receiveEvent`, exposé à Eel) : aiguillage vers
 * le lancement en cours, l'assistant ou la dictée, et fin de lancement.
 */
/* ── événements du moteur ──────────────────────────────────────────── */
/**
 * Point d'entrée des événements poussés par Python (exposé à Eel plus bas).
 * Ordre de traitement :
 *  1. `portal_run_saved`  : fin du lancement, résultat enregistré → `finishLiveRun`
 *  2. `assist_*`          : progression de l'assistant → `handleAssistantEvent`
 *  3. événements de dictée (si une dictée est active) → `handleVoiceEvent`
 *  4. événements du moteur (`step_*`, `workflow_*`, `*_required`), ignorés si
 *     aucun lancement n'est en cours.
 * Pour les deux « portes » (validation / saisie), l'étape en attente est déduite
 * comme étant celle actuellement « run » : on suppose une étape active à la fois.
 */
function receiveEvent(event) {
  const liveRun = appState.liveRun,
    eventType = event && event.type;
  if (eventType == "portal_run_saved") {
    appState.liveRun = markRunSaved(liveRun, event.run_id);
    appState.busy = false;
    finishLiveRun(event);
    return;
  }
  if (eventType && eventType.indexOf("assist_") == 0) {
    handleAssistantEvent(event);
    return;
  }
  if (
    appState.voice &&
    (eventType == "user_input_progress" ||
      eventType == "user_input_transcribed" ||
      eventType == "user_input_error")
  ) {
    handleVoiceEvent(event);
    return;
  }
  if (!liveRun || !liveRun.running) return;
  appState.liveRun = reduceLiveRunEvent(liveRun, event);
  renderLiveRun();
}
/** Traite les événements de transcription : le texte reconnu est AJOUTÉ (nouvelle ligne) au contenu existant de la zone cible, jamais écrasé. */
function handleVoiceEvent(event) {
  const targetTextarea =
    appState.voice.target == "asst"
      ? $("#assistant-textarea")
      : $("#gate-input-textarea");
  const { voice, transcript } = reduceVoiceEvent(appState.voice, event, {
    hasTarget: !!targetTextarea,
  });
  appState.voice = voice;
  if (transcript)
    targetTextarea.value = appendTranscript(targetTextarea.value, transcript);
  if (event.type == "user_input_transcribed" && targetTextarea)
    targetTextarea.focus();
  renderVoiceControls();
}
/**
 * Fin d'un lancement : recharge la liste, puis, si le panneau du même workflow
 * est toujours ouvert, affiche le bilan (statut, durée, bouton « Voir le résultat »).
 */
async function finishLiveRun(event) {
  const liveRun = appState.liveRun;
  await reloadWorkflowList();
  if (
    !liveRun ||
    !appState.currentWorkflow ||
    appState.currentWorkflow.filename != liveRun.filename
  ) {
    renderEnvChips();
    return;
  }
  appState.runHistory = await callBackend("portal_runs", liveRun.filename, 8);
  if (!Array.isArray(appState.runHistory)) appState.runHistory = [];
  renderRunHistory();
  const lastRun = appState.runHistory[0];
  const outputElement = $("#live-run-output");
  if (!outputElement) return;
  if (lastRun) {
    const tone = runTone(lastRun.status);
    outputElement.innerHTML = html`
      <div class="run-summary ${tone}">
        <b>${RUN_STATUS_LABELS[lastRun.status]}</b> en ${formatDuration(lastRun.duration)}${lastRun.error ? html` · ${lastRun.error}` : ""}
        <div class="button-row" style="margin-top:8px">
          <button class="button-primary" id="panel-view-result-button">Voir le résultat</button>
        </div>
      </div>
    `;
    $("#panel-view-result-button").onclick = () => openRunResult(lastRun.id);
  }
  const stopButton = $("#stop-run-button"),
    stopNote = $("#stop-run-note");
  if (stopButton) stopButton.hidden = true;
  if (stopNote) stopNote.hidden = true;
  $("#launch-button").textContent = "Relancer";
  syncLaunchButton();
  renderEnvChips();
}
// Rend `receiveEvent` appelable depuis Python (nom identique des deux côtés).
eel.expose(receiveEvent, "receiveEvent");
