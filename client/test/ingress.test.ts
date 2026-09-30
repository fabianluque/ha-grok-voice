import { describe, expect, it, vi } from "vitest";
import {
  accessToken,
  authHandshake,
  authModeForUrl,
  debugVoiceScriptUrl,
  debugVoiceSocketUrl,
  describeDuplexChoice,
  KIOSK_CLIENT_PATH,
  isKioskSatelliteProxyHost,
  isLoopbackHostname,
  isOpenWebUiPath,
  LAN_HA_FALLBACK_HOST,
  LAN_VOICE_DEBUG_URL,
  LAN_VOICE_SCRIPT_URL,
  pageHostNeedsHaIngressHost,
  pageVoiceSocketUrl,
  parseDebugPort,
  readHassTokens,
  resolveAccessToken,
  resolveKioskClientScript,
  resolveKioskVoiceSocket,
  resolveVoiceSocketAuthority,
  resolveVoiceSocketUrl,
  saveDebugToken,
  savedDebugToken,
  shouldOfferTokenField,
  tokenFromHassConnection,
  VOICE_DEBUG_PORT,
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

  it("replaces a loopback, 127.0.0.1, or Kiosk Satellite proxy host with the Home Assistant host", () => {
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname("::1")).toBe(true);
    expect(isLoopbackHostname("192.168.1.10")).toBe(false);
    expect(isKioskSatelliteProxyHost("127.0.0.1:2325")).toBe(true);
    expect(isKioskSatelliteProxyHost("localhost:2325")).toBe(true);
    expect(isKioskSatelliteProxyHost("127.0.0.1:8123")).toBe(false);
    expect(isKioskSatelliteProxyHost("192.168.1.10:2325")).toBe(false);
    expect(pageHostNeedsHaIngressHost("127.0.0.1")).toBe(true);
    expect(pageHostNeedsHaIngressHost("127.0.0.1:2325")).toBe(true);
    expect(pageHostNeedsHaIngressHost("localhost:2325")).toBe(true);
    expect(pageHostNeedsHaIngressHost("192.168.1.10:8123")).toBe(false);

    const hass = {
      callWS: async () => ({}),
      auth: { data: { hassUrl: "http://192.168.1.10:8123", access_token: "t" } },
    };
    expect(resolveVoiceSocketAuthority("http:", "127.0.0.1:2325", hass)).toEqual({
      protocol: "http:",
      host: "192.168.1.10:8123",
      source: "auth.hassUrl",
    });
    expect(resolveVoiceSocketAuthority("http:", "127.0.0.1", hass)).toEqual({
      protocol: "http:",
      host: "192.168.1.10:8123",
      source: "auth.hassUrl",
    });
    expect(resolveVoiceSocketAuthority("http:", "localhost:2325", hass)).toEqual({
      protocol: "http:",
      host: "192.168.1.10:8123",
      source: "auth.hassUrl",
    });
    expect(resolveVoiceSocketAuthority("https:", "homeassistant.local:8123", hass)).toEqual({
      protocol: "https:",
      host: "homeassistant.local:8123",
      source: "page",
    });
  });

  it("opens the add-on debug port on the Home Assistant host from a KS loopback dashboard", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    expect(VOICE_DEBUG_PORT).toBe(8080);
    expect(KIOSK_CLIENT_PATH).toBe("/grok-voice.js");
    expect(debugVoiceSocketUrl("http:", "192.168.1.10:8123")).toBe("ws://192.168.1.10:8080/");
    expect(debugVoiceScriptUrl("http:", "192.168.1.10:8123")).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(LAN_VOICE_SCRIPT_URL).toBe(`http://${LAN_HA_FALLBACK_HOST}:8080/grok-voice.js`);
    expect(
      resolveKioskClientScript({
        hass: {
          callWS: async () => ({}),
          auth: { data: { hassUrl: "http://192.168.1.10:8123", access_token: "t" } },
        },
        pageProtocol: "http:",
        pageHost: "127.0.0.1:2325",
      }).url,
    ).toBe("http://192.168.1.10:8080/grok-voice.js");
    expect(
      resolveKioskClientScript({
        pageProtocol: "http:",
        pageHost: "127.0.0.1:2325",
        explicit: "http://192.168.1.10:8080/grok-voice.js?dev=1",
      }).authority.source,
    ).toBe("explicit");
    expect(parseDebugPort("9090")).toBe(9090);
    expect(parseDebugPort("nope")).toBe(8080);
    await expect(
      resolveVoiceSocketUrl(
        {
          callWS: async () => {
            throw new Error("kiosk inject must not look up ingress");
          },
          auth: { data: { hassUrl: "http://192.168.1.10:8123", access_token: "t" } },
        },
        "http:",
        "127.0.0.1:2325",
      ),
    ).resolves.toBe("ws://192.168.1.10:8080/");
    expect(log).toHaveBeenCalledWith("[Grok Voice] duplex host 192.168.1.10:8080 via auth.hassUrl auth token");
    log.mockRestore();
  });

  it("uses the Home Assistant host when the page host is 127.0.0.1", async () => {
    await expect(
      resolveVoiceSocketUrl(
        {
          callWS: async () => ({}),
          auth: { data: { hassUrl: "http://192.168.1.10:8123" } },
        },
        "http:",
        "127.0.0.1",
      ),
    ).resolves.toBe("ws://192.168.1.10:8080/");
  });

  it("uses config.internal_url when the KS page host and hassUrl are both loopback", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const hass = {
      callWS: async () => ({}),
      auth: {
        data: { hassUrl: "http://127.0.0.1:2325" },
        wsUrl: "ws://127.0.0.1:2325/api/websocket",
      },
      hassUrl: () => "http://127.0.0.1:2325/",
      config: { internal_url: "http://192.168.1.10:8123", external_url: "http://127.0.0.1:2325" },
    };
    expect(resolveVoiceSocketAuthority("http:", "127.0.0.1:2325", hass)).toEqual({
      protocol: "http:",
      host: "192.168.1.10:8123",
      source: "config.internal_url",
    });
    await expect(resolveVoiceSocketUrl(hass, "http:", "127.0.0.1:2325")).resolves.toBe("ws://192.168.1.10:8080/");
    expect(log).toHaveBeenCalledWith("[Grok Voice] duplex host 192.168.1.10:8080 via config.internal_url auth token");
    log.mockRestore();
  });

  it("uses connection.host when every other hass authority is the KS loopback proxy", () => {
    const hass = {
      callWS: async () => ({}),
      auth: { data: { hassUrl: "http://127.0.0.1:2325" } },
      connection: { host: "192.168.1.10" },
      config: { internal_url: "http://127.0.0.1:2325" },
    };
    expect(resolveVoiceSocketAuthority("http:", "127.0.0.1:2325", hass)).toEqual({
      protocol: "http:",
      host: "192.168.1.10",
      source: "connection.host",
    });
    expect(
      resolveKioskVoiceSocket({
        hass,
        pageProtocol: "http:",
        pageHost: "127.0.0.1:2325",
      }),
    ).toMatchObject({
      url: "ws://192.168.1.10:8080/",
      authMode: "token",
      debugPort: 8080,
    });
  });

  it("uses the LAN fallback debug port when every hass authority is also the KS loopback proxy", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const hass = {
      callWS: async () => ({}),
      auth: {
        data: { hassUrl: "http://127.0.0.1:2325" },
        wsUrl: "ws://127.0.0.1:2325/api/websocket",
      },
      connection: { options: { auth: { data: { hassUrl: "http://127.0.0.1:2325" }, wsUrl: "ws://127.0.0.1:2325/api/websocket" } } },
      hassUrl: () => "http://127.0.0.1:2325/",
      config: { internal_url: "http://127.0.0.1:2325", external_url: "http://127.0.0.1:2325" },
    };
    expect(resolveVoiceSocketAuthority("http:", "127.0.0.1:2325", hass)).toEqual({
      protocol: "http:",
      host: LAN_HA_FALLBACK_HOST,
      source: "lan-fallback",
    });
    expect(LAN_VOICE_DEBUG_URL).toBe(`ws://${LAN_HA_FALLBACK_HOST}:8080/`);
    const url = await resolveVoiceSocketUrl(hass, "http:", "127.0.0.1:2325");
    expect(url).toBe(LAN_VOICE_DEBUG_URL);
    expect(url).not.toMatch(/hassio_ingress|:8123\//);
    expect(log).toHaveBeenCalledWith(
      `[Grok Voice] duplex host ${LAN_HA_FALLBACK_HOST}:8080 via lan-fallback auth token`,
    );
    log.mockRestore();
  });

  it("honors GROK_VOICE_DEBUG_PORT on the kiosk debug socket", () => {
    const resolved = resolveKioskVoiceSocket({
      hass: { callWS: async () => ({}), auth: { data: { hassUrl: "http://192.168.1.10:8123" } } },
      pageProtocol: "http:",
      pageHost: "127.0.0.1:2325",
      debugPort: 9099,
    });
    expect(resolved.url).toBe("ws://192.168.1.10:9099/");
    expect(resolved.authMode).toBe("token");
    expect(
      describeDuplexChoice({
        authority: resolved.authority,
        host: "192.168.1.10:9099",
        authMode: resolved.authMode,
      }),
    ).toBe("[Grok Voice] duplex host 192.168.1.10:9099 via auth.hassUrl auth token");
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
    const path = "/api/hassio_ingress/exampleIngressToken/";
    expect(isOpenWebUiPath(path)).toBe(true);
    expect(isOpenWebUiPath("/")).toBe(false);
    expect(shouldOfferTokenField({ pathname: path, authFailed: false })).toBe(false);
    expect(shouldOfferTokenField({ pathname: path, authFailed: true })).toBe(true);
    expect(shouldOfferTokenField({ pathname: "/", authFailed: false })).toBe(true);
    expect(authHandshake({ ingress: true, token: "" })).toEqual({ type: "auth", via: "ingress" });
    expect(authHandshake({ ingress: true, token: "long-lived" })).toEqual({
      type: "auth",
      via: "ingress",
    });
    expect(
      authHandshake({
        ingress: false,
        token: "long-lived",
        url: "ws://192.168.1.10:8123/api/hassio_ingress/exampleIngressToken/",
      }),
    ).toEqual({ type: "auth", via: "ingress" });
    expect(authModeForUrl("ws://192.168.1.10:8080/")).toBe("token");
    expect(authHandshake({ ingress: false, token: "long-lived" })).toEqual({
      type: "auth",
      token: "long-lived",
    });
    expect(
      authHandshake({
        ingress: false,
        token: "long-lived",
        url: "ws://192.168.1.10:8080/",
        area: { name: "Attic", id: "attic" },
        device: { name: "Attic Dashboard", id: "attic-tablet" },
      }),
    ).toEqual({
      type: "auth",
      token: "long-lived",
      area: { name: "Attic", id: "attic" },
      device: { name: "Attic Dashboard", id: "attic-tablet" },
    });
  });
});
