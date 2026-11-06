"use strict";
/**
 * Panneau latéral d'un workflow : paramètres, lancement, suivi en direct
 * (`renderLiveRun`), arrêt, validations humaines et saisies demandées.
 */
/* ── panneau ───────────────────────────────────────────────────────── */
/**
 * Coloration d'UNE ligne de YAML (clé, commentaire, valeur entre guillemets).
 * Volontairement simplifiée : ce n'est pas un parseur, les scalaires multilignes
 * ou les clés à guillemets ne sont pas reconnus. Sert uniquement à l'affichage
 * en lecture seule ; la validation réelle est faite par `portal_check` (Python).
 */
function highlightYamlLine(line) {
  const token = tokenizeYamlLine(line);
  if (token.kind == "comment")
    return html`<span class="yaml-comment">${token.text}</span>`;
  if (token.kind == "plain") return html`${token.text}`;
  const comment = token.comment
    ? html`<span class="yaml-comment">${token.comment}</span>`
    : "";
  return html`${token.indent}<span class="yaml-key">${token.key}</span>:${token.quoted ? html`<span class="yaml-quoted">${token.value}</span>` : token.value}${comment}`;
}
/**
 * Ouvre le panneau latéral d'un workflow : description, paramètres (champs
 * texte `data-input-name`), actions, étapes et historique.
 * @param {string}  f   nom de fichier du workflow
 * @param {boolean} run si vrai, lance aussitôt (bouton « Lancer » d'une carte,
 *                      ou « Relancer » depuis un résultat)
 * Le panneau est entièrement reconstruit à chaque appel : les handlers sont donc
 * réassignés ici, et les éléments `#launch-button`, `#stop-run-button`, `#live-run-gate`, `#live-run-output`… n'existent
 * que tant qu'il est ouvert (d'où les gardes `if (!$("#step-list"))` ailleurs).
 */
async function openWorkflowPanel(filename, launchNow) {
  const workflow = findCard(filename);
  if (!workflow) return;
  appState.currentWorkflow = workflow;
  appState.runHistory = await callBackend("portal_runs", filename, 8);
  if (!Array.isArray(appState.runHistory)) appState.runHistory = [];
  const inputEntries = Object.entries(workflow.inputs);
  $("#workflow-panel").innerHTML = html`
    <button class="button-secondary close-button" id="panel-close-button">Fermer</button>
    <h2>${workflow.title}</h2>
    <p style="margin:0;color:var(--mute)">${workflow.description || ""}</p>
    ${renderTags(workflow)}
    ${
      workflow.missing.length
        ? html`
            <div class="message-box">
              <b>Ce workflow ne peut pas être lancé :</b>
              <ul>${workflow.missing.map((missingItem) => html`<li>${missingItem}</li>`)}</ul>
            </div>
          `
        : ""
    }
    ${
      inputEntries.length
        ? html`<div class="inputs-form">${inputEntries.map(
            ([inputName, defaultValue]) => html`
                <div>
                  <label for="input-${inputName}">${inputName}</label>
                  <input type="text" id="input-${inputName}" data-input-name="${inputName}" value="${defaultValue}">
                </div>
              `,
          )}</div>`
        : html`<p style="margin:0;color:var(--mute)">Aucun paramètre.</p>`
    }
    <div class="button-row">
      <button class="button-primary" id="launch-button">Lancer le workflow</button>
      <button class="button-primary is-danger" id="stop-run-button" hidden>Arrêter</button>
      <button class="button-primary is-ghost" id="panel-yaml-toggle-button" aria-expanded="false">Voir le YAML</button>
      <button class="button-primary is-ghost" id="panel-duplicate-button">Dupliquer</button>
      <button class="button-primary is-ghost" id="panel-rename-button">Renommer</button>
      <button class="button-primary is-danger" id="panel-delete-button">Supprimer</button>
    </div>
    <p class="hint-text" id="stop-run-note" hidden></p>
    <div id="panel-yaml-section" hidden></div>
    <div id="live-run-gate"></div>
    <div id="live-run-output"></div>
    <ol class="step-list" id="step-list">${workflow.steps.map(
      (step, i) => html`
          <li id="step-${i}">
            <span class="step-icon">·</span>
            <span>${step.label}<small>${step.id} · ${STEP_TYPE_LABELS[step.type] || step.type}</small></span>
          </li>
        `,
    )}</ol>
    <div>
      <h3 style="font-size:16px;margin:0 0 8px">Derniers lancements</h3>
      <div id="run-history"></div>
    </div>
  `;
  renderRunHistory();
  const panelDialog = $("#workflow-panel-dialog");
  if (!panelDialog.open) panelDialog.showModal();
  $("#panel-close-button").onclick = () => panelDialog.close();
  $("#launch-button").onclick = () => launchWorkflow();
  $("#stop-run-button").onclick = () => requestStop();
  $("#panel-yaml-toggle-button").onclick = () => toggleYamlSection();
  $("#panel-delete-button").onclick = () =>
    askDeleteConfirmation(workflow.filename);
  $("#panel-duplicate-button").onclick = () =>
    openNameDialog("dup", workflow.filename);
  $("#panel-rename-button").onclick = () =>
    openNameDialog("ren", workflow.filename);
  $("#run-history").onclick = (event) => {
    const runButton = event.target.closest("[data-run-id]");
    if (runButton) openRunResult(runButton.dataset.runId);
  };
  syncLaunchButton();
  if (appState.liveRun && appState.liveRun.filename == filename)
    renderLiveRun();
  if (launchNow) launchWorkflow();
}
/** Active/désactive « Lancer » : impossible si prérequis manquants ou si un lancement est déjà en cours. */
function syncLaunchButton() {
  const launchButton = $("#launch-button");
  if (!launchButton || !appState.currentWorkflow) return;
  const workflow = appState.currentWorkflow;
  launchButton.disabled =
    !workflow.ready ||
    appState.busy ||
    (appState.liveRun && appState.liveRun.running);
}
/** Liste des derniers lancements du workflow ouvert (depuis `appState.runHistory`). */
function renderRunHistory() {
  const historyContainer = $("#run-history");
  if (!historyContainer) return;
  historyContainer.className = "run-history";
  historyContainer.innerHTML = appState.runHistory.length
    ? html`${appState.runHistory.map(
        (run) => html`
          <button class="${run.status}" data-run-id="${run.id}">
            <span>${formatDateTime(run.started)} · ${formatDuration(run.duration)}</span>
            <i>${RUN_STATUS_ICONS[run.status]} ${RUN_STATUS_LABELS[run.status]}</i>
          </button>
        `,
      )}`
    : html`<p style="margin:0;color:var(--mute);font-size:14px">Aucun lancement pour l'instant.</p>`;
}
/**
 * Lance le workflow ouvert avec les valeurs saisies dans les champs du panneau.
 * Après acceptation par le backend, `appState.liveRun` est initialisé ; la suite
 * (progression, validations, fin) arrive par `receiveEvent`. Si le backend
 * refuse (`ok: false`), l'erreur s'affiche dans `#live-run-output` et rien n'est lancé.
 */
