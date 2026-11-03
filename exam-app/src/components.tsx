import { useEffect, useRef, useState, type ReactNode } from "react";
import { fetchPhoto, useExam } from "./exam";

export function Brand() {
  return (
    <div className="brand">
      <strong>ExamSolution</strong>
      <span>Candidate</span>
    </div>
  );
}

/** The candidate's photo once fetched, or null (none held, or not yet loaded). */
export function usePhoto(): string | null {
  const { exam } = useExam();
  const [url, setUrl] = useState<string | null>(null);
  const wanted = exam.details?.photo && exam.token && exam.sessionId;
  useEffect(() => {
    if (!wanted) return;
    let alive = true;
    void fetchPhoto(exam.token!, exam.sessionId!).then((u) => alive && setUrl(u));
    return () => {
      alive = false;
      setUrl(null);
    };
  }, [wanted, exam.token, exam.sessionId]);
  return url;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function Photo({ className, url, name }: { className: string; url: string | null; name: string | null | undefined }) {
  if (url) return <img className={className} src={url} alt={`Photo of ${name ?? "the candidate"}`} />;
  return (
    <div className={className} role="img" aria-label="No photo held">
      {initials(name)}
    </div>
  );
}

/** The current time, ticking once a second. */
export function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function clock(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  const parts = [];
  if (h) parts.push(`${h} hour${h === 1 ? "" : "s"}`);
  if (m) parts.push(`${m} minute${m === 1 ? "" : "s"}`);
  return parts.join(" ") || "less than a minute";
}

export function Dialog({
  title,
  children,
  onClose,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose?: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    if (!onClose) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="scrim">
      <div
        className={`dialog${wide ? " dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <div className="row-between">
          <h2>{title}</h2>
          {onClose && (
            <button className="btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

// --- Calculator --------------------------------------------------------------------------
// Four functions, key by key, like a desk calculator. Nothing typed is ever evaluated as
// code.

type Op = "+" | "−" | "×" | "÷";

function apply(a: number, op: Op, b: number): number {
  switch (op) {
    case "+":
      return a + b;
    case "−":
      return a - b;
    case "×":
      return a * b;
    case "÷":
      return b === 0 ? NaN : a / b;
  }
}

function show(n: number): string {
  if (!Number.isFinite(n)) return "Error";
  return String(Number(n.toPrecision(12)));
}

export function Calculator({ onClose }: { onClose: () => void }) {
  const [display, setDisplay] = useState("0");
  const [held, setHeld] = useState<{ value: number; op: Op } | null>(null);
  const [fresh, setFresh] = useState(true);

  const digit = (d: string) => {
    if (display === "Error" || fresh) {
      setDisplay(d === "." ? "0." : d);
      setFresh(false);
      return;
    }
    if (d === "." && display.includes(".")) return;
    if (display.length >= 14) return;
    setDisplay(display === "0" && d !== "." ? d : display + d);
  };
  const operate = (op: Op) => {
    const value = Number(display);
    const result = held && !fresh ? apply(held.value, held.op, value) : value;
    setDisplay(show(result));
    setHeld(Number.isFinite(result) ? { value: result, op } : null);
    setFresh(true);
  };
  const equals = () => {
    if (!held) return;
    setDisplay(show(apply(held.value, held.op, Number(display))));
    setHeld(null);
    setFresh(true);
  };
  const clear = () => {
    setDisplay("0");
    setHeld(null);
    setFresh(true);
  };
  const keys: [string, () => void, string?][] = [
    ["C", clear, "op"], ["±", () => setDisplay(show(-Number(display))), "op"],
    ["%", () => setDisplay(show(Number(display) / 100)), "op"], ["÷", () => operate("÷"), "op"],
    ["7", () => digit("7")], ["8", () => digit("8")], ["9", () => digit("9")], ["×", () => operate("×"), "op"],
    ["4", () => digit("4")], ["5", () => digit("5")], ["6", () => digit("6")], ["−", () => operate("−"), "op"],
    ["1", () => digit("1")], ["2", () => digit("2")], ["3", () => digit("3")], ["+", () => operate("+"), "op"],
    ["0", () => digit("0")], [".", () => digit(".")], ["√", () => setDisplay(show(Math.sqrt(Number(display)))), "op"],
    ["=", equals, "eq"],
  ];
  return (
    <div className="calculator" role="dialog" aria-label="Calculator">
      <div className="row-between">
        <strong>Calculator</strong>
        <button className="btn" onClick={onClose} aria-label="Close calculator">
          ✕
        </button>
      </div>
      <div className="calc-display" aria-live="polite">
        {display}
      </div>
      <div className="calc-keys">
        {keys.map(([label, run, kind]) => (
          <button key={label} className={kind} onClick={run}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
