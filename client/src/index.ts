import { createBrowserSession } from "./browser";
import {
  accessToken,
  authModeForUrl,
  describeDuplexChoice,
  hostnameOf,
  pageHass,
  parseDebugPort,
  resolveKioskVoiceSocket,
} from "./ingress";
import type { NativeAssistHass } from "./native-assist";
import { installGrokVoice, type KioskApi, type WakeHost } from "./wake";

interface KioskWindow extends Window {
  kioskSatellite?: KioskApi;
  GROK_VOICE_URL?: string;
  GROK_VOICE_DEBUG_PORT?: number | string;
}

function showLine(overlay: HTMLElement, role: string, text: string): void {
  const line = document.createElement("p");
  line.textContent = `${role === "user" ? "You" : "Grok"}: ${text}`;
  overlay.appendChild(line);
}

function boot(): void {
  const kiosk = (window as KioskWindow).kioskSatellite;
  installGrokVoice({
    kiosk,
    events: window,
    host: window as Window & WakeHost,
    document,
    hass: () => pageHass() as NativeAssistHass | null,
    openSession: async () => {
      const hass = pageHass();
      const explicit = (window as KioskWindow).GROK_VOICE_URL;
      const debugPort = parseDebugPort((window as KioskWindow).GROK_VOICE_DEBUG_PORT);
      const resolved = explicit
        ? {
            url: explicit,
            authority: { source: "explicit" as const, protocol: "", host: "" },
            debugPort,
            authMode: authModeForUrl(explicit),
          }
        : hass
          ? resolveKioskVoiceSocket({
              hass,
              pageProtocol: location.protocol,
              pageHost: location.host,
              debugPort,
            })
          : null;
      const url = resolved?.url || "";
      if (!url || !hass) {
        throw new Error("Home Assistant is not available on this page");
      }
      const authMode = resolved.authMode;
      const hostLabel = explicit
        ? new URL(explicit, "http://localhost").host
        : `${hostnameOf(resolved.authority.host)}:${resolved.debugPort}`;
      console.log(describeDuplexChoice({ authority: resolved.authority, host: hostLabel, authMode }));
      const token = accessToken(hass);
      console.log(`[Grok Voice] Opening duplex ${url} auth ${authMode}`);
      const overlay = document.createElement("div");
      overlay.id = "grok-voice-overlay";
      overlay.style.cssText =
        "position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;color:white;font:16px sans-serif;text-shadow:0 1px 2px black;";
      document.body.appendChild(overlay);
      try {
        const { session } = await createBrowserSession({
          url,
          token,
          ingress: authMode === "ingress",
          onTranscript: (role, text) => showLine(overlay, role, text),
        });
        const originalFinish = session.finish.bind(session);
        session.finish = (reason) => {
          overlay.remove();
          originalFinish(reason);
        };
        return session;
      } catch (error) {
        overlay.remove();
        throw error;
      }
    },
  });
}

boot();
