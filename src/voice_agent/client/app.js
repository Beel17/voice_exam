// The voice client for the exam PC: talk key and microphone, playback of the service's
// speech, sound cues, keyboard fallback, reconnect and resume. Everything the candidate
// hears comes from the service; this page only records, plays and shows captions.

import { Chunker, DOWN_RATE, downsample, pcm16ToFloat, Playback } from "./audio.js";

const RECONNECT_MS = 2000;
const KEYS = { 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", r: "R", n: "N", p: "P", Enter: "Enter", Escape: "Esc" };
const CUES = ["listening", "saved", "not_understood", "time_warning"];

const $ = (id) => document.getElementById(id);

// Per-PC conveniences only: nothing the exam depends on is kept in the browser.
const saved = {
  get(key) { try { return localStorage.getItem(`voice-exam.${key}`) ?? ""; } catch { return ""; } },
  set(key, value) { try { localStorage.setItem(`voice-exam.${key}`, value); } catch { /* private mode */ } },
};

let audio; // AudioContext, created on the first click (browsers require a gesture)
let playback;
let socket = null;
let sessionId = "";
let lastSeq = null;
let talking = false;
let capture = null; // the microphone worklet, once started
let cueVolume = 0.75;
const chunker = new Chunker();
const cueBuffers = new Map();
const doneTimers = new Map(); // utterance id -> timer that reports playback_done
let credentials = null;

// --- Set-up -----------------------------------------------------------------------------

// Sign-in fields and the name each is remembered under on this PC.
const FIELDS = { token: "token", reg: "reg", "exam-code": "exam" };
for (const [id, key] of Object.entries(FIELDS)) $(id).value = saved.get(key);
// Signed in on this PC before: one key press (Enter) starts, with or without a screen reader.
const empty = Object.keys(FIELDS).find((id) => !$(id).value);
$(empty ?? "start").focus();

$("setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  credentials = { token: $("token").value.trim(), reg: $("reg").value.trim(), exam: $("exam-code").value.trim() };
  for (const [key, value] of Object.entries(credentials)) saved.set(key, value);
  $("setup-error").textContent = "";
  try {
    if (!audio) await startAudio(); // once per page: signing in again keeps the same audio
  } catch (error) {
    $("setup-error").textContent = `Audio could not start: ${error.message}`;
    return;
  }
  $("setup").hidden = true;
  $("exam").hidden = false;
  $("talk").focus(); // focus left on the hidden form would leave a screen reader nowhere
  connect();
});

async function startAudio() {
  audio = new AudioContext({ latencyHint: "interactive" });
  await audio.resume();
  playback = new Playback(() => audio.currentTime, (samples, at) => {
    const buffer = audio.createBuffer(1, samples.length, DOWN_RATE);
    buffer.copyToChannel(samples, 0);
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    source.start(at);
    return { duration: buffer.duration, cancel: () => { try { source.stop(); } catch { /* ended */ } } };
  });
  await Promise.all(CUES.map(async (name) => {
    const response = await fetch(`/v1/cues/${name}.wav`);
    cueBuffers.set(name, await audio.decodeAudioData(await response.arrayBuffer()));
  }));
  await startMicrophone();
}

