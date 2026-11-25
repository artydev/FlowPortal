"use strict";
// Tests du cœur pur : `node --test` (aucune dépendance).
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../ui/core.js");

/** Gèle récursivement : toute mutation accidentelle d'un argument lève une erreur (mode strict). */
const deepFreeze = (value) => {
  if (value && typeof value == "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
  }
  return value;
};

test("formatDuration", () => {
  assert.equal(core.formatDuration(3.24), "3,2 s");
  assert.equal(core.formatDuration(0), "0,0 s");
  assert.equal(core.formatDuration(undefined), "0,0 s");
  assert.equal(core.formatDuration(125), "2 min 05 s");
});

test("formatTimeAgo avec un instant imposé", () => {
  const now = Date.parse("2026-11-06T12:00:00Z");
  const ago = (seconds) =>
    core.formatTimeAgo(new Date(now - seconds * 1000).toISOString(), now);
  assert.equal(core.formatTimeAgo("", now), "jamais lancé");
  assert.equal(ago(30), "à l'instant");
  assert.equal(ago(300), "il y a 5 min");
  assert.equal(ago(7200), "il y a 2 h");
  assert.equal(ago(100000), "hier");
  assert.equal(ago(400000), "il y a 4 j");
});

test("formatDateTime, formatClock, formatOffset, plural", () => {
  assert.equal(core.formatDateTime(""), "");
  assert.equal(
    core.formatDateTime(new Date(2026, 10, 5, 9, 7).toISOString()),
    "05/11/2026 09:07",
  );
  assert.equal(core.formatClock(75), "01:15");
  assert.equal(core.formatOffset(2.54), "+2,5 s");
  assert.equal(core.plural(1, "étape"), "étape");
  assert.equal(core.plural(2, "étape"), "étapes");
});

test("describeRun et runTone", () => {
  const run = {
    started: new Date(2026, 10, 5, 10, 0).toISOString(),
    duration: 5,
    steps: [{ status: "ok" }, { status: "ok" }, { status: "error" }],
    files: [{}],
  };
  assert.equal(
    core.describeRun(run),
    "Lancé le 05/11/2026 10:00 · durée 5,0 s · 2 étapes sur 3 terminées · 1 fichier",
  );
  assert.equal(core.runTone("ok"), "");
  assert.equal(core.runTone("rejected"), "stop");
  assert.equal(core.runTone("cancelled"), "stop");
  assert.equal(core.runTone("error"), "bad");
  assert.equal(core.runTone("interrupted"), "bad");
});

test("filterWorkflows", () => {
  const workflows = deepFreeze([
    {
      title: "Alpha",
      description: "x",
      filename: "a.yaml",
      tags: { agent: true },
    },
    {
      title: "Beta",
      description: "Rapport",
      filename: "b.yaml",
      tags: { human: true },
    },
    { title: "Gamma", description: "", filename: "g.yaml", tags: {} },
  ]);
  const names = (options) =>
    core.filterWorkflows(workflows, options).map((w) => w.title);
  assert.deepEqual(names({}), ["Alpha", "Beta", "Gamma"]);
  assert.deepEqual(names({ filter: "agent" }), ["Alpha"]);
  assert.deepEqual(names({ filter: "solo" }), ["Beta", "Gamma"]);
  assert.deepEqual(names({ searchText: "RAPPORT" }), ["Beta"]);
  assert.deepEqual(names({ searchText: "g.yaml" }), ["Gamma"]);
  assert.deepEqual(names({ filter: "mcp" }), []);
});

test("filterWorkflows par catégorie", () => {
  const workflows = deepFreeze([
    { title: "A", description: "", filename: "a.yaml", tags: {}, category: "Démo" },
    { title: "B", description: "", filename: "b.yaml", tags: {}, category: "IA" },
    { title: "C", description: "", filename: "c.yaml", tags: {}, category: "" },
    { title: "D", description: "", filename: "d.yaml", tags: {} },
  ]);
  const names = (options) =>
    core.filterWorkflows(workflows, options).map((w) => w.title);
  assert.deepEqual(names({ category: "Démo" }), ["A"]);
  assert.deepEqual(names({ category: core.NO_CATEGORY }), ["C", "D"]);
  assert.deepEqual(names({ category: "all" }), ["A", "B", "C", "D"]);
  assert.deepEqual(names({ searchText: "ia" }), ["B"]);
});

