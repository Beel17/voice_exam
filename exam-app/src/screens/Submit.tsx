// Submitting, in two steps as the service requires: a summary first, then a final
// warning. Both dialogs follow the service's pending confirmation, so a reload or a
// reconnect mid-way shows the step the service is actually waiting on.

import { useExam } from "../exam";
import { Dialog, Photo, usePhoto } from "../components";
import type { SectionView } from "../protocol";

export function SubmitDialogs({ sections }: { sections: SectionView[] }) {
  const { exam, act } = useExam();
  const s = exam.state!;
  const pending = s.pending_confirm;
  if (pending?.kind !== "submit") return null;

  const answered = new Set(s.answered_questions);
  const unanswered = Array.from({ length: s.of }, (_, k) => k + 1).filter((n) => !answered.has(n));
  const cancel = () => act("cancel");

  if (!pending.final) {
    return (
      <Dialog title="Submit your exam?" onClose={cancel}>
        <div className="summary">
          <div>
            <b>{s.answered}</b>
            Answered
          </div>
          <div>
            <b>{s.unanswered}</b>
            Unanswered
          </div>
          <div>
            <b>{s.flagged.length}</b>
            To revisit
          </div>
        </div>
        {unanswered.length > 0 && (
          <>
            <p className="warning">
              You have not answered {unanswered.length} question{unanswered.length === 1 ? "" : "s"}. Go back to
              one by clicking it below.
            </p>
            {sections.map((section) => {
              const missing = unanswered.filter((n) => section.first_question <= n && n <= section.last_question);
              if (!missing.length) return null;
              return (
                <div key={section.number}>
                  <strong>{section.title}</strong>
                  <div className="jump-list">
                    {missing.map((n) => (
                      <button
                        key={n}
                        className="num"
                        aria-label={`Go to ${section.title} question ${n - section.first_question + 1}`}
                        onClick={() => act("go_to", n)}
                      >
                        {n - section.first_question + 1}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </>
        )}
        <div className="row-between">
          <button className="btn btn-large" onClick={cancel}>
            Go back
          </button>
          <button className="btn btn-primary btn-large" onClick={() => act("confirm")}>
            Continue
          </button>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title="Submitting is final" onClose={cancel}>
      <p>
        Once you submit, you cannot change any answer. {s.unanswered > 0 && <strong>{s.unanswered} unanswered.</strong>}
      </p>
      <div className="row-between">
        <button className="btn btn-large" onClick={cancel}>
          Go back
        </button>
        <button className="btn btn-danger btn-large" onClick={() => act("confirm")}>
          Yes, submit
        </button>
      </div>
    </Dialog>
  );
}

export function Receipt() {
  const { exam, finish } = useExam();
  const photo = usePhoto();
  const d = exam.details;
  const s = exam.state!;
  const timeUp = s.ended === "time_up";
  return (
    <main className="centered">
      <section className="panel" aria-labelledby="receipt-title" style={{ textAlign: "center" }}>
        <div className="tick" aria-hidden="true">
          ✓
        </div>
        <div>
          <h1 id="receipt-title">{timeUp ? "Time is up" : "Exam submitted"}</h1>
          <p className="muted">
            {timeUp
              ? "Your exam has ended and the answers you gave are submitted."
              : "Your answers are submitted. Thank you."}
          </p>
        </div>
        <div className="identity" style={{ textAlign: "left" }}>
          <Photo className="photo" url={photo} name={d?.full_name} />
          <dl className="facts">
            <dt>Name</dt>
            <dd>{d?.full_name ?? "—"}</dd>
            <dt>Examination number</dt>
            <dd>{d?.exam_number ?? "—"}</dd>
            <dt>Exam</dt>
            <dd>{d?.exam_title ?? "—"}</dd>
            <dt>Answered</dt>
            <dd>
              {s.answered} of {s.of}
            </dd>
          </dl>
        </div>
        <p className="muted">Please leave your seat quietly and let the invigilator know you have finished.</p>
        <button className="btn" onClick={finish}>
          Done — ready for the next candidate
        </button>
      </section>
    </main>
  );
}
