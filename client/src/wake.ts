import type { VoiceSession } from "./session";

export const WAKE_EVENT = "kiosksatellite:wakeword";

const SESSION_KEY = "__vsSession";

export interface KioskApi {
  platform: string;
  setInteractionActive(active: boolean, reason?: string): Promise<boolean>;
  setWakeWordActive(active: boolean): Promise<boolean>;
  pipelineRun?(params: unknown): Promise<unknown>;
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

export function installGrokVoice(deps: WakeDeps): { installed: boolean } {
  if (!deps.kiosk || deps.kiosk.platform !== "kiosksatellite") {
    return { installed: false };
  }

  if (deps.host) {
    watchVoiceSatellite(deps.host);
  }
  hideAssistChrome(deps.document);

  let active = false;
  const onWake = async () => {
    if (active) {
      return;
    }
    active = true;
    blockAssistWake(deps.host?.__vsSession);
    await deps.kiosk!.setInteractionActive(true, "voice");
    try {
      const session = await deps.openSession();
      await new Promise<void>((resolve) => {
        session.onEnd(() => resolve());
        void session.start();
      });
    } finally {
      active = false;
      await deps.kiosk!.setWakeWordActive(true);
      await deps.kiosk!.setInteractionActive(false, "voice");
    }
  };

  claimWakeEvent(deps.events, onWake as (event: Event) => void);
  return { installed: true };
}
