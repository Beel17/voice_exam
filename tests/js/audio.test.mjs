// Run with: node --test tests/js
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  Chunker,
  downsample,
  FRAME_BYTES,
  Playback,
  pcm16ToFloat,
  UP_RATE,
} from "../../src/voice_agent/client/audio.js";

test("downsampling 48 kHz to 16 kHz keeps the length and the tone", () => {
  const seconds = 0.5;
  const input = new Float32Array(48000 * seconds);
  for (let i = 0; i < input.length; i++) input[i] = 0.5 * Math.sin((2 * Math.PI * 440 * i) / 48000);
  const out = downsample(input, 48000);
  assert.equal(out.length, UP_RATE * seconds);
  // Count zero crossings: a 440 Hz tone crosses zero 880 times a second.
  let crossings = 0;
  for (let i = 1; i < out.length; i++) if (out[i - 1] < 0 !== out[i] < 0) crossings++;
  assert.ok(Math.abs(crossings - 880 * seconds) <= 2, `crossings ${crossings}`);
  assert.ok(Math.max(...out) > 0.45 * 0x7fff);
});

test("downsampling clips instead of wrapping around", () => {
  const out = downsample(new Float32Array([2, 2, 2, -2, -2, -2]), 48000);
  assert.deepEqual(Array.from(out), [0x7fff, -0x8000]);
});

test("44.1 kHz input (common on Windows) gives the right length", () => {
  assert.equal(downsample(new Float32Array(44100), 44100).length, 16000);
});

test("chunks are whole 100 ms frames, with the rest on release", () => {
  const chunker = new Chunker();
  assert.equal(chunker.push(new Int16Array(1000)).length, 0); // 2000 bytes: not yet a frame
  const frames = chunker.push(new Int16Array(2000)); // 6000 bytes in all
  assert.deepEqual(frames.map((f) => f.length), [FRAME_BYTES]);
  assert.deepEqual(chunker.flush().map((f) => f.length), [6000 - FRAME_BYTES]);
  assert.deepEqual(chunker.flush(), []);
});

test("16-bit PCM back to float", () => {
  const bytes = new Uint8Array(new Int16Array([0, 0x4000, -0x8000]).buffer);
  assert.deepEqual(Array.from(pcm16ToFloat(bytes)), [0, 0.5, -1]);
});

function fakeClock() {
  const scheduled = [];
  let time = 10;
  const playback = new Playback(
    () => time,
    (samples, at) => {
      const source = { at, duration: samples.length / 1000, cancelled: false };
      source.cancel = () => (source.cancelled = true);
      scheduled.push(source);
      return source;
    },
  );
  return { playback, scheduled, advance: (s) => (time += s) };
}

test("speech plays back to back and each reply knows when it ends", () => {
  const { playback, scheduled } = fakeClock();
  playback.begin("u1");
  playback.push(new Float32Array(500)); // 0.5 s
  playback.push(new Float32Array(500));
  playback.begin("u2");
  playback.push(new Float32Array(1000));
  assert.equal(scheduled[1].at, scheduled[0].at + 0.5); // no gap
  assert.equal(scheduled[2].at, scheduled[1].at + 0.5);
  assert.equal(playback.end("u1"), scheduled[2].at);
  assert.equal(playback.end("u2"), scheduled[2].at + 1);
});

test("barge-in silences everything and starts fresh", () => {
  const { playback, scheduled, advance } = fakeClock();
  playback.begin("u1");
  playback.push(new Float32Array(2000));
  advance(0.3);
  playback.stopAll();
  assert.ok(scheduled.every((s) => s.cancelled));
  playback.begin("u2");
  playback.push(new Float32Array(100));
  assert.ok(Math.abs(scheduled.at(-1).at - 10.32) < 1e-9); // now, not after the old audio
});

test("audio arriving late is never scheduled in the past", () => {
  const { playback, scheduled, advance } = fakeClock();
  playback.begin("u1");
  playback.push(new Float32Array(100)); // 0.1 s
  advance(5); // the network stalled
  playback.push(new Float32Array(100));
  assert.ok(scheduled[1].at >= 15);
});
