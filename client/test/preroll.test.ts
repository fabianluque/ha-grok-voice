import { describe, expect, it } from "vitest";
import { PCM_BYTES_PER_SECOND, PcmPreroll, PREROLL_MS } from "../src/audio";

describe("pcm preroll", () => {
  it("keeps only the last 900ms of mic audio", () => {
    const preroll = new PcmPreroll();
    expect(PREROLL_MS).toBe(900);
    const early = new ArrayBuffer(PCM_BYTES_PER_SECOND);
    const keep = new ArrayBuffer(Math.floor((PCM_BYTES_PER_SECOND * PREROLL_MS) / 1000));
    preroll.push(early);
    preroll.push(keep);
    const drained = preroll.drain();
    expect(drained).toEqual([keep]);
    expect(preroll.drain()).toEqual([]);
  });
});
