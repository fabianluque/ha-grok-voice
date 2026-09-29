export interface HassLike {
  callWS(message: unknown): Promise<IngressInfo>;
  auth?: { data?: { access_token?: string }; accessToken?: string };
  connection?: { options?: { auth?: { accessToken?: string } } };
}

interface IngressInfo {
  data?: { ingress_entry?: string };
  ingress_entry?: string;
}

export const DEBUG_TOKEN_KEY = "grok-voice-ha-token";

export type TokenSource = "hass" | "hassConnection" | "hassTokens" | "saved" | "explicit" | "none";

export interface ResolvedToken {
  token: string;
  source: TokenSource;
}

export interface TokenStorage {
  getItem(key: string): string | null;
  setItem?(key: string, value: string): void;
  removeItem?(key: string): void;
}

export function accessToken(hass: HassLike): string {
  return (
    hass.auth?.data?.access_token ||
    hass.auth?.accessToken ||
    hass.connection?.options?.auth?.accessToken ||
    ""
  );
}

export function pageHass(doc: Document = document): HassLike | null {
  const root = documentRoot(doc);
  return root?.hass ?? null;
}

export function discoverHass(win: Window = window): HassLike | null {
  const local = pageHass(win.document);
  if (local) {
    return local;
  }
  try {
    if (win.parent && win.parent !== win) {
      return pageHass(win.parent.document);
    }
  } catch {
    return null;
  }
  return null;
}

export function voiceSocketUrl(pageProtocol: string, host: string, ingressEntry: string): string {
  const proto = pageProtocol === "https:" ? "wss:" : "ws:";
  const path = ingressEntry.endsWith("/") ? ingressEntry : `${ingressEntry}/`;
  return `${proto}//${host}${path}`;
}

export function pageVoiceSocketUrl(pageProtocol: string, host: string, pathname: string): string {
  const proto = pageProtocol === "https:" ? "wss:" : "ws:";
  let path = pathname || "/";
  const last = path.split("/").pop() || "";
  if (last.includes(".")) {
    const slash = path.lastIndexOf("/");
    path = slash <= 0 ? "/" : path.slice(0, slash + 1);
  } else if (!path.endsWith("/")) {
    path = `${path}/`;
  }
  return `${proto}//${host}${path}`;
}

export async function resolveVoiceSocketUrl(hass: HassLike, pageProtocol: string, host: string): Promise<string> {
  const info = await hass.callWS({
    type: "supervisor/api",
    endpoint: "/addons/grok_voice_agent/info",
    method: "get",
  });
  const entry = info.data?.ingress_entry || info.ingress_entry;
  if (!entry) {
    throw new Error("Grok Voice ingress is not available for this user");
  }
  return voiceSocketUrl(pageProtocol, host, entry);
}

export function readHassTokens(storage: TokenStorage): string {
  const raw = storage.getItem("hassTokens");
  if (!raw) {
    return "";
  }
  try {
    const parsed = JSON.parse(raw) as { access_token?: string; accessToken?: string };
    return parsed.access_token || parsed.accessToken || "";
  } catch {
    return "";
  }
}

export function savedDebugToken(storage: TokenStorage): string {
  return (storage.getItem(DEBUG_TOKEN_KEY) || "").trim();
}

export function saveDebugToken(token: string, storage: TokenStorage): void {
  const trimmed = token.trim();
  if (!trimmed) {
    storage.removeItem?.(DEBUG_TOKEN_KEY);
    return;
  }
  storage.setItem?.(DEBUG_TOKEN_KEY, trimmed);
}

export function resolveAccessToken(input: {
  hass?: HassLike | null;
  explicit?: string;
  storage?: TokenStorage | null;
}): ResolvedToken {
  const hassToken = input.hass ? accessToken(input.hass) : "";
  if (hassToken) {
    return { token: hassToken, source: "hass" };
  }
  if (input.storage) {
    const fromHassStore = readHassTokens(input.storage);
    if (fromHassStore) {
      return { token: fromHassStore, source: "hassTokens" };
    }
  }
  const explicit = (input.explicit || "").trim();
  if (explicit) {
    return { token: explicit, source: "explicit" };
  }
  if (input.storage) {
    const saved = savedDebugToken(input.storage);
    if (saved) {
      return { token: saved, source: "saved" };
    }
  }
  return { token: "", source: "none" };
}

export async function tokenFromHassConnection(
  win: HassConnectionHost = window,
): Promise<string> {
  const windows: HassConnectionHost[] = [win];
  try {
    if (win.parent && win.parent !== win) {
      windows.push(win.parent as HassConnectionHost);
    }
  } catch {
    // Cross-origin iframe; parent is not readable.
  }
  for (const candidate of windows) {
    const pending = candidate.hassConnection;
    if (!pending) {
      continue;
    }
    try {
      const resolved = (await pending) as {
        auth?: { data?: { access_token?: string }; accessToken?: string };
      };
      const token = resolved?.auth?.data?.access_token || resolved?.auth?.accessToken || "";
      if (token) {
        return token;
      }
    } catch {
      continue;
    }
  }
  return "";
}

export interface HassConnectionHost {
  parent?: unknown;
  hassConnection?: Promise<unknown> | unknown;
}

function documentRoot(doc: Document): { hass?: HassLike } | null {
  return doc.querySelector("home-assistant") as { hass?: HassLike } | null;
}
