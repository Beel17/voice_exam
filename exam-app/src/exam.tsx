// The exam as the screen knows it: what the service last said, kept in one reducer.
// The screen never decides anything about the exam itself; every click goes to the
// service, and the screen shows the state that comes back.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { AudioEngine } from "./audio/engine";
import { Connection, type Closed, type Link } from "./connection";
import type {
  Details,
  GuideStep,
  Key,
  OptionLetter,
  QuestionView,
  ScreenAction,
  ServerMessage,
  State,
} from "./protocol";

const TOKEN_KEY = "exam.deviceToken";
const DEVICE_KEY = "exam.deviceId";
const SESSION_KEY = "exam.sessionId";
const MODE_KEY = "exam.mode";

export type Mode = "standard" | "audio";

// Storage can be unavailable (a locked-down browser); the exam still runs without it.
function load(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* not kept: a reload asks again */
  }
}

function deviceId(): string {
  let id = load(DEVICE_KEY);
  if (!id) {
    id = `pc-${Math.random().toString(36).slice(2, 10)}`;
    store(DEVICE_KEY, id);
  }
  return id;
}

export interface Notice {
  text: string;
  at: number;
}

export interface Spoken {
  id: string;
  text: string;
  kind: string;
}

export interface ExamState {
  mode: Mode;
  token: string | null;
  tokenRefused: boolean;
  link: Link;
  closed: Closed | null;
  sessionId: string | null;
  signingIn: boolean;
  loginError: string | null;
  details: Details | null;
  question: QuestionView | null;
  state: State | null;
  // The latest thing the service said that is not exam content ("Saved.", "Question 3
  // flagged to revisit.", why something was refused).
  notice: Notice | null;
  savedAt: number | null;
  // Time left as last reported by the service, and when; the screen counts down from it.
  time: { seconds: number; at: number } | null;
  // Audio mode: the last few replies (the newest is being spoken), and what was heard.
  spoken: Spoken[];
  heard: string | null;
  talking: boolean;
}

type Event =
  | { kind: "message"; message: ServerMessage; now: number }
  | { kind: "link"; link: Link }
  | { kind: "closed"; reason: Closed }
  | { kind: "token"; token: string }
  | { kind: "signing_in" }
  | { kind: "mode"; mode: Mode }
  | { kind: "talking"; talking: boolean }
  | { kind: "finished" };

function initial(): ExamState {
  return {
    mode: load(MODE_KEY) === "audio" ? "audio" : "standard",
    token: load(TOKEN_KEY),
    tokenRefused: false,
    link: "connecting",
    closed: null,
    sessionId: load(SESSION_KEY),
    signingIn: false,
    loginError: null,
    details: null,
    question: null,
    state: null,
    notice: null,
    savedAt: null,
    time: null,
    spoken: [],
    heard: null,
    talking: false,
  };
}

function reduce(s: ExamState, e: Event): ExamState {
  switch (e.kind) {
    case "link":
      return { ...s, link: e.link };
    case "closed":
      return e.reason === "unauthorized"
        ? { ...s, closed: null, token: null, tokenRefused: true }
        : { ...s, closed: e.reason };
    case "token":
      return { ...s, token: e.token, tokenRefused: false };
    case "signing_in":
      return { ...s, signingIn: true, loginError: null };
    case "mode":
      return { ...s, mode: e.mode };
    case "talking":
      return { ...s, talking: e.talking };
    case "finished":
      return { ...initial(), mode: s.mode, token: s.token, sessionId: null, link: s.link };
    case "message":
      return onMessage(s, e.message, e.now);
  }
}

function onMessage(s: ExamState, m: ServerMessage, now: number): ExamState {
  switch (m.type) {
    case "session":
      return { ...s, sessionId: m.session_id, signingIn: false, loginError: null };
    case "details":
      return { ...s, details: m };
    case "question":
      return { ...s, question: m };
    case "state": {
      // A repeated report of the same time is not news; resetting to it would make the
      // clock jump back between the service's checks.
      const fresh = m.time_left_s !== null && m.time_left_s !== s.state?.time_left_s;
      const time = fresh ? { seconds: m.time_left_s!, at: now } : s.time;
      return { ...s, state: m, time };
    }
    case "say":
      if (s.mode === "audio") {
        const spoken = [...s.spoken, { id: m.utterance_id, text: m.text, kind: m.kind }].slice(-6);
        const signedIn = s.signingIn && m.kind === "prompt";
        return { ...s, spoken, signingIn: false, loginError: signedIn ? m.text : s.loginError };
      }
      if (m.kind === "question" || m.kind === "options") return s; // shown, not told
      if (s.signingIn) return { ...s, signingIn: false, loginError: m.text };
      return { ...s, notice: { text: m.text, at: now } };
    case "cue":
      return m.name === "saved" ? { ...s, savedAt: now } : s;
    case "heard":
      return { ...s, heard: m.text };
    case "error":
      if (m.code === "session_not_found") {
        return { ...s, sessionId: null, details: null, question: null, state: null };
      }
      return s;
    default:
      return s;
  }
}

interface Api {
  exam: ExamState;
  setToken(token: string): void;
  login(examNumber: string): void;
  act(action: ScreenAction, question?: number, choice?: OptionLetter | null): void;
  finish(): void;
  setMode(mode: Mode): void;
  // Audio mode.
  startAudio(): Promise<AudioEngine>;
  audioStarted(): boolean;
  silence(): void;
  guide(step: GuideStep): Promise<void>;
  key(key: Key): void;
  talk(down: boolean): void;
  tone(side: "left" | "right"): Promise<void>;
}

