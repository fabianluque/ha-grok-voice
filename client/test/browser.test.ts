import { describe, expect, it } from "vitest";
import { PCM_BYTES_PER_SECOND, PREROLL_MS } from "../src/audio";
import { openSocket, type DuplexSocket } from "../src/browser";
import { VoiceSession } from "../src/session";

class FakeSocket implements DuplexSocket {
  binaryType = "arraybuffer";
  sent: Array<string | ArrayBuffer> = [];
  private readonly listeners = new Map<string, Array<(event: { data?: unknown }) => void>>();

  addEventListener(type: "open" | "message" | "close", handler: (event: { data?: unknown }) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(handler);
    this.listeners.set(type, list);
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }

  close(): void {}

  emit(type: "open" | "message" | "close", data?: unknown): void {
    for (const handler of this.listeners.get(type) ?? []) {
      handler({ data });
    }
  }
}

describe("duplex preroll flush", () => {
  it("holds early mic PCM until ready, then flushes the 900ms buffer", () => {
    const fake = new FakeSocket();
    const session = new VoiceSession(
      () => ({ stop() {} }),
      () => ({ send() {}, close() {} }),
    );
    const socket = openSocket("ws://ha.local:8080/", "token", session, false, undefined, undefined, () => fake);
    const early = new ArrayBuffer(PCM_BYTES_PER_SECOND);
    const keep = new ArrayBuffer(Math.floor((PCM_BYTES_PER_SECOND * PREROLL_MS) / 1000));
    socket.send(early);
    socket.send(keep);
    fake.emit("open");
    expect(fake.sent).toHaveLength(1);
    expect(JSON.parse(String(fake.sent[0])).type).toBe("auth");
    fake.emit("message", JSON.stringify({ type: "ready", sampleRate: 24000, idleTimeoutSeconds: 20 }));
    expect(fake.sent).toHaveLength(2);
    expect(fake.sent[1]).toBe(keep);
  });
});
