import {
  describeArea,
  immediateKioskArea,
  peekCachedKioskArea,
  prefetchKioskArea,
} from "./area";
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
import { mountKioskStatus, voiceStatusFromMessage } from "./kiosk-status";
import type { NativeAssistHass } from "./native-assist";
import { installGrokVoice, type KioskApi, type WakeHost } from "./wake";

interface KioskWindow extends Window {
  kioskSatellite?: KioskApi;
  GROK_VOICE_URL?: string;
  GROK_VOICE_DEBUG_PORT?: number | string;
  GROK_VOICE_AREA?: string;
  GROK_VOICE_AREA_ID?: string;
}

function boot(): void {
  const kiosk = (window as KioskWindow).kioskSatellite;
  const areaInput = () => ({
    kiosk,
    hass: pageHass() as NativeAssistHass | null,
    explicit: {
      area: (window as KioskWindow).GROK_VOICE_AREA,
      areaId: (window as KioskWindow).GROK_VOICE_AREA_ID,
    },
  });
  void prefetchKioskArea(areaInput());
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
      const area = immediateKioskArea({
        explicit: areaInput().explicit,
        cached: peekCachedKioskArea(),
      });
      void prefetchKioskArea(areaInput());
      console.log(`[Grok Voice] Area ${describeArea(area)}`);
      const status = mountKioskStatus(document);
      try {
        const { session } = await createBrowserSession({
          url,
          token,
          ingress: authMode === "ingress",
          area,
          onTranscript: (role, text, final) => {
            status.addMessage(role, text, final !== false);
          },
          onServerText: (message) => {
            const next = voiceStatusFromMessage(message.type);
            if (next) {
              status.set(next);
            }
          },
        });
        const originalFinish = session.finish.bind(session);
        session.finish = (reason) => {
          status.remove();
          originalFinish(reason);
        };
        return session;
      } catch (error) {
        status.remove();
        throw error;
      }
    },
  });
}

boot();
