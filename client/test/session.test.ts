import { describe, expect, it, vi } from "vitest";
import { MIC_HOLD_MAX_BYTES, MicHold } from "../src/mic-hold";
import { VoiceSession } from "../src/session";

function pcm(label: number, size = 4): ArrayBuffer {
  const bytes = new Uint8Array(size);
  bytes.fill(label);
  return bytes.buffer;
}

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

describe("mic hold", () => {
  it("drops the oldest frames once the cap is reached", () => {
    const hold = new MicHold(8);
    hold.push(pcm(1, 4));
    hold.push(pcm(2, 4));
    hold.push(pcm(3, 4));
    const drained = hold.drain().map((chunk) => new Uint8Array(chunk)[0]);
    expect(drained).toEqual([2, 3]);
    expect(hold.size).toBe(0);
    expect(MIC_HOLD_MAX_BYTES).toBe(48 * 1500);
  });
});

describe("voice session pre-roll", () => {
  it("holds mic PCM until the add-on sends ready, then flushes in order", async () => {
    const sent: Array<ArrayBuffer | string> = [];
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({
        send(data) {
          sent.push(data);
        },
        close() {},
      }),
    );
    session.armCapture();
    session.sendMic(pcm(1));
    session.sendMic(pcm(2));
    await session.start();
    session.sendMic(pcm(3));
    expect(sent).toEqual([]);
    session.handleServerText({ type: "ready" });
    expect(sent).toHaveLength(3);
    expect(new Uint8Array(sent[0] as ArrayBuffer)[0]).toBe(1);
    expect(new Uint8Array(sent[1] as ArrayBuffer)[0]).toBe(2);
    expect(new Uint8Array(sent[2] as ArrayBuffer)[0]).toBe(3);
    session.sendMic(pcm(4));
    expect(sent).toHaveLength(4);
    expect(new Uint8Array(sent[3] as ArrayBuffer)[0]).toBe(4);
  });

  it("imports a wake hold before the socket exists", async () => {
    const sent: ArrayBuffer[] = [];
    const hold = new MicHold();
    hold.push(pcm(9));
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({
        send(data) {
          if (data instanceof ArrayBuffer) {
            sent.push(data);
          }
        },
        close() {},
      }),
    );
    session.armCapture();
    session.importHold(hold);
    await session.start();
    session.handleServerText({ type: "ready" });
    expect(new Uint8Array(sent[0])[0]).toBe(9);
  });

  it("does not send held audio after finish", async () => {
    const send = vi.fn();
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({ send, close() {} }),
    );
    session.armCapture();
    await session.start();
    session.sendMic(pcm(1));
    session.finish("stop");
    session.handleServerText({ type: "ready" });
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: "stop", reason: "stop" }));
    expect(send.mock.calls.some((call) => call[0] instanceof ArrayBuffer)).toBe(false);
  });
});
