// Everything before the first question: setting up the PC (once, by the invigilator),
// the starter guide, signing in, and the instructions with the identity check.

import { useEffect, useState, type FormEvent } from "react";
import { takeGivenCandidate } from "../address";
import { Brand, duration, Photo, usePhoto } from "../components";
import { useExam } from "../exam";

export function Setup() {
  const { exam, setToken } = useExam();
  const [token, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (token.trim().length >= 16) setToken(token.trim());
  };
  return (
    <main className="centered">
      <form className="panel" onSubmit={submit}>
        <Brand />
        <div>
          <h1>Set up this exam PC</h1>
          <p className="muted">
            For the invigilator. Enter the device token issued when the exam service was installed
            (the "Device token" shortcut in the Start menu). It is kept on this PC.
          </p>
        </div>
        {exam.tokenRefused && <p className="error">The service did not accept that token. Please check it.</p>}
        <label className="field">
          Device token
          <input
            value={token}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            autoFocus
          />
        </label>
        <button className="btn btn-primary btn-large" disabled={token.trim().length < 16}>
          Save
        </button>
      </form>
    </main>
  );
}

const GUIDE = [
  {
    art: "🪪",
    title: "Sign in with your examination number",
    text: "Type the examination number printed on your exam slip. Then check that the name and photo on the screen are yours.",
  },
  {
    art: "🖱️",
    title: "Click an option to answer",
    text: "Your answer is saved the moment you click it. Click another option to change it, or Undo Answer to leave the question unanswered.",
  },
  {
    art: "🔢",
    title: "Move around freely",
    text: "Use Previous and Next, or click any number in the question bar. Green numbers are answered, pink are not, and blue is the question you are on.",
  },
  {
    art: "🔖",
    title: "Revisit later",
    text: "Not sure yet? Click Revisit Later to mark the question. Marked questions show a yellow dot, so you can find them before you submit.",
  },
  {
    art: "✅",
    title: "Submit when you are done",
    text: "Click Submit Exam, check the summary, and confirm. When the time is up, your answers are submitted as they are.",
  },
];

export function Guide({ onDone }: { onDone: () => void }) {
  const [page, setPage] = useState(0);
  const slide = GUIDE[page];
  const last = page === GUIDE.length - 1;
  return (
    <main className="centered">
      <section className="panel" aria-labelledby="guide-title">
        <div className="row-between">
          <Brand />
          <button className="btn" onClick={onDone}>
            Skip guide
          </button>
        </div>
        <div className="guide-art" aria-hidden="true">
          {slide.art}
        </div>
        <div>
          <p className="muted">
            How it works · {page + 1} of {GUIDE.length}
          </p>
          <h1 id="guide-title">{slide.title}</h1>
          <p>{slide.text}</p>
        </div>
        <div className="dots" aria-hidden="true">
          {GUIDE.map((_, k) => (
            <span key={k} className={k === page ? "on" : ""} />
          ))}
        </div>
        <div className="row-between">
          <button className="btn btn-large" onClick={() => setPage(page - 1)} disabled={page === 0}>
            Back
          </button>
          <button className="btn btn-primary btn-large" onClick={() => (last ? onDone() : setPage(page + 1))}>
            {last ? "Sign in" : "Next"}
          </button>
        </div>
      </section>
    </main>
  );
}

export function Login({ onGuide }: { onGuide: () => void }) {
  const { exam, login, setMode } = useExam();
  const [number, setNumber] = useState("");
  const value = number.trim().toUpperCase();
  const valid = /^[A-Z0-9._:/-]{1,64}$/.test(value);
  // Signed in by the browser that opened the app, if it named the candidate.
  useEffect(() => {
    if (exam.link !== "online" || exam.signingIn) return;
    const given = takeGivenCandidate();
    if (given) login(given);
  }, [exam.link, exam.signingIn, login]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid && !exam.signingIn) login(value);
  };
  return (
    <main className="centered">
      <form className="panel" onSubmit={submit}>
        <Brand />
        <div>
          <h1>Sign in</h1>
          <p className="muted">Enter your examination number, exactly as it is on your exam slip.</p>
        </div>
        {exam.loginError && (
          <p className="error" role="alert">
            {exam.loginError}
          </p>
        )}
        <label className="field">
          Examination number
          <input
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="characters"
            autoFocus
          />
        </label>
        <button className="btn btn-primary btn-large" disabled={!valid || exam.signingIn || exam.link !== "online"}>
          {exam.signingIn ? "Signing in…" : "Continue"}
        </button>
        <div className="row-between">
          <button type="button" className="btn" onClick={onGuide}>
            How it works
          </button>
          <button type="button" className="btn" onClick={() => setMode("audio")}>
            Audio mode (for candidates who listen)
          </button>
        </div>
      </form>
    </main>
  );
}

const RULES = [
  "Every question has one correct option. Click an option to answer; your answer is saved at once.",
  "You can change any answer until you submit, and move between questions and subjects freely.",
  "The time shown counts down for the whole exam. When it reaches zero, the exam ends and your answers are submitted.",
  "Do not leave this screen or talk to other candidates. Raise your hand if you need the invigilator.",
];

export function Instructions() {
  const { exam, act, finish } = useExam();
  const photo = usePhoto();
  const [confirmed, setConfirmed] = useState(false);
  const d = exam.details;
  if (!d) {
    return (
      <main className="centered">
        <p className="muted">Loading your details…</p>
      </main>
    );
  }
  return (
    <main className="centered">
      <section className="panel panel-wide" aria-labelledby="instructions-title">
        <Brand />
        <div>
          <h1 id="instructions-title">{d.exam_title ?? "Your exam"}</h1>
          <p className="muted">Check your details, read the instructions, then start.</p>
        </div>

        <div className="identity">
          <Photo className="photo" url={photo} name={d.full_name} />
          <dl className="facts">
            <dt>Name</dt>
            <dd>{d.full_name ?? "—"}</dd>
            <dt>Examination number</dt>
            <dd>{d.exam_number ?? "—"}</dd>
            {d.seat && (
              <>
                <dt>Seat</dt>
                <dd>{d.seat}</dd>
              </>
            )}
            <dt>Subjects</dt>
            <dd>
              {d.sections.length
                ? d.sections.map((s) => `${s.title} (${s.last_question - s.first_question + 1})`).join(", ")
                : `${d.question_count} questions`}
            </dd>
            <dt>Time allowed</dt>
            <dd>{duration(d.duration_s)}</dd>
          </dl>
        </div>

        <div>
          <h2>Instructions</h2>
          <ol className="rules">
            {RULES.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ol>
        </div>

        <label className="confirm-line">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>These are my details, and I have read the instructions.</span>
        </label>

        <div className="row-between">
          <button className="btn" onClick={finish}>
            Not me — sign out
          </button>
          <button className="btn btn-primary btn-large" disabled={!confirmed} onClick={() => act("go_to", 1)}>
            Start exam
          </button>
        </div>
      </section>
    </main>
  );
}
