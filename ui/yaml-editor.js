"use strict";
/**
 * Lecture, édition, validation et enregistrement du YAML, dans le panneau et
 * dans l'éditeur plein écran.
 */
/* ── YAML : lecture, édition, plein écran ──────────────────────────── */
/**
 * YAML « courant » du workflow ouvert : brouillon s'il existe, sinon version
 * enregistrée. Toute l'édition passe par cette fonction.
 */
const currentYaml = () => {
  const filename = appState.currentWorkflow.filename;
  return filename in appState.drafts
    ? appState.drafts[filename]
    : appState.savedYaml[filename];
};
/** Vrai si le brouillon diffère de la version enregistrée. */
const hasUnsavedChanges = () => {
  const filename = appState.currentWorkflow.filename;
  return (
    filename in appState.drafts &&
    appState.drafts[filename] !== appState.savedYaml[filename]
  );
};
/**
 * `validation` : résultat de la dernière validation du YAML :
 *  - issues : problèmes [{ level: "error" | "warn", line, message }] triés par ligne
 *  - errorCount / warningCount : nombre d'erreurs / d'avertissements (les erreurs bloquent l'enregistrement)
 * `validationTimer` est le minuteur d'anti-rebond, `validationSeq` un compteur qui permet d'ignorer
 * les réponses de validation arrivées en retard (voir `validateYaml`).
 */
let validation = {
    issues: [],
    errorCount: 0,
    warningCount: 0,
  },
  validationTimer = null,
  validationSeq = 0;
/**
 * Affiche/masque la section YAML du panneau. Lit le fichier au premier affichage
 * (`portal_read`), puis ouvre en édition s'il y a déjà un brouillon, sinon en lecture.
 */
async function toggleYamlSection() {
  const yamlSection = $("#panel-yaml-section"),
    toggleButton = $("#panel-yaml-toggle-button"),
    isOpening = yamlSection.hidden;
  yamlSection.hidden = !isOpening;
  toggleButton.setAttribute("aria-expanded", isOpening);
  toggleButton.textContent = isOpening ? "Masquer le YAML" : "Voir le YAML";
  if (!isOpening) return;
  const filename = appState.currentWorkflow.filename;
  if (!(filename in appState.savedYaml)) {
    const result = await callBackend("portal_read", filename);
    if (!result.ok) {
      yamlSection.innerHTML = html`<div class="message-box">${result.error}</div>`;
      return;
    }
    appState.savedYaml[filename] = result.yaml;
  }
  renderYamlSection(hasUnsavedChanges() ? "edit" : "view");
}
/**
 * Dessine la section YAML du panneau.
 *  - "view" : lecture seule colorée (`highlightYamlLine`), boutons Modifier / Copier
 *  - "edit" : éditeur texte `#panel-yaml-textarea` avec problèmes, Enregistrer / Annuler
 */
