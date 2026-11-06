"use strict";
/**
 * Écran de résultat d'un lancement : onglets Étapes, Fichiers, Journal, Entrées.
 */
/* ── résultat d'un lancement ───────────────────────────────────────── */
// Écran de résultat : `shownRun` = lancement affiché, `runDetailTab` = onglet actif, `selectedFileIndex` = index du fichier sélectionné.
let shownRun = null,
  runDetailTab = "steps",
  selectedFileIndex = 0;
/** Ouvre le résultat détaillé d'un lancement (`portal_run_detail`) en plein écran, onglet « Étapes » par défaut. */
async function openRunResult(runId) {
  const result = await callBackend("portal_run_detail", runId);
  if (!result.ok) {
    alert(result.error);
    return;
  }
  shownRun = result.run;
  runDetailTab = "steps";
  selectedFileIndex = 0;
  const workflow = findCard(shownRun.file);
  $("#run-result-title").innerHTML =
    html`${workflow ? workflow.title : shownRun.name} <span class="run-status-badge ${shownRun.status}">${RUN_STATUS_LABELS[shownRun.status]}</span>`;
  $("#run-result-meta").textContent = describeRun(shownRun);
  $("#run-result-relaunch-button").onclick = () => {
    $("#run-result-dialog").close();
    openWorkflowPanel(shownRun.file, true);
  };
  $("#run-result-close-button").onclick = () => $("#run-result-dialog").close();
  $("#run-result-relaunch-button").disabled =
    !workflow || !workflow.ready || appState.busy;
  if (!$("#run-result-dialog").open) $("#run-result-dialog").showModal();
  renderRunTabs();
}
/** Dessine la barre d'onglets (Étapes, Fichiers, Journal, Entrées) puis le contenu. */
function renderRunTabs() {
  const run = shownRun,
    tabDefs = [
      ["steps", "Étapes"],
      ["files", `Fichiers (${run.files.length})`],
      ["log", "Journal"],
      ["in", "Entrées"],
    ];
  $("#run-result-tabs").innerHTML =
    html`${tabDefs.map(([key, label]) => html`<button role="tab" data-tab="${key}" aria-selected="${key == runDetailTab}">${label}</button>`)}`;
  $("#run-result-tabs").onclick = (event) => {
    const tabButton = event.target.closest("[data-tab]");
    if (tabButton) {
      runDetailTab = tabButton.dataset.tab;
      renderRunTabs();
    }
  };
  renderRunTabContent();
}
/**
 * Contenu de l'onglet actif. Onglet « Fichiers » : l'aperçu est chargé de façon
 * asynchrone, d'où le contrôle `tab != "files"` après l'attente (l'utilisateur a
 * pu changer d'onglet entre-temps). Onglet « Journal » : les instants sont
 * reconstitués en cumulant les durées d'étapes, ce ne sont pas des horodatages réels.
 */