test("listCategories et groupByCategory", () => {
  const workflows = deepFreeze([
    { title: "1", category: "Tchap" },
    { title: "2", category: "" },
    { title: "3", category: "démo" },
    { title: "4", category: "Actuel" },
    { title: "5", category: "démo" },
    { title: "6" },
  ]);
  assert.deepEqual(core.listCategories(workflows), [
    { name: "Actuel", count: 1 },
    { name: "démo", count: 2 },
    { name: "Tchap", count: 1 },
    { name: "", count: 2 },
  ]);
  assert.deepEqual(
    core.groupByCategory(workflows).map((g) => g.workflows.map((w) => w.title)),
    [["4"], ["3", "5"], ["1"], ["2", "6"]],
  );
  assert.deepEqual(core.listCategories([]), []);
});

test("categoryHue est stable et dans [0, 360)", () => {
  assert.equal(core.categoryHue("Démo"), core.categoryHue("Démo"));
  for (const name of ["Démo", "IA", "Tchap", "Actuel", "🙂"]) {
    const hue = core.categoryHue(name);
    assert.ok(Number.isInteger(hue) && hue >= 0 && hue < 360);
  }
});

test("suggestCopyName cherche le premier nom libre", () => {
  assert.equal(core.suggestCopyName("flux.yaml", []), "flux-copie");
  assert.equal(
    core.suggestCopyName("flux.yaml", ["flux-copie.yaml", "FLUX-COPIE-2.YAML"]),
    "flux-copie-3",
  );
  assert.equal(core.stripYamlExtension("a.YML"), "a");
});

test("validateFilename", () => {
  const existing = ["a.yaml", "B.yaml"];
  const check = (raw, mode, currentFilename) =>
    core.validateFilename(raw, {
      existingFilenames: existing,
      mode,
      currentFilename,
    });
  assert.deepEqual(check("  ", "new"), {
    ok: false,
    message: "Donnez un nom au fichier.",
  });
  assert.deepEqual(check("neuf", "new"), { ok: true, filename: "neuf.yaml" });
  assert.deepEqual(check("neuf.yml", "new"), {
    ok: true,
    filename: "neuf.yml",
  });
  assert.equal(
    check("b", "new").message,
    "Un workflow porte déjà ce nom : « b.yaml ».",
  );
  assert.equal(
    check("a.yaml", "ren", "a.yaml").message,
    "Le nouveau nom est identique à l'ancien.",
  );
  assert.equal(check("A.yaml", "ren", "a.yaml").ok, true); // changement de casse seul : autorisé
  assert.equal(check("b.yaml", "ren", "a.yaml").ok, false);
  assert.equal(check("a.yaml", "dup", "a.yaml").ok, false); // une copie ne peut pas porter le nom de l'original
});

test("reduceLiveRunEvent : étapes, avertissements, portes (sans mutation)", () => {
  let run = deepFreeze(core.createLiveRun("a.yaml"));
  run = core.reduceLiveRunEvent(run, { type: "step_start", step_id: "s1" });
  run = core.reduceLiveRunEvent(run, { type: "step_done", step_id: "s1" });
  run = core.reduceLiveRunEvent(run, { type: "step_start", step_id: "s2" });
  run = core.reduceLiveRunEvent(run, {
    type: "workflow_warning",
    message: "attention",
  });
  assert.deepEqual(run.stepStates, { s1: "ok", s2: "run" });
  assert.deepEqual(run.warnings, ["attention"]);

  deepFreeze(run);
  const waiting = core.reduceLiveRunEvent(run, {
    type: "human_approval_required",
    message: "",
  });
  assert.deepEqual(waiting.gate, { kind: "approval", message: "Approuver ?" });
  assert.equal(waiting.waitingStepId, "s2");
  assert.equal(waiting.stepStates.s2, "wait");
  assert.equal(run.stepStates.s2, "run", "l'entrée n'est pas modifiée");

  const answered = core.closeGate(deepFreeze(waiting));
  assert.equal(answered.gate, null);
  assert.equal(answered.stepStates.s2, "run");

  const dismissed = core.closeInputGate(deepFreeze(waiting));
  assert.equal(dismissed.gate, null);
  assert.equal(dismissed.stepStates.s2, "wait");

  const input = core.reduceLiveRunEvent(run, {
    type: "user_input_required",
    prompt: "Nom ?",
    default: "zz",
    voice: 1,
  });
  assert.deepEqual(input.gate, {
    kind: "input",
    prompt: "Nom ?",
    def: "zz",
    required: true,
    voice: true,
  });

  const cancelled = core.reduceLiveRunEvent(deepFreeze(input), {
    type: "workflow_cancelled",
    step_id: "s2",
  });
  assert.equal(cancelled.gate, null);
  assert.equal(cancelled.stepStates.s2, "stop");
});