function renderYamlSection(mode) {
  const yamlSection = $("#panel-yaml-section"),
    filename = appState.currentWorkflow.filename,
    lineCount = currentYaml().split("\n").length;
  if (mode == "view") {
    yamlSection.innerHTML = html`
      <div class="yaml-toolbar">
        <span>workflows/${filename} · ${lineCount} lignes</span>
        <span class="button-row">
          <button class="button-secondary" id="panel-yaml-edit-button">Modifier</button>
          <button class="button-secondary" id="panel-yaml-copy-button">Copier</button>
        </span>
      </div>
      <pre class="yaml-view" tabindex="0">${joinHtml(appState.savedYaml[filename].split("\n").map(highlightYamlLine), "\n")}</pre>
    `;
    $("#panel-yaml-edit-button").onclick = () => renderYamlSection("edit");
    $("#panel-yaml-copy-button").onclick = async () => {
      try {
        await navigator.clipboard.writeText(appState.savedYaml[filename]);
        $("#panel-yaml-copy-button").textContent = "Copié";
      } catch (error) {
        $("#panel-yaml-copy-button").textContent = "Copie impossible";
      }
      setTimeout(() => {
        const copyButton = $("#panel-yaml-copy-button");
        if (copyButton) copyButton.textContent = "Copier";
      }, 1500);
    };
  } else {
    yamlSection.innerHTML = html`
      <div class="yaml-toolbar">
        <span id="panel-yaml-status"></span>
        <span class="button-row">
          <button class="button-secondary" id="panel-open-assistant-button">Assistant</button>
          <button class="button-secondary" id="panel-open-fullscreen-button">Plein écran</button>
          <button class="button-secondary" id="panel-yaml-view-button">Lecture</button>
        </span>
      </div>
      <textarea class="yaml-textarea" id="panel-yaml-textarea" spellcheck="false" wrap="off" aria-label="Éditeur YAML"></textarea>
      <div class="button-row">
        <button class="button-primary" id="panel-yaml-save-button">Enregistrer</button>
        <button class="button-primary is-ghost" id="panel-yaml-revert-button">Annuler les modifications</button>
      </div>
      <div id="panel-yaml-issue-list"></div>
    `;
    bindYamlTextarea($("#panel-yaml-textarea"), false);
    $("#panel-yaml-save-button").onclick = saveYaml;
    $("#panel-yaml-revert-button").onclick = discardDraft;
    $("#panel-open-fullscreen-button").onclick = () => openFullscreenEditor();
    $("#panel-open-assistant-button").onclick = () => openFullscreenEditor("a");
    $("#panel-yaml-view-button").onclick = () => renderYamlSection("view");
    $("#panel-yaml-issue-list").onclick = (event) => {
      const issueButton = event.target.closest("[data-line]");
      if (issueButton)
        selectLine($("#panel-yaml-textarea"), +issueButton.dataset.line);
    };
    validateYaml();
  }
}
/** Liste cliquable des problèmes de validation (clic → saute à la ligne, voir `selectLine`). */
function renderIssueList(issues) {
  if (!issues.length)
    return html`<span class="success-message">Aucun problème détecté.</span>`;
  return html`${issues.map(
    (issue) => html`
      <button class="issue-item ${issue.level}" data-line="${issue.line || 1}">
        ${issue.line ? `L${issue.line} · ` : ""}${issue.message}
      </button>
    `,
  )}`;
}
/** Texte d'état de l'éditeur : nouveau / modifié / enregistré, plus nombre d'erreurs et d'avertissements. */
function editorStatusHtml() {
  const isDirty = hasUnsavedChanges(),
    filename = appState.currentWorkflow.filename;
  return (
    (appState.currentWorkflow.isNew
      ? html`<span class="unsaved-indicator">Nouveau workflow (non enregistré)</span> · `
      : isDirty
        ? html`<span class="unsaved-indicator">Modifications non enregistrées</span> · `
        : appState.currentWorkflow.justSaved
          ? html`<span class="success-message">Enregistré</span> · `
          : "") +
    (validation.errorCount
      ? html`<span class="error-message">${validation.errorCount} erreur${validation.errorCount > 1 ? "s" : ""}</span>`
      : html`<span class="success-message">Structure valide</span>`) +
    (validation.warningCount
      ? ` · ${validation.warningCount} avertissement${validation.warningCount > 1 ? "s" : ""}`
      : "")
  );
}
/**
 * Synchronise tout ce qui dépend du texte et des problèmes : statut, listes de
 * problèmes (panneau et plein écran), numéros de ligne, état des boutons.
 * Enregistrer exige un changement (ou un nouveau workflow) ET zéro erreur.
 * Chaque élément est testé avant écriture car panneau et plein écran ne sont pas
 * toujours tous deux présents.
 */
function refreshEditorUi() {
  if (!appState.currentWorkflow) return;
  const isDirty = hasUnsavedChanges(),
    lineCount = currentYaml().split("\n").length,
    setHtml = (i, html) => {
      const element = $(i);
      if (element) element.innerHTML = html;
    };
  setHtml("#panel-yaml-status", editorStatusHtml());
  setHtml("#panel-yaml-issue-list", renderIssueList(validation.issues));
  setHtml("#fullscreen-editor-status", editorStatusHtml());
  setHtml(
    "#fullscreen-issue-list",
    html`
      <h3>Problèmes</h3>
      ${renderIssueList(validation.issues)}
    `,
  );
  if ($("#fullscreen-editor-highlight"))
    $("#fullscreen-editor-highlight").textContent = Array.from(
      {
        length: lineCount,
      },
      (_, i) => i + 1,
    ).join("\n");
  ["#panel-yaml-save-button", "#fullscreen-editor-save-button"].forEach(
    (buttonSelector) => {
      const button = $(buttonSelector);
      if (button)
        button.disabled =
          !(isDirty || appState.currentWorkflow.isNew) ||
          validation.errorCount > 0;
    },
  );
  ["#panel-yaml-revert-button", "#fullscreen-editor-revert-button"].forEach(
    (buttonSelector) => {
      const button = $(buttonSelector);
      if (button) button.disabled = !isDirty;
    },
  );
}
/**
 * Valide le YAML côté Python (`portal_check`) avec anti-rebond de 150 ms.
 * `validationSeq` est incrémenté à chaque appel : une réponse qui n'est plus la dernière
 * demandée est ignorée, ce qui évite d'afficher les problèmes d'un texte périmé.
 * Un premier `refreshEditorUi` immédiat met à jour l'interface sans attendre la réponse.
 */
