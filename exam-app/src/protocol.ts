// The WebSocket messages, as src/voice_agent/contract.py defines them. Only what the
// exam screen uses is typed; the service validates everything it receives.

export const SCHEMA_VERSION = 1;

export type OptionLetter = "A" | "B" | "C" | "D" | "E";

// --- To the service ----------------------------------------------------------------------

export type ScreenAction =
  | "select"
  | "go_to"
  | "next"
  | "previous"
  | "flag"
  | "unflag"
  | "submit"
  | "confirm"
  | "cancel";

export type Key = "1" | "2" | "3" | "4" | "5" | "R" | "N" | "P" | "S" | "F" | "T" | "Enter" | "Esc";

export type GuideStep = "welcome" | "left" | "right" | "sound_check" | "mic_check" | "sign_in";

export type ClientMessage =
  | {
      type: "hello";
      schema_version: number;
      device_id: string;
      token: string;
      resume_session_id?: string;
      audio: boolean;
    }
  | { type: "login"; reg_no: string }
  | { type: "act"; action: ScreenAction; question?: number; choice?: OptionLetter | null }
  | { type: "key"; key: Key }
  | { type: "guide"; step: GuideStep }
  | { type: "talk_start" }
  | { type: "talk_end" }
  | { type: "playback_done"; utterance_id: string }
  | { type: "ping" };

// --- From the service --------------------------------------------------------------------

export interface SectionView {
  number: number;
  title: string;
  first_question: number;
  last_question: number;
}

export interface Details {
  type: "details";
  question_count: number;
  duration_s: number;
  sections: SectionView[];
  exam_title: string | null;
  exam_number: string | null;
  full_name: string | null;
  seat: string | null;
  audio_mode: boolean | null;
  photo: boolean;
}

export interface QuestionView {
  type: "question";
  number: number;
  of: number;
  section: number | null;
  section_title: string | null;
  text: string;
  options: { letter: OptionLetter; text: string }[];
}

export interface PendingConfirm {
  kind: "answer" | "clear" | "submit";
  question: number | null;
  choice: OptionLetter | null;
  final: boolean;
}

export interface State {
  type: "state";
  question: number;
  of: number;
  answered: number;
  unanswered: number;
  time_left_s: number | null;
  pending_confirm: PendingConfirm | null;
  speed: number;
  cue_volume: number;
  started: boolean;
  current_answer: OptionLetter | null;
  answered_questions: number[];
  flagged: number[];
  ended: "submitted" | "time_up" | null;
}

export type ServerMessage =
  | { type: "session"; session_id: string; resumed: boolean }
  | Details
  | QuestionView
  | { type: "say"; utterance_id: string; text: string; kind: "question" | "options" | "prompt" | "confirm" }
  | { type: "cue"; name: string; volume: number }
  | State
  | { type: "error"; code: string; message: string }
  | { type: "pong" }
  | { type: "heard"; text: string; confidence: number; seq: number }
  | { type: "stop" | "audio_end"; utterance_id: string };
