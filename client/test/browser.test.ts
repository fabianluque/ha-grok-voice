import { afterEach, describe, expect, it } from "vitest";
import { PCM_BYTES_PER_SECOND, PREROLL_MS } from "../src/audio";
import {
  AUDIO_CONTEXT_CLOSED,
  MIC_GRAPH_ABORTED,
  captureMic,
  createBrowserSession,
  ensureOpenAudioContext,
  isAudioContextOpen,
  openSocket,
  resetAudioContextGate,
  type DuplexSocket,
} from "../src/browser";
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

class FakeAudioContext {
  state = "running";
  sampleRate = 24000;
  destination = {};
  currentTime = 0;
  created: string[] = [];
  connected = 0;

  private node(kind: string) {
    this.created.push(kind);
    if (this.state === "closed") {
      throw new Error(`Construction of ${kind} is not useful when context is closed.`);
    }
    return {
      connect: () => {
        if (this.state === "closed") {
          throw new Error("Connecting nodes after the context has been closed is not useful.");
        }
        this.connected += 1;
      },
      disconnect: () => undefined,
      gain: { value: 0 },
      onaudioprocess: null as ((event: { inputBuffer: { getChannelData(channel: number): Float32Array } }) => void) | null,
    };
  }

  createMediaStreamSource() {
    return this.node("MediaStreamAudioSourceNode");
  }

  createScriptProcessor() {
    return this.node("ScriptProcessorNode");
  }

  createGain() {
    return this.node("GainNode");
  }

  async resume() {
    if (this.state === "suspended") {
      this.state = "running";
    }
  }

  async close() {
    this.state = "closed";
  }
}

function fakeStream(): MediaStream {
  const track = { stop: () => undefined };
  return { getTracks: () => [track] } as unknown as MediaStream;
}

describe("live AudioContext before mic graph", () => {
  afterEach(() => {
    resetAudioContextGate();
  });

  it("resumes a suspended context and replaces a closed one", async () => {
    const suspended = new FakeAudioContext();
    suspended.state = "suspended";
    const resumed = await ensureOpenAudioContext(suspended, () => {
      throw new Error("should resume, not recreate");
    });
    expect(resumed.state).toBe("running");
    expect(isAudioContextOpen(resumed)).toBe(true);

    const closed = new FakeAudioContext();
    closed.state = "closed";
    const fresh = new FakeAudioContext();
    const next = await ensureOpenAudioContext(closed, () => fresh);
    expect(next).toBe(fresh);
    expect(next.created).toEqual([]);
  });

  it("does not construct MediaStream/ScriptProcessor/Gain nodes on a closed context", async () => {
    const closed = new FakeAudioContext();
    closed.state = "closed";
    const replacement = new FakeAudioContext();
    await expect(
      captureMic(closed as unknown as AudioContext, () => undefined, {
        getUserMedia: async () => fakeStream(),
        replaceContext: async () => closed as unknown as AudioContext,
      }),
    ).rejects.toThrow(AUDIO_CONTEXT_CLOSED);
    expect(closed.created).toEqual([]);
    expect(closed.connected).toBe(0);

    const captured = await captureMic(closed as unknown as AudioContext, () => undefined, {
      getUserMedia: async () => fakeStream(),
      replaceContext: async () =>
        ensureOpenAudioContext(closed, () => replacement) as Promise<AudioContext>,
    });
    expect(replacement.created).toEqual(["MediaStreamAudioSourceNode", "ScriptProcessorNode", "GainNode"]);
    expect(replacement.connected).toBe(3);
    captured.stop();
  });

  it("aborts graph attach when the duplex session already ended during getUserMedia", async () => {
    const context = new FakeAudioContext();
    let release!: (stream: MediaStream) => void;
    const pending = new Promise<MediaStream>((resolve) => {
      release = resolve;
    });
    let live = true;
    const pendingCapture = captureMic(context as unknown as AudioContext, () => undefined, {
      isLive: () => live,
      getUserMedia: () => pending,
    });
    live = false;
    release(fakeStream());
    await expect(pendingCapture).rejects.toThrow(MIC_GRAPH_ABORTED);
    expect(context.created).toEqual([]);
    expect(context.connected).toBe(0);
  });

  it("does not build a mic graph on the closed context after Assist cancel kills the first socket", async () => {
    const context = new FakeAudioContext();
    const fake = new FakeSocket();
    let releaseMic!: (stream: MediaStream) => void;
    const pendingMic = new Promise<MediaStream>((resolve) => {
      releaseMic = resolve;
    });
    const pending = createBrowserSession({
      url: "ws://ha.local:8080/",
      token: "token",
      socketFactory: () => fake,
      createAudioContext: () => context as unknown as AudioContext,
      getUserMedia: () => pendingMic,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    fake.emit("close");
    context.state = "closed";
    releaseMic(fakeStream());
    await expect(pending).rejects.toThrow(MIC_GRAPH_ABORTED);
    expect(context.created).toEqual([]);
    expect(context.connected).toBe(0);
  });
});

