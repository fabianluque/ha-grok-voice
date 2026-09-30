import { describe, expect, it, vi } from "vitest";
import { FOLLOWUP_IDLE_GRACE_MS, SessionEndWatch } from "../src/session-end";
import { VoiceSession } from "../src/session";

describe("session end watch", () => {
  it("waits for the ack turn after a closer, then hangs up", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    expect(
      watch.handle({
        type: "transcript",
        role: "user",
        text: "oh, that's great, thank you",
        final: true,
      }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
    watch.handle({ type: "response_started" });
    expect(onEnd).not.toHaveBeenCalled();
    expect(watch.handle({ type: "response_done" })).toBe("done");
    expect(onEnd).toHaveBeenCalledWith("done");
  });

  it("does not hang up on a closer until response_done if Grok is already speaking", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    watch.handle({ type: "response_started" });
    expect(
      watch.handle({
        type: "transcript",
        role: "user",
        text: "thank you",
        final: true,
      }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
    expect(watch.handle({ type: "response_done" })).toBe("done");
    expect(onEnd).toHaveBeenCalledWith("done");
  });

  it("does not end when a closer is only in the middle of a request", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    expect(
      watch.handle({
        type: "transcript",
        role: "user",
        text: "thank you for turning on the lights",
        final: true,
      }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("ends on a completed thank-you after the ack turn", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready", idleTimeoutSeconds: 20 });
    expect(
      watch.handle({ type: "transcript", role: "user", text: "Thank you", final: true }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
    watch.handle({ type: "response_started" });
    expect(watch.handle({ type: "response_done" })).toBe("done");
    expect(onEnd).toHaveBeenCalledWith("done");
  });

  it("hangs up after ack grace if a closer gets no spoken reply", () => {
    vi.useFakeTimers();
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    watch.handle({ type: "transcript", role: "user", text: "thank you", final: true });
    vi.advanceTimersByTime(2_499);
    expect(onEnd).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(onEnd).toHaveBeenCalledWith("done");
    watch.dispose();
    vi.useRealTimers();
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

  it("ignores a closer until the user transcript is marked final", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    expect(
      watch.handle({ type: "transcript", role: "user", text: "thank you" }),
    ).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("does not hang up after a closer if Grok asks a follow-up", () => {
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 60_000, onEnd });
    watch.handle({ type: "ready" });
    watch.handle({ type: "transcript", role: "user", text: "thank you", final: true });
    watch.handle({ type: "response_started" });
    watch.handle({
      type: "transcript",
      role: "assistant",
      text: "You're welcome. Anything else?",
    });
    expect(watch.handle({ type: "response_done" })).toBeNull();
    expect(onEnd).not.toHaveBeenCalled();
    watch.dispose();
  });

  it("gives extra idle after an assistant follow-up, then still ends", () => {
    vi.useFakeTimers();
    const onEnd = vi.fn();
    const watch = new SessionEndWatch({ idleMs: 1_000, onEnd });
    watch.handle({ type: "ready" });
    watch.handle({ type: "response_started" });
    watch.handle({
      type: "transcript",
      role: "assistant",
      text: "The Mets won 4-2. Want last night's highlights?",
    });
    watch.handle({ type: "response_done" });
    vi.advanceTimersByTime(1_000 + FOLLOWUP_IDLE_GRACE_MS - 2);
    expect(onEnd).not.toHaveBeenCalled();
    vi.advanceTimersByTime(4);
    expect(onEnd).toHaveBeenCalledWith("idle");
    watch.dispose();
    vi.useRealTimers();
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

  it("waits for queued assistant audio before closing the socket", async () => {
    let release!: () => void;
    const ended = new Promise<void>((resolve) => {
      release = resolve;
    });
    const close = vi.fn();
    const sent: string[] = [];
    const session = new VoiceSession(
      () => ({ stop() {}, ended }),
      () => ({
        send(data) {
          if (typeof data === "string") {
            sent.push(data);
          }
        },
        close,
      }),
    );
    await session.start();
    session.handleServerText({ type: "response_started" });
    expect(session.handleServerBinary(new ArrayBuffer(2))).toBe(true);
    const endedReason = new Promise<string>((resolve) => session.onEnd(resolve));
    session.finish("done");
    expect(close).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    expect(session.captureActive).toBe(false);
    release();
    await expect(endedReason).resolves.toBe("done");
    expect(close).toHaveBeenCalledOnce();
    expect(sent).toEqual([JSON.stringify({ type: "stop", reason: "done" })]);
  });

  it("does not flush mid-session tool follow-up audio while hanging up waits", async () => {
    const stopped: ArrayBuffer[] = [];
    let release!: () => void;
    const ended = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = new ArrayBuffer(2);
    const followup = new ArrayBuffer(4);
    const session = new VoiceSession(
      (chunk) => ({
        stop: () => stopped.push(chunk),
        ended: chunk === followup ? ended : Promise.resolve(),
      }),
      () => ({ send() {}, close() {} }),
    );
    await session.start();
    session.handleServerText({ type: "response_started" });
    expect(session.handleServerBinary(first)).toBe(true);
    session.handleServerText({ type: "response_started" });
    expect(session.handleServerBinary(followup)).toBe(true);
    expect(stopped).toEqual([]);
    session.finish("done");
    expect(stopped).toEqual([]);
    release();
    await new Promise<string>((resolve) => session.onEnd(resolve));
    expect(stopped).toEqual([]);
  });
});