test("reduceLiveRunEvent ignore ce qui ne le concerne pas", () => {
  const stopped = deepFreeze({
    ...core.createLiveRun("a.yaml"),
    running: false,
  });
  assert.equal(
    core.reduceLiveRunEvent(stopped, { type: "step_start", step_id: "x" }),
    stopped,
  );
  assert.equal(core.reduceLiveRunEvent(null, { type: "step_start" }), null);
  const running = deepFreeze(core.createLiveRun("a.yaml"));
  assert.equal(core.reduceLiveRunEvent(running, { type: "inconnu" }), running);
});

test("markRunSaved, markStopping, clearStopping", () => {
  const run = deepFreeze({
    ...core.createLiveRun("a.yaml"),
    gate: { kind: "approval" },
  });
  assert.deepEqual(
    { ...core.markRunSaved(run, "r9") },
    { ...run, running: false, runId: "r9" },
  );
  assert.equal(core.markRunSaved(null, "r9"), null);
  const stopping = core.markStopping(run);
  assert.equal(stopping.stopping, true);
  assert.equal(stopping.gate, null);
  assert.equal(core.clearStopping(stopping).stopping, false);
});

test("assistant : demande, progression, fin avec proposition", () => {
  let assistant = deepFreeze(core.createAssistantState());
  assert.equal(
    core.reduceAssistantEvent(assistant, { type: "assist_status", text: "x" }),
    assistant,
    "ignoré tant qu'aucune demande n'est en cours",
  );

  assistant = deepFreeze(
    core.startAssistantRequest(assistant, "ajoute une étape", "name: a\n"),
  );
  assert.equal(assistant.busy, true);
  assert.deepEqual(assistant.messages, [
    { role: "user", text: "ajoute une étape" },
  ]);

  const thinking = core.reduceAssistantEvent(assistant, {
    type: "assist_event",
    event: { type: "agent_thought", data: "  je   réfléchis \n encore " },
  });
  assert.equal(thinking.status, "je réfléchis encore");
  const calling = core.reduceAssistantEvent(assistant, {
    type: "assist_event",
    event: { type: "agent_call", data: { name: "outil" } },
  });
  assert.equal(calling.status, "CSAgent utilise : outil");
  const asking = core.reduceAssistantEvent(assistant, {
    type: "assist_event",
    event: { type: "human_approval_required" },
  });
  assert.deepEqual(asking.gate, { message: "Approuver ?" });
  assert.equal(core.closeAssistantGate(asking).gate, null);
  assert.equal(core.markAssistantStopping(assistant).status, "Arrêt en cours…");

  const done = core.reduceAssistantEvent(assistant, {
    type: "assist_done",
    ok: true,
    changed: true,
    draft: "name: b\n",
    diff: [{ op: "add", text: "name: b" }],
    answer: "fait",
  });
  assert.equal(done.busy, false);
  assert.deepEqual(done.proposal, {
    draft: "name: b\n",
    diff: [{ op: "add", text: "name: b" }],
    issues: [],
    answer: "fait",
    baseYaml: "name: a\n",
  });

  const unchanged = core.reduceAssistantEvent(assistant, {
    type: "assist_done",
    ok: true,
    changed: false,
    answer: "rien à faire",
  });
  assert.equal(
    unchanged.messages.at(-1).text,
    "CSAgent n'a rien modifié.\nrien à faire",
  );
  const failed = core.reduceAssistantEvent(assistant, {
    type: "assist_done",
    ok: false,
  });
  assert.deepEqual(failed.messages.at(-1), {
    role: "err",
    text: "L'assistant a échoué.",
  });
  assert.equal(
    core.failAssistantRequest(assistant, "").messages.at(-1).text,
    "Envoi impossible.",
  );
});

