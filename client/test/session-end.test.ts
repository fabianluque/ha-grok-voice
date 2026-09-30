import { describe, expect, it, vi } from "vitest";
import { SessionEndWatch } from "../src/session-end";
import { VoiceSession } from "../src/session";

describe("session end watch", () => {
  it("ends on a completed thank-you transcript", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready", idleTimeoutSeconds: 20 });
    expect(
      watch.handle({ type: "transcript", role: "user", text: "Thank you", final: true }),
    ).toBe("done");
    expect(onEnd).toHaveBeenCalledWith("done");
  });

  it("ignores streaming thank-you that is not final", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    expect(
      watch.handle({ type: "transcript", role: "user", text: "thank you", final: false }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("idles only after the assistant is done and the user is not speaking", () => {
    vi.useFakeTimers();
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 1_000, onEnd });
    watch.handle({ type: "ready" });
    watch.handle({ type: "speech_started" });
    vi.advanceTimersByTime(1_500);
    expect(onEnd).not.toHaveBeenCalled();
    watch.handle({ type: "speech_stopped" });
    watch.handle({ type: "response_started" });
    vi.advanceTimersByTime(1_500);
    expect(onEnd).not.toHaveBeenCalled();
    watch.handle({ type: "response_done" });
    vi.advanceTimersByTime(999);
    expect(onEnd).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(onEnd).toHaveBeenCalledWith("idle");
    watch.dispose();
    vi.useRealTimers();
  });
});

describe("voice session hang-up", () => {
  it("sends stop with the hang-up reason then closes", () => {
    const sent: string[] = [];
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({
        send(data) {
          if (typeof data === "string") {
            sent.push(data);
          }
        },
        close() {},
      }),
    );
    void session.start();
    session.finish("done");
    expect(sent).toEqual([JSON.stringify({ type: "stop", reason: "done" })]);
    expect(session.captureActive).toBe(false);
  });
});
