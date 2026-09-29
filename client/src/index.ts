import { createBrowserSession } from "./browser";
import { accessToken, pageHass, resolveVoiceSocketUrl } from "./ingress";
import { installGrokVoice, type KioskApi, type WakeHost } from "./wake";

interface KioskWindow extends Window {
  kioskSatellite?: KioskApi;
  GROK_VOICE_URL?: string;
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
    openSession: async () => {
      const hass = pageHass();
      const explicit = (window as KioskWindow).GROK_VOICE_URL;
      const url = explicit || (hass ? await resolveVoiceSocketUrl(hass, location.protocol, location.host) : "");
      if (!url || !hass) {
        throw new Error("Home Assistant ingress is not available on this page");
      }
      const token = accessToken(hass);
      const overlay = document.createElement("div");
      overlay.id = "grok-voice-overlay";
      overlay.style.cssText =
        "position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;color:white;font:16px sans-serif;text-shadow:0 1px 2px black;";
      document.body.appendChild(overlay);
      try {
        const { session } = await createBrowserSession({
          url,
          token,
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
