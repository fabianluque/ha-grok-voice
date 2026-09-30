import { describe, expect, it, vi } from "vitest";
import { VoiceSession } from "../src/session";

describe("voice session socket", () => {
  it("opens the duplex socket once even if start is called twice", async () => {
    const close = vi.fn();
    const factory = vi.fn(() => ({ send() {}, close }));
    const session = new VoiceSession(
      () => ({ stop() {} }),
      factory,
    );
    await session.start();
    await session.start();
    expect(factory).toHaveBeenCalledOnce();
    expect(session.captureActive).toBe(true);
    session.finish("closed");
    expect(session.isEnded).toBe(true);
    await session.start();
    expect(session.captureActive).toBe(false);
    expect(factory).toHaveBeenCalledOnce();
  });

  it("replays an end that happened before onEnd was registered", async () => {
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({ send() {}, close() {} }),
    );
    await session.start();
    session.finish("closed");
    const ended = await new Promise<string>((resolve) => {
      session.onEnd(resolve);
    });
    expect(ended).toBe("closed");
    expect(session.isEnded).toBe(true);
  });

  it("closes immediately on dashboard unload without waiting for ack TTS", async () => {
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
    session.handleServerBinary(new ArrayBuffer(2));
    const reason = new Promise<string>((resolve) => session.onEnd(resolve));
    session.finish("unload");
    await expect(reason).resolves.toBe("unload");
    expect(close).toHaveBeenCalledOnce();
    expect(sent).toEqual([JSON.stringify({ type: "stop", reason: "unload" })]);
    release();
  });

  it("notifies every onEnd handler after playback drains", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({ send() {}, close() {} }),
    );
    session.onEnd(first);
    await session.start();
    session.finish("done");
    session.onEnd(second);
    expect(first).toHaveBeenCalledWith("done");
    expect(second).toHaveBeenCalledWith("done");
  });

  it("ignores a socket close while an ack is still draining", async () => {
    let release!: () => void;
    const ended = new Promise<void>((resolve) => {
      release = resolve;
    });
    const close = vi.fn();
    const session = new VoiceSession(
      () => ({ stop() {}, ended }),
      () => ({ send() {}, close }),
    );
    await session.start();
    session.handleServerText({ type: "response_started" });
    session.handleServerBinary(new ArrayBuffer(2));
    const reason = new Promise<string>((resolve) => session.onEnd(resolve));
    session.finish("done");
    session.finish("closed");
    expect(close).not.toHaveBeenCalled();
    release();
    await expect(reason).resolves.toBe("done");
    expect(close).toHaveBeenCalledOnce();
  });
});
