"use strict";
/**
 * Interactions globales (délégation de clics, recherche, filtres, thème) et
 * démarrage de l'application. Chargé en dernier.
 */
/* ── interactions générales ────────────────────────────────────────── */
// Délégation de clic unique pour cartes et lignes (rendues dynamiquement), via les attributs data-* :
//   data-launch-workflow lancer · data-open-workflow ouvrir · data-edit-workflow modifier · data-delete-workflow supprimer
//   data-duplicate-workflow dupliquer · data-rename-workflow renommer · data-run-id résultat d'un lancement récent
// L'ordre est important : `data-launch-workflow` est testé en premier et stoppe la propagation
// pour ne pas ouvrir la carte dont le bouton fait partie.
document.addEventListener("click", (event) => {
  const runButton = event.target.closest("[data-launch-workflow]");
  if (runButton) {
    event.stopPropagation();
    openWorkflowPanel(runButton.dataset.launchWorkflow, true);
    return;
  }
  const openLink = event.target.closest("[data-open-workflow]");
  if (openLink) {
    openWorkflowPanel(openLink.dataset.openWorkflow);
    return;
  }
  const editButton = event.target.closest("[data-edit-workflow]");
  if (editButton) {
    openEditorForWorkflow(editButton.dataset.editWorkflow);
    return;
  }
  const deleteButton = event.target.closest("[data-delete-workflow]");
  if (deleteButton) {
    askDeleteConfirmation(deleteButton.dataset.deleteWorkflow);
    return;
  }
  const duplicateButton = event.target.closest("[data-duplicate-workflow]");
  if (duplicateButton) {
    openNameDialog("dup", duplicateButton.dataset.duplicateWorkflow);
    return;
  }
  const renameButton = event.target.closest("[data-rename-workflow]");
  if (renameButton) {
    openNameDialog("ren", renameButton.dataset.renameWorkflow);
    return;
  }
  const recentButton = event.target.closest("#recent-runs-bar [data-run-id]");
  if (recentButton) {
    openRunResult(recentButton.dataset.runId);
    return;
  }
  const cardElement = event.target.closest(".workflow-card[data-filename]");
  if (cardElement) openWorkflowPanel(cardElement.dataset.filename);
});
// Accessibilité : Entrée sur une carte focalisée l'ouvre (les cartes sont des div role="button").
document.addEventListener("keydown", (event) => {
  if (event.key == "Enter" && event.target.classList.contains("workflow-card"))
    openWorkflowPanel(event.target.dataset.filename);
});
$("#search-input").oninput = (event) => {
  appState.searchText = event.target.value.toLowerCase();
  renderWorkflowList();
};
document.querySelectorAll(".toolbar .button-secondary[data-filter]").forEach(
  (filterButton) =>
    (filterButton.onclick = () => {
      appState.filter = filterButton.dataset.filter;
      document
        .querySelectorAll(".toolbar .button-secondary[data-filter]")
        .forEach((otherButton) =>
          otherButton.setAttribute("aria-pressed", otherButton == filterButton),
        );
      renderWorkflowList();
    }),
);
// Thème : bascule clair/sombre ; sans choix enregistré, on suit la préférence du système (CSS `prefers-color-scheme`).
$("#theme-toggle-button").onclick = () => {
  const root = document.documentElement,
    nextTheme =
      (root.dataset.theme ||
        (matchMedia("(prefers-color-scheme:dark)").matches
          ? "dark"
          : "light")) == "dark"
        ? "light"
        : "dark";
  root.dataset.theme = nextTheme;
  try {
    localStorage.setItem("fp-theme", nextTheme);
  } catch (error) {}
};
try {
  const savedTheme = localStorage.getItem("fp-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
} catch (error) {}
// Démarrage. Sans Eel, la page a été ouverte directement dans un navigateur :
// on explique comment lancer l'application. Sinon, on charge la liste puis on la
// rafraîchit toutes les 30 s, sauf pendant un lancement ou quand une boîte de
// dialogue est ouverte (pour ne pas écraser ce que l'utilisateur est en train de faire).
if (typeof eel === "undefined" || !eel.portal_list) {
  $("#notice-banner").innerHTML =
    html`<div class="message-box" style="margin-bottom:16px">Le portail doit être ouvert par FlowPortal : lancez <b>FlowPortal</b> (ou <b>python app.py</b>).</div>`;
} else {
  bootApp();
  setInterval(() => {
    if (
      !(appState.liveRun && appState.liveRun.running) &&
      !$("#workflow-panel-dialog").open &&
      !$("#run-result-dialog").open &&
      !$("#fullscreen-editor-dialog").open &&
      !$("#new-workflow-dialog").open &&
      !$("#delete-dialog").open &&
      !$("#rename-dialog").open
    )
      reloadWorkflowList();
  }, 30000);
}
