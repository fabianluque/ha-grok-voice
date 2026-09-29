import { formatUnknown } from "./ingress";
import { cancelNativeAssist, resolveNativeCancelService, type NativeAssistHass } from "./native-assist";
import type { VoiceSession } from "./session";

export const WAKE_EVENT = "kiosksatellite:wakeword";

const SESSION_KEY = "__vsSession";

export interface KioskApi {
  platform: string;
  setInteractionActive(active: boolean, reason?: string): Promise<boolean>;
  setWakeWordActive(active: boolean): Promise<boolean>;
  pipelineRun?(params: unknown): Promise<unknown>;
  getDeviceInfo?(): Promise<{ name?: string } | null | undefined>;
}

export interface WakeEventTarget {
  addEventListener: (
    type: string,
    handler: (event: Event) => void,
    options?: boolean | AddEventListenerOptions,
  ) => void;
  removeEventListener?: (
    type: string,
    handler: (event: Event) => void,
    options?: boolean | EventListenerOptions,
  ) => void;
  dispatchEvent?: (event: Event) => boolean;
}

export interface VoiceSatelliteUi {
  showBlurOverlay?: (reason?: string) => unknown;
  hideBlurOverlay?: (reason?: string) => unknown;
  hideBar?: () => unknown;
}

export interface VoiceSatellitePipeline {
  start?: (opts?: Record<string, unknown>) => unknown;
  stop?: () => unknown;
}

/** Voice Satellite's page session (`window.__vsSession`). */
export interface VoiceSatelliteSession {
  onWakeAction?: (opts?: unknown) => unknown;
  pipeline?: VoiceSatellitePipeline;
  ui?: VoiceSatelliteUi;
}

export interface WakeHost {
  __vsSession?: VoiceSatelliteSession;
}

export interface WakeDeps {
  kiosk: KioskApi | null | undefined;
  events: WakeEventTarget;
  /** Page object that holds `__vsSession`. Defaults to the event target when it is the page. */
  host?: WakeHost;
  document?: Document;
  /** Home Assistant page object, used to cancel this kiosk's native Assist turn. */
  hass?: () => NativeAssistHass | null | undefined;
  openSession(): Promise<VoiceSession>;
}

const protoDispatch = EventTarget.prototype.dispatchEvent;
const protoAdd = EventTarget.prototype.addEventListener;
const wakeClone = new EventTarget();
let wakeHandler: ((event: Event) => void) | null = null;
let prototypePatched = false;
const watchedHosts = new WeakSet<object>();
const blockedPipelines = new WeakSet<object>();

protoAdd.call(wakeClone, WAKE_EVENT, (event) => {
  wakeHandler?.(event);
});

function deliverWake(event: Event): boolean {
  return protoDispatch.call(wakeClone, event);
}

function ensurePrototypeClaim(): void {
  if (prototypePatched) {
    return;
  }
  prototypePatched = true;
  EventTarget.prototype.dispatchEvent = function (event: Event): boolean {
    if (wakeHandler && event?.type === WAKE_EVENT) {
      return deliverWake(event);
    }
    return protoDispatch.call(this, event);
  };
  EventTarget.prototype.addEventListener = function (
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ): void {
    if (type === WAKE_EVENT) {
      return;
    }
    protoAdd.call(this, type, listener, options);
  };
}

/** Test hook. Production boot does not call this. */
export function restoreWakeClaim(): void {
  if (prototypePatched) {
    EventTarget.prototype.dispatchEvent = protoDispatch;
    EventTarget.prototype.addEventListener = protoAdd;
    prototypePatched = false;
  }
  wakeHandler = null;
}

function isWakePipelineStart(opts?: Record<string, unknown>): boolean {
  if (!opts) {
    return false;
  }
  return opts.start_stage === "stt" || typeof opts.wake_word_phrase === "string";
}

/**
 * Voice Satellite opens the Assist overlay from more than the DOM listener.
 * `onWakeAction` → `triggerWake`, and `WakeWordManager._onDetection`, both call
 * `ui.showBlurOverlay` and `pipeline.start({ start_stage: "stt" })`.
 */
