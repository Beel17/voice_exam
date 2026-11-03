// Audio mode, for blind and visually impaired candidates: the service reads everything
// aloud and listens while the Space bar is held. The screen repeats what is said in large
// type, for candidates with some sight and for the invigilator.

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Brand, clock, Photo, useNow, usePhoto } from "../components";
import { secondsLeft, useExam } from "../exam";
import type { Key } from "../protocol";

const KEYS: Record<string, Key> = {
  "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
  r: "R", n: "N", p: "P", s: "S", f: "F", t: "T", Enter: "Enter", Escape: "Esc",
};

function typing(e: KeyboardEvent): boolean {
  return (e.target as HTMLElement).closest("input, textarea") !== null;
}

/** Hold Space to talk, wherever the candidate is. */
function useTalkKey(enabled: boolean) {
  const { talk } = useExam();
  useEffect(() => {
    if (!enabled) return;
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || typing(e)) return;
      e.preventDefault();
      if (!e.repeat) talk(true);
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || typing(e)) return;
      e.preventDefault();
      talk(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      talk(false);
    };
  }, [enabled, talk]);
}

/** What is being said, newest highlighted; ``after`` hides what was said before a point. */
function Captions({ after }: { after?: string | null }) {
  const { exam } = useExam();
  const from = after ? exam.spoken.findIndex((s) => s.id === after) + 1 : 0;
  const shown = exam.spoken.slice(from).slice(-4);
  const latest = shown[shown.length - 1];
  return (
    <section className="captions" aria-label="What is being said" aria-live="off">
      {shown.map((s) => (
        <p key={s.id} className={s === latest ? "now" : ""}>
          {s.text}
        </p>
      ))}
    </section>
  );
}

function Listening() {
  const { exam } = useExam();
  return (
    <div className="listening-row">
      <span className={`talk-indicator${exam.talking ? " on" : ""}`}>
        {exam.talking ? "● Listening…" : "Hold Space to talk"}
      </span>
      {exam.heard !== null && <span className="muted">You said: “{exam.heard || "nothing heard"}”</span>}
    </div>
  );
}

// --- Before sign-in -------------------------------------------------------------------------

type Stage = "press" | "welcome" | "sound" | "sound_ok" | "mic" | "sign_in";

const STAGE_TITLES: Record<Stage, string> = {
  press: "Audio mode",
  welcome: "Welcome",
  sound: "Checking your headphones",
  sound_ok: "Checking your headphones",
  mic: "Checking your microphone",
  sign_in: "Sign in",
};

const STAGE_HINTS: Record<Stage, string> = {
  press: "Press Space to begin.",
  welcome: "Press Enter when you are ready.",
  sound: "Listen for a sound in each ear.",
  sound_ok: "Heard both? Press Enter. If not, raise your hand.",
  mic: "Hold Space and say: testing, one, two, three. Press Enter to go on.",
  sign_in: "Hold Space and say your examination number. Then say yes, or press Enter. Press R to hear this again.",
};

export function AudioStart() {
  const { exam, startAudio, guide, tone, key, setMode, login, silence } = useExam();
  const [stage, setStage] = useState<Stage>("press");
  const [problem, setProblem] = useState<string | null>(null);
  // What was said before the guide began (the service's general sign-in prompt) is not shown.
  const [after, setAfter] = useState<string | null>(null);
  const latestId = exam.spoken[exam.spoken.length - 1]?.id ?? null;
  const latestRef = useRef(latestId);
  latestRef.current = latestId;
  // Each step is a run; a key press starts the next one at once, cutting off the speech
  // of the last, and a run that has been overtaken stops where it is.
  const runs = useRef(0);

  const run = useCallback(
    async (next: Stage) => {
      const mine = ++runs.current;
      const current = () => runs.current === mine;
      silence();
      setStage(next);
      try {
        if (next === "welcome") {
          setAfter(latestRef.current);
          const engine = await startAudio();
          if (engine.microphone !== "on") {
            setProblem("The microphone is not available: the candidate can still listen and use the keys.");
          }
          if (current()) await guide("welcome");
        } else if (next === "sound") {
          for (const side of ["left", "right"] as const) {
            if (current()) await guide(side);
            if (current()) await tone(side);
          }
          if (current()) {
            setStage("sound_ok");
            await guide("sound_check");
          }
        } else if (next === "mic") {
          await guide("mic_check");
        } else if (next === "sign_in") {
          await guide("sign_in");
        }
      } catch (e) {
        setProblem(`Audio could not start: ${(e as Error).message}`);
      }
    },
    [startAudio, guide, tone, silence],
  );

  useTalkKey(stage === "mic" || stage === "sign_in");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e) || e.repeat) return;
      if (stage === "press" && e.code === "Space") {
        e.preventDefault();
        void run("welcome");
      } else if (e.key === "Enter" && stage === "welcome") void run("sound");
      else if (e.key === "Enter" && (stage === "sound" || stage === "sound_ok")) void run("mic");
      else if (e.key === "Enter" && stage === "mic") void run("sign_in");
      else if (stage === "sign_in" && (e.key === "Enter" || e.key === "Escape")) key(e.key === "Enter" ? "Enter" : "Esc");
      else if (stage === "sign_in" && e.key.toLowerCase() === "r") void run("sign_in");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stage, run, key]);

  // For the invigilator: sign the candidate in by typing, if speech is not working.
  const [number, setNumber] = useState("");
  const typed = (e: FormEvent) => {
    e.preventDefault();
    const value = number.trim().toUpperCase();
    if (/^[A-Z0-9._:/-]{1,64}$/.test(value)) login(value);
  };

  return (
    <main className="audio-page">
      <header className="topbar">
        <Brand />
        <button className="btn" onClick={() => setMode("standard")}>
          Switch to standard mode
        </button>
      </header>
      <section className="audio-stage" aria-live="polite">
        <p className="muted">{stage === "press" ? "For candidates who listen" : "Audio mode"}</p>
        <h1>{STAGE_TITLES[stage]}</h1>
        <p className="stage-hint">{STAGE_HINTS[stage]}</p>
        {stage === "press" && (
          <button className="btn btn-primary btn-large" onClick={() => void run("welcome")} autoFocus>
            Begin (Space)
          </button>
        )}
        {problem && <p className="error">{problem}</p>}
        {stage !== "press" && <Captions after={after} />}
        {(stage === "mic" || stage === "sign_in") && <Listening />}
      </section>
      {stage === "sign_in" && (
        <form className="invigilator" onSubmit={typed}>
          <span className="muted">Invigilator: or type the examination number</span>
          <input
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            aria-label="Examination number"
            autoComplete="off"
            spellCheck={false}
          />
          <button className="btn" disabled={exam.link !== "online"}>
            Sign in
          </button>
        </form>
      )}
    </main>
  );
}

