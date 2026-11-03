import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ExamProvider, useExam } from "./exam";
import { AudioExam, AudioStart } from "./screens/Audio";
import { Guide, Instructions, Login, Setup } from "./screens/Before";
import { Exam } from "./screens/Exam";
import { Receipt } from "./screens/Submit";
import "./styles.css";

function App() {
  const { exam } = useExam();
  const [guide, setGuide] = useState(true);

  // Each new candidate starts with the guide.
  useEffect(() => {
    if (!exam.sessionId) setGuide(true);
  }, [exam.sessionId]);

  if (!exam.token) return <Setup />;
  if (exam.closed === "superseded") {
    return (
      <main className="centered">
        <section className="panel">
          <h1>This exam is open on another screen</h1>
          <p className="muted">Please ask the invigilator. Your answers are safe.</p>
        </section>
      </main>
    );
  }

  const offline = exam.link !== "online" && (
    <div className="offline-bar" role="status">
      Reconnecting to the exam service… Your saved answers are safe.
    </div>
  );
  let screen;
  if (exam.mode === "audio" && !exam.sessionId) {
    screen = <AudioStart />;
  } else if (!exam.sessionId) {
    screen = guide ? <Guide onDone={() => setGuide(false)} /> : <Login onGuide={() => setGuide(true)} />;
  } else if (!exam.state || !exam.details) {
    screen = (
      <main className="centered">
        <p className="muted">Opening your exam…</p>
      </main>
    );
  } else if (exam.state.ended) {
    screen = <Receipt />;
  } else if (exam.mode === "audio") {
    screen = <AudioExam />;
  } else if (exam.state.started) {
    screen = <Exam />;
  } else {
    screen = <Instructions />;
  }
  return (
    <>
      {offline}
      {screen}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ExamProvider>
      <App />
    </ExamProvider>
  </StrictMode>,
);
