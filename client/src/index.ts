import {
  describeArea,
  immediateKioskArea,
  immediateKioskDevice,
  peekCachedKioskArea,
  prefetchKioskArea,
} from "./area";
import { createBrowserSession } from "./browser";
import {
  accessToken,
  authModeForUrl,
  configuredDuplexLanHost,
  describeDuplexChoice,
  hostnameOf,
  kioskScriptHost,
  kioskScriptOrigin,
  pageHass,
  parseDebugPort,
  prefetchKioskConfig,
  resolveKioskVoiceSocket,
} from "./ingress";
import { mountKioskStatus, voiceStatusFromMessage } from "./kiosk-status";
import type { NativeAssistHass } from "./native-assist";
import { installGrokVoice, type KioskApi, type WakeHost } from "./wake";

interface KioskWindow extends Window {
  kioskSatellite?: KioskApi;
  GROK_VOICE_URL?: string;
  GROK_VOICE_DEBUG_PORT?: number | string;
  GROK_VOICE_DUPLEX_LAN_HOST?: string;
  GROK_VOICE_AREA?: string;
  GROK_VOICE_AREA_ID?: string;
  __grokVoiceInstalled?: boolean;
}

function boot(): void {
  const page = window as KioskWindow;
  if (page.__grokVoiceInstalled) {
    return;
  }
  page.__grokVoiceInstalled = true;
  const kiosk = page.kioskSatellite;
  void prefetchKioskConfig({ origin: kioskScriptOrigin(document) });
  const areaInput = () => ({
    kiosk,
    hass: pageHass() as NativeAssistHass | null,
    explicit: {
      area: (window as KioskWindow).GROK_VOICE_AREA,
      areaId: (window as KioskWindow).GROK_VOICE_AREA_ID,
    },
  });
  void prefetchKioskArea(areaInput());
  let session: { finish(reason: string): void } | null = null;
  let overlay: ReturnType<typeof mountKioskStatus> | null = null;
  const showOverlay = () => {
    if (!overlay) {
      overlay = mountKioskStatus(document, {
        onDismiss: () => session?.finish("done"),
      });
    } else {
      overlay.set("listening");
    }
    return overlay;
  };
  const hideOverlay = () => {
    overlay?.remove();
    overlay = null;
  };
  installGrokVoice({
    kiosk,
    events: window,
    host: window as Window & WakeHost,
    document,
    hass: () => pageHass() as NativeAssistHass | null,
    onWakeVisual: showOverlay,
    onWakeEnd: () => {
      session = null;
      hideOverlay();
    },
    openSession: async () => {
      const status = showOverlay();
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
              lanHost: configuredDuplexLanHost(page.GROK_VOICE_DUPLEX_LAN_HOST),
              scriptHost: kioskScriptHost(document),
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
      const device = immediateKioskDevice();
      void prefetchKioskArea(areaInput());
      console.log(`[Grok Voice] Area ${describeArea(area)}`);
      console.log(`[Grok Voice] Device ${device.name} id=${device.id}`);
      const created = await createBrowserSession({
        url,
        token,
        ingress: authMode === "ingress",
        area,
        device,
        onTranscript: (role, text, final, itemId) => {
          status.addMessage(role, text, final === true, itemId);
        },
        onServerText: (message) => {
          status.handleDuplex(message.type);
          const next = voiceStatusFromMessage(message.type);
          if (next) {
            status.set(next);
          }
        },
      });
      session = created.session;
      return created.session;
    },
  });
}

boot();