async function renderRunTabContent() {
  const run = shownRun,
    container = $("#run-result-body"),
    maxDuration = Math.max(
      ...run.steps.map((step) => +step.duration || 0),
      0.1,
    );
  if (runDetailTab == "steps") {
    const failedStep = run.steps.find((step) => step.status == "error");
    const failureHtml = failedStep
      ? html`<div class="message-box" style="margin-bottom:14px"><b>L'étape « ${failedStep.id} » a échoué.</b> ${(failedStep.output || "").split("\n")[0]}</div>`
      : run.error
        ? html`<div class="message-box" style="margin-bottom:14px">${run.error}</div>`
        : "";
    const warningsHtml =
      run.warnings && run.warnings.length
        ? html`<div class="message-box" style="margin-bottom:14px;color:var(--human);border-color:var(--human)">${joinHtml(run.warnings, html`<br>`)}</div>`
        : "";
    const stepsHtml = run.steps.map(
      (step) => html`
        <details class="step-result ${step.status}" ${step.status == "error" ? "open" : ""}>
          <summary>
            <span class="step-result-icon">${
              {
                ok: "✓",
                error: "✕",
                skipped: "–",
                notrun: "–",
                rejected: "⏸",
                cancelled: "■",
              }[step.status] || "·"
            }</span>
            <span>${step.label}<small>${step.id} · ${STEP_TYPE_LABELS[step.type] || step.type}</small></span>
            <span class="bw">
              <div class="step-result-time">${step.status == "notrun" || step.status == "skipped" ? "" : formatDuration(step.duration)}</div>
              <div class="step-result-bar"><b style="width:${Math.max(3, ((+step.duration || 0) / maxDuration) * 100)}%"></b></div>
            </span>
          </summary>
          ${step.output ? html`<pre>${step.output}</pre>` : html`<pre style="color:var(--mute)">${step.status == "notrun" ? "Non exécutée." : step.status == "skipped" ? "Étape ignorée (condition skip_if)." : "Pas de sortie."}</pre>`}
        </details>
      `,
    );
    container.innerHTML = html`${failureHtml}${warningsHtml}${stepsHtml}`;
  }
  if (runDetailTab == "files") {
    if (!run.files.length) {
      container.innerHTML = html`<div class="empty-state">Ce lancement n'a produit aucun fichier.</div>`;
      return;
    }
    const file = run.files[selectedFileIndex] || run.files[0];
    container.innerHTML = html`
      <div class="file-browser">
        <div class="file-list">${run.files.map(
          (file, i) => html`
              <button data-file-index="${i}" aria-pressed="${i == selectedFileIndex}">
                ${file.path}
                <span>${file.size == null ? "absent" : `${Math.max(1, Math.round(file.size / 1024))} Ko`}</span>
              </button>
            `,
        )}</div>
        <div id="run-file-preview"><p style="color:var(--mute)">Chargement…</p></div>
      </div>
    `;
    container.querySelector(".file-list").onclick = (event) => {
      const fileButton = event.target.closest("[data-file-index]");
      if (fileButton) {
        selectedFileIndex = +fileButton.dataset.fileIndex;
        renderRunTabContent();
      }
    };
    const preview = await callBackend("portal_file_preview", file.path),
      previewElement = $("#run-file-preview");
    if (!previewElement || runDetailTab != "files") return;
    const previewBodyHtml = !preview.ok
      ? html`<div class="empty-state">${preview.error}</div>`
      : preview.binary
        ? html`<div class="empty-state">Fichier binaire (${Math.max(1, Math.round(preview.size / 1024))} Ko) : ouvrez-le avec son application.</div>`
        : html`<pre class="code-block">${preview.text}${preview.truncated ? "\n… (aperçu tronqué)" : ""}</pre>`;
    previewElement.innerHTML = html`
      ${previewBodyHtml}
      <div class="button-row" style="margin-top:10px">
        <button class="button-primary" id="run-file-open-button" ${preview.ok ? "" : "disabled"}>Ouvrir</button>
        <span id="run-file-open-message" style="align-self:center;color:var(--mute);font-size:13px"></span>
      </div>
    `;
    const openButton = $("#run-file-open-button");
    if (openButton)
      openButton.onclick = async () => {
        const opened = await callBackend("portal_open_file", file.path);
        $("#run-file-open-message").textContent = opened.ok
          ? "Ouvert dans l'application par défaut."
          : opened.error;
      };
  }
  if (runDetailTab == "log") {
    const logLines = buildRunLog(run.steps).map((entry) => {
      if (entry.kind == "error")
        return html`<span class="log-error">            ${entry.message}</span>`;
      const time = html`<span class="log-time">${formatOffset(entry.offset).padStart(9)}</span>`;
      if (entry.kind == "start")
        return html`${time}  ▶ ${entry.stepId} (${entry.stepType})`;
      return html`${time}  <span class="${entry.status == "error" ? "log-error" : "log-success"}">${entry.status == "error" ? "✕ échec" : entry.status == "rejected" ? "⏸ refusé" : "✓ terminé"}</span> ${entry.stepId} · ${formatDuration(entry.duration)}`;
    });
    container.innerHTML = html`
      <div class="yaml-toolbar" style="margin-bottom:8px">
        <span>${logLines.length} lignes · temps cumulés (les durées des étapes sont mesurées par le moteur)</span>
        <button class="button-secondary" id="run-log-copy-button">Copier</button>
      </div>
      <pre class="code-block">${joinHtml(logLines, "\n")}</pre>
    `;
    $("#run-log-copy-button").onclick = async () => {
      try {
        await navigator.clipboard.writeText(
          container.querySelector(".code-block").textContent,
        );
        $("#run-log-copy-button").textContent = "Copié";
      } catch (error) {
        $("#run-log-copy-button").textContent = "Copie impossible";
      }
    };
  }
  if (runDetailTab == "in") {
    const inputEntries = Object.entries(run.inputs || {});
    container.innerHTML = inputEntries.length
      ? html`<div class="key-value-list">${inputEntries.map(
          ([name, value]) => html`
            <b>${name}</b>
            <span>${value}</span>
          `,
        )}</div>`
      : html`<div class="empty-state">Ce workflow n'a pas de paramètres.</div>`;
  }
}
