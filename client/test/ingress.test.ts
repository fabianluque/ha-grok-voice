import { describe, expect, it } from "vitest";
import {
  accessToken,
  authHandshake,
  isOpenWebUiPath,
  pageVoiceSocketUrl,
  readHassTokens,
  resolveAccessToken,
  resolveVoiceSocketUrl,
  saveDebugToken,
  savedDebugToken,
  shouldOfferTokenField,
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

  it("uses the store-prefixed slug when that addon info call succeeds first", async () => {
    const endpoints: string[] = [];
    await expect(
      resolveVoiceSocketUrl(
        {
          callWS: async (message: unknown) => {
            const endpoint = (message as { endpoint?: string }).endpoint ?? "";
            endpoints.push(endpoint);
            expect(endpoint).toBe("/addons/b4d5c281_grok_voice_agent/info");
            return { data: { ingress_entry: "/api/hassio_ingress/abc" } };
          },
        },
        "https:",
        "homeassistant.local:8123",
      ),
    ).resolves.toBe("wss://homeassistant.local:8123/api/hassio_ingress/abc/");
    expect(endpoints).toEqual(["/addons/b4d5c281_grok_voice_agent/info"]);
  });

  it("falls back to grok_voice_agent when the store-prefixed slug is missing", async () => {
    const endpoints: string[] = [];
    await expect(
      resolveVoiceSocketUrl(
        {
          callWS: async (message: unknown) => {
            const endpoint = (message as { endpoint?: string }).endpoint ?? "";
            endpoints.push(endpoint);
            if (endpoint === "/addons/b4d5c281_grok_voice_agent/info") {
              throw { code: "unknown_error", message: "App b4d5c281_grok_voice_agent does not exist" };
            }
            expect(endpoint).toBe("/addons/grok_voice_agent/info");
            return { data: { ingress_entry: "/api/hassio_ingress/fallback" } };
          },
        },
        "https:",
        "homeassistant.local:8123",
      ),
    ).resolves.toBe("wss://homeassistant.local:8123/api/hassio_ingress/fallback/");
    expect(endpoints).toEqual(["/addons/b4d5c281_grok_voice_agent/info", "/addons/grok_voice_agent/info"]);
  });

  it("includes the last supervisor message when every slug is missing", async () => {
    const endpoints: string[] = [];
    await expect(
      resolveVoiceSocketUrl(
        {
          callWS: async (message: unknown) => {
            const endpoint = (message as { endpoint?: string }).endpoint ?? "";
            endpoints.push(endpoint);
            if (endpoint === "/addons/b4d5c281_grok_voice_agent/info") {
              throw { code: "unknown_error", message: "App b4d5c281_grok_voice_agent does not exist" };
            }
            throw { code: "unknown_error", message: "App grok_voice_agent does not exist" };
          },
        },
        "https:",
        "homeassistant.local:8123",
      ),
    ).rejects.toThrow(
      'Grok Voice ingress lookup failed: {"code":"unknown_error","message":"App grok_voice_agent does not exist"}',
    );
    expect(endpoints).toEqual(["/addons/b4d5c281_grok_voice_agent/info", "/addons/grok_voice_agent/info"]);
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

describe("Open Web UI session", () => {
  it("treats the ingress iframe as a signed-in session and hides the token field", () => {
    const path = "/api/hassio_ingress/OMwnLs6XGmfQ-r0Fn5pc_Fblx9OpR8BUERKIrvOBLuA/";
    expect(isOpenWebUiPath(path)).toBe(true);
    expect(isOpenWebUiPath("/")).toBe(false);
    expect(shouldOfferTokenField({ pathname: path, authFailed: false })).toBe(false);
    expect(shouldOfferTokenField({ pathname: path, authFailed: true })).toBe(true);
    expect(shouldOfferTokenField({ pathname: "/", authFailed: false })).toBe(true);
    expect(authHandshake({ ingress: true, token: "" })).toEqual({ type: "auth", via: "ingress" });
    expect(authHandshake({ ingress: true, token: "long-lived" })).toEqual({
      type: "auth",
      token: "long-lived",
    });
    expect(authHandshake({ ingress: false, token: "long-lived" })).toEqual({
      type: "auth",
      token: "long-lived",
    });
  });
});