async function launchWorkflow() {
  const workflow = appState.currentWorkflow;
  if (!workflow || (appState.liveRun && appState.liveRun.running)) return;
  const inputValues = {};
  document
    .querySelectorAll("#workflow-panel input[data-input-name]")
    .forEach((i) => (inputValues[i.dataset.inputName] = i.value));
  const result = await callBackend(
    "portal_run",
    workflow.filename,
    inputValues,
  );
  if (!result.ok) {
    $("#live-run-output").innerHTML =
      html`<div class="message-box">${result.error}</div>`;
    return;
  }
  appState.liveRun = {
    filename: workflow.filename,
    running: true,
    stopping: false,
    stepStates: {},
    warnings: [],
    gate: null,
    runId: null,
  };
  appState.busy = true;
  renderLiveRun();
  renderEnvChips();
  renderWorkflowList();
}
/**
 * Reflète `appState.liveRun` dans le panneau ouvert : pictogrammes des étapes, boutons
 * Lancer/Arrêter, avertissements, et « porte » (gate) quand le workflow attend
 * l'utilisateur :
 *  - gate.kind == "approval" : Approuver / Refuser
 *  - gate.kind == "input"    : zone de saisie, dictée vocale optionnelle
 * Ne fait rien si le panneau affiche un autre workflow que celui en cours.
 * La porte n'est reconstruite que si elle a changé (comparaison par référence
 * avec `liveRun.renderedGate`) : sinon chaque événement effacerait le texte en cours de
 * saisie.
 */