test("assistant : rejet, application, confirmation d'écrasement", () => {
  const withProposal = deepFreeze({
    ...core.createAssistantState(),
    proposal: { draft: "d" },
    overwriteArmed: true,
  });
  const rejected = core.rejectAssistantProposal(withProposal);
  assert.equal(rejected.proposal, null);
  assert.equal(rejected.overwriteArmed, false);
  assert.equal(rejected.messages.at(-1).text, "Proposition rejetée.");
  const applied = core.markProposalApplied(withProposal);
  assert.equal(
    applied.messages.at(-1).text,
    "Proposition appliquée dans l'éditeur (non enregistrée).",
  );
  assert.equal(
    core.armOverwrite(core.createAssistantState()).overwriteArmed,
    true,
  );
});

test("dictée : progression, texte reconnu, erreur", () => {
  const voice = deepFreeze({
    state: "transcribing",
    message: "",
    errorMessage: "",
    target: "gate",
  });
  const progress = core.reduceVoiceEvent(
    voice,
    { type: "user_input_progress", part: 1, total: 3 },
    { hasTarget: true },
  );
  assert.equal(progress.voice.message, "Transcription… partie 1/3");
  assert.equal(progress.voice.state, "transcribing");

  const ok = core.reduceVoiceEvent(
    voice,
    { type: "user_input_transcribed", text: " bonjour " },
    { hasTarget: true },
  );
  assert.equal(ok.transcript, "bonjour");
  assert.equal(ok.voice.state, "idle");
  assert.match(ok.voice.message, /relisez et corrigez/);
  const forAssistant = core.reduceVoiceEvent(
    { ...voice, target: "asst" },
    { type: "user_input_transcribed", text: "x" },
    { hasTarget: true },
  );
  assert.match(forAssistant.voice.message, /puis envoyez/);
  const forChat = core.reduceVoiceEvent(
    { ...voice, target: "chat" },
    { type: "user_input_transcribed", text: "x" },
    { hasTarget: true },
  );
  assert.match(forChat.voice.message, /puis envoyez/);

  const silence = core.reduceVoiceEvent(
    voice,
    { type: "user_input_transcribed", text: " " },
    { hasTarget: true },
  );
  assert.equal(silence.voice.errorMessage, "Aucune parole détectée.");
  const noTarget = core.reduceVoiceEvent(
    voice,
    { type: "user_input_transcribed", text: "x" },
    { hasTarget: false },
  );
  assert.equal(noTarget.transcript, "");
  const failure = core.reduceVoiceEvent(
    voice,
    { type: "user_input_error" },
    { hasTarget: true },
  );
  assert.equal(failure.voice.errorMessage, "Transcription impossible.");

  assert.equal(core.appendTranscript("", "salut"), "salut");
  assert.equal(core.appendTranscript("déjà  \n", "salut"), "déjà\nsalut");
});

test("tokenizeYamlLine", () => {
  assert.deepEqual(core.tokenizeYamlLine("# note"), {
    kind: "comment",
    text: "# note",
  });
  assert.deepEqual(core.tokenizeYamlLine("  - name: Alpha # c"), {
    kind: "entry",
    indent: "  - ",
    key: "name",
    value: " Alpha",
    comment: " # c",
    quoted: false,
  });
  assert.equal(core.tokenizeYamlLine('t: "x"').quoted, true);
  assert.deepEqual(core.tokenizeYamlLine("  texte libre"), {
    kind: "plain",
    text: "  texte libre",
  });
});

test("summarizeIssues trie par ligne sans modifier l'entrée", () => {
  const raw = deepFreeze([
    { level: "warn", line: 5 },
    { level: "error", line: 2 },
    { level: "error" },
  ]);
  const summary = core.summarizeIssues(raw);
  assert.deepEqual(
    summary.issues.map((i) => i.line),
    [undefined, 2, 5],
  );
  assert.equal(summary.errorCount, 2);
  assert.equal(summary.warningCount, 1);
  assert.deepEqual(core.summarizeIssues(undefined), {
    issues: [],
    errorCount: 0,
    warningCount: 0,
  });
});

