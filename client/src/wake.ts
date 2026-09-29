import type { VoiceSession } from "./session";

export const WAKE_EVENT = "kiosksatellite:wakeword";

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

export interface WakeDeps {
  kiosk: KioskApi | null | undefined;
  events: WakeEventTarget;
  openSession(): Promise<VoiceSession>;
}

/**
 * Voice Satellite listens for `kiosksatellite:wakeword` on `window` and starts
 * Assist from that listener. This script is injected after load, and page code
 * cannot list listeners to remove them. Kiosk Satellite also has no
 * removeListener / pipeline-skip method.
 *
 * A new EventTarget has an empty listener list. Wake dispatch, and any later
 * add/remove for that event, are moved onto the clone. Assist's listener stays
 * on `window` and is never invoked. Only `handler` runs.
 */
export function claimWakeEvent(target: WakeEventTarget, handler: (event: Event) => void): void {
  const clone = new EventTarget();
  clone.addEventListener(WAKE_EVENT, handler);

  const nativeAdd = target.addEventListener.bind(target);
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
      return clone.dispatchEvent(event);
    }
    return nativeDispatch(event);
  };
}

export function installGrokVoice(deps: WakeDeps): { installed: boolean } {
  if (!deps.kiosk || deps.kiosk.platform !== "kiosksatellite") {
    return { installed: false };
  }

  let active = false;
  const onWake = async () => {
    if (active) {
      return;
    }
    active = true;
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
