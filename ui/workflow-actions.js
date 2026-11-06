"use strict";
/**
 * Actions sur les fichiers : suppression, renommage/duplication, bascule
 * cartes/liste et création d'un nouveau workflow.
 */
/* ── liste : modifier, supprimer ───────────────────────────────────── */
/** Message d'information/erreur dans `#notice-banner`, effacé automatiquement après 8 s (`bad` = style erreur). */
let noticeTimer = null;
function showNotice(message, isError) {
  const alertBox = $("#notice-banner");
  alertBox.innerHTML = html`<div class="${isError ? "message-box" : "run-summary"}" style="margin-bottom:16px" role="status">${message}</div>`;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    alertBox.innerHTML = "";
  }, 8000);
}
/** Ouvre directement l'éditeur plein écran depuis la vue liste (lit le YAML si pas encore en cache). */
async function openEditorForWorkflow(filename) {
  const workflow = findCard(filename);
  if (!workflow) return;
  appState.currentWorkflow = workflow;
  if (!(filename in appState.savedYaml)) {
    const result = await callBackend("portal_read", filename);
    if (!result.ok) {
      showNotice(result.error, true);
      return;
    }
    appState.savedYaml[filename] = result.yaml;
  }
  validation = {
    issues: [],
    errorCount: 0,
    warningCount: 0,
  };
  openFullscreenEditor();
}
// Fichier en attente de confirmation de suppression.
let pendingDeleteFilename = null;
/**
 * Demande confirmation avant suppression. Refusé pendant un lancement. La
 * suppression est réversible : le fichier est déplacé dans workflows/.corbeille/
 * et l'historique est conservé.
 */
function askDeleteConfirmation(filename) {
  const workflow = findCard(filename);
  if (!workflow) return;
  if (appState.busy || (appState.liveRun && appState.liveRun.running)) {
    showNotice(
      "Un lancement est en cours : attendez la fin ou arrêtez-le avant de supprimer.",
      true,
    );
    return;
  }
  pendingDeleteFilename = filename;
  $("#delete-title").textContent = `Supprimer « ${workflow.title} » ?`;
  $("#delete-error").hidden = true;
  $("#delete-dialog").showModal();
  $("#delete-cancel-button").focus();
}
$("#delete-cancel-button").onclick = () => $("#delete-dialog").close();
$("#delete-confirm-button").onclick = async () => {
  const filename = pendingDeleteFilename,
    result = await callBackend("portal_delete", filename);
  if (!result.ok) {
    $("#delete-error").textContent = result.error || "Suppression impossible.";
    $("#delete-error").hidden = false;
    return;
  }
  delete appState.drafts[filename];
  delete appState.savedYaml[filename];
  $("#delete-dialog").close();
  if ($("#workflow-panel-dialog").open) $("#workflow-panel-dialog").close();
  if (appState.currentWorkflow && appState.currentWorkflow.filename == filename)
    appState.currentWorkflow = null;
  await reloadWorkflowList();
  showNotice(
    `« ${filename} » a été déplacé dans workflows/.corbeille/ : vous pouvez le récupérer à la main.`,
  );
};
/* renommer / dupliquer : même boîte de dialogue */
// Boîte « renommer / dupliquer » partagée : mode courant ("ren" | "dup") et fichier source.
let nameDialogMode = "ren",
  nameDialogFilename = null;
/**
 * Ouvre la boîte de nom. En duplication, propose « <nom>-copie », puis
 * « -copie-2 », « -copie-3 »… jusqu'à trouver un nom libre (comparaison sans
 * tenir compte de la casse). Renommer est refusé pendant un lancement.
 */
