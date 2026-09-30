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
  });
});
