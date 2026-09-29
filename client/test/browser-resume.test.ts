import { describe, expect, it, vi } from "vitest";
import { resumeAudioContext } from "../src/browser";

describe("audio context resume", () => {
  it("retries a suspended context after the dashboard leaves pause", async () => {
    let state = "suspended";
    const resume = vi.fn(async () => {
      if (resume.mock.calls.length < 2) {
        throw new Error("interrupted");
      }
      state = "running";
    });
    await resumeAudioContext({
      get state() {
        return state;
      },
      resume,
    });
    expect(resume).toHaveBeenCalledTimes(2);
    expect(state).toBe("running");
  });
});
