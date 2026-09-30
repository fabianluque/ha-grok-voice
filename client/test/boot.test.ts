import { describe, expect, it } from "vitest";
import { bootKioskClient, kioskClientScriptUrl, type BootWindow } from "../src/boot";
import { LAN_VOICE_SCRIPT_URL } from "../src/ingress";

function fakeHost(options: {
  protocol?: string;
  host?: string;
  hassUrl?: string;
  explicit?: string;
  debugPort?: number | string;
  lanHost?: string;
  alreadyBooted?: boolean;
}): { page: BootWindow; scripts: Array<{ src: string; async: boolean; dataset: { grokVoice?: string } }> } {
  const scripts: Array<{ src: string; async: boolean; dataset: { grokVoice?: string } }> = [];
  const hass = options.hassUrl
    ? { callWS: async () => ({}), auth: { data: { hassUrl: options.hassUrl } } }
    : null;
  const homeAssistant = hass ? { hass } : null;
  const page: BootWindow = {
    GROK_VOICE_SCRIPT: options.explicit,
    GROK_VOICE_DEBUG_PORT: options.debugPort,
    GROK_VOICE_DUPLEX_LAN_HOST: options.lanHost,
    __grokVoiceBoot: options.alreadyBooted,
    location: { protocol: options.protocol ?? "http:", host: options.host ?? "127.0.0.1:2325" },
    document: {
      querySelector(selectors: string) {
        if (selectors === "home-assistant") {
          return homeAssistant;
        }
        if (selectors.includes("data-grok-voice")) {
          return scripts[0] ?? null;
        }
        return null;
      },
      querySelectorAll(selectors: string) {
        if (selectors.includes("script")) {
          return scripts;
        }
        return [];
      },
      createElement(tagName: string) {
        if (tagName !== "script") {
          throw new Error(tagName);
        }
        return { src: "", async: false, dataset: {} };
      },
      head: {
        appendChild(node) {
          scripts.push(node);
        },
      },
      documentElement: {
        appendChild(node) {
          scripts.push(node);
        },
      },
    },
  };
  return { page, scripts };
}

describe("kiosk bootstrap", () => {
  it("injects grok-voice.js from the add-on debug port using the HA LAN host", () => {
    const { page, scripts } = fakeHost({ hassUrl: "http://192.168.1.10:8123" });
    expect(kioskClientScriptUrl(page)).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(bootKioskClient(page)).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(scripts).toHaveLength(1);
    expect(scripts[0]?.src).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(scripts[0]?.async).toBe(true);
    expect(scripts[0]?.dataset.grokVoice).toBe("client");
    expect(bootKioskClient(page)).toBeNull();
    expect(scripts).toHaveLength(1);
  });

  it("uses the LAN fallback when the KS page host is loopback and hass is missing", () => {
    const { page, scripts } = fakeHost({});
    expect(bootKioskClient(page)).toBe(LAN_VOICE_SCRIPT_URL);
    expect(scripts[0]?.src).toBe(LAN_VOICE_SCRIPT_URL);
  });

  it("honors GROK_VOICE_SCRIPT and GROK_VOICE_DEBUG_PORT", () => {
    const custom = fakeHost({ explicit: "http://192.168.1.10:8080/grok-voice.js?dev=1" });
    expect(bootKioskClient(custom.page)).toBe("http://192.168.1.10:8080/grok-voice.js?dev=1");
    const port = fakeHost({ hassUrl: "http://192.168.1.10:8123", debugPort: 9099 });
    expect(kioskClientScriptUrl(port.page)).toBe("http://192.168.1.10:9099/grok-voice.js");
  });

  it("loads grok-voice.js from GROK_VOICE_DUPLEX_LAN_HOST when hass is loopback", () => {
    const { page, scripts } = fakeHost({ lanHost: "192.168.86.38" });
    expect(bootKioskClient(page)).toBe("http://192.168.86.38:8080/grok-voice.js");
    expect(scripts[0]?.src).toBe("http://192.168.86.38:8080/grok-voice.js");
  });

  it("does not inject twice when a client script tag is already on the page", () => {
    const { page, scripts } = fakeHost({ hassUrl: "http://192.168.1.10:8123" });
    scripts.push({ src: "http://192.168.1.10:8080/grok-voice.js", async: true, dataset: { grokVoice: "client" } });
    expect(bootKioskClient(page)).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(scripts).toHaveLength(1);
  });
});
