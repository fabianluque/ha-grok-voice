import { cancelNativeAssist, resolveNativeCancelService, type NativeAssistHass } from "./native-assist";
import type { VoiceSession } from "./session";
import { cancelTimeout, scheduleTimeout } from "./timers";

export const WAKE_EVENT = "kiosksatellite:wakeword";

const SESSION_KEY = "__vsSession";

export interface WakeWordState {
  listening?: boolean;
  active?: boolean;
  status?: string;
}

export interface KioskApi {
  platform: string;
  setInteractionActive(active: boolean, reason?: string): Promise<boolean>;
  setWakeWordActive(active: boolean): Promise<boolean>;
  getWakeWordState?(): Promise<WakeWordState | null | undefined>;
  pipelineRun?(params: unknown): Promise<unknown>;
  getDeviceInfo?(): Promise<Record<string, unknown> | null | undefined>;
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
  /** Paint Listening immediately on wake, before cancel / socket / mic. */
  onWakeVisual?: () => void;
  /** Hide the overlay when this wake is fully finished (including retries). */
  onWakeEnd?: () => void;
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
  pageLeaveHandler = null;
  if (pageLeaveTarget?.removeEventListener) {
    for (const type of PAGE_LEAVE_EVENTS) {
      pageLeaveTarget.removeEventListener(type, pageLeaveListener);
    }
  }
  pageLeaveTarget = null;
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

const PAGE_LEAVE_EVENTS = ["pagehide", "beforeunload"] as const;
let pageLeaveHandler: (() => void) | null = null;
let pageLeaveTarget: WakeEventTarget | null = null;
const pageLeaveListener = (): void => {
  pageLeaveHandler?.();
};

function bindPageLeave(target: WakeEventTarget, handler: () => void): void {
  pageLeaveHandler = handler;
  if (pageLeaveTarget === target) {
    return;
  }
  if (pageLeaveTarget?.removeEventListener) {
    for (const type of PAGE_LEAVE_EVENTS) {
      pageLeaveTarget.removeEventListener(type, pageLeaveListener);
    }
  }
  pageLeaveTarget = target;
  for (const type of PAGE_LEAVE_EVENTS) {
    target.addEventListener(type, pageLeaveListener);
  }
}

/**
 * Kept for tests and docs. Overlay and duplex no longer wait this long after
 * vs_cancel: Assist pause that kills the first socket is handled by runDuplex
 * retries so Listening can paint immediately.
 */
export const CANCEL_SETTLE_MS = 400;

/** Let getUserMedia tracks drop so native wake can reclaim the microphone. */
export const MIC_RELEASE_MS = 150;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    scheduleTimeout(resolve, ms);
  });
}

/** Home Assistant `callWS` rejects with a plain `{ code, message }` object. */
export function formatReject(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (error && typeof error === "object") {
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  }
  return String(error);
}

/**
 * Native Assist pauses the dashboard as soon as its overlay is up, which
 * closes the duplex socket in about 100ms. Retry that collapse. A real end
 * (`idle`, `done`, `end`) returns immediately. Four quick closes is a real failure
 * (wrong host, proxy drop), not a successful session.
 */