function openNameDialog(mode, filename) {
  const workflow = findCard(filename);
  if (!workflow) return;
  if (
    mode == "ren" &&
    (appState.busy || (appState.liveRun && appState.liveRun.running))
  ) {
    showNotice(
      "Un lancement est en cours : attendez la fin ou arrêtez-le avant de renommer.",
      true,
    );
    return;
  }
  nameDialogMode = mode;
  nameDialogFilename = filename;
  const suggestedName =
    mode == "dup"
      ? suggestCopyName(
          filename,
          appState.workflows.map((other) => other.filename),
        )
      : stripYamlExtension(filename);
  $("#rename-title").textContent =
    `${(mode == "ren" ? "Renommer « " : "Dupliquer « ") + workflow.title} »`;
  $("#rename-submit-button").textContent =
    mode == "ren" ? "Renommer" : "Dupliquer";
  $("#rename-hint").textContent =
    mode == "ren"
      ? "L'historique des lancements suit le fichier. Si le nom interne (name:) était identique à l'ancien nom, il est mis à jour."
      : "Le nom interne (name:) de la copie prend le nouveau nom, pour distinguer les deux cartes.";
  $("#rename-name-input").value = suggestedName;
  $("#rename-error").hidden = true;
  $("#rename-dialog").showModal();
  $("#rename-name-input").focus();
  $("#rename-name-input").select();
}
$("#rename-cancel-button").onclick = () => $("#rename-dialog").close();
// Validation du nom : non vide, extension .yaml ajoutée si absente, différent de
// l'ancien nom, et pas déjà pris (casse ignorée, sauf le fichier lui-même).
$("#rename-form").onsubmit = async (event) => {
  event.preventDefault();
  const errorBox = $("#rename-error"),
    showError = (message) => {
      errorBox.textContent = message;
      errorBox.hidden = false;
    };
  errorBox.hidden = true;
  const check = validateFilename($("#rename-name-input").value, {
    existingFilenames: appState.workflows.map((other) => other.filename),
    mode: nameDialogMode,
    currentFilename: nameDialogFilename,
  });
  if (!check.ok) return showError(check.message);
  const newName = check.filename;
  const result = await callBackend(
    nameDialogMode == "ren" ? "portal_rename" : "portal_duplicate",
    nameDialogFilename,
    newName,
  );
  if (!result.ok) return showError(result.error || "Opération impossible.");
  $("#rename-dialog").close();
  if (nameDialogMode == "ren") {
    delete appState.drafts[nameDialogFilename];
    delete appState.savedYaml[nameDialogFilename];
    if ($("#workflow-panel-dialog").open) $("#workflow-panel-dialog").close();
    if (
      appState.currentWorkflow &&
      appState.currentWorkflow.filename == nameDialogFilename
    )
      appState.currentWorkflow = null;
  }
  await reloadWorkflowList();
  showNotice(
    nameDialogMode == "ren"
      ? `« ${nameDialogFilename} » est maintenant « ${result.filename} ».`
      : `« ${nameDialogFilename} » a été dupliqué en « ${result.filename} ».`,
  );
};
// Bascule cartes/liste ; le choix est mémorisé (localStorage, protégé par try/catch car il peut être indisponible).
$("#view-mode-toggle-button").onclick = () => {
  appState.viewMode = appState.viewMode == "list" ? "cards" : "list";
  try {
    localStorage.setItem("fp-view", appState.viewMode);
  } catch (error) {}
  renderWorkflowList();
};
try {
  if (localStorage.getItem("fp-view") == "list") appState.viewMode = "list";
} catch (error) {}

/* ── nouveau workflow ──────────────────────────────────────────────── */
// Modèles de départ proposés pour un nouveau workflow, chargés une seule fois (`portal_templates`).
let workflowTemplates = [];
/** Boîte « Nouveau workflow » : liste des modèles + l'option spéciale `__assist` (décrire à CSAgent). */
async function openNewWorkflowDialog() {
  if (!workflowTemplates.length) {
    const result = await callBackend("portal_templates");
    workflowTemplates = Array.isArray(result) ? result : [];
  }
  const options = workflowTemplates.concat([
    {
      id: "__assist",
      label: "Décrire à CSAgent",
      description:
        "Parlez ou écrivez ce que doit faire le workflow ; vous relisez la proposition avant de l'appliquer.",
    },
  ]);
  $("#new-workflow-templates").innerHTML = html`
    <legend class="hint-text" style="padding:0 0 4px">Point de départ</legend>
    ${options.map(
      (template, i) => html`
          <label class="template-option">
            <input type="radio" name="tpl" value="${template.id}"${i == 0 ? " checked" : ""}>
            <span><b>${template.label}</b><small>${template.description}</small></span>
          </label>
        `,
    )}
  `;
  $("#new-workflow-name-input").value = "";
  $("#new-workflow-error").hidden = true;
  $("#new-workflow-dialog").showModal();
  $("#new-workflow-name-input").focus();
}
/**
 * Prépare un nouveau workflow SANS écrire de fichier : le contenu du modèle est
 * gardé en mémoire (`appState.savedYaml`, avec `appState.currentWorkflow.isNew`) et l'éditeur s'ouvre ; le
 * fichier n'est créé qu'à l'enregistrement (voir `saveYaml`). L'option `__assist` part d'un modèle vide et
 * ouvre l'onglet Assistant.
 */
async function submitNewWorkflow(event) {
  event.preventDefault();
  const errorBox = $("#new-workflow-error"),
    showError = (message) => {
      errorBox.textContent = message;
      errorBox.hidden = false;
    };
  errorBox.hidden = true;
  const check = validateFilename($("#new-workflow-name-input").value, {
    existingFilenames: appState.workflows.map((other) => other.filename),
    mode: "new",
  });
  if (!check.ok) return showError(check.message);
  const filename = check.filename;
  const selectedRadio = document.querySelector("input[name=tpl]:checked"),
    mode = selectedRadio ? selectedRadio.value : "vide",
    result = await callBackend(
      "portal_template",
      mode == "__assist" ? "vide" : mode,
      filename,
    );
  if (!result.ok) return showError(result.error || "Création impossible.");
  $("#new-workflow-dialog").close();
  appState.savedYaml[filename] = result.yaml;
  delete appState.drafts[filename];
  appState.currentWorkflow = {
    filename: filename,
    title: stripYamlExtension(filename),
    isNew: true,
    steps: [],
    inputs: {},
    missing: [],
    ready: true,
    tags: {},
  };
  validation = {
    issues: [],
    errorCount: 0,
    warningCount: 0,
  };
  openFullscreenEditor(mode == "__assist" ? "a" : "p");
}
$("#new-workflow-button").onclick = openNewWorkflowDialog;
$("#new-workflow-form").onsubmit = submitNewWorkflow;
$("#new-workflow-cancel-button").onclick = () =>
  $("#new-workflow-dialog").close();
