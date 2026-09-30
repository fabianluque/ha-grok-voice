import { afterEach, describe, expect, it, vi } from "vitest";
import type { NativeAssistHass } from "../src/native-assist";
import { VoiceSession } from "../src/session";
import {
  CANCEL_SETTLE_MS,
  MIC_RELEASE_MS,
  formatReject,
  installGrokVoice,
  rearmNativeWake,
  restoreWakeClaim,
  WAKE_EVENT,
  type KioskApi,
} from "../src/wake";

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

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function finishAndSettle(session: VoiceSession, reason = "idle"): Promise<void> {
  session.finish(reason);
  await delay(MIC_RELEASE_MS + 30);
  await flush();
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
    await finishAndSettle(session);
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(true, "voice");
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
    await finishAndSettle(session);
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

    await finishAndSettle(session);

    expect(pipelineRun).not.toHaveBeenCalled();
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(true, "voice");
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
    expect(openSession).not.toHaveBeenCalled();

    await delay(CANCEL_SETTLE_MS + 20);
    await flush();
    expect(callService).toHaveBeenCalledTimes(1);
    expect(callService).toHaveBeenCalledWith("esphome", "ks_attic_dashboard_vs_cancel", {});
    expect(pipelineRun).not.toHaveBeenCalled();
    expect(openSession).toHaveBeenCalledOnce();
    expect(session.captureActive).toBe(true);
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(true, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(false);

    await finishAndSettle(session);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenLastCalledWith(true);
    expect(openSession).toHaveBeenCalledOnce();
    log.mockRestore();
  });

  it("formats Home Assistant plain-object rejects for the console", () => {
    expect(formatReject(new Error("named"))).toBe("named");
    expect(formatReject({ code: "unknown_command", message: "Connection lost" })).toBe(
      '{"code":"unknown_command","message":"Connection lost"}',
    );
    expect(formatReject("plain")).toBe("plain");
  });

  it("retries a plain-object openSession reject and keeps the duplex session up", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);

    const events = wakeTarget();
    let created = 0;
    let kept!: VoiceSession;
    const openSession = vi.fn(async () => {
      created += 1;
      if (created === 1) {
        throw { code: "unknown_command", message: "Connection lost" };
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
      setWakeWordActive: vi.fn(async () => true),
    };
    installGrokVoice({ kiosk, events, openSession });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    expect(openSession).toHaveBeenCalledOnce();
    await delay(300);
    await flush();

    expect(openSession).toHaveBeenCalledTimes(2);
    expect(kept.captureActive).toBe(true);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(true, "voice");
    expect(warn).toHaveBeenCalledWith(
      "[Grok Voice] openSession attempt 1 failed",
      '{"code":"unknown_command","message":"Connection lost"}',
    );
    await finishAndSettle(kept);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(unhandled).toEqual([]);
    process.off("unhandledRejection", onUnhandled);
    warn.mockRestore();
  });

  it("swallows a failed wake after retries so the Assist cancel path does not throw #<Object>", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);

    const events = wakeTarget();
    const rejectObject = { code: "unknown_command", message: "Connection lost" };
    const openSession = vi.fn(async () => {
      throw rejectObject;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    installGrokVoice({ kiosk, events, openSession });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await delay(1200);
    await flush();

    expect(openSession).toHaveBeenCalledTimes(4);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenLastCalledWith(true);
    expect(errorLog).toHaveBeenCalledWith(
      "[Grok Voice] Duplex session failed after wake",
      '{"code":"unknown_command","message":"Connection lost"}',
    );
    expect(unhandled).toEqual([]);
    process.off("unhandledRejection", onUnhandled);
    warn.mockRestore();
    errorLog.mockRestore();
  });

  it("does not leave an uncaught rejection when the wake-word poke fails", async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);
    let session!: VoiceSession;
    const events = wakeTarget();
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async (active: boolean) => {
        if (!active) {
          throw { code: "bridge_error", message: "wake poke failed" };
        }
        return true;
      }),
    };
    installGrokVoice({
      kiosk,
      events,
      openSession: async () => {
        session = new VoiceSession(
          () => ({ stop() {} }),
          () => ({ send() {}, close() {} }),
        );
        return session;
      },
    });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    expect(session.captureActive).toBe(true);
    await delay(250);
    await finishAndSettle(session);
    expect(unhandled).toEqual([]);
    process.off("unhandledRejection", onUnhandled);
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
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(true, "voice");
    await finishAndSettle(kept);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });

  it("throws after four quick socket closes so a silent duplex failure is visible", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const events = wakeTarget();
    const openSession = vi.fn(async () => {
      const session = new VoiceSession(
        () => ({ stop() {} }),
        () => ({ send() {}, close() {} }),
      );
      const start = session.start.bind(session);
      session.start = async () => {
        await start();
        session.finish("closed");
      };
      return session;
    });
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    installGrokVoice({ kiosk, events, openSession });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await delay(1200);
    await flush();

    expect(openSession).toHaveBeenCalledTimes(4);
    expect(warn.mock.calls.some((call) => String(call[0]).includes("duplex attempt 1 closed after"))).toBe(true);
    expect(warn.mock.calls.some((call) => String(call[0]).includes("duplex attempt 4 closed after"))).toBe(true);
    expect(errorLog).toHaveBeenCalledWith("[Grok Voice] Duplex session failed after wake", "socket closed quickly");
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenLastCalledWith(true);
    warn.mockRestore();
    errorLog.mockRestore();
  });

  it("re-arms native wake after hiding Assist chrome", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const hideBlurOverlay = vi.fn();
    const hideBar = vi.fn();
    const stop = vi.fn();
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
      getWakeWordState: vi.fn(async () => ({ active: true, listening: true, status: "listening" })),
    };
    await expect(
      rearmNativeWake({
        kiosk,
        session: { pipeline: { stop }, ui: { hideBlurOverlay, hideBar } },
        settleMs: 0,
      }),
    ).resolves.toBe(true);
    expect(stop).toHaveBeenCalledOnce();
    expect(hideBlurOverlay).toHaveBeenCalledWith("pipeline");
    expect(hideBar).toHaveBeenCalledOnce();
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(false, "voice");
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setWakeWordActive).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith("[Grok Voice] Re-armed native wake listening");
    log.mockRestore();
  });

  it("retries setWakeWordActive when the first resume is still suspended", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    let calls = 0;
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => {
        calls += 1;
        return calls > 1;
      }),
      getWakeWordState: vi.fn(async () =>
        calls > 1 ? { active: true, status: "listening" } : { active: false, status: "suspended" },
      ),
    };
    await expect(rearmNativeWake({ kiosk, settleMs: 0, wait: async () => undefined })).resolves.toBe(true);
    expect(kiosk.setWakeWordActive).toHaveBeenCalledTimes(2);
    expect(log).toHaveBeenCalledWith("[Grok Voice] Re-armed native wake listening");
    log.mockRestore();
  });

  it("restores KS wake listening when the inject boots after a dashboard refresh", async () => {
    const events = wakeTarget();
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    expect(
      installGrokVoice({
        kiosk,
        events,
        openSession: async () => {
          throw new Error("should not open");
        },
      }).installed,
    ).toBe(true);
    await flush();
    expect(kiosk.setWakeWordActive).toHaveBeenCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenCalledWith(false, "voice");
  });

  it("hangs up an orphan duplex and re-arms KS wake on pagehide", async () => {
    const events = wakeTarget();
    let session!: VoiceSession;
    const sent: string[] = [];
    const close = vi.fn();
    const kiosk: KioskApi = {
      platform: "kiosksatellite",
      setInteractionActive: vi.fn(async () => true),
      setWakeWordActive: vi.fn(async () => true),
    };
    installGrokVoice({
      kiosk,
      events,
      openSession: async () => {
        session = new VoiceSession(
          () => ({ stop() {} }),
          () => ({
            send(data) {
              if (typeof data === "string") {
                sent.push(data);
              }
            },
            close,
          }),
        );
        return session;
      },
    });
    events.dispatchEvent(new Event(WAKE_EVENT));
    await flush();
    expect(session.captureActive).toBe(true);
    events.dispatchEvent(new Event("pagehide"));
    await flush();
    expect(session.captureActive).toBe(false);
    expect(close).toHaveBeenCalled();
    expect(sent).toContain(JSON.stringify({ type: "stop", reason: "unload" }));
    expect(kiosk.setWakeWordActive).toHaveBeenLastCalledWith(true);
    expect(kiosk.setInteractionActive).toHaveBeenLastCalledWith(false, "voice");
  });
});
