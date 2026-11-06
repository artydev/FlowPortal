"use strict";
/**
 * Démarrage (`bootApp`), liste des workflows (cartes ou lignes), bandeau des
 * lancements récents et pastilles d'environnement.
 */
/**
 * Démarrage : le serveur Python peut mettre un instant à répondre, on réessaie
 * `portal_list` toutes les 250 ms pendant 10 s au plus avant d'afficher l'erreur.
 */
async function bootApp() {
  for (let i = 0; i < 40; i++) {
    const result = await callBackend("portal_list");
    if (result && result.cards) {
      appState.workflows = result.cards;
      appState.recentRuns = result.recent || [];
      appState.busy = !!result.busy;
      renderWorkflowList();
      renderRecentRuns();
      renderEnvChips();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  $("#notice-banner").innerHTML =
    html`<div class="message-box" style="margin-bottom:16px">Impossible de joindre FlowPortal. Fermez cette page et relancez l'application.</div>`;
}
/* ── liste ─────────────────────────────────────────────────────────── */
/** Rafraîchit la liste depuis le backend puis redessine cartes, lancements récents et pastilles. */
async function reloadWorkflowList() {
  const result = await callBackend("portal_list");
  if (result && result.cards) {
    appState.workflows = result.cards;
    appState.recentRuns = result.recent || [];
    appState.busy = !!result.busy;
  }
  renderWorkflowList();
  renderRecentRuns();
  renderEnvChips();
}
/**
 * Pastilles d'en-tête : nombre de workflows, et état global
 * (lancement en cours / prérequis manquants / tout est prêt).
 */
function renderEnvChips() {
  const missingPrereqCount = appState.workflows.filter(
    (workflow) => !workflow.ready,
  ).length;
  $("#status-chip-workflow-count").innerHTML =
    html`<i class="status-dot"></i>${appState.workflows.length} workflow${appState.workflows.length > 1 ? "s" : ""}`;
  $("#status-chip-engine-state").innerHTML = html`
    <i class="status-dot ${appState.liveRun && appState.liveRun.running ? "run" : missingPrereqCount ? "off" : ""}"></i>
    ${appState.liveRun && appState.liveRun.running ? "Lancement en cours" : missingPrereqCount ? `${missingPrereqCount} avec prérequis manquants` : "Prérequis réunis"}
  `;
}
/**
 * Mini frise d'une carte : un point par étape (la forme/couleur vient de la
 * classe CSS `.n.<type>`), reliés par des traits. L'étape « end » est omise.
 */
function renderStepTimeline(workflow) {
  const visibleSteps = workflow.steps.filter((step) => step.type != "end");
  return html`<div class="step-timeline" aria-hidden="true">${visibleSteps.map(
    (step, i) => html`
      ${i ? html`<i class="step-connector"></i>` : ""}
      <i class="step-dot ${step.type}"></i>
    `,
  )}</div>`;
}
/** Pastilles d'une carte : CSAgent / validation humaine / MCP / problème / nombre d'étapes. */
function renderTags(workflow) {
  return html`
    <div class="tag-list">
      ${workflow.tags.agent ? html`<span class="tag tag-agent">CSAgent</span>` : ""}
      ${workflow.tags.human ? html`<span class="tag tag-human">Validation humaine</span>` : ""}
      ${workflow.tags.mcp ? html`<span class="tag tag-mcp">MCP</span>` : ""}
      ${workflow.error ? html`<span class="tag bad">Illisible</span>` : !workflow.ready ? html`<span class="tag bad">Prérequis manquant</span>` : ""}
      <span class="tag">${workflow.steps.length} étapes</span>
    </div>
  `;
}
/**
 * HTML d'une carte (vue « cartes »). Le bouton Lancer porte `data-launch-workflow` : le
 * gestionnaire de clic global l'intercepte avant l'ouverture de la carte.
 */
function renderCardHtml(workflow) {
  return html`
    <div class="workflow-card" tabindex="0" role="button" data-filename="${workflow.filename}">
      <h3>${workflow.title}</h3>
      <p>${workflow.summary || "Pas de description."}</p>
      ${renderStepTimeline(workflow)}
      ${renderTags(workflow)}
      <div class="card-footer">
        <span>${formatTimeAgo(workflow.last_run && workflow.last_run.started)}</span>
        <button class="button-primary" ${workflow.ready && !appState.busy ? "" : "disabled"} data-launch-workflow="${workflow.filename}">Lancer</button>
      </div>
    </div>
  `;
}
/** HTML d'une ligne de la vue « liste ». Les actions utilisent des attributs `data-*` (voir le gestionnaire de clic global). */
function renderRowHtml(workflow) {
  return html`
    <tr>
      <th scope="row">
        <button class="link-button" data-open-workflow="${workflow.filename}">${workflow.title}</button>
        <small>${workflow.filename}</small>
      </th>
      <td>${workflow.steps.length}</td>
      <td>${formatTimeAgo(workflow.last_run && workflow.last_run.started)}${workflow.error ? html` · <span class="error-message">illisible</span>` : !workflow.ready ? html` · <span class="error-message">prérequis manquant</span>` : ""}</td>
      <td class="row-actions">
        <button class="button-secondary" data-launch-workflow="${workflow.filename}" ${workflow.ready && !appState.busy ? "" : "disabled"}>Lancer</button>
        <button class="button-secondary" data-edit-workflow="${workflow.filename}">Modifier</button>
        <button class="button-secondary" data-duplicate-workflow="${workflow.filename}">Dupliquer</button>
        <button class="button-secondary" data-rename-workflow="${workflow.filename}">Renommer</button>
        <button class="button-secondary is-danger" data-delete-workflow="${workflow.filename}">Supprimer</button>
      </td>
    </tr>
  `;
}
/**
 * Redessine la grille selon la vue, la recherche (titre + description + nom de
 * fichier) et le filtre. Le filtre « solo » signifie « sans CSAgent » ; les
 * autres filtres correspondent à une clé de `workflow.tags`.
 */
function renderWorkflowList() {
  const isListView = appState.viewMode == "list";
  const visibleWorkflows = filterWorkflows(appState.workflows, appState);
  $("#workflow-grid").classList.toggle("list-view", isListView);
  const viewToggleButton = $("#view-mode-toggle-button");
  viewToggleButton.textContent = isListView ? "Vue cartes" : "Vue liste";
  $("#workflow-grid").innerHTML =
    appState.workflows.length == 0
      ? html`<div class="empty-state">Aucun workflow dans le dossier workflows. Cliquez sur « Nouveau workflow » ou ajoutez un fichier .yaml.</div>`
      : visibleWorkflows.length
        ? isListView
          ? html`
            <div class="list-table-wrapper">
              <table class="list-table">
                <thead>
                  <tr>
                    <th scope="col">Workflow</th>
                    <th scope="col">Étapes</th>
                    <th scope="col">Dernier lancement</th>
                    <th scope="col"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>${visibleWorkflows.map(renderRowHtml)}</tbody>
              </table>
            </div>
          `
          : html`${visibleWorkflows.map(renderCardHtml)}`
        : html`<div class="empty-state">Aucun workflow ne correspond. Effacez la recherche ou choisissez « Tous ».</div>`;
}
/** Bandeau des lancements récents (clic → résultat détaillé). */
function renderRecentRuns() {
  $("#recent-runs-bar").innerHTML = html`${appState.recentRuns.map((run) => {
    const workflow = findCard(run.file);
    return html`<button data-run-id="${run.id}"><b>${workflow ? workflow.title : run.name}</b><span>${formatTimeAgo(run.started)} · ${RUN_STATUS_LABELS[run.status]}</span></button>`;
  })}`;
}
