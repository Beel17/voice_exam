// One WebSocket to the service for the whole exam. It reconnects by itself and resumes
// the session, so a dropped network or a restarted service costs the candidate nothing:
// every answer is already saved on the service before the screen shows it.

import { engineSocket } from "./address";
import { SCHEMA_VERSION, type ClientMessage, type ServerMessage } from "./protocol";

export type Link = "connecting" | "online" | "offline";

export type Closed = "unauthorized" | "superseded";

interface Handlers {
  message(message: ServerMessage): void;
  audio(data: ArrayBuffer): void;
  link(link: Link): void;
  closed(reason: Closed): void;
}

const CLOSE_UNAUTHORIZED = 4401;
const CLOSE_SUPERSEDED = 4409;

export class Connection {
  private ws: WebSocket | null = null;
  private retry = 0;
  private timer: number | undefined;
  private stopped = false;

  constructor(
    private readonly token: string,
    private readonly deviceId: string,
    private readonly sessionId: () => string | null,
    // Audio mode: the service speaks every reply, and hears the microphone.
    private readonly audio: boolean,
    private readonly on: Handlers,
  ) {}

  open(): void {
    this.stopped = false;
    this.on.link("connecting");
    const ws = new WebSocket(engineSocket("/v1/session"));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      const resume = this.sessionId();
      this.raw({
        type: "hello",
        schema_version: SCHEMA_VERSION,
        device_id: this.deviceId,
        token: this.token,
        audio: this.audio,
        ...(resume ? { resume_session_id: resume } : {}),
      });
      this.on.link("online");
    };
    ws.onmessage = (event) => {
      if (typeof event.data === "string") this.on.message(JSON.parse(event.data) as ServerMessage);
      else this.on.audio(event.data as ArrayBuffer);
    };
    ws.onclose = (event) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.on.link("offline");
      if (event.code === CLOSE_UNAUTHORIZED) return this.on.closed("unauthorized");
      if (event.code === CLOSE_SUPERSEDED) return this.on.closed("superseded");
      if (this.stopped) return;
      // Quickly at first, then every five seconds: the service may be restarting.
      const delay = Math.min(5000, 500 * 2 ** this.retry++);
      this.timer = window.setTimeout(() => this.open(), delay);
    };
  }

  close(): void {
    this.stopped = true;
    window.clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    ws?.close();
  }

  /** Start again on a fresh connection (a new candidate, or after sign-in failed). */
  restart(): void {
    this.close();
    this.open();
  }

  send(message: Exclude<ClientMessage, { type: "hello" }>): boolean {
    return this.raw(message);
  }

  /** Microphone audio, while the talk key is held. */
  sendAudio(frame: Uint8Array): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame);
  }

  private raw(message: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(message));
    return true;
  }
}