const ExamContext = createContext<Api | null>(null);

export function ExamProvider({ children }: { children: ReactNode }) {
  const [exam, dispatch] = useReducer(reduce, undefined, initial);
  const sessionRef = useRef(exam.sessionId);
  sessionRef.current = exam.sessionId;
  const connection = useRef<Connection | null>(null);
  const engine = useRef<AudioEngine | null>(null);
  if (!engine.current) {
    engine.current = new AudioEngine({
      sendJson: (message) => connection.current?.send(message as never),
      sendAudio: (frame) => connection.current?.sendAudio(frame),
    });
  }

  useEffect(() => store(SESSION_KEY, exam.sessionId), [exam.sessionId]);
  useEffect(() => store(MODE_KEY, exam.mode), [exam.mode]);

  useEffect(() => {
    if (!exam.token) return;
    const audio = engine.current!;
    const c = new Connection(exam.token, deviceId(), () => sessionRef.current, exam.mode === "audio", {
      message: (message) => {
        // The sound side of the service's messages; the reducer keeps the rest.
        if (message.type === "say") audio.begin(message.utterance_id);
        else if (message.type === "audio_end") audio.audioEnd(message.utterance_id);
        else if (message.type === "stop") audio.stopAll();
        else if (message.type === "cue") audio.cue(message.name, message.volume);
        dispatch({ kind: "message", message, now: Date.now() });
      },
      audio: (data) => audio.audio(data),
      link: (link) => {
        if (link !== "online") audio.stopAll();
        dispatch({ kind: "link", link });
      },
      closed: (reason) => dispatch({ kind: "closed", reason }),
    });
    connection.current = c;
    c.open();
    return () => c.close();
  }, [exam.token, exam.mode]);

  const setToken = useCallback((token: string) => {
    store(TOKEN_KEY, token);
    dispatch({ kind: "token", token });
  }, []);

  const login = useCallback((examNumber: string) => {
    if (connection.current?.send({ type: "login", reg_no: examNumber })) {
      dispatch({ kind: "signing_in" });
    }
  }, []);

  const act = useCallback((action: ScreenAction, question?: number, choice?: OptionLetter | null) => {
    connection.current?.send({
      type: "act",
      action,
      ...(question !== undefined ? { question } : {}),
      ...(choice !== undefined ? { choice } : {}),
    });
  }, []);

  const finish = useCallback(() => {
    sessionRef.current = null;
    forgetPhotos();
    dispatch({ kind: "finished" });
    connection.current?.restart(); // the next candidate signs in on a fresh connection
  }, []);

  // Changing mode reconnects; a signed-in candidate's exam resumes in the new mode.
  const setMode = useCallback((mode: Mode) => dispatch({ kind: "mode", mode }), []);

  const startAudio = useCallback(async () => {
    await engine.current!.start(import.meta.env.BASE_URL);
    return engine.current!;
  }, []);

  const guide = useCallback((step: GuideStep) => {
    const heard = engine.current!.heardNext();
    connection.current?.send({ type: "guide", step });
    // Without speech (or if a reply is lost) the guide must not wait for ever.
    return Promise.race([heard, new Promise<void>((resolve) => window.setTimeout(resolve, 30000))]);
  }, []);

  const key = useCallback((k: Key) => {
    engine.current!.stopAll();
    connection.current?.send({ type: "key", key: k });
  }, []);

  const talk = useCallback((down: boolean) => {
    const changed = down ? engine.current!.startTalking() : engine.current!.stopTalking();
    if (changed) dispatch({ kind: "talking", talking: down });
  }, []);

  const audioStarted = useCallback(() => engine.current!.started, []);
  const silence = useCallback(() => engine.current!.silence(), []);

  const tone = useCallback((side: "left" | "right") => engine.current!.tone(side), []);

  const api = useMemo(
    () => ({ exam, setToken, login, act, finish, setMode, startAudio, audioStarted, silence, guide, key, talk, tone }),
    [exam, setToken, login, act, finish, setMode, startAudio, audioStarted, silence, guide, key, talk, tone],
  );
  return <ExamContext.Provider value={api}>{children}</ExamContext.Provider>;
}

export function useExam(): Api {
  const api = useContext(ExamContext);
  if (!api) throw new Error("useExam outside ExamProvider");
  return api;
}

/** Seconds left now, counting down from the service's last report. */
export function secondsLeft(exam: ExamState, now: number): number | null {
  if (!exam.time) return null;
  return Math.max(0, exam.time.seconds - Math.floor((now - exam.time.at) / 1000));
}

const photos = new Map<string, Promise<string | null>>();

/** The candidate's photo for the identity check, as an object URL (the device token is
 * needed, so an <img src> cannot fetch it directly). Fetched once per session. */
export function fetchPhoto(token: string, sessionId: string): Promise<string | null> {
  let photo = photos.get(sessionId);
  if (!photo) {
    photo = fetch(`/v1/sessions/${encodeURIComponent(sessionId)}/photo`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (response) => (response.ok ? URL.createObjectURL(await response.blob()) : null))
      .catch(() => null);
    photos.set(sessionId, photo);
  }
  return photo;
}

/** Forget a finished candidate's photo, so nothing of theirs stays in the page. */
export function forgetPhotos(): void {
  for (const photo of photos.values()) {
    void photo.then((url) => url && URL.revokeObjectURL(url));
  }
  photos.clear();
}
