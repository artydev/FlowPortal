"use strict";
/**
 * FlowPortal — interface web (cartes de workflows YAML).
 *
 * Architecture :
 *  - Aucune dépendance, aucun module ni build : des scripts classiques chargés dans
 *    l'ordre de `index2.html` (les fonctions et constantes de haut niveau sont
 *    partagées entre fichiers). Le rendu passe par les gabarits `html` (qui
 *    échappent automatiquement les valeurs) puis câblage des événements à la main.
 *  - `core.js` : cœur pur (formatage, validation, réducteurs), testé par `core.test.js`.
 *  - Le backend Python est joint via Eel : `eel.<fonction>()` (appels, voir
 *    `callBackend`) et `receiveEvent` (événements poussés par Python).
 *  - Tout l'état de l'interface vit dans l'objet `appState`. Les fonctions de rendu
 *    relisent `appState` et réécrivent le DOM ; il n'y a pas de rendu réactif :
 *    après avoir modifié `appState`, il faut rappeler la fonction de rendu concernée.
 *
 * Ordre de chargement : core → html-template → state → workflow-list →
 * workflow-panel → voice → engine-events → yaml-editor → workflow-actions →
 * assistant → run-result → main. Un fichier ne doit exécuter, au chargement, que
 * ce qui est défini dans les fichiers précédents (le câblage des événements, qui
 * appelle des fonctions plus tard, est sans risque).
 */
/** Type d'étape (champ `type` du YAML) → libellé affiché. Un type inconnu s'affiche tel quel. */
const STEP_TYPE_LABELS = {
  agent: "Agent",
  file_write: "Écrit un fichier",
  file_read: "Lit un fichier",
  python: "Python",
  shell: "Commande",
  http_get: "HTTP GET",
  http_post: "HTTP POST",
  http_put: "HTTP PUT",
  http_delete: "HTTP DELETE",
  human_approval: "Validation",
  user_input: "Saisie",
  mcp_call: "Appel MCP",
  mcp_tools: "Outils MCP",
  log: "Journal",
  end: "Fin",
  transform: "Transformation",
};
/** Statut d'un lancement → libellé (RUN_STATUS_LABELS). */
const RUN_STATUS_LABELS = {
  ok: "Réussi",
  error: "Échoué",
  rejected: "Refusé",
  interrupted: "Interrompu",
  cancelled: "Arrêté",
};
/** Statut d'un lancement → pictogramme court (historique des lancements). */
const RUN_STATUS_ICONS = {
  ok: "✓",
  error: "✕",
  rejected: "⏸",
  interrupted: "✕",
  cancelled: "■",
};
/**
 * État global de l'interface.
 *  - workflows: workflows renvoyés par `portal_list` (un objet par fichier YAML :
 *             filename, title, summary, steps, inputs, tags, ready, missing,
 *             error, last_run…)
 *  - recentRuns: derniers lancements, tous workflows confondus
 *  - busy   : le moteur exécute un workflow (un seul à la fois, côté Python)
 *  - filter : "all" | "agent" | "human" | "mcp" | "solo" (« Sans IA »)
 *  - searchText: recherche en minuscules
 *  - currentWorkflow: carte ouverte (panneau ou éditeur). Pour un nouveau workflow non
 *             encore créé, c'est un objet minimal avec `isNew: true`
 *  - liveRun: lancement en cours ou le plus récent (voir `launchWorkflow`) :
 *             { filename, running, stopping, stepStates: {id_étape: état}, warnings: [],
 *               gate, renderedGate, waitingStepId, runId }
 *             états d'étape : run | ok | err | wait | skip | stop
 *  - drafts : YAML modifié mais non enregistré, par nom de fichier
 *  - savedYaml: dernier YAML connu sur disque, par nom de fichier (cache de lecture)
 *  - runHistory: historique des lancements du workflow ouvert
 *  - assistant: état de l'assistant (voir `createAssistantState`)
 *  - voice  : dictée en cours { state: "idle" | "recording" | "transcribing",
 *             message, errorMessage, elapsedSeconds, target: "gate" | "asst" } ou null
 *  - viewMode: "cards" | "list" (mémorisé dans localStorage)
 */
const appState = {
  workflows: [],
  recentRuns: [],
  busy: false,
  filter: "all",
  searchText: "",
  currentWorkflow: null,
  liveRun: null,
  drafts: {},
  savedYaml: {},
  runHistory: [],
  assistant: createAssistantState(),
  voice: null,
  viewMode: "cards",
};
/** Retrouve une carte par nom de fichier. */
const findCard = (filename) =>
  appState.workflows.find((workflow) => workflow.filename == filename);
/**
 * Appelle une fonction Python exposée par Eel : `callBackend("portal_run", fichier, valeurs)`.
 * Ne lève jamais d'exception : en cas d'échec de communication, renvoie
 * `{ ok: false, error }`, au même format que les erreurs métier du backend.
 * Les appelants peuvent donc tester `r.ok` sans try/catch.
 */
const callBackend = async (backendFunctionName, ...args) => {
  try {
    return await eel[backendFunctionName](...args)();
  } catch (error) {
    return {
      ok: false,
      error: `Erreur de communication : ${(error && error.message) || error}`,
    };
  }
};
