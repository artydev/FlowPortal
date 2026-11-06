"use strict";
/**
 * Cœur pur de FlowPortal : toute la logique qui ne touche ni au DOM, ni à Eel,
 * ni à l'état global `appState`.
 *
 * Règles du fichier :
 *  - une fonction reçoit des valeurs et renvoie une valeur ; elle ne modifie
 *    JAMAIS ses arguments (les « réducteurs » renvoient un nouvel objet, ou le
 *    même objet s'il n'y a rien à changer) ;
 *  - rien ne dépend de l'heure ou de la locale cachée : l'instant courant est un
 *    paramètre (`now`) ;
 *  - le fichier se charge comme un script classique dans la page (les fonctions
 *    deviennent globales pour `portal.js`) et sous Node via `require` pour les
 *    tests (`core.test.js`).
 *
 * `portal.js` reste la « coquille » : il lit `appState`, appelle ces fonctions,
 * range le résultat dans `appState` puis redessine.
 */

/* ── Formatage ─────────────────────────────────────────────────────────── */

/** Durée en secondes : « 3,2 s » puis « 2 min 05 s » à partir d'une minute. */
function formatDuration(seconds) {
  seconds = +seconds || 0;
  return seconds < 60
    ? `${seconds.toFixed(1).replace(".", ",")} s`
    : `${Math.floor(seconds / 60)} min ${String(Math.round(seconds % 60)).padStart(2, "0")} s`;
}

/**
 * Date ISO → « il y a 5 min », « hier »… (« jamais lancé » si vide).
 * @param {number} now instant de référence en ms (par défaut l'heure actuelle)
 */
function formatTimeAgo(isoDate, now = Date.now()) {
  if (!isoDate) return "jamais lancé";
  const elapsedSeconds = (now - new Date(isoDate)) / 1000;
  if (elapsedSeconds < 60) return "à l'instant";
  if (elapsedSeconds < 3600)
    return `il y a ${Math.floor(elapsedSeconds / 60)} min`;
  if (elapsedSeconds < 86400)
    return `il y a ${Math.floor(elapsedSeconds / 3600)} h`;
  if (elapsedSeconds < 172800) return "hier";
  return `il y a ${Math.floor(elapsedSeconds / 86400)} j`;
}

/** Date ISO → « jj/mm/aaaa hh:mm » (locale fr-FR, fuseau de la machine). */
function formatDateTime(isoDate) {
  if (!isoDate) return "";
  const date = new Date(isoDate);
  return `${date.toLocaleDateString("fr-FR")} ${date.toLocaleTimeString(
    "fr-FR",
    {
      hour: "2-digit",
      minute: "2-digit",
    },
  )}`;
}

