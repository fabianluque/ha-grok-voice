import { afterEach, describe, expect, it, vi } from "vitest";
import type { NativeAssistHass } from "../src/native-assist";
import { VoiceSession } from "../src/session";
import { installGrokVoice, restoreWakeClaim, WAKE_EVENT, type KioskApi } from "../src/wake";

function pcm(size: number): ArrayBuffer {
  return new ArrayBuffer(size);
}

function wakeTarget(): EventTarget {
  return new EventTarget();
}

async function flush(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve();
  }
}

describe("kiosk wake handoff", () => {
  afterEach(() => {
    restoreWakeClaim();
  });

  it("does nothing when Kiosk Satellite is missing", () => {
    const events = wakeTarget();
    const assist = vi.fn();
    events.addEventListener(WAKE_EVENT, assist);
    const installed = installGrokVoice({
      kiosk: undefined,
      events,
      openSession: async () => {
        throw new Error("should not open");
      },
    });
    expect(installed.installed).toBe(false);
    events.dispatchEvent(new Event(WAKE_EVENT));
    expect(assist).toHaveBeenCalledOnce();
  });

  it("replaces Assist's wake listener and ignores a later Assist bind", async () => {
    const events = wakeTarget();
    const pipelineRun = vi.fn();
    const assist = vi.fn(() => {
      pipelineRun({ start_stage: "stt" });
    });
    events.addEventListener(WAKE_EVENT, assist);
    let session!: VoiceSession;
    const openSession = vi.fn(async () => {
      session = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      return session;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      pipelineRun,
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };

    expect(installGrokVoice({ kiosk, events, openSession }).installed).toBe(true);
    events.addEventListener(WAKE_EVENT, assist);

    events.dispatchEvent(new CustomEvent(WAKE_EVENT, { detail: { phrase: "hey jarvis" } }));
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();

    expect(assist).not.toHaveBeenCalled();
    expect(pipelineRun).not.toHaveBeenCalled();
    expect(openSession).toHaveBeenCalledOnce();
    expect(session.captureActive).toBe(true);
    session.finish("idle");
    await flush();
    expect(kiosk.setInteractionActive).toHaveBeenNthCalledWith(1, true, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });

  it("blocks Assist even when the wake event is delivered through EventTarget.prototype", async () => {
    const events = wakeTarget();
    const pipelineRun = vi.fn();
    const pipelineStart = vi.fn(async () => "started");
    const showBlurOverlay = vi.fn();
    const assist = vi.fn(() => {
      pipelineRun({ start_stage: "stt" });
    });
    EventTarget.prototype.addEventListener.call(events, WAKE_EVENT, assist);
    const host = {
      __vsSession: {
        onWakeAction(opts: Record<string, unknown>) {
          showBlurOverlay("pipeline");
          return pipelineStart(opts);
        },
        pipeline: { start: pipelineStart, stop: vi.fn() },
        ui: { showBlurOverlay, hideBlurOverlay: vi.fn(), hideBar: vi.fn() },
      },
    };
    let session!: VoiceSession;
    const openSession = vi.fn(async () => {
      session = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      return session;
    });
    installGrokVoice({
      kiosk: {
        platform: "kiosksatellite",
        pipelineRun,
        setInteractionActive: vi.fn(async () => true),
        setWakeWordActive: vi.fn(async () => true),
      },
      events,
      host,
      openSession,
    });

    EventTarget.prototype.dispatchEvent.call(events, new Event(WAKE_EVENT));
    host.__vsSession.onWakeAction?.({ detected: true, wake_word_phrase: "hey jarvis" });
    host.__vsSession.ui?.showBlurOverlay?.("pipeline");
    await host.__vsSession.pipeline?.start?.({ start_stage: "stt", wake_word_phrase: "hey jarvis" });
    await flush();

    expect(assist).not.toHaveBeenCalled();
    expect(pipelineRun).not.toHaveBeenCalled();
    expect(showBlurOverlay).not.toHaveBeenCalled();
    expect(pipelineStart).not.toHaveBeenCalled();
    expect(openSession).toHaveBeenCalledOnce();
    session.finish("idle");
    await flush();
  });

  it("still delivers other Kiosk Satellite events", () => {
    const events = wakeTarget();
    const motion = vi.fn();
    events.addEventListener("kiosksatellite:motion", motion);
    installGrokVoice({
      kiosk: {
        platform: "kiosksatellite",
        setInteractionActive: vi.fn(async () => true),
        setWakeWordActive: vi.fn(async () => true),
      },
      events,
      openSession: async () => {
        throw new Error("should not open");
      },
    });
    const lateMotion = vi.fn();
    events.addEventListener("kiosksatellite:motion", lateMotion);
    events.dispatchEvent(new Event("kiosksatellite:motion"));
    expect(motion).toHaveBeenCalledOnce();
    expect(lateMotion).toHaveBeenCalledOnce();
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
    const events = wakeTarget();
    installGrokVoice({
      kiosk,
      events,
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

    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
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
    await flush();

    expect(pipelineRun).not.toHaveBeenCalled();
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenNthCalledWith(1, true, "voice");
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });

  it("cancels native Assist on this kiosk and keeps the duplex session open", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const callService = vi.fn(async () => undefined);
    const hass: NativeAssistHass = {
      callService,
      services: {
        esphome: {
          ks_attic_dashboard_vs_cancel: {},
          ks_dining_room_vs_cancel: {},
        },
      },
      callWS: async (message: unknown) => {
        const type = (message as { type?: string }).type;
        if (type === "config/device_registry/list") {
          return [
            { id: "attic", name: "Attic Dashboard" },
            { id: "dining", name: "Dining Room" },
          ];
        }
        if (type === "config/entity_registry/list") {
          return [
            { entity_id: "button.ks_attic_dashboard_reload", device_id: "attic", platform: "esphome" },
            { entity_id: "button.ks_dining_room_reload", device_id: "dining", platform: "esphome" },
            {
              entity_id: "assist_satellite.dining_room_dining_room_dashboard",
              device_id: "attic",
              platform: "esphome",
            },
          ];
        }
        return [];
      },
    };
    const events = wakeTarget();
    const pipelineRun = vi.fn();
    let session!: VoiceSession;
    const openSession = vi.fn(async () => {
      session = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      return session;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      pipelineRun,
      getDeviceInfo: async () => ({ name: "Attic Dashboard" }),
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };

    installGrokVoice({ kiosk, events, hass: () => hass, openSession });
    expect(log).toHaveBeenCalledWith("[Grok Voice] Installed Kiosk Satellite wake override");
    await flush();

    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    expect(log).toHaveBeenCalledWith("[Grok Voice] Cancelled native Assist via esphome.ks_attic_dashboard_vs_cancel");
    expect(log).toHaveBeenCalledWith("[Grok Voice] Dashboard websocket resumed");
    expect(log).toHaveBeenCalledWith("[Grok Voice] Duplex session open");

    expect(callService).toHaveBeenCalledTimes(1);
    expect(callService).toHaveBeenCalledWith("esphome", "ks_attic_dashboard_vs_cancel", {});
    expect(pipelineRun).not.toHaveBeenCalled();
    expect(openSession).toHaveBeenCalledOnce();
    expect(session.captureActive).toBe(true);
    expect(kiosk.setInteractionActive).toHaveBeenCalledTimes(1);
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(true, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(false);

    session.finish("idle");
    await flush();
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenLastCalledWith(true);
    expect(openSession).toHaveBeenCalledOnce();
    log.mockRestore();
  });

  it("reopens the duplex session when the native overlay closes it immediately", async () => {
    const events = wakeTarget();
    let created = 0;
    let kept!: VoiceSession;
    const openSession = vi.fn(async () => {
      created += 1;
      const session = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      if (created === 1) {
        const start = session.start.bind(session);
        session.start = async () => {
          await start();
          session.finish("closed");
        };
      } else {
        kept = session;
      }
      return session;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    installGrokVoice({ kiosk, events, openSession });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    await new Promise((resolve) => setTimeout(resolve, 400));
    await flush();

    expect(openSession.mock.calls.length).toBeGreaterThan(1);
    expect(kept.captureActive).toBe(true);
    expect(kiosk.setInteractionActive).not.toHaveBeenCalledWith(false, "voice");
    kept.finish("idle");
    await flush();
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });

  it("retries after a Home Assistant error object and does not release the session", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const events = wakeTarget();
    let created = 0;
    let kept!: VoiceSession;
    const openSession = vi.fn(async () => {
      created += 1;
      if (created < 3) {
        throw { code: "unknown_error", message: "websocket closed" };
      }
      kept = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      return kept;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async (active: boolean) => {
        if (!active) {
          throw { code: "unavailable", message: "paused" };
        }
        return true;
      }),
    };
    installGrokVoice({ kiosk, events, openSession });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    await new Promise((resolve) => setTimeout(resolve, 900));
    await flush();

    expect(log).toHaveBeenCalledWith(
      '[Grok Voice] Duplex session failed: {"code":"unknown_error","message":"websocket closed"}',
    );
    expect(log).toHaveBeenCalledWith('[Grok Voice] setWakeWordActive failed: {"code":"unavailable","message":"paused"}');
    expect(log).toHaveBeenCalledWith("[Grok Voice] Duplex session open");
    expect(kept.captureActive).toBe(true);
    expect(kiosk.setInteractionActive).not.toHaveBeenCalledWith(false, "voice");
    kept.finish("idle");
    await flush();
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(log).not.toHaveBeenCalledWith(expect.stringContaining("Wake failed"));
    log.mockRestore();
  });
});