function validateYaml() {
  clearTimeout(validationTimer);
  if (!appState.currentWorkflow) return;
  const requestSeq = ++validationSeq,
    yamlText = currentYaml();
  validationTimer = setTimeout(async () => {
    const result = await callBackend("portal_check", yamlText);
    if (
      !appState.currentWorkflow ||
      requestSeq != validationSeq ||
      (!result.issues && !result.ok && result.error)
    )
      return;
    validation = summarizeIssues(result.issues);
    refreshEditorUi();
  }, 150);
  refreshEditorUi();
}
/** Sélectionne la ligne `lineNumber` (à partir de 1) de la zone de texte et la fait défiler dans la vue (lignes de 20 px). */
function selectLine(textarea, lineNumber) {
  const lines = textarea.value.split("\n");
  let offset = 0;
  for (let i = 0; i < lineNumber - 1; i++) offset += lines[i].length + 1;
  textarea.focus();
  textarea.setSelectionRange(
    offset,
    offset + (lines[lineNumber - 1] || "").length,
  );
  textarea.scrollTop = Math.max(0, (lineNumber - 4) * 20);
}
/**
 * Branche une zone d'édition YAML. Il y a deux zones synchronisées : `#panel-yaml-textarea` dans
 * le panneau (isFullscreen = false) et `#fullscreen-editor-textarea` en plein écran (isFullscreen = true).
 * Chaque frappe met à jour le brouillon, recopie le texte dans l'AUTRE zone et
 * relance la validation. Tab insère deux espaces ; Ctrl/Cmd+S enregistre.
 * En plein écran, le défilement est aussi répercuté sur la colonne des numéros.
 */
function bindYamlTextarea(textarea, isFullscreen) {
  textarea.value = currentYaml();
  textarea.oninput = () => {
    appState.drafts[appState.currentWorkflow.filename] = textarea.value;
    appState.currentWorkflow.justSaved = false;
    const otherTextarea = isFullscreen
      ? $("#panel-yaml-textarea")
      : $("#fullscreen-editor-textarea");
    if (otherTextarea) otherTextarea.value = textarea.value;
    validateYaml();
  };
  textarea.onkeydown = (event) => {
    if (event.key == "Tab") {
      event.preventDefault();
      textarea.setRangeText(
        "  ",
        textarea.selectionStart,
        textarea.selectionEnd,
        "end",
      );
      textarea.dispatchEvent(new Event("input"));
    }
    if ((event.ctrlKey || event.metaKey) && event.key == "s") {
      event.preventDefault();
      saveYaml();
    }
  };
  if (isFullscreen)
    textarea.onscroll = () => {
      $("#fullscreen-editor-highlight").scrollTop = textarea.scrollTop;
    };
}
/**
 * Enregistre le brouillon. Deux cas :
 *  - nouveau workflow (`isNew`) : création du fichier (`portal_create`) puis
 *    ouverture de sa carte ;
 *  - workflow existant : écrasement (`portal_save`), liste rechargée, panneau
 *    rouvert en conservant l'affichage du YAML si besoin.
 * Si le backend refuse, ses problèmes sont affichés et rien n'est perdu.
 */
