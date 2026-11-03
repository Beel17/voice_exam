// Audio conversions and playback scheduling, as in the voice client (client/audio.js,
// tested there under node --test): no browser APIs here.

export const UP_RATE = 16000; // microphone audio sent to the service
export const DOWN_RATE = 22050; // speech received from the service
export const FRAME_BYTES = 3200; // 100 ms of 16 kHz 16-bit audio per WebSocket frame

/** Float samples at `inRate` to 16 kHz 16-bit PCM, averaging the samples each covers. */
export function downsample(input: Float32Array, inRate: number): Int16Array {
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
export function pcm16ToFloat(bytes: Uint8Array): Float32Array {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let k = 0; k < out.length; k++) out[k] = view.getInt16(2 * k, true) / 0x8000;
  return out;
}

/** Collects 16-bit samples and hands out whole 100 ms frames, as the service expects. */
export class Chunker {
  private pending = new Uint8Array(0);

  push(samples: Int16Array): Uint8Array[] {
    const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
    const joined = new Uint8Array(this.pending.length + bytes.length);
    joined.set(this.pending);
    joined.set(bytes, this.pending.length);
    const frames: Uint8Array[] = [];
    let at = 0;
    while (joined.length - at >= FRAME_BYTES) {
      frames.push(joined.slice(at, at + FRAME_BYTES));
      at += FRAME_BYTES;
    }
    this.pending = joined.slice(at);
    return frames;
  }

  flush(): Uint8Array[] {
    const rest = this.pending;
    this.pending = new Uint8Array(0);
    return rest.length ? [rest] : [];
  }
}

export interface Scheduled {
  duration: number;
  cancel(): void;
}

/** Plays utterances back to back with no gaps, and knows when each one ends. */
export class Playback {
  private nextAt = 0;
  private sources: Scheduled[] = [];
  private endsAt = new Map<string, number>();
  private current: string | null = null;

  constructor(
    private readonly now: () => number,
    private readonly schedule: (samples: Float32Array, at: number) => Scheduled,
  ) {}

  begin(utteranceId: string): void {
    this.current = utteranceId;
    this.endsAt.set(utteranceId, Math.max(this.nextAt, this.now()));
  }

  push(samples: Float32Array): void {
    const at = Math.max(this.nextAt, this.now() + 0.02); // never schedule in the past
    const source = this.schedule(samples, at);
    this.sources.push(source);
    this.nextAt = at + source.duration;
    if (this.current !== null) this.endsAt.set(this.current, this.nextAt);
  }

  /** When the given utterance will have finished playing, in audio time. */
  end(utteranceId: string): number {
    return this.endsAt.get(utteranceId) ?? this.now();
  }

  /** Barge-in: silence everything now. */
  stopAll(): void {
    for (const source of this.sources) source.cancel();
    this.sources = [];
    this.nextAt = this.now();
    this.endsAt.clear();
    this.current = null;
  }
}
