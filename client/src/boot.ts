import {
  pageHass,
  parseDebugPort,
  resolveKioskClientScript,
  type HassLike,
} from "./ingress";

export const BOOT_FLAG = "__grokVoiceBoot";
export const CLIENT_SCRIPT_MARK = "client";

export interface BootWindow {
  GROK_VOICE_SCRIPT?: string;
  GROK_VOICE_DEBUG_PORT?: number | string;
  __grokVoiceBoot?: boolean;
  location: { protocol: string; host: string };
  document: BootDocument;
}

interface BootDocument {
  querySelector(selectors: string): { hass?: HassLike } | null;
  createElement(tagName: string): BootScript;
  head?: { appendChild(node: BootScript): void } | null;
  documentElement: { appendChild(node: BootScript): void };
}

interface BootScript {
  src: string;
  async: boolean;
  dataset: { grokVoice?: string };
}

function existingClientScript(doc: BootDocument): BootScript | null {
  const found = doc.querySelector(`script[data-grok-voice="${CLIENT_SCRIPT_MARK}"]`);
  return found as BootScript | null;
}

function injectClientScript(doc: BootDocument, src: string): void {
  if (existingClientScript(doc)) {
    return;
  }
  const script = doc.createElement("script");
  script.src = src;
  script.async = true;
  script.dataset.grokVoice = CLIENT_SCRIPT_MARK;
  (doc.head || doc.documentElement).appendChild(script);
}

/** Resolve ``http://<HA-LAN>:8080/grok-voice.js`` the same way duplex finds ``ws://``. */
export function kioskClientScriptUrl(host: BootWindow): string {
  return resolveKioskClientScript({
    hass: pageHass(host.document as unknown as Document),
    pageProtocol: host.location.protocol,
    pageHost: host.location.host,
    debugPort: parseDebugPort(host.GROK_VOICE_DEBUG_PORT),
    explicit: host.GROK_VOICE_SCRIPT,
  }).url;
}

/**
 * One-time attic inject: load the add-on's current grok-voice.js.
 * Dining-room dashboards that never call this stay untouched.
 */
export function bootKioskClient(host: BootWindow): string | null {
  if (host.__grokVoiceBoot) {
    return null;
  }
  host.__grokVoiceBoot = true;
  const src = kioskClientScriptUrl(host);
  injectClientScript(host.document, src);
  return src;
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  bootKioskClient(window as unknown as BootWindow);
}