/** Secondes entières → « mm:ss ». */
function formatClock(totalSeconds) {
  return `${String(Math.floor(totalSeconds / 60)).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

/** Décalage dans le journal d'un lancement : « +2,5 s ». */
function formatOffset(seconds) {
  return `+${seconds.toFixed(1).replace(".", ",")} s`;
}

/** Met le mot au pluriel (simple « s ») si `count` est supérieur à 1 : plural(2, "étape") → « étapes ». */
function plural(count, word) {
  return count > 1 ? `${word}s` : word;
}

/**
 * Ligne de synthèse d'un lancement terminé (en-tête de l'écran de résultat).
 * @param {{started, duration, steps: {status}[], files: any[]}} run
 */
function describeRun(run) {
  const completed = run.steps.filter((step) => step.status == "ok").length;
  return `Lancé le ${formatDateTime(run.started)} · durée ${formatDuration(run.duration)} · ${completed} ${plural(completed, "étape")} sur ${run.steps.length} ${plural(completed, "terminée")} · ${run.files.length} ${plural(run.files.length, "fichier")}`;
}

/** Classe CSS du bilan d'un lancement : "" (réussi), "stop" (refusé/arrêté) ou "bad" (échec). */
function runTone(status) {
  if (status == "ok") return "";
  return status == "rejected" || status == "cancelled" ? "stop" : "bad";
}

/* ── Liste des workflows ───────────────────────────────────────────────── */

/**
 * Workflows visibles selon le filtre et la recherche.
 *  - filter : "all" | "solo" (sans CSAgent) | clé de `workflow.tags` ("agent", "human", "mcp")
 *  - searchText : cherché (sans tenir compte de la casse) dans titre, description et nom de fichier
 */
function filterWorkflows(workflows, { searchText = "", filter = "all" } = {}) {
  const query = searchText.toLowerCase();
  return workflows.filter(
    (workflow) =>
      (filter == "all" ||
        (filter == "solo" ? !workflow.tags.agent : workflow.tags[filter])) &&
      `${workflow.title} ${workflow.description} ${workflow.filename}`
        .toLowerCase()
        .includes(query),
  );
}

/* ── Noms de fichiers ──────────────────────────────────────────────────── */

/** « mon-flux.yaml » → « mon-flux ». */
function stripYamlExtension(filename) {
  return filename.replace(/\.ya?ml$/i, "");
}

/**
 * Nom proposé pour une copie : « <nom>-copie », sinon « -copie-2 », « -copie-3 »…
 * jusqu'à un nom libre (comparaison sans tenir compte de la casse).
 */
function suggestCopyName(filename, existingFilenames) {
  const stem = stripYamlExtension(filename);
  const taken = new Set(existingFilenames.map((name) => name.toLowerCase()));
  let suggestion = `${stem}-copie`;
  for (let i = 2; taken.has(`${suggestion}.yaml`.toLowerCase()); i++)
    suggestion = `${stem}-copie-${i}`;
  return suggestion;
}

/**
 * Valide le nom saisi dans une boîte « nouveau / dupliquer / renommer ».
 * Règles : non vide ; extension .yaml ajoutée si absente ; en renommage, différent
 * de l'ancien nom ; jamais déjà pris (casse ignorée, le fichier renommé lui-même
 * est exclu).
 * @param {{existingFilenames: string[], mode: "new"|"dup"|"ren", currentFilename?: string}} context
 * @returns {{ok: true, filename: string} | {ok: false, message: string}}
 */
function validateFilename(
  rawName,
  { existingFilenames, mode, currentFilename },
) {
  let filename = rawName.trim();
  if (!filename) return { ok: false, message: "Donnez un nom au fichier." };
  if (!/\.ya?ml$/i.test(filename)) filename = `${filename}.yaml`;
  if (mode == "ren" && filename == currentFilename)
    return { ok: false, message: "Le nouveau nom est identique à l'ancien." };
  const lowered = filename.toLowerCase();
  const alreadyTaken = existingFilenames.some(
    (other) =>
      other.toLowerCase() == lowered &&
      !(mode == "ren" && other == currentFilename),
  );
  if (alreadyTaken)
    return {
      ok: false,
      message: `Un workflow porte déjà ce nom : « ${filename} ».`,
    };
  return { ok: true, filename };
}

/* ── Lancement en cours (liveRun) ──────────────────────────────────────── */
/*
 * Forme d'un lancement :
 *   { filename, running, stopping, stepStates: {idÉtape: état}, warnings: [],
 *     gate, runId, waitingStepId, renderedGate }
 *   états d'étape : run | ok | err | wait | skip | stop
 *   gate : null | { kind: "approval", message }
 *               | { kind: "input", prompt, def, required, voice }
 * `renderedGate` est un repère de rendu (voir `renderLiveRun`) : les réducteurs
 * le recopient sans le toucher.
 */

/** Lancement qui vient d'être accepté par le backend. */
function createLiveRun(filename) {
  return {
    filename,
    running: true,
    stopping: false,
    stepStates: {},
    warnings: [],
    gate: null,
    runId: null,
  };
}

/** Passe l'étape en cours en « attente » et ouvre la porte (validation ou saisie). */
function openGate(liveRun, gate) {
  const waitingStepId = Object.keys(liveRun.stepStates).find(
    (stepId) => liveRun.stepStates[stepId] == "run",
  );
  return {
    ...liveRun,
    stepStates: waitingStepId
      ? { ...liveRun.stepStates, [waitingStepId]: "wait" }
      : liveRun.stepStates,
    waitingStepId,
    gate,
  };
}

/**
 * Applique un événement du moteur à un lancement. Renvoie le même objet si le
 * lancement n'est pas en cours ou si l'événement est inconnu.
 * Pour les deux portes, l'étape en attente est celle qui est « run » (on suppose
 * une seule étape active à la fois).
 */
function reduceLiveRunEvent(liveRun, event) {
  if (!liveRun || !liveRun.running) return liveRun;
  const stepId = event.step_id;
  const withStep = (state) => ({
    ...liveRun,
    stepStates: { ...liveRun.stepStates, [stepId]: state },
  });
  switch (event.type) {
    case "step_start":
      return withStep("run");
    case "step_done":
      return withStep("ok");
    case "step_error":
      return withStep("err");
    case "step_skip":
      return withStep("skip");
    case "workflow_cancelled":
      return { ...(stepId ? withStep("stop") : liveRun), gate: null };
    case "workflow_warning":
      return { ...liveRun, warnings: [...liveRun.warnings, event.message] };
    case "human_approval_required":
      return openGate(liveRun, {
        kind: "approval",
        message: event.message || "Approuver ?",
      });
    case "user_input_required":
      return openGate(liveRun, {
        kind: "input",
        prompt: event.prompt || "Saisie demandée",
        def: event.default || "",
        required: event.required !== false,
        voice: !!event.voice,
      });
    default:
      return liveRun;
  }
}

/** Le moteur a enregistré le résultat : le lancement n'est plus en cours. */
function markRunSaved(liveRun, runId) {
  return liveRun ? { ...liveRun, running: false, runId } : liveRun;
}

/** L'utilisateur a répondu à la porte : elle se ferme et l'étape en attente repart. */
function closeGate(liveRun) {
  const { waitingStepId } = liveRun;
  return {
    ...liveRun,
    gate: null,
    stepStates: waitingStepId
      ? { ...liveRun.stepStates, [waitingStepId]: "run" }
      : liveRun.stepStates,
  };
}

/** Saisie validée ou annulée : la porte se ferme (l'étape reste affichée « en attente » jusqu'au prochain événement). */
function closeInputGate(liveRun) {
  return { ...liveRun, gate: null };
}

/** Arrêt demandé : la porte éventuelle disparaît, `stopping` évite le double clic. */
function markStopping(liveRun) {
  return { ...liveRun, stopping: true, gate: null };
}

/** Le backend a refusé l'arrêt. */
function clearStopping(liveRun) {
  return { ...liveRun, stopping: false };
}

/* ── Assistant CSAgent ─────────────────────────────────────────────────── */
/*
 * Forme de l'état de l'assistant :
 *   busy, messages [{role: "user"|"bot"|"err", text}], proposal
 *   {draft, diff, issues, answer, baseYaml} | null, status, gate {message} | null,
 *   renderedGate (repère de rendu), baseYaml (YAML envoyé avec la demande),
 *   overwriteArmed (confirmation d'écrasement demandée)
 */

/** État vierge de l'assistant. */
function createAssistantState() {
  return {
    busy: false,
    messages: [],
    proposal: null,
    status: "",
    gate: null,
    renderedGate: 0,
    baseYaml: "",
    overwriteArmed: false,
  };
}

/** Texte lisible d'une donnée d'événement de l'agent (chaîne, ou objet avec name/tool/text). */
function agentDataToText(data) {
  if (data == null) return "";
  if (typeof data == "string") return data;
  return String(data.name || data.tool || data.text || "");
}

const addMessage = (assistant, role, text) => [
  ...assistant.messages,
  { role, text },
];

/** L'utilisateur envoie une demande : message ajouté, assistant occupé, proposition précédente écartée. */
function startAssistantRequest(assistant, text, baseYaml) {
  return {
    ...assistant,
    messages: addMessage(assistant, "user", text),
    busy: true,
    status: "Envoi à CSAgent…",
    proposal: null,
    overwriteArmed: false,
    baseYaml,
  };
}

/** L'envoi de la demande a échoué côté backend. */
function failAssistantRequest(assistant, errorMessage) {
  return {
    ...assistant,
    busy: false,
    status: "",
    messages: addMessage(assistant, "err", errorMessage || "Envoi impossible."),
  };
}

/**
 * Applique un événement `assist_*`. Ignoré (même objet) si aucune demande n'est
 * en cours.
 *  - assist_status : texte de progression
 *  - assist_event  : événement de l'agent (accord demandé, réflexion, appel d'outil)
 *  - assist_done   : fin ; échec, « rien modifié », ou proposition à relire
 */
function reduceAssistantEvent(assistant, event) {
  if (!assistant || !assistant.busy) return assistant;
  if (event.type == "assist_status")
    return { ...assistant, status: event.text || "" };
  if (event.type == "assist_event") {
    const agentEvent = event.event || {};
    const agentEventType = agentEvent.type || "";
    if (agentEventType == "human_approval_required")
      return {
        ...assistant,
        gate: { message: agentEvent.message || "Approuver ?" },
        status: "CSAgent attend votre accord.",
      };
    if (agentEventType == "agent_thought") {
      const text = agentDataToText(agentEvent.data).replace(/\s+/g, " ").trim();
      return text ? { ...assistant, status: text.slice(0, 160) } : assistant;
    }
    if (agentEventType == "agent_call") {
      const text = agentDataToText(agentEvent.data);
      return text
        ? { ...assistant, status: `CSAgent utilise : ${text.slice(0, 80)}` }
        : assistant;
    }
    return assistant;
  }
  if (event.type == "assist_done") {
    const finished = { ...assistant, busy: false, status: "", gate: null };
    if (!event.ok)
      return {
        ...finished,
        messages: addMessage(
          assistant,
          "err",
          event.error || "L'assistant a échoué.",
        ),
      };
    if (!event.changed)
      return {
        ...finished,
        messages: addMessage(
          assistant,
          "bot",
          `CSAgent n'a rien modifié.${event.answer ? `\n${event.answer}` : ""}`,
        ),
      };
    return {
      ...finished,
      proposal: {
        draft: event.draft,
        diff: event.diff || [],
        issues: event.issues || [],
        answer: event.answer || "",
        baseYaml: assistant.baseYaml,
      },
      overwriteArmed: false,
    };
  }
  return assistant;
}

