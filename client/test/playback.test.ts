import { describe, expect, it } from "vitest";
import { PlaybackQueue } from "../src/playback";

function pcm(size: number): ArrayBuffer {
  return new ArrayBuffer(size);
}

describe("playback queue", () => {
  it("does not cut a playing reply when a later response starts", () => {
    const stopped: ArrayBuffer[] = [];
    const played: ArrayBuffer[] = [];
    const queue = new PlaybackQueue();
    const play = (chunk: ArrayBuffer) => {
      played.push(chunk);
      return { stop: () => stopped.push(chunk) };
    };
    const first = pcm(2);
    const followup = pcm(4);
    queue.responseStarted();
    expect(queue.enqueue(first, play)).toBe(true);
    queue.responseStarted();
    expect(queue.enqueue(followup, play)).toBe(true);
    expect(played).toEqual([first, followup]);
    expect(stopped).toEqual([]);
  });

  it("flushes queued audio only when the user barges in", () => {
    const stopped: ArrayBuffer[] = [];
    const queue = new PlaybackQueue();
    const play = (chunk: ArrayBuffer) => ({ stop: () => stopped.push(chunk) });
    const first = pcm(2);
    queue.responseStarted();
    expect(queue.enqueue(first, play)).toBe(true);
    queue.speechStarted();
    expect(stopped).toEqual([first]);
    expect(queue.enqueue(pcm(8), play)).toBe(false);
    queue.responseStarted();
    expect(queue.enqueue(pcm(6), play)).toBe(true);
  });
});
