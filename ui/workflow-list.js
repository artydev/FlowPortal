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
      setKnownCategories(appState.workflows);
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
    setKnownCategories(appState.workflows);
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
 * classe CSS `.n.<type>`), reliés par des traits. L'étape « end » est omise. Au-delà de ~12 étapes la frise
 * est plafonnée (début, ellipse « +N », dernier point).
 */
function renderStepTimeline(workflow) {
  const visibleSteps = capTimelineSteps(workflow.steps.filter((step) => step.type != "end"));
  return html`<div class="step-timeline" aria-hidden="true">${visibleSteps.map(
    (step, i) => html`
      ${i ? html`<i class="step-connector"></i>` : ""}
      ${step.type === "ellipsis"
        ? html`<span class="step-ellipsis">+${step.hidden}</span>`
        : html`<i class="step-dot ${step.type}"></i>`}
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
/** Étiquette colorée de catégorie (rien si le workflow n'en a pas). */
function renderCategoryLabel(category) {
  return category
    ? html`<span class="category-label" style="--cat-hue:${categoryHue(category)}">${category}</span>`
    : "";
}
/**
 * Barre des catégories : « Toutes » puis une pastille par catégorie avec son effectif.
 * Masquée tant qu'aucun workflow n'a de catégorie. Une catégorie qui n'existe plus
 * (workflow renommé ou supprimé) ramène le filtre sur « Toutes ».
 */
function renderCategoryBar() {
  const categories = listCategories(appState.workflows);
  const hasCategory = categories.some((category) => category.name != NO_CATEGORY);
  if (
    appState.category != "all" &&
    !categories.some((category) => category.name == appState.category)
  )
    appState.category = "all";
  const bar = $("#category-bar");
  bar.hidden = !hasCategory;
  bar.innerHTML = !hasCategory
    ? ""
    : html`
        <button class="button-secondary" data-category="all" aria-pressed="${appState.category == "all"}">Toutes</button>
        ${categories.map(
          (category) => html`<button class="button-secondary category-chip" data-category="${category.name}" aria-pressed="${appState.category == category.name}" ${category.name == NO_CATEGORY ? "" : html`style="--cat-hue:${categoryHue(category.name)}"`}><i class="category-dot"></i>${category.name || NO_CATEGORY_LABEL} <span class="category-count">${category.count}</span></button>`,
        )}
      `;
}
/** Replie ou déplie un groupe de la vue liste ; le choix est mémorisé par le navigateur. */
function toggleCategoryGroup(name) {
  const collapsed = new Set(appState.collapsedCategories);
  collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name);
  appState.collapsedCategories = [...collapsed];
  try {
    localStorage.setItem("fp-collapsed-categories", JSON.stringify(appState.collapsedCategories));
  } catch (error) {}
  renderWorkflowList();
}
/**
 * Lignes d'une catégorie dans la vue liste : un en-tête cliquable (replier/déplier)
 * puis, si le groupe est ouvert, ses workflows. Une recherche ou un filtre actif
 * garde tous les groupes ouverts pour ne pas cacher des résultats.
 */
function renderCategoryGroupHtml(group, forceOpen) {
  const isOpen = forceOpen || !appState.collapsedCategories.includes(group.name);
  return html`
    <tbody class="category-group" style="--cat-hue:${categoryHue(group.name)}">
      <tr class="category-row">
        <th colspan="4" scope="colgroup">
          <button class="category-toggle" type="button" data-toggle-category="${group.name}" aria-expanded="${isOpen}">
            <span class="category-caret" aria-hidden="true">${isOpen ? "▾" : "▸"}</span>
            ${group.name ? html`<span class="category-label">${group.name}</span>` : html`<span class="category-none">${NO_CATEGORY_LABEL}</span>`}
            <span class="category-count">${group.workflows.length}</span>
          </button>
        </th>
      </tr>
      ${isOpen ? group.workflows.map(renderRowHtml) : ""}
    </tbody>
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
      ${renderCategoryLabel(workflow.category)}
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
  renderCategoryBar();
  const visibleWorkflows = filterWorkflows(appState.workflows, appState);
  const hasCategories = appState.workflows.some((workflow) => workflow.category);
  const isFiltering = !!appState.searchText || appState.filter != "all" || appState.category != "all";
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
                ${hasCategories
                  ? groupByCategory(visibleWorkflows).map((group) => renderCategoryGroupHtml(group, isFiltering))
                  : html`<tbody>${visibleWorkflows.map(renderRowHtml)}</tbody>`}
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