/** L'utilisateur a répondu à la demande d'accord de l'agent. */
function closeAssistantGate(assistant) {
  return { ...assistant, gate: null };
}

/** Arrêt demandé pendant que l'agent travaille. */
function markAssistantStopping(assistant) {
  return { ...assistant, status: "Arrêt en cours…" };
}

/** Proposition rejetée par l'utilisateur. */
function rejectAssistantProposal(assistant) {
  return {
    ...assistant,
    proposal: null,
    overwriteArmed: false,
    messages: addMessage(assistant, "bot", "Proposition rejetée."),
  };
}

/** Proposition appliquée dans l'éditeur (le texte lui-même est géré par la coquille). */
function markProposalApplied(assistant) {
  return {
    ...assistant,
    proposal: null,
    overwriteArmed: false,
    messages: addMessage(
      assistant,
      "bot",
      "Proposition appliquée dans l'éditeur (non enregistrée).",
    ),
  };
}

/** Premier clic sur « Appliquer » alors que le texte a changé : on demande confirmation. */
function armOverwrite(assistant) {
  return { ...assistant, overwriteArmed: true };
}

/* ── Dictée vocale ─────────────────────────────────────────────────────── */
/*
 * Forme : { state: "idle"|"recording"|"transcribing", message, errorMessage,
 *           elapsedSeconds, target: "gate"|"asst" }
 */

