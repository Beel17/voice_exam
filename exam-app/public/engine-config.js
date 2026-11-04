// Where the exam engine is. Leave it empty when the engine serves this app (the usual way):
// the app then uses the engine's own address.
//
// When the app's files are bundled inside a secure browser instead, put the engine's
// address here before compiling the browser, for example:
//   window.EXAM_ENGINE = "https://exam-server:8765";
// and allow the browser's origin in the engine's centre.toml (allowed_origins).
window.EXAM_ENGINE = "";

// The candidate, when the browser already knows who signs in (their candidate ID or
// examination number): they are signed in without typing. Or open index.html?candidate=ID.
//   window.EXAM_CANDIDATE = "...";