export function blockAssistWake(session: VoiceSatelliteSession | null | undefined): void {
  if (!session) {
    return;
  }
  const ignoreWake = () => undefined;
  try {
    Object.defineProperty(session, "onWakeAction", {
      configurable: true,
      enumerable: true,
      writable: false,
      value: ignoreWake,
    });
  } catch {
    session.onWakeAction = ignoreWake;
  }

  const ui = session.ui;
  if (ui) {
    ui.showBlurOverlay = () => undefined;
    try {
      ui.hideBlurOverlay?.("pipeline");
    } catch {
      // Overlay may already be gone.
    }
    try {
      ui.hideBar?.();
    } catch {
      // Bar may already be gone.
    }
  }

  const pipeline = session.pipeline;
  if (pipeline && typeof pipeline.start === "function" && !blockedPipelines.has(pipeline)) {
    const original = pipeline.start.bind(pipeline);
    blockedPipelines.add(pipeline);
    pipeline.start = (opts) => {
      if (isWakePipelineStart(opts)) {
        return Promise.resolve();
      }
      return original(opts);
    };
  }
  try {
    pipeline?.stop?.();
  } catch {
    // No run was active.
  }
}

function watchVoiceSatellite(host: WakeHost): void {
  if (watchedHosts.has(host)) {
    blockAssistWake(host.__vsSession);
    return;
  }
  watchedHosts.add(host);
  let current = host.__vsSession;
  if (current) {
    blockAssistWake(current);
  }
  try {
    Object.defineProperty(host, SESSION_KEY, {
      configurable: true,
      enumerable: true,
      get() {
        return current;
      },
      set(next: VoiceSatelliteSession | undefined) {
        current = next;
        blockAssistWake(next);
      },
    });
  } catch {
    blockAssistWake(current);
  }
}

function hideAssistChrome(doc: Document | undefined): void {
  if (!doc?.getElementById || doc.getElementById("grok-voice-hide-assist")) {
    return;
  }
  const style = doc.createElement("style");
  style.id = "grok-voice-hide-assist";
  style.textContent =
    ".vs-blur-overlay,.vs-rainbow-bar{display:none!important;visibility:hidden!important;}";
  (doc.head || doc.documentElement).appendChild(style);
}

/**
 * Kiosk Satellite delivers `kiosksatellite:wakeword` with `window.dispatchEvent`.
 * Replacing only that instance method misses `EventTarget.prototype.dispatchEvent`
 * and any listener already registered on the native method. Wake events are
 * delivered to one clone. Grok's handler is also registered on the target so a
 * native dispatch still starts the session.
 */
export function claimWakeEvent(target: WakeEventTarget, handler: (event: Event) => void): void {
  wakeHandler = handler;
  const nativeAdd = target.addEventListener.bind(target);
  nativeAdd(WAKE_EVENT, handler);
  ensurePrototypeClaim();

  const nativeRemove = target.removeEventListener?.bind(target);
  const nativeDispatch = target.dispatchEvent?.bind(target);

  target.addEventListener = (type, listener, options) => {
    if (type === WAKE_EVENT) {
      return;
    }
    nativeAdd(type, listener, options);
  };

  if (nativeRemove) {
    target.removeEventListener = (type, listener, options) => {
      if (type === WAKE_EVENT) {
        return;
      }
      nativeRemove(type, listener, options);
    };
  }

  if (!nativeDispatch) {
    return;
  }

  target.dispatchEvent = (event) => {
    if (event?.type === WAKE_EVENT) {
      return deliverWake(event);
    }
    return nativeDispatch(event);
  };
}

const QUICK_CLOSE_MS = 800;
const RETRIES = 8;
const RETRY_MS = 300;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * vs_cancel returns before the dashboard WebView has left Assist's pause.
 * `hass.callWS` rejects with `{code, message}` until that pause lifts.
 */
async function waitForDashboard(deps: WakeDeps): Promise<void> {
  const hass = deps.hass?.();
  if (!hass?.callWS) {
    await wait(600);
    console.log("[Grok Voice] Dashboard settle finished");
    return;
  }
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      await hass.callWS({ type: "ping" });
      console.log("[Grok Voice] Dashboard websocket resumed");
      return;
    } catch (error) {
      console.log(`[Grok Voice] Dashboard websocket not ready: ${formatUnknown(error)}`);
      await wait(400);
    }
  }
  console.log("[Grok Voice] Dashboard websocket still failing, opening the session anyway");
}

/**
 * Native Assist pauses the dashboard as soon as its overlay is up, which
 * used to close the duplex socket before the page had resumed. Retry that
 * collapse. A real end (`idle`, `end`) returns immediately.
 */