/** Ajoute le texte reconnu à la suite du texte existant (nouvelle ligne), sans l'écraser. */
function appendTranscript(existingText, transcript) {
  return existingText.trim()
    ? `${existingText.replace(/\s+$/, "")}\n${transcript}`
    : transcript;
}

/**
 * Applique un événement de transcription.
 * @param {{hasTarget: boolean}} context `hasTarget` : la zone de texte cible existe dans la page
 * @returns {{voice: object, transcript: string}} nouvel état, et texte à ajouter ("" si rien)
 */
function reduceVoiceEvent(voice, event, { hasTarget }) {
  if (event.type == "user_input_progress")
    return {
      voice: {
        ...voice,
        message:
          event.total > 1
            ? `Transcription… partie ${event.part}/${event.total}`
            : "Transcription en cours…",
      },
      transcript: "",
    };
  if (event.type == "user_input_transcribed") {
    const transcript = String(event.text || "").trim();
    if (transcript && hasTarget)
      return {
        voice: {
          ...voice,
          state: "idle",
          message:
            voice.target == "asst"
              ? "Transcription terminée : relisez, corrigez si besoin, puis envoyez."
              : "Transcription terminée : relisez et corrigez le texte avant de valider.",
        },
        transcript,
      };
    return {
      voice: {
        ...voice,
        state: "idle",
        message: "",
        errorMessage: "Aucune parole détectée.",
      },
      transcript: "",
    };
  }
  return {
    voice: {
      ...voice,
      state: "idle",
      message: "",
      errorMessage: event.message || "Transcription impossible.",
    },
    transcript: "",
  };
}

