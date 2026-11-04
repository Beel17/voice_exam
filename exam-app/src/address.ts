// Where the exam engine is. Served by the engine, the app uses the engine's own address.
// Bundled inside a secure browser, the app's files are local, so engine-config.js (beside
// index.html, edited before the browser is compiled) names the engine instead.

declare global {
  interface Window {
    EXAM_ENGINE?: string;
    EXAM_CANDIDATE?: string;
  }
}

/** The engine's base address, like "https://exam-server:8765", with no trailing slash. */
export function engineUrl(): string {
  const configured = (window.EXAM_ENGINE ?? "").trim().replace(/\/+$/, "");
  return configured || location.origin;
}

/** An address on the engine: ``path`` starts with "/v1/". */
export function engine(path: string): string {
  return `${engineUrl()}${path}`;
}

/** The engine's WebSocket address for ``path``: wss:// when the engine is https://. */
export function engineSocket(path: string): string {
  return engine(path).replace(/^http/, "ws");
}

let candidateTaken = false;

/** The candidate the browser says is signing in (?candidate= in the address, or
 * window.EXAM_CANDIDATE), the first time it is asked for only: after signing out, or a
 * refusal, the candidate types their number as usual. */
export function takeGivenCandidate(): string | null {
  if (candidateTaken) return null;
  candidateTaken = true;
  const given = (new URLSearchParams(location.search).get("candidate") ?? window.EXAM_CANDIDATE ?? "").trim();
  return /^[A-Za-z0-9._:/-]{1,64}$/.test(given) ? given : null;
}
