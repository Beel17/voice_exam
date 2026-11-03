// The objective exam screen, as in the design: the candidate and subject at the top, the
// question bar for the current subject, the question on the left and its options on the
// right. Every click goes to the service; what is shown is the state it sends back.

import { useEffect, useMemo, useRef, useState } from "react";
import { Brand, Calculator, clock, Dialog, Photo, useNow, usePhoto } from "../components";
import { secondsLeft, useExam } from "../exam";
import type { OptionLetter, SectionView } from "../protocol";
import { SubmitDialogs } from "./Submit";

const LETTERS: OptionLetter[] = ["A", "B", "C", "D", "E"];
const LOW_TIME_S = 5 * 60;
// Replies the screen already shows another way (the "Auto saved" mark, its own dialogs).
const QUIET =
  /^(Saved\.|Say a letter|Question \d+ of \d+|Question \d+ (is already )?flagged|Flag removed|Question \d+ is not flagged)/;

function sectionOf(sections: SectionView[], n: number): SectionView | null {
  return sections.find((s) => s.first_question <= n && n <= s.last_question) ?? null;
}

export function Exam() {
  const { exam, act } = useExam();
  const photo = usePhoto();
  const now = useNow();
  const [calculator, setCalculator] = useState(false);
  const [viewing, setViewing] = useState(false);
  const [info, setInfo] = useState(false);
  const shownSince = useRef(Date.now());

  const d = exam.details!;
  const s = exam.state!;
  const q = exam.question;
  const answered = useMemo(() => new Set(s.answered_questions), [s.answered_questions]);
  const flagged = useMemo(() => new Set(s.flagged), [s.flagged]);
  const sections: SectionView[] = d.sections.length
    ? d.sections
    : [{ number: 1, title: d.exam_title ?? "Questions", first_question: 1, last_question: d.question_count }];
  const section = sectionOf(sections, s.question) ?? sections[0];
  const inSection = section.last_question - section.first_question + 1;
  const answeredInSection = s.answered_questions.filter(
    (n) => section.first_question <= n && n <= section.last_question,
  ).length;
  const left = secondsLeft(exam, now);
  const current = q && q.number === s.question ? q : null;
  const busy = s.pending_confirm?.kind === "submit";
  const flaggedNow = flagged.has(s.question);

  // Keyboard: A-E answer, arrows move. Not while a dialog or the calculator is open.
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (busy || viewing || info || calculator || e.ctrlKey || e.altKey || e.metaKey) return;
      if ((e.target as HTMLElement).closest("input, textarea")) return;
      const letter = e.key.toUpperCase() as OptionLetter;
      if (current && LETTERS.includes(letter) && LETTERS.indexOf(letter) < current.options.length) {
        act("select", current.number, letter);
      } else if (e.key === "ArrowRight") act("next");
      else if (e.key === "ArrowLeft") act("previous");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [act, busy, viewing, info, calculator, current]);

  const notice =
    exam.notice && exam.notice.at >= shownSince.current && !busy && !QUIET.test(exam.notice.text)
      ? exam.notice
      : null;
  const recentlySaved = exam.savedAt !== null && now - exam.savedAt < 4000;

  return (
    <div className="exam">
      <div className="watermark" aria-hidden="true">
        {Array.from({ length: 24 }, (_, k) => (
          <span key={k}>{d.exam_number}</span>
        ))}
      </div>

      <header className="topbar">
        <Brand />
        <span className="muted">{d.exam_title}</span>
        <span className={`online${exam.link === "online" ? "" : " off"}`}>
          {exam.link === "online" ? "Online" : "Reconnecting…"}
        </span>
      </header>

      <section className="candidate-bar" aria-label="Candidate and subject">
        <div className="who">
          <Photo className="avatar" url={photo} name={d.full_name} />
          <div>
            <div>{d.full_name}</div>
            <div className="muted">{d.exam_number}</div>
          </div>
        </div>
        <div className="subject-card">
          <strong>{section.title}</strong>
          <small>
            {s.question - section.first_question + 1} of {inSection}
            <span className="bar" aria-hidden="true">
              <span style={{ width: `${(100 * answeredInSection) / inSection}%` }} />
            </span>
          </small>
        </div>
        <div className="tools">
          <button className="btn" onClick={() => setCalculator(!calculator)} aria-pressed={calculator}>
            Calculator
          </button>
          <div
            className={`timer${left !== null && left <= LOW_TIME_S ? " low" : ""}`}
            role="timer"
            aria-label="Time left"
          >
            {left === null ? "--:--" : clock(left)}
          </div>
          <button className="btn btn-danger" onClick={() => act("submit")} disabled={busy}>
            Submit Exam
          </button>
        </div>
      </section>

      <nav className="navigator" aria-label="Questions">
        <div className="section-chip">
          Subject {section.number} of {sections.length}
          <small>{inSection} Questions</small>
        </div>
        <button
          className="arrow"
          aria-label="Previous subject"
          disabled={section.number === 1}
          onClick={() => act("go_to", sections[section.number - 2].first_question)}
        >
          ‹
        </button>
        <div className="numbers">
          {Array.from({ length: inSection }, (_, k) => {
            const n = section.first_question + k;
            const status = n === s.question ? "current" : answered.has(n) ? "answered" : "";
            const label = `Question ${n}, ${answered.has(n) ? "answered" : "not answered"}${
              flagged.has(n) ? ", marked to revisit" : ""
            }`;
            return (
              <button
                key={n}
                className={`num ${status}${flagged.has(n) ? " flagged" : ""}`}
                aria-label={label}
                aria-current={n === s.question ? "true" : undefined}
                onClick={() => act("go_to", n)}
              >
                {n - section.first_question + 1}
              </button>
            );
          })}
        </div>
        <button
          className="arrow"
          aria-label="Next subject"
          disabled={section.number === sections.length}
          onClick={() => act("go_to", sections[section.number].first_question)}
        >
          ›
        </button>
        <div className="legend">
          <span>
            <span>
              <i className="dot" style={{ background: "var(--green)" }} />
              Answered
            </span>
            <b>{s.answered}</b>
          </span>
          <span>
            <span>
              <i className="dot" style={{ background: "var(--red)" }} />
              Unanswered
            </span>
            <b>{s.unanswered}</b>
          </span>
          <span>
            <span>
              <i className="dot" style={{ background: "var(--amber)" }} />
              Revisit
            </span>
            <b>{s.flagged.length}</b>
          </span>
        </div>
      </nav>

      <main className="workspace">
        <article className="card" aria-labelledby="question-title">
          <div className="card-head">
            <h2 id="question-title">Question {s.question - section.first_question + 1}</h2>
            <div className="row">
              <button className="btn" onClick={() => setViewing(true)} disabled={!current}>
                View Question ⤢
              </button>
              <button
                className={`btn${flaggedNow ? " btn-on" : ""}`}
                aria-pressed={flaggedNow}
                onClick={() => act(flaggedNow ? "unflag" : "flag")}
              >
                {flaggedNow ? "Marked to revisit" : "Revisit Later"}
              </button>
              <button className="btn" onClick={() => setInfo(true)} aria-label="Instructions">
                ⓘ
              </button>
            </div>
          </div>
          <div className="card-body">
            <p className="question-text">{current ? current.text : "Loading…"}</p>
          </div>
        </article>

        <section className="card" aria-labelledby="answer-title">
          <div className="card-head">
            <h2 id="answer-title">Answer</h2>
            <button
              className="btn btn-outline-green"
              onClick={() => current && act("select", current.number, null)}
              disabled={!current || !s.current_answer}
            >
              ↶ Undo Answer
            </button>
          </div>
          <div className="card-body">
            <ul className="options" role="radiogroup" aria-labelledby="question-title">
              {current?.options.map((o) => {
                const chosen = s.current_answer === o.letter;
                return (
                  <li key={o.letter}>
                    <button
                      className={`option${chosen ? " chosen" : ""}`}
                      role="radio"
                      aria-checked={chosen}
                      onClick={() => act("select", current.number, o.letter)}
                    >
                      <span className="letter">{o.letter}</span>
                      <span>{o.text}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="status">
          <span>
            Attempted {s.answered} of {s.of}
          </span>
          <span className="saved" aria-live="polite">
            {recentlySaved ? "✓ Auto saved" : ""}
          </span>
          {notice && (
            <span className="notice" role="status">
              {notice.text}
            </span>
          )}
        </div>
        <div className="row">
          <button className="btn btn-large" onClick={() => act("previous")} disabled={s.question <= 1}>
            ← Previous
          </button>
          <button className="btn btn-primary btn-large" onClick={() => act("next")} disabled={s.question >= s.of}>
            Next →
          </button>
        </div>
      </footer>

      {calculator && <Calculator onClose={() => setCalculator(false)} />}
      {viewing && current && (
        <Dialog title={`Question ${s.question - section.first_question + 1}`} onClose={() => setViewing(false)} wide>
          <p className="question-text" style={{ maxHeight: "none", fontSize: 19 }}>
            {current.text}
          </p>
          <button className="btn btn-large" onClick={() => setViewing(false)}>
            Close
          </button>
        </Dialog>
      )}
      {info && (
        <Dialog title="Instructions" onClose={() => setInfo(false)}>
          <ul className="rules">
            <li>Click an option to answer. It is saved at once; click another to change it.</li>
            <li>Undo Answer leaves the question unanswered.</li>
            <li>Revisit Later marks a question with a yellow dot so you can find it again.</li>
            <li>Keys: A to E choose an option; the left and right arrows move between questions.</li>
            <li>When the time is up, the exam ends and your answers are submitted.</li>
          </ul>
          <button className="btn btn-large" onClick={() => setInfo(false)}>
            Close
          </button>
        </Dialog>
      )}
      <SubmitDialogs sections={sections} />
    </div>
  );
}