/* ── YAML et validation ────────────────────────────────────────────────── */

/**
 * Analyse UNE ligne de YAML pour la coloration (ce n'est pas un parseur : les
 * scalaires multilignes et les clés entre guillemets ne sont pas reconnus).
 * @returns {{kind: "comment", text} | {kind: "plain", text}
 *   | {kind: "entry", indent, key, value, comment, quoted}}
 */
function tokenizeYamlLine(line) {
  if (/^\s*#/.test(line)) return { kind: "comment", text: line };
  const match = line.match(/^(\s*(?:- )?)([A-Za-z_][\w-]*)(:)(.*)$/);
  if (!match) return { kind: "plain", text: line };
  let value = match[4];
  const commentIndex = value.indexOf(" #");
  const comment = commentIndex > -1 ? value.slice(commentIndex) : "";
  if (commentIndex > -1) value = value.slice(0, commentIndex);
  return {
    kind: "entry",
    indent: match[1],
    key: match[2],
    value,
    comment,
    quoted: /^\s*["'][^"']*["']\s*$/.test(value),
  };
}

/**
 * Trie les problèmes de validation par ligne et les compte.
 * @returns {{issues: object[], errorCount: number, warningCount: number}}
 */
function summarizeIssues(rawIssues) {
  const issues = [...(rawIssues || [])].sort(
    (a, b) => (a.line || 0) - (b.line || 0),
  );
  return {
    issues,
    errorCount: issues.filter((issue) => issue.level == "error").length,
    warningCount: issues.filter((issue) => issue.level != "error").length,
  };
}

/* ── Journal d'un lancement ────────────────────────────────────────────── */

/**
 * Journal reconstitué d'un lancement : les instants sont cumulés à partir des
 * durées d'étapes (ce ne sont pas des horodatages réels). Les étapes non
 * exécutées sont omises.
 * @returns {({kind: "start", offset, stepId, stepType}
 *   | {kind: "end", offset, stepId, status, duration}
 *   | {kind: "error", message})[]}
 */
function buildRunLog(steps) {
  const entries = [];
  let elapsed = 0;
  for (const step of steps) {
    if (["notrun", "skipped", "pending"].includes(step.status)) continue;
    entries.push({
      kind: "start",
      offset: elapsed,
      stepId: step.id,
      stepType: step.type,
    });
    elapsed += +step.duration || 0;
    entries.push({
      kind: "end",
      offset: elapsed,
      stepId: step.id,
      status: step.status,
      duration: step.duration,
    });
    if (step.status == "error")
      entries.push({
        kind: "error",
        message: (step.output || "").split("\n")[0],
      });
  }
  return entries;
}

/* ── Export pour les tests Node ────────────────────────────────────────── */
if (typeof module !== "undefined" && module.exports)
  module.exports = {
    formatDuration,
    formatTimeAgo,
    formatDateTime,
    formatClock,
    formatOffset,
    plural,
    describeRun,
    runTone,
    filterWorkflows,
    stripYamlExtension,
    suggestCopyName,
    validateFilename,
    createLiveRun,
    reduceLiveRunEvent,
    markRunSaved,
    closeGate,
    closeInputGate,
    markStopping,
    clearStopping,
    createAssistantState,
    agentDataToText,
    startAssistantRequest,
    failAssistantRequest,
    reduceAssistantEvent,
    closeAssistantGate,
    markAssistantStopping,
    rejectAssistantProposal,
    markProposalApplied,
    armOverwrite,
    appendTranscript,
    reduceVoiceEvent,
    tokenizeYamlLine,
    summarizeIssues,
    buildRunLog,
  };
