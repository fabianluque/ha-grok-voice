import { describe, expect, it, vi } from "vitest";
import { VoiceSession } from "../src/session";
import { installGrokVoice, type KioskApi } from "../src/wake";

function pcm(size: number): ArrayBuffer {
  return new ArrayBuffer(size);
}

describe("kiosk wake handoff", () => {
  it("does nothing when Kiosk Satellite is missing", () => {
    const addEventListener = vi.fn();
    const installed = installGrokVoice({
      kiosk: undefined,
      addEventListener,
      openSession: async () => {
        throw new Error("should not open");
      },
    });
    expect(installed.installed).toBe(false);
    expect(addEventListener).not.toHaveBeenCalled();
  });

  it("keeps the mic open during playback, flushes barge-in, and re-arms on end", async () => {
    const played: ArrayBuffer[] = [];
    const stopped: ArrayBuffer[] = [];
    const sent: ArrayBuffer[] = [];
    let session!: VoiceSession;
    const pipelineRun = vi.fn();
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      pipelineRun,
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    let wake: (() => Promise<void>) | undefined;
    installGrokVoice({
      kiosk,
      addEventListener: (_type, handler) => {
        wake = handler as () => Promise<void>;
      },
      openSession: async () => {
        session = new VoiceSession(
          (chunk) => {
            played.push(chunk);
            return { stop: () => stopped.push(chunk) };
          },
          () => ({
            send: (data) => {
              if (data instanceof ArrayBuffer) {
                sent.push(data);
              }
            },
            close: () => undefined,
          }),
        );
        return session;
      },
    });

    const running = wake!();
    await Promise.resolve();
    await Promise.resolve();
    expect(session.captureActive).toBe(true);

    const first = pcm(2);
    const cancelled = pcm(4);
    const next = pcm(6);
    session.handleServerText({ type: "response_started" });
    expect(session.handleServerBinary(first)).toBe(true);
    session.sendMic(pcm(8));
    expect(session.captureActive).toBe(true);
    expect(sent).toHaveLength(1);

    session.handleServerText({ type: "speech_started" });
    expect(stopped).toEqual([first]);
    expect(session.handleServerBinary(cancelled)).toBe(false);
    expect(played).toEqual([first]);

    session.handleServerText({ type: "response_started" });
    expect(session.handleServerBinary(next)).toBe(true);
    expect(played).toEqual([first, next]);

    session.finish("idle");
    await running;

    expect(pipelineRun).not.toHaveBeenCalled();
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenNthCalledWith(1, true, "voice");
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });
});