async function runDuplex(open: () => Promise<VoiceSession>): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < RETRIES; attempt += 1) {
    const started = Date.now();
    let stayTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      const session = await open();
      console.log("[Grok Voice] Duplex session open");
      const reason = await new Promise<string>((resolve) => {
        stayTimer = setTimeout(() => {
          console.log("[Grok Voice] Duplex session stayed open");
        }, QUICK_CLOSE_MS);
        session.onEnd((endReason) => resolve(endReason));
        void session.start();
      });
      clearTimeout(stayTimer);
      const collapsed = reason === "closed" && Date.now() - started < QUICK_CLOSE_MS;
      if (!collapsed) {
        console.log(`[Grok Voice] Duplex session ended: ${reason}`);
        return;
      }
      console.log("[Grok Voice] Duplex session closed early, retrying");
    } catch (error) {
      if (stayTimer) {
        clearTimeout(stayTimer);
      }
      last = error;
      console.log(`[Grok Voice] Duplex session failed: ${formatUnknown(error)}`);
      if (attempt === RETRIES - 1) {
        throw error instanceof Error ? error : new Error(formatUnknown(error));
      }
    }
    await wait(RETRY_MS);
  }
  if (last) {
    throw last instanceof Error ? last : new Error(formatUnknown(last));
  }
}

function lookupNativeCancel(deps: WakeDeps): Promise<string | null> {
  return (async () => {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const hass = deps.hass?.() ?? null;
      if (!hass?.callService || !deps.kiosk?.getDeviceInfo) {
        if (!deps.hass && !deps.kiosk?.getDeviceInfo) {
          return null;
        }
        await wait(300);
        continue;
      }
      const name = (await deps.kiosk.getDeviceInfo())?.name?.trim() ?? "";
      if (!name) {
        return null;
      }
      return resolveNativeCancelService(hass, name);
    }
    return null;
  })();
}

/**
 * While Grok holds the microphone, native onIdle tries to re-arm the wake
 * word and take the mic back. Keep it suspended until the duplex session ends.
 */
function holdNativeWakeOff(kiosk: KioskApi): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const poke = () => {
    if (stopped) {
      return;
    }
    void Promise.resolve()
      .then(() => kiosk.setWakeWordActive(false))
      .catch((error: unknown) => {
        console.log(`[Grok Voice] setWakeWordActive failed: ${formatUnknown(error)}`);
      });
    timer = setTimeout(poke, 200);
  };
  poke();
  return () => {
    stopped = true;
    if (timer) {
      clearTimeout(timer);
    }
  };
}

export function installGrokVoice(deps: WakeDeps): { installed: boolean } {
  if (!deps.kiosk || deps.kiosk.platform !== "kiosksatellite") {
    return { installed: false };
  }

  if (deps.host) {
    watchVoiceSatellite(deps.host);
  }
  hideAssistChrome(deps.document);
  const nativeCancel = lookupNativeCancel(deps);

  let active = false;
  const onWake = async () => {
    if (active) {
      return;
    }
    active = true;
    let releaseWake = () => {};
    let held = false;
    try {
      blockAssistWake(deps.host?.__vsSession);
      const service = await nativeCancel;
      const cancelled = await cancelNativeAssist(deps.hass?.() ?? null, service);
      if (cancelled && service) {
        console.log(`[Grok Voice] Cancelled native Assist via esphome.${service}`);
      } else if (service) {
        console.log(`[Grok Voice] Native Assist cancel failed for esphome.${service}`);
      } else {
        console.log("[Grok Voice] Native Assist cancel skipped; no esphome vs_cancel matched this kiosk");
      }
      releaseWake = holdNativeWakeOff(deps.kiosk!);
      await deps.kiosk!.setInteractionActive(true, "voice");
      held = true;
      if (cancelled) {
        await waitForDashboard(deps);
      }
      await runDuplex(deps.openSession);
    } catch (error) {
      console.log(`[Grok Voice] Wake session stopped: ${formatUnknown(error)}`);
    } finally {
      releaseWake();
      active = false;
      if (held) {
        try {
          await deps.kiosk!.setWakeWordActive(true);
          await deps.kiosk!.setInteractionActive(false, "voice");
        } catch (error) {
          console.log(`[Grok Voice] Could not re-arm wake word: ${formatUnknown(error)}`);
        }
      }
    }
  };

  claimWakeEvent(deps.events, () => {
    void onWake().catch((error: unknown) => {
      console.log(`[Grok Voice] Wake failed: ${formatUnknown(error)}`);
    });
  });
  console.log("[Grok Voice] Installed Kiosk Satellite wake override");
  return { installed: true };
}
