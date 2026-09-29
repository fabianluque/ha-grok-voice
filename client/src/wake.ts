import type { VoiceSession } from "./session";

export interface KioskApi {
  platform: string;
  setInteractionActive(active: boolean, reason?: string): Promise<boolean>;
  setWakeWordActive(active: boolean): Promise<boolean>;
  pipelineRun?(params: unknown): Promise<unknown>;
}

export interface WakeDeps {
  kiosk: KioskApi | null | undefined;
  addEventListener(type: string, handler: (event: Event) => void): void;
  openSession(): Promise<VoiceSession>;
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

  deps.addEventListener("kiosksatellite:wakeword", onWake as (event: Event) => void);
  return { installed: true };
}