async function startMicrophone() {
  if (!navigator.mediaDevices?.getUserMedia) {
    // Browsers only give the microphone to secure pages (https, or this PC itself).
    log("No microphone access here: use the keys, or open this page over https or on this PC.", true);
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (error) {
    // No microphone, or permission refused: the candidate still hears everything and can
    // use the keys; the invigilator sees why.
    log(`Microphone unavailable (${error.name}): keys only.`, true);
    return;
  }
  await audio.audioWorklet.addModule("capture-worklet.js");
  capture = new AudioWorkletNode(audio, "capture");
  capture.port.onmessage = ({ data }) => {
    if (!talking) return;
    for (const frame of chunker.push(downsample(data, audio.sampleRate))) send(frame);
  };
  audio.createMediaStreamSource(stream).connect(capture);
}

// --- Connection -------------------------------------------------------------------------

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/v1/session`);
  socket.binaryType = "arraybuffer";
  setConnection("Connecting…");

  socket.onopen = () => {
    setConnection("Connected");
    const hello = { type: "hello", schema_version: 1, device_id: deviceId(), token: credentials.token };
    if (sessionId) Object.assign(hello, { resume_session_id: sessionId, last_seq: lastSeq });
    send(hello);
    if (!sessionId) send({ type: "login", reg_no: credentials.reg, exam_code: credentials.exam });
  };
  socket.onmessage = ({ data }) => (typeof data === "string" ? onMessage(JSON.parse(data)) : onAudio(data));
  socket.onclose = (event) => {
    socket = null;
    stopPlayback();
    if (event.code === 4401) {
      rejectedToken();
      return;
    }
    setConnection("Reconnecting…", true);
    if (event.code !== 4409) setTimeout(connect, RECONNECT_MS);
  };
}

// The service does not know this token (a new bundle makes a new one, but this PC still
// remembers the old). Back to the form, with the token cleared and selected, saying why:
// waiting on "not authorised" would leave a candidate with nothing to do.
function rejectedToken() {
  sessionId = "";
  saved.set("token", "");
  $("token").value = "";
  $("exam").hidden = true;
  $("setup").hidden = false;
  $("setup-error").textContent =
    "This device token was not accepted. Paste the token from DEVICE-TOKEN.txt, then press Start.";
  $("token").focus();
}

function send(message) {
  if (socket?.readyState !== WebSocket.OPEN) return;
  socket.send(message instanceof Uint8Array ? message : JSON.stringify(message));
}

function deviceId() {
  let id = saved.get("device");
  if (!id) {
    id = `pc-${crypto.randomUUID().slice(0, 8)}`;
    saved.set("device", id);
  }
  return id;
}

// --- From the service -------------------------------------------------------------------

function onMessage(message) {
  switch (message.type) {
    case "session":
      sessionId = message.session_id;
      break;
    case "say":
      if (message.kind === "question") $("captions").replaceChildren();
      caption(message.text);
      playback.begin(message.utterance_id);
      break;
    case "audio_end": {
      const id = message.utterance_id;
      const wait = Math.max(0, playback.end(id) - audio.currentTime) * 1000;
      doneTimers.set(id, setTimeout(() => {
        doneTimers.delete(id);
        send({ type: "playback_done", utterance_id: id });
      }, wait));
      break;
    }
    case "stop":
      stopPlayback();
      break;
    case "cue":
      cueVolume = message.volume;
      playCue(message.name);
      break;
    case "heard":
      lastSeq = message.seq;
      $("heard").textContent = message.text ? `Heard: “${message.text}”` : "Heard nothing";
      break;
    case "state":
      showState(message);
      break;
    case "error":
      if (message.code === "session_not_found") {
        sessionId = "";
        send({ type: "login", reg_no: credentials.reg, exam_code: credentials.exam });
      }
      log(`${message.code}: ${message.message}`, true);
      break;
  }
}

function onAudio(data) {
  playback.push(pcm16ToFloat(new Uint8Array(data)));
}

function stopPlayback() {
  playback?.stopAll();
  for (const timer of doneTimers.values()) clearTimeout(timer);
  doneTimers.clear();
}

function playCue(name) {
  const buffer = cueBuffers.get(name);
  if (!buffer) return;
  const source = audio.createBufferSource();
  const gain = audio.createGain();
  gain.gain.value = cueVolume;
  source.buffer = buffer;
  source.connect(gain).connect(audio.destination);
  source.start();
}

// --- Talking ----------------------------------------------------------------------------

function startTalking() {
  if (talking) return;
  if (socket?.readyState !== WebSocket.OPEN) {
    playCue("not_understood"); // the talk key is off until the connection is back
    return;
  }
  talking = true;
  capture?.port.postMessage("on");
  $("talk").dataset.on = "true";
  stopPlayback(); // barge-in: silence at once, without waiting for the service
  playCue("listening");
  chunker.flush();
  send({ type: "talk_start" });
}

function stopTalking() {
  if (!talking) return;
  talking = false;
  capture?.port.postMessage("off");
  $("talk").dataset.on = "false";
  for (const frame of chunker.flush()) send(frame);
  send({ type: "talk_end" });
}

function typing(event) {
  return event.target instanceof HTMLInputElement;
}

document.addEventListener("keydown", (event) => {
  if (typing(event) || $("exam").hidden) return;
  if (event.code === "Space") {
    event.preventDefault();
    if (!event.repeat) startTalking();
    return;
  }
  const key = KEYS[event.key.length === 1 ? event.key.toLowerCase() : event.key];
  if (key && !event.repeat) {
    event.preventDefault();
    stopPlayback();
    send({ type: "key", key });
  }
});
document.addEventListener("keyup", (event) => {
  if (event.code === "Space" && !typing(event)) {
    event.preventDefault();
    stopTalking();
  }
});
$("talk").addEventListener("pointerdown", startTalking);
for (const end of ["pointerup", "pointerleave", "pointercancel"]) $("talk").addEventListener(end, stopTalking);

// --- Captions and status, for the invigilator ---------------------------------------------

function caption(text) {
  const p = document.createElement("p");
  p.textContent = text;
  $("captions").append(p);
  while ($("captions").childElementCount > 8) $("captions").firstElementChild.remove();
}

function showState(state) {
  $("progress").textContent = `Question ${state.question} of ${state.of}`;
  $("answered").textContent = `${state.answered} answered`;
  $("time-left").textContent = state.time_left_s == null ? "" : `${Math.floor(state.time_left_s / 60)} min left`;
  $("speed").textContent = `Speed ${state.speed}×`;
  cueVolume = state.cue_volume;
}

function setConnection(text, warn = false) {
  $("connection").textContent = text;
  $("connection").classList.toggle("warn", warn);
}

function log(text, warn = false) {
  const line = document.createElement("div");
  line.textContent = text;
  line.classList.toggle("warn", warn);
  $("log").append(line);
}
