// Pure audio helpers for the voice client: no browser APIs, so they run under node --test.

export const UP_RATE = 16000; // microphone audio sent to the service
export const DOWN_RATE = 22050; // speech received from the service
export const FRAME_BYTES = 3200; // 100 ms of 16 kHz 16-bit audio per WebSocket frame

/**
 * Float samples at `inRate` to 16 kHz 16-bit PCM. Each output sample averages the input
 * samples it covers, which also filters out what 16 kHz cannot carry.
 * @param {Float32Array} input
 * @param {number} inRate
 * @returns {Int16Array}
 */
export function downsample(input, inRate) {
  const ratio = inRate / UP_RATE;
  const out = new Int16Array(Math.floor(input.length / ratio));
  for (let k = 0; k < out.length; k++) {
    const start = Math.floor(k * ratio);
    const end = Math.max(start + 1, Math.floor((k + 1) * ratio));
    let sum = 0;
    for (let i = start; i < end; i++) sum += input[i];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    out[k] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return out;
}

/** 16-bit little-endian PCM bytes to float samples, for playback. */
export function pcm16ToFloat(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let k = 0; k < out.length; k++) out[k] = view.getInt16(2 * k, true) / 0x8000;
  return out;
}

/**
 * Collects 16-bit samples and hands out whole 100 ms frames, as the service expects;
 * `flush` hands out what is left when the talk key is released.
 */
export class Chunker {
  constructor() {
    this.pending = new Uint8Array(0);
  }

  /** @param {Int16Array} samples @returns {Uint8Array[]} */
  push(samples) {
    const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
    const joined = new Uint8Array(this.pending.length + bytes.length);
    joined.set(this.pending);
    joined.set(bytes, this.pending.length);
    const frames = [];
    let at = 0;
    while (joined.length - at >= FRAME_BYTES) {
      frames.push(joined.slice(at, at + FRAME_BYTES));
      at += FRAME_BYTES;
    }
    this.pending = joined.slice(at);
    return frames;
  }

  /** @returns {Uint8Array[]} */
  flush() {
    const rest = this.pending;
    this.pending = new Uint8Array(0);
    return rest.length ? [rest] : [];
  }
}

/**
 * Plays utterances back to back with no gaps, and reports each one finished once its last
 * audio has played. Times are in the audio clock's seconds; `schedule` and `cancel` are
 * supplied by the page (Web Audio there, plain functions in tests).
 */
export class Playback {
  constructor(now, schedule) {
    this.now = now; // () => current audio time
    this.schedule = schedule; // (samples, at) => { cancel(), duration }
    this.nextAt = 0;
    this.sources = [];
    this.endsAt = new Map(); // utterance id -> when its audio ends
    this.current = null;
  }

  begin(utteranceId) {
    this.current = utteranceId;
    this.endsAt.set(utteranceId, Math.max(this.nextAt, this.now()));
  }

  /** @param {Float32Array} samples */
  push(samples) {
    const at = Math.max(this.nextAt, this.now() + 0.02); // never schedule in the past
    const source = this.schedule(samples, at);
    this.sources.push(source);
    this.nextAt = at + source.duration;
    if (this.current !== null) this.endsAt.set(this.current, this.nextAt);
  }

  /** When the given utterance will have finished playing, in audio time. */
  end(utteranceId) {
    return this.endsAt.get(utteranceId) ?? this.now();
  }

  /** Barge-in: silence everything now. */
  stopAll() {
    for (const source of this.sources) source.cancel();
    this.sources = [];
    this.nextAt = this.now();
    this.endsAt.clear();
    this.current = null;
  }
}
