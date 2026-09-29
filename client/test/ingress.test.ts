import { describe, expect, it } from "vitest";
import {
  accessToken,
  pageVoiceSocketUrl,
  readHassTokens,
  resolveAccessToken,
  saveDebugToken,
  savedDebugToken,
  tokenFromHassConnection,
  voiceSocketUrl,
} from "../src/ingress";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    getItem(key: string) {
      return key in data ? data[key] : null;
    },
    setItem(key: string, value: string) {
      data[key] = value;
    },
    removeItem(key: string) {
      delete data[key];
    },
  };
}

describe("voice socket URLs", () => {
  it("keeps the kiosk ingress entry including its trailing slash", () => {
    expect(voiceSocketUrl("https:", "homeassistant.local:8123", "/api/hassio_ingress/abc")).toBe(
      "wss://homeassistant.local:8123/api/hassio_ingress/abc/",
    );
  });

  it("uses the page directory so ingress and debug ports share one handshake", () => {
    expect(pageVoiceSocketUrl("http:", "127.0.0.1:8080", "/")).toBe("ws://127.0.0.1:8080/");
    expect(pageVoiceSocketUrl("http:", "127.0.0.1:8080", "/index.html")).toBe("ws://127.0.0.1:8080/");
    expect(pageVoiceSocketUrl("https:", "homeassistant.local:8123", "/api/hassio_ingress/abc")).toBe(
      "wss://homeassistant.local:8123/api/hassio_ingress/abc/",
    );
    expect(pageVoiceSocketUrl("https:", "homeassistant.local:8123", "/api/hassio_ingress/abc/")).toBe(
      "wss://homeassistant.local:8123/api/hassio_ingress/abc/",
    );
  });
});

describe("browser access tokens", () => {
  it("reads the same hass token fields the kiosk overlay uses", () => {
    expect(
      accessToken({
        callWS: async () => ({}),
        auth: { data: { access_token: "from-hass" } },
      }),
    ).toBe("from-hass");
    expect(
      accessToken({
        callWS: async () => ({}),
        connection: { options: { auth: { accessToken: "from-connection" } } },
      }),
    ).toBe("from-connection");
  });

  it("prefers the live hass session, then hassTokens, then a saved debug token", () => {
    const storage = memoryStorage({
      hassTokens: JSON.stringify({ access_token: "stored-ha" }),
    });
    saveDebugToken("saved-debug", storage);
    expect(
      resolveAccessToken({
        hass: { callWS: async () => ({}), auth: { data: { access_token: "live" } } },
        storage,
        explicit: "typed",
      }),
    ).toEqual({ token: "live", source: "hass" });
    expect(resolveAccessToken({ storage })).toEqual({ token: "stored-ha", source: "hassTokens" });
    expect(resolveAccessToken({ storage: memoryStorage(), explicit: " typed " })).toEqual({
      token: "typed",
      source: "explicit",
    });
    const debugOnly = memoryStorage();
    saveDebugToken("saved-debug", debugOnly);
    expect(resolveAccessToken({ storage: debugOnly })).toEqual({ token: "saved-debug", source: "saved" });
    expect(readHassTokens(memoryStorage({ hassTokens: "not-json" }))).toBe("");
    expect(savedDebugToken(debugOnly)).toBe("saved-debug");
  });

  it("reads hassConnection from this window or its parent", async () => {
    const parent = {
      hassConnection: Promise.resolve({ auth: { data: { access_token: "parent-token" } } }),
    };
    await expect(tokenFromHassConnection({ parent, hassConnection: undefined })).resolves.toBe("parent-token");
    await expect(
      tokenFromHassConnection({
        hassConnection: Promise.resolve({ auth: { accessToken: "local-token" } }),
      }),
    ).resolves.toBe("local-token");
  });
});