async function saveYaml() {
  if (
    !(hasUnsavedChanges() || appState.currentWorkflow.isNew) ||
    validation.errorCount
  )
    return;
  if (appState.currentWorkflow.isNew) {
    const filename = appState.currentWorkflow.filename,
      yamlText = currentYaml(),
      result = await callBackend("portal_create", filename, yamlText);
    if (!result.ok) {
      validation = {
        issues: result.issues || [
          {
            level: "error",
            line: null,
            message: result.error,
          },
        ],
        errorCount: 1,
        warningCount: 0,
      };
      refreshEditorUi();
      return;
    }
    appState.savedYaml[filename] = yamlText;
    delete appState.drafts[filename];
    await reloadWorkflowList();
    await openWorkflowPanel(filename);
    $("#fullscreen-editor-dialog").close();
    return;
  }
  const filename = appState.currentWorkflow.filename,
    result = await callBackend(
      "portal_save",
      filename,
      appState.drafts[filename],
    );
  if (!result.ok) {
    validation = {
      issues: result.issues || [
        {
          level: "error",
          line: null,
          message: result.error,
        },
      ],
      errorCount: 1,
      warningCount: 0,
    };
    refreshEditorUi();
    return;
  }
  appState.savedYaml[filename] = appState.drafts[filename];
  delete appState.drafts[filename];
  await reloadWorkflowList();
  const wasFullscreen = $("#fullscreen-editor-dialog").open,
    panelWasOpen = $("#workflow-panel-dialog").open,
    keepYamlOpen =
      panelWasOpen &&
      $("#panel-yaml-section") &&
      !$("#panel-yaml-section").hidden;
  if (panelWasOpen) {
    await openWorkflowPanel(filename);
    if (keepYamlOpen) await toggleYamlSection();
  } else
    appState.currentWorkflow = findCard(filename) || appState.currentWorkflow;
  appState.currentWorkflow.justSaved = true;
  if (wasFullscreen) $("#fullscreen-editor-dialog").close();
  if ($("#panel-yaml-status")) refreshEditorUi();
  setTimeout(() => {
    if (appState.currentWorkflow) {
      appState.currentWorkflow.justSaved = false;
      if ($("#panel-yaml-status")) refreshEditorUi();
    }
  }, 2500);
}
/** Abandonne le brouillon et restaure le texte enregistré dans les deux zones. */
function discardDraft() {
  delete appState.drafts[appState.currentWorkflow.filename];
  if ($("#panel-yaml-textarea"))
    $("#panel-yaml-textarea").value = currentYaml();
  if ($("#fullscreen-editor-textarea"))
    $("#fullscreen-editor-textarea").value = currentYaml();
  validateYaml();
}
/**
 * Verrou du bouton « Fermer » de l'éditeur plein écran : le premier clic avec
 * des modifications non enregistrées change le libellé en « Fermer sans
 * enregistrer », le second ferme. À ne pas confondre avec `appState.assistant.overwriteArmed`.
 */
let closeConfirmArmed = false;
/**
 * Ouvre l'éditeur YAML en plein écran.
 * @param {"a"|undefined} tab "a" ouvre directement l'onglet Assistant, sinon « Problèmes »
 * Suppose que `appState.currentWorkflow` et `appState.savedYaml[fichier]` sont déjà renseignés.
 */
function openFullscreenEditor(initialSideTab) {
  closeConfirmArmed = false;
  $("#fullscreen-editor-title").textContent =
    `${appState.currentWorkflow.isNew ? "Nouveau · " : ""}workflows/${appState.currentWorkflow.filename}`;
  $("#fullscreen-editor-close-button").textContent = "Fermer";
  bindYamlTextarea($("#fullscreen-editor-textarea"), true);
  $("#fullscreen-editor-save-button").onclick = saveYaml;
  $("#fullscreen-editor-revert-button").onclick = discardDraft;
  $("#fullscreen-issue-list").onclick = (event) => {
    const issueButton = event.target.closest("[data-line]");
    if (issueButton)
      selectLine($("#fullscreen-editor-textarea"), +issueButton.dataset.line);
  };
  $("#fullscreen-editor-dialog").showModal();
  validateYaml();
  selectSideTab(initialSideTab === "a" ? "a" : "p");
  if (initialSideTab !== "a") $("#fullscreen-editor-textarea").focus();
}
$("#fullscreen-editor-close-button").onclick = () => {
  if (hasUnsavedChanges() && !closeConfirmArmed) {
    closeConfirmArmed = true;
    $("#fullscreen-editor-close-button").textContent =
      "Fermer sans enregistrer";
    return;
  }
  $("#fullscreen-editor-dialog").close();
};
// Fermeture de l'éditeur (par n'importe quel moyen, y compris Échap) :
// interrompt l'assistant, abandonne la dictée, réinitialise l'assistant et, pour un
// nouveau workflow jamais enregistré, jette le brouillon.
$("#fullscreen-editor-dialog").addEventListener("close", () => {
  const assistant = appState.assistant;
  if (assistant && assistant.busy) callBackend("portal_stop");
  VoiceRecorder.abort();
  appState.voice = null;
  appState.assistant = createAssistantState();
  callBackend("portal_assist_voice", false);
  if (appState.currentWorkflow && appState.currentWorkflow.isNew) {
    const filename = appState.currentWorkflow.filename;
    delete appState.drafts[filename];
    delete appState.savedYaml[filename];
    appState.currentWorkflow = null;
    return;
  }
  if (
    appState.currentWorkflow &&
    $("#panel-yaml-section") &&
    !$("#panel-yaml-section").hidden
  )
    renderYamlSection(hasUnsavedChanges() ? "edit" : "view");
});