async function runDuplex(open: () => Promise<VoiceSession>): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const started = Date.now();
    try {
      const session = await open();
      const reason = await new Promise<string>((resolve) => {
        session.onEnd((endReason) => resolve(endReason));
        void session.start();
      });
      const elapsed = Date.now() - started;
      const collapsed = reason === "closed" && elapsed < QUICK_CLOSE_MS;
      if (!collapsed) {
        return;
      }
      last = new Error("socket closed quickly");
      console.warn(`[Grok Voice] duplex attempt ${attempt + 1} closed after ${elapsed}ms`, reason);
    } catch (error) {
      last = error;
      console.warn(`[Grok Voice] openSession attempt ${attempt + 1} failed`, formatReject(error));
    }
    if (attempt === 3) {
      throw last ?? new Error("socket closed quickly");
    }
    await wait(250);
  }
  if (last) {
    throw last;
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
/**
 * KS handoff: native capture is already stopped on wakeword. After duplex,
 * stop VS chrome, wait for the browser mic to drop, then setWakeWordActive(true).
 * Do not call releaseWakeWord — that hard-closes the mic until a new config push.
 */
export async function rearmNativeWake(input: {
  kiosk: KioskApi;
  session?: VoiceSatelliteSession | null;
  settleMs?: number;
  wait?: (ms: number) => Promise<void>;
}): Promise<boolean> {
  const vs = input.session;
  try {
    vs?.pipeline?.stop?.();
  } catch {
    // No run was active.
  }
  try {
    vs?.ui?.hideBlurOverlay?.("pipeline");
  } catch {
    // Overlay may already be gone.
  }
  try {
    vs?.ui?.hideBar?.();
  } catch {
    // Bar may already be gone.
  }

  const pause = input.wait ?? wait;
  const settle = input.settleMs ?? MIC_RELEASE_MS;
  if (settle > 0) {
    await pause(settle);
  }

  await input.kiosk.setInteractionActive(false, "voice").catch(() => false);
  let ok = (await input.kiosk.setWakeWordActive(true).catch(() => false)) === true;
  const state = await input.kiosk.getWakeWordState?.().catch(() => undefined);
  const blocked =
    state?.status === "suspended" || state?.status === "browser" || state?.active === false;
  if (!ok || blocked) {
    await pause(200);
    ok = (await input.kiosk.setWakeWordActive(true).catch(() => false)) === true;
  }
  console.log(
    ok
      ? "[Grok Voice] Re-armed native wake listening"
      : "[Grok Voice] Native wake re-arm did not confirm; Voice Satellite may still show listening off",
  );
  return ok;
}

function holdNativeWakeOff(kiosk: KioskApi): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const poke = () => {
    if (stopped) {
      return;
    }
    void kiosk.setWakeWordActive(false).catch(() => undefined);
    timer = scheduleTimeout(poke, 200);
  };
  poke();
  return () => {
    stopped = true;
    if (timer) {
      cancelTimeout(timer);
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
  let live: VoiceSession | null = null;
  let releaseWake: () => void = () => {};

  const restoreWake = async () => {
    if (active) {
      return false;
    }
    const restored = await rearmNativeWake({
      kiosk: deps.kiosk!,
      session: deps.host?.__vsSession,
      settleMs: 0,
    });
    if (active) {
      void deps.kiosk!.setWakeWordActive(false).catch(() => undefined);
      void deps.kiosk!.setInteractionActive(true, "voice").catch(() => undefined);
      return false;
    }
    return restored;
  };

  void restoreWake();

  const abandonPage = () => {
    releaseWake();
    live?.finish("unload");
    deps.onWakeEnd?.();
    void rearmNativeWake({ kiosk: deps.kiosk!, session: deps.host?.__vsSession, settleMs: 0 });
  };

  const onWake = async () => {
    if (active) {
      return;
    }
    active = true;
    deps.onWakeVisual?.();
    blockAssistWake(deps.host?.__vsSession);
    const release = holdNativeWakeOff(deps.kiosk!);
    releaseWake = release;
    void (async () => {
      const service = await nativeCancel;
      const cancelled = await cancelNativeAssist(deps.hass?.() ?? null, service);
      if (cancelled && service) {
        console.log(`[Grok Voice] Cancelled native Assist via esphome.${service}`);
      } else if (service) {
        console.log(`[Grok Voice] Native Assist cancel failed for esphome.${service}`);
      } else {
        console.log("[Grok Voice] Native Assist cancel skipped; no esphome vs_cancel matched this kiosk");
      }
    })().catch((error) => {
      console.warn("[Grok Voice] Native Assist cancel failed", formatReject(error));
    });
    await deps.kiosk!.setInteractionActive(true, "voice").catch(() => false);
    try {
      await runDuplex(async () => {
        const session = await deps.openSession();
        live = session;
        return session;
      });
    } catch (error) {
      console.error("[Grok Voice] Duplex session failed after wake", formatReject(error));
    } finally {
      release();
      if (releaseWake === release) {
        releaseWake = () => {};
      }
      live = null;
      active = false;
      deps.onWakeEnd?.();
      await rearmNativeWake({ kiosk: deps.kiosk!, session: deps.host?.__vsSession });
    }
  };

  claimWakeEvent(deps.events, onWake as (event: Event) => void);
  bindPageLeave(deps.events, abandonPage);
  console.log("[Grok Voice] Installed Kiosk Satellite wake override");
  return { installed: true };
}
