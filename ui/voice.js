"use strict";
/**
 * Dictée vocale : enregistrement navigateur (`VoiceRecorder`) et boutons de
 * dictée. La transcription revient par les événements du moteur.
 */
/* ── dictée : enregistrement dans le navigateur, transcription par CSAgent (côté Python) ── */
/**
 * Durée maximale d'un enregistrement vocal, en secondes (arrêt automatique).
 *
 * Dictée : le navigateur enregistre l'audio (MediaRecorder), l'envoie en base64
 * à Python (`audio_upload`), qui le transcrit avec CSAgent. Le résultat revient
 * de façon asynchrone par les événements `user_input_progress`,
 * `user_input_transcribed` et `user_input_error` (voir `handleVoiceEvent`).
 * `appState.voice.target` indique où insérer le texte : "gate" (saisie demandée par un
 * workflow) ou "asst" (zone de l'assistant).
 */
const VOICE_MAX_SECONDS = 20 * 60;
/**
 * Met à jour boutons et messages de dictée. Délègue à `renderAssistantControls` quand la cible
 * est l'assistant. Pour une saisie de workflow, bloque aussi « Valider » pendant
 * un enregistrement/une transcription et tant qu'un texte obligatoire est vide.
 */
function renderVoiceControls() {
  if (appState.voice && appState.voice.target == "asst") {
    renderAssistantControls();
    return;
  }
  const gate = appState.liveRun && appState.liveRun.gate;
  if (!gate || gate.kind != "input") return;
  const voice = appState.voice,
    textarea = $("#gate-input-textarea"),
    validateButton = $("#gate-input-submit-button");
  if (!textarea || !validateButton) return;
  validateButton.disabled =
    (voice && voice.state != "idle") ||
    (gate.required && !textarea.value.trim());
  if (!gate.voice || !voice) return;
  const micButton = $("#gate-mic-button");
  micButton.className = `button-primary ${voice.state == "recording" ? "is-danger" : "is-ghost"}`;
  micButton.setAttribute("aria-pressed", voice.state == "recording");
  micButton.disabled = voice.state == "transcribing";
  micButton.textContent =
    voice.state == "recording"
      ? `⏹ Arrêter (${formatClock(voice.elapsedSeconds)})`
      : voice.state == "transcribing"
        ? "… Transcription"
        : "🎙 Dicter";
  $("#gate-voice-status").textContent =
    voice.state == "recording"
      ? "Enregistrement en cours… parlez, puis cliquez sur Arrêter."
      : voice.message;
  const errorBadge = $("#gate-voice-error");
  errorBadge.hidden = !voice.errorMessage;
  errorBadge.textContent = voice.errorMessage;
}
/**
 * Gestionnaire d'enregistrement audio (objet unique, état partagé).
 * Cycle : start → (enregistrement) → stop → finish (envoi) → événements Python.
 * `abort` abandonne sans rien envoyer (`discard`) ; utilisé quand la porte de
 * saisie disparaît ou que l'éditeur se ferme.
 */
const VoiceRecorder = {
  recorder: null,
  micStream: null,
  audioChunks: [],
  timerId: null,
  startedAtMs: 0,
  discardOnStop: false,
  // Le micro exige un contexte sécurisé : la page doit être servie en localhost/https.
  isSupported() {
    return !!(
      navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia &&
      window.MediaRecorder
    );
  },
  // Coupe le chronomètre et libère le micro (sinon l'indicateur d'enregistrement du navigateur reste allumé).
  releaseMic() {
    clearInterval(VoiceRecorder.timerId);
    if (VoiceRecorder.micStream) {
      VoiceRecorder.micStream.getTracks().forEach((track) => track.stop());
      VoiceRecorder.micStream = null;
    }
  },
  // Choisit le premier format audio pris en charge ; l'enregistrement est découpé en morceaux d'1 s.
  async start() {
    const voice = appState.voice;
    voice.errorMessage = "";
    voice.message = "";
    if (!VoiceRecorder.isSupported()) {
      voice.errorMessage =
        "Le micro n'est pas disponible dans ce navigateur (la page doit être ouverte en localhost).";
      renderVoiceControls();
      return;
    }
    try {
      VoiceRecorder.micStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
    } catch (error) {
      voice.errorMessage = `Micro refusé ou introuvable (${(error && error.name) || error}). Autorisez le micro puis réessayez.`;
      renderVoiceControls();
      return;
    }
    const mime =
      [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((type) => window.MediaRecorder.isTypeSupported(type)) || "";
    VoiceRecorder.audioChunks = [];
    VoiceRecorder.discardOnStop = false;
    VoiceRecorder.recorder = new MediaRecorder(
      VoiceRecorder.micStream,
      mime
        ? {
            mimeType: mime,
          }
        : undefined,
    );
    VoiceRecorder.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size)
        VoiceRecorder.audioChunks.push(event.data);
    };
    VoiceRecorder.recorder.onstop = () =>
      VoiceRecorder.sendRecording(
        VoiceRecorder.recorder.mimeType || mime || "audio/webm",
      );
    VoiceRecorder.recorder.start(1000);
    voice.state = "recording";
    voice.elapsedSeconds = 0;
    VoiceRecorder.startedAtMs = Date.now();
    VoiceRecorder.timerId = setInterval(() => {
      // L'état `voice` peut être remplacé par un événement : on le relit à chaque tick.
      const currentVoice = appState.voice;
      if (!currentVoice) return;
      currentVoice.elapsedSeconds = Math.floor(
        (Date.now() - VoiceRecorder.startedAtMs) / 1000,
      );
      if (currentVoice.elapsedSeconds >= VOICE_MAX_SECONDS)
        VoiceRecorder.stop();
      renderVoiceControls();
    }, 500);
    renderVoiceControls();
  },
  stop() {
    if (VoiceRecorder.recorder && VoiceRecorder.recorder.state != "inactive")
      VoiceRecorder.recorder.stop();
    VoiceRecorder.releaseMic();
  },
  abort() {
    if (!appState.voice) return;
    VoiceRecorder.discardOnStop = true;
    if (VoiceRecorder.recorder && VoiceRecorder.recorder.state != "inactive") {
      try {
        VoiceRecorder.recorder.stop();
      } catch (error) {}
    }
    VoiceRecorder.releaseMic();
    appState.voice.state = "idle";
  },
  // Appelé par `recorder.onstop` : assemble les morceaux, encode en base64 (sans le préfixe data:) et envoie à Python.
  async sendRecording(mime) {
    VoiceRecorder.releaseMic();
    const voice = appState.voice;
    if (VoiceRecorder.discardOnStop || !voice) {
      if (voice) voice.state = "idle";
      renderVoiceControls();
      return;
    }
    const blob = new Blob(VoiceRecorder.audioChunks, {
      type: mime,
    });
    voice.state = "transcribing";
    voice.message = "Envoi de l'enregistrement…";
    renderVoiceControls();
    const base64Audio = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () =>
        resolve(String(reader.result).split(",")[1] || "");
      reader.readAsDataURL(blob);
    });
    const result = await callBackend("audio_upload", base64Audio, mime);
    const currentVoice = appState.voice;
    if (currentVoice && (!result || !result.ok)) {
      currentVoice.state = "idle";
      currentVoice.message = "";
      currentVoice.errorMessage =
        (result && result.error) || "Envoi impossible.";
    }
    renderVoiceControls();
  },
};
