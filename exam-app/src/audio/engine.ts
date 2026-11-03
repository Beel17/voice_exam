// Audio mode's sound: the microphone while the talk key is held, the service's speech
// played back without gaps, sound cues, and the tones of the headphone check. Everything
// the candidate hears in words comes from the service; this only records and plays.

import { engine } from "../address";
import { Chunker, DOWN_RATE, downsample, pcm16ToFloat, Playback } from "./pcm";

const CUES = ["listening", "saved", "not_understood", "time_warning"] as const;

export interface EngineIO {
  sendJson(message: object): void;
  sendAudio(frame: Uint8Array): void;
}

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private playback: Playback | null = null;
  private cues = new Map<string, AudioBuffer>();
  private chunker = new Chunker();
  private talking = false;
  private capture: AudioWorkletNode | null = null;
  // After barge-in, the rest of the silenced reply may still be arriving: it is dropped
  // until the service starts a new one.
  private muted = false;
  private cueVolume = 0.75;
  private doneTimers = new Map<string, number>();
  private waiters: (() => void)[] = [];
  microphone: "on" | "none" | "refused" = "none";

  constructor(private readonly io: EngineIO) {}

  private starting: Promise<void> | null = null;

  get started(): boolean {
    return this.ctx !== null;
  }

  /** Must run from a key press or click: browsers only start audio after one. */
  start(base: string): Promise<void> {
    this.starting ??= this.open(base).catch((e: unknown) => {
      this.starting = null;
      throw e;
    });
    return this.starting;
  }

  private async open(base: string): Promise<void> {
    const ctx = new AudioContext({ latencyHint: "interactive" });
    await ctx.resume();
    this.ctx = ctx; // only once it is actually running
    this.playback = new Playback(
      () => ctx.currentTime,
      (samples, at) => {
        const buffer = ctx.createBuffer(1, samples.length, DOWN_RATE);
        buffer.copyToChannel(samples, 0);
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.start(at);
        return {
          duration: buffer.duration,
          cancel: () => {
            try {
              source.stop();
            } catch {
              /* already ended */
            }
          },
        };
      },
    );
    await Promise.all(
      CUES.map(async (name) => {
        const response = await fetch(engine(`/v1/cues/${name}.wav`));
        this.cues.set(name, await ctx.decodeAudioData(await response.arrayBuffer()));
      }),
    ).catch(() => undefined); // cues are a help, not a requirement
    await this.startMicrophone(base);
  }

  private async startMicrophone(base: string): Promise<void> {
    const ctx = this.ctx!;
    if (!navigator.mediaDevices?.getUserMedia) return; // only on secure pages or this PC
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      this.microphone = "refused"; // the candidate can still hear everything and use keys
      return;
    }
    await ctx.audioWorklet.addModule(`${base}capture-worklet.js`);
    const capture = new AudioWorkletNode(ctx, "capture");
    this.capture = capture;
    capture.port.onmessage = ({ data }: MessageEvent<Float32Array>) => {
      if (!this.talking) return;
      for (const frame of this.chunker.push(downsample(data, ctx.sampleRate))) this.io.sendAudio(frame);
    };
    ctx.createMediaStreamSource(stream).connect(capture);
    this.microphone = "on";
  }

  // --- From the service ----------------------------------------------------------------

  /** A ``say`` started: its audio follows. */
  begin(utteranceId: string): void {
    this.muted = false;
    this.playback?.begin(utteranceId);
  }

  audio(data: ArrayBuffer): void {
    if (this.muted || !this.playback) return;
    this.playback.push(pcm16ToFloat(new Uint8Array(data)));
  }

  /** All of a reply's audio has arrived: tell the service once it has been heard. */
  audioEnd(utteranceId: string): void {
    const ctx = this.ctx;
    if (!ctx || !this.playback) return this.settle();
    const wait = Math.max(0, this.playback.end(utteranceId) - ctx.currentTime) * 1000;
    this.doneTimers.set(
      utteranceId,
      window.setTimeout(() => {
        this.doneTimers.delete(utteranceId);
        this.io.sendJson({ type: "playback_done", utterance_id: utteranceId });
        this.settle();
      }, wait),
    );
  }

  /** Cut off what is playing, and drop the rest of it as it arrives. */
  silence(): void {
    this.muted = true;
    this.stopAll();
  }

  stopAll(): void {
    this.playback?.stopAll();
    for (const timer of this.doneTimers.values()) window.clearTimeout(timer);
    this.doneTimers.clear();
    this.settle();
  }

  cue(name: string, volume: number): void {
    this.cueVolume = volume;
    this.playCue(name);
  }

  /** Resolves once the next reply has been played to the end (or cut off). */
  heardNext(): Promise<void> {
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  private settle(): void {
    const waiting = this.waiters;
    this.waiters = [];
    for (const resolve of waiting) resolve();
  }

  // --- Talking --------------------------------------------------------------------------

  /** Talk key down: silence at once (barge-in), then record. */
  startTalking(): boolean {
    if (this.talking || !this.ctx) return false;
    this.talking = true;
    this.capture?.port.postMessage("on");
    this.muted = true;
    this.stopAll();
    this.playCue("listening");
    this.chunker.flush();
    this.io.sendJson({ type: "talk_start" });
    return true;
  }

  stopTalking(): boolean {
    if (!this.talking) return false;
    this.talking = false;
    this.capture?.port.postMessage("off");
    for (const frame of this.chunker.flush()) this.io.sendAudio(frame);
    this.io.sendJson({ type: "talk_end" });
    return true;
  }

  // --- Sounds made here -----------------------------------------------------------------

  private playCue(name: string): void {
    const ctx = this.ctx;
    const buffer = this.cues.get(name);
    if (!ctx || !buffer) return;
    const source = ctx.createBufferSource();
    const gain = ctx.createGain();
    gain.gain.value = this.cueVolume;
    source.buffer = buffer;
    source.connect(gain).connect(ctx.destination);
    source.start();
  }

  /** A short tone in one ear, for the headphone check. Resolves when it has played. */
  tone(side: "left" | "right"): Promise<void> {
    const ctx = this.ctx;
    if (!ctx) return Promise.resolve();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const pan = ctx.createStereoPanner();
    osc.frequency.value = side === "left" ? 523 : 659;
    pan.pan.value = side === "left" ? -1 : 1;
    const t = ctx.currentTime;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(0.3, t + 0.05);
    gain.gain.setValueAtTime(0.3, t + 0.9);
    gain.gain.linearRampToValueAtTime(0, t + 1.0);
    osc.connect(gain).connect(pan).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 1.0);
    return new Promise((resolve) => window.setTimeout(resolve, 1200));
  }

  close(): void {
    this.stopAll();
    void this.ctx?.close();
    this.ctx = null;
    this.playback = null;
    this.starting = null;
  }
}