test("buildRunLog cumule les durées et omet les étapes non exécutées", () => {
  const log = core.buildRunLog(
    deepFreeze([
      { id: "s1", type: "agent", status: "ok", duration: 2 },
      {
        id: "s2",
        type: "shell",
        status: "error",
        duration: 1.5,
        output: "boom\nsuite",
      },
      { id: "s3", type: "log", status: "notrun", duration: 0 },
    ]),
  );
  assert.deepEqual(log, [
    { kind: "start", offset: 0, stepId: "s1", stepType: "agent" },
    { kind: "end", offset: 2, stepId: "s1", status: "ok", duration: 2 },
    { kind: "start", offset: 2, stepId: "s2", stepType: "shell" },
    { kind: "end", offset: 3.5, stepId: "s2", status: "error", duration: 1.5 },
    { kind: "error", message: "boom" },
  ]);
});

test("capTimelineSteps : plafonne en gardant le début, une ellipse « +N » et le dernier point", () => {
  const steps = deepFreeze(Array.from({ length: 26 }, (_, i) => ({ type: i == 25 ? "human_approval" : "shell", id: "s" + i })));
  const shown = core.capTimelineSteps(steps);
  assert.equal(shown.length, 13);                                   // 11 premiers + ellipse + dernier
  assert.deepEqual(shown[11], { type: "ellipsis", hidden: 14 });
  assert.equal(shown[12].type, "human_approval");                   // la forme de l'étape finale reste visible
  assert.equal(shown[0], steps[0]);
});

test("capTimelineSteps : pas de plafond jusqu'à 12 étapes, liste vide acceptée", () => {
  const twelve = Array.from({ length: 12 }, () => ({ type: "shell" }));
  assert.equal(core.capTimelineSteps(twelve), twelve);
  assert.deepEqual(core.capTimelineSteps([]), []);
  assert.equal(core.capTimelineSteps(Array.from({ length: 13 }, () => ({ type: "shell" }))).length, 13);
});

/** Générateur pseudo-aléatoire déterministe (les tests doivent être reproductibles). */
const seededNames = (count, seed) => {
  let state = seed;
  const next = () => (state = (state * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  return Array.from({ length: count }, () =>
    Array.from({ length: 4 + Math.floor(next() * 8) }, () => String.fromCharCode(97 + Math.floor(next() * 26))).join(""));
};
const minHueGap = (hues) => {
  let gap = 360;
  hues.forEach((a, i) => hues.slice(i + 1).forEach((b) => (gap = Math.min(gap, Math.abs(a - b), 360 - Math.abs(a - b)))));
  return gap;
};

test("assignCategoryHues : teintes de la palette, jamais deux fois la même jusqu'à 12 catégories", () => {
  for (let count = 1; count <= 12; count++)
    for (let seed = 1; seed <= 200; seed++) {
      const hues = [...core.assignCategoryHues(seededNames(count, seed * 31 + count)).values()];
      assert.equal(new Set(hues).size, hues.length);
      hues.forEach((hue) => assert.ok(core.CATEGORY_HUES.includes(hue)));
      if (hues.length > 1) assert.ok(minHueGap(hues) >= 30);
      if (hues.length > 1 && hues.length <= 4) assert.ok(minHueGap(hues) >= core.CATEGORY_MIN_HUE_GAP);
    }
});

test("assignCategoryHues : indépendant de l'ordre d'arrivée, stable, et boucle au-delà de la palette", () => {
  const names = ["Démo", "Courriels", "Finance", "RH", "Archives"];
  const forward = core.assignCategoryHues(names);
  const backward = core.assignCategoryHues([...names].reverse());
  names.forEach((name) => assert.equal(forward.get(name), backward.get(name)));
  assert.deepEqual([...core.assignCategoryHues(names)], [...forward]);
  const many = core.assignCategoryHues(seededNames(20, 5));
  assert.equal(many.size, 20);
  many.forEach((hue) => assert.ok(core.CATEGORY_HUES.includes(hue)));
  assert.equal(core.assignCategoryHues(["a", "a", "a"]).size, 1);
  assert.equal(core.assignCategoryHues([]).size, 0);
});

test("categoryHue utilise les catégories déclarées et accepte une catégorie inconnue", () => {
  core.setKnownCategories([{ category: "Courriels" }, { category: "Démo" }, { category: "" }, { category: "Démo" }]);
  const hues = ["Courriels", "Démo", ""].map(core.categoryHue);
  assert.equal(new Set(hues).size, 3);
  assert.equal(core.categoryHue("Démo"), core.categoryHue("Démo"));
  assert.ok(Number.isInteger(core.categoryHue("Nouvelle")) && core.categoryHue("Nouvelle") >= 0 && core.categoryHue("Nouvelle") < 360);
});