function renderLiveRun() {
  const liveRun = appState.liveRun;
  if (
    !liveRun ||
    !appState.currentWorkflow ||
    appState.currentWorkflow.filename != liveRun.filename ||
    !$("#step-list")
  )
    return;
  appState.currentWorkflow.steps.forEach((step, i) => {
    const stepItem = $(`#step-${i}`);
    if (!stepItem) return;
    const stepState = liveRun.stepStates[step.id] || "";
    stepItem.className = stepState;
    stepItem.querySelector(".step-icon").textContent =
      {
        run: "▶",
        ok: "✓",
        err: "✕",
        wait: "⏸",
        skip: "–",
        stop: "■",
      }[stepState] || "·";
  });
  $("#launch-button").disabled =
    !!liveRun.running || appState.busy || !appState.currentWorkflow.ready;
  $("#launch-button").textContent = liveRun.running ? "En cours…" : "Relancer";
  const stopButton = $("#stop-run-button"),
    stopNote = $("#stop-run-note");
  stopButton.hidden = !liveRun.running;
  stopButton.disabled = !!liveRun.stopping;
  stopButton.textContent = liveRun.stopping ? "Arrêt en cours…" : "Arrêter";
  stopNote.hidden = !liveRun.stopping;
  stopNote.textContent =
    "Les scripts, commandes et étapes CSAgent s'interrompent tout de suite ; les autres étapes se terminent d'abord.";
  const gateContainer = $("#live-run-gate"),
    gate = liveRun.gate;
  if (liveRun.renderedGate !== gate || (gate && !gateContainer.firstChild)) {
    liveRun.renderedGate = gate;
    if (!gate) VoiceRecorder.abort();
    gateContainer.innerHTML = "";
    if (gate && gate.kind == "approval") {
      gateContainer.innerHTML = html`
        <div class="decision-gate">
          <b>Votre décision est attendue</b>
          <span>${gate.message}</span>
          <div class="button-row">
            <button class="button-primary" id="approval-approve-button">Approuver</button>
            <button class="button-primary is-ghost" id="approval-reject-button">Refuser</button>
          </div>
        </div>
      `;
      $("#approval-approve-button").onclick = () => answerApproval("approved");
      $("#approval-reject-button").onclick = () => answerApproval("rejected");
      $("#approval-approve-button").focus();
    }
    if (gate && gate.kind == "input") {
      gateContainer.innerHTML = html`
        <div class="decision-gate">
          <b>${gate.prompt}</b>
          <textarea class="text-input" id="gate-input-textarea" rows="4" aria-label="${gate.prompt}" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit"></textarea>
          ${
            gate.voice
              ? html`
                  <div class="voice-controls">
                    <button class="button-primary is-ghost" id="gate-mic-button" type="button" aria-pressed="false">🎙 Dicter</button>
                    <span class="voice-status" id="gate-voice-status" role="status" aria-live="polite"></span>
                    <span class="voice-error" id="gate-voice-error" role="alert" hidden></span>
                  </div>
                `
              : ""
          }
          <div class="button-row">
            <button class="button-primary" id="gate-input-submit-button">Valider</button>
            <button class="button-primary is-ghost" id="gate-input-cancel-button">Annuler</button>
            <span class="hint-text" style="align-self:center">Ctrl + Entrée pour valider</span>
          </div>
        </div>
      `;
      const textarea = $("#gate-input-textarea");
      textarea.value = gate.def || "";
      textarea.focus();
      textarea.oninput = renderVoiceControls;
      textarea.onkeydown = (event) => {
        if (event.key == "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          if (!$("#gate-input-submit-button").disabled)
            $("#gate-input-submit-button").click();
        }
      };
      if (gate.voice) {
        appState.voice = {
          state: "idle",
          message: "",
          errorMessage: "",
          elapsedSeconds: 0,
          target: "gate",
        };
        $("#gate-mic-button").onclick = () =>
          appState.voice.state == "recording"
            ? VoiceRecorder.stop()
            : VoiceRecorder.start();
      }
      $("#gate-input-submit-button").onclick = async () => {
        const text = $("#gate-input-textarea").value;
        VoiceRecorder.abort();
        appState.liveRun = closeInputGate(appState.liveRun);
        renderLiveRun();
        await callBackend("submit_input", text, false);
      };
      $("#gate-input-cancel-button").onclick = async () => {
        VoiceRecorder.abort();
        appState.liveRun = closeInputGate(appState.liveRun);
        renderLiveRun();
        await callBackend("submit_input", "", true);
      };
      renderVoiceControls();
    }
  }
  if (liveRun.warnings.length)
    $("#live-run-output").innerHTML =
      html`<div class="message-box" style="color:var(--human);border-color:var(--human)">${joinHtml(liveRun.warnings, html`<br>`)}</div>`;
}
/**
 * Demande l'arrêt du lancement en cours. L'arrêt est coopératif : scripts,
 * commandes et étapes CSAgent s'interrompent tout de suite, les autres étapes
 * se terminent d'abord (cf. le message affiché). `stopping` évite le double clic.
 */
async function requestStop() {
  const liveRun = appState.liveRun;
  if (!liveRun || !liveRun.running || liveRun.stopping) return;
  appState.liveRun = markStopping(liveRun);
  renderLiveRun();
  const result = await callBackend("portal_stop");
  if (!result.ok) {
    if (appState.liveRun) appState.liveRun = clearStopping(appState.liveRun);
    renderLiveRun();
    $("#live-run-output").innerHTML =
      html`<div class="message-box">${result.error}</div>`;
  }
}
/**
 * Répond à une validation humaine ("approved" | "rejected"). L'étape qui
 * attendait repasse à « en cours » en attendant les événements suivants.
 */
async function answerApproval(decision) {
  appState.liveRun = closeGate(appState.liveRun);
  renderLiveRun();
  await callBackend("resolve_approval", decision);
}