// --- The spoken exam ------------------------------------------------------------------------

export function AudioExam() {
  const { exam, key, startAudio, audioStarted } = useExam();
  const photo = usePhoto();
  const now = useNow();
  const [audioOn, setAudioOn] = useState(audioStarted);
  const d = exam.details!;
  const s = exam.state!;
  const q = exam.question && exam.question.number === s.question ? exam.question : null;

  // After a reload the sound must be started again by a key press; the question is then
  // read again, since what was said while it was off was not heard.
  const resume = useCallback(async () => {
    await startAudio();
    setAudioOn(true);
    key("R");
  }, [startAudio, key]);

  useTalkKey(audioOn);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e) || e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      if (!audioOn) {
        if (e.code === "Space") {
          e.preventDefault();
          void resume();
        }
        return;
      }
      const k = KEYS[e.key.length === 1 ? e.key.toLowerCase() : e.key];
      if (!k) return;
      e.preventDefault();
      key(k);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [audioOn, key, resume]);

  const left = secondsLeft(exam, now);
  return (
    <div className="audio-page">
      <header className="topbar">
        <Brand />
        <div className="who">
          <Photo className="avatar" url={photo} name={d.full_name} />
          <div>
            <div>{d.full_name}</div>
            <div className="muted">{d.exam_number}</div>
          </div>
        </div>
        <div className="tools">
          <span className={`online${exam.link === "online" ? "" : " off"}`}>
            {exam.link === "online" ? "Online" : "Reconnecting…"}
          </span>
          <div className="timer" role="timer" aria-label="Time left">
            {left === null ? "--:--" : clock(left)}
          </div>
        </div>
      </header>

      {!audioOn && (
        <div className="resume-bar">
          <strong>Press Space to turn the sound back on.</strong>
        </div>
      )}

      <main className="audio-exam">
        {s.started && q ? (
          <article className="big-question" aria-label={`Question ${q.number} of ${q.of}`}>
            <p className="muted">
              Question {q.number} of {q.of}
              {q.section_title ? ` · ${q.section_title}` : ""}
            </p>
            <p className="big-text">{q.text}</p>
            <ol className="big-options">
              {q.options.map((o) => (
                <li key={o.letter} className={s.current_answer === o.letter ? "chosen" : ""}>
                  <b>{o.letter}</b> {o.text}
                </li>
              ))}
            </ol>
          </article>
        ) : (
          <article className="big-question">
            <p className="muted">{d.exam_title}</p>
            <div className="identity">
              <Photo className="photo" url={photo} name={d.full_name} />
              <dl className="facts">
                <dt>Name</dt>
                <dd>{d.full_name ?? "—"}</dd>
                <dt>Examination number</dt>
                <dd>{d.exam_number ?? "—"}</dd>
              </dl>
            </div>
          </article>
        )}
        <aside className="audio-side">
          <Captions />
          <Listening />
          <p className="muted">
            Answered {s.answered} of {s.of} · Speed {s.speed}× · {s.flagged.length} to revisit
          </p>
        </aside>
      </main>

      <footer className="keys-hint">
        Hold <kbd>Space</kbd> to talk · <kbd>1</kbd>–<kbd>5</kbd> choose · <kbd>Enter</kbd> yes ·{" "}
        <kbd>Esc</kbd> no · <kbd>R</kbd> repeat · <kbd>N</kbd>/<kbd>P</kbd> next and previous ·{" "}
        <kbd>F</kbd> flag · <kbd>T</kbd> time left · <kbd>S</kbd> submit
      </footer>
    </div>
  );
}
