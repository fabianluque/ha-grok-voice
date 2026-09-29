export interface HassAuthLike {
  data?: { access_token?: string; hassUrl?: string };
  accessToken?: string;
  wsUrl?: string;
}

export interface HassLike {
  callWS(message: unknown): Promise<IngressInfo>;
  auth?: HassAuthLike;
  connection?: { options?: { auth?: HassAuthLike } };
  hassUrl?: string | ((path?: string) => string);
  config?: { internal_url?: string | null; external_url?: string | null };
}

interface IngressInfo {
  data?: { ingress_entry?: string };
  ingress_entry?: string;
}

export const DEBUG_TOKEN_KEY = "grok-voice-ha-token";

export type TokenSource = "ingress" | "hass" | "hassConnection" | "hassTokens" | "saved" | "explicit" | "none";

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

export function isOpenWebUiPath(pathname: string): boolean {
  return pathname.includes("/api/hassio_ingress/");
}

export function authHandshake(options: {
  ingress: boolean;
  token: string;
}): { type: "auth"; via?: "ingress"; token?: string } {
  if (options.ingress && !options.token.trim()) {
    return { type: "auth", via: "ingress" };
  }
  return { type: "auth", token: options.token };
}

export function shouldOfferTokenField(input: { pathname: string; authFailed: boolean }): boolean {
  if (input.authFailed) {
    return true;
  }
  return !isOpenWebUiPath(input.pathname);
}

/** Kiosk Satellite's secure-context proxy listens on loopback :2325. */
export const KIOSK_SATELLITE_PROXY_PORT = "2325";

/**
 * This install's Home Assistant LAN. KS remaps auth.hassUrl / wsUrl /
 * hass.hassUrl / config URLs to 127.0.0.1:2325, so none of those can carry
 * an ingress WebSocket to the add-on.
 * TODO: discover the installed HA LAN URL instead of hardcoding this host.
 */
export const LAN_HA_FALLBACK_URL = "http://192.168.86.38:8123";

export type VoiceSocketHostSource =
  | "page"
  | "auth.hassUrl"
  | "auth.wsUrl"
  | "connection.hassUrl"
  | "connection.wsUrl"
  | "hass.hassUrl"
  | "config.internal_url"
  | "config.external_url"
  | "lan-fallback";

export interface VoiceSocketAuthority {
  protocol: string;
  host: string;
  source: VoiceSocketHostSource;
}

export function hostnameOf(host: string): string {
  const raw = host.trim();
  if (!raw) {
    return "";
  }
  try {
    const url = new URL(raw.includes("://") ? raw : `http://${raw}`);
    return url.hostname.toLowerCase();
  } catch {
    const noPath = raw.split("/")[0] ?? raw;
    const noBrackets = noPath.replace(/^\[|\]$/g, "");
    const colon = noBrackets.lastIndexOf(":");
    if (colon > 0 && noBrackets.indexOf(":") === colon) {
      return noBrackets.slice(0, colon).toLowerCase();
    }
    return noBrackets.toLowerCase();
  }
}

export function isLoopbackHostname(hostname: string): boolean {
  const host = hostname.trim().toLowerCase().replace(/^\[|\]$/g, "");
  return host === "127.0.0.1" || host === "localhost" || host === "::1";
}

export function isKioskSatelliteProxyHost(host: string): boolean {
  try {
    const url = new URL(host.includes("://") ? host : `http://${host}`);
    return isLoopbackHostname(url.hostname) && url.port === KIOSK_SATELLITE_PROXY_PORT;
  } catch {
    return false;
  }
}

/** Page origins that cannot carry Supervisor ingress WebSockets to the add-on. */
export function pageHostNeedsHaIngressHost(host: string): boolean {
  return isLoopbackHostname(hostnameOf(host)) || isKioskSatelliteProxyHost(host);
}

function authorityFromUrl(value: string | null | undefined): { protocol: string; host: string } | null {
  if (!value || typeof value !== "string") {
    return null;
  }
  const raw = value.trim();
  if (!raw) {
    return null;
  }
  try {
    const url = new URL(raw.includes("://") ? raw : `http://${raw}`);
    if (!url.hostname) {
      return null;
    }
    return { protocol: url.protocol, host: url.host };
  } catch {
    return null;
  }
}

function hassUrlValue(hassUrl: HassLike["hassUrl"]): string {
  if (typeof hassUrl === "function") {
    try {
      return hassUrl("/") || hassUrl() || "";
    } catch {
      return "";
    }
  }
  return hassUrl || "";
}

function hassAuthorities(hass: HassLike): VoiceSocketAuthority[] {
  const raw: Array<[VoiceSocketHostSource, string | null | undefined]> = [
    ["auth.hassUrl", hass.auth?.data?.hassUrl],
    ["auth.wsUrl", hass.auth?.wsUrl],
    ["connection.hassUrl", hass.connection?.options?.auth?.data?.hassUrl],
    ["connection.wsUrl", hass.connection?.options?.auth?.wsUrl],
    ["hass.hassUrl", hassUrlValue(hass.hassUrl)],
    ["config.internal_url", hass.config?.internal_url],
    ["config.external_url", hass.config?.external_url],
  ];
  const out: VoiceSocketAuthority[] = [];
  for (const [source, value] of raw) {
    const authority = authorityFromUrl(value);
    if (authority) {
      out.push({ ...authority, source });
    }
  }
  return out;
}

/**
 * Ingress WebSockets from a Kiosk Satellite loopback dashboard never reach
 * the add-on. Prefer a non-loopback Home Assistant host. When KS has remapped
 * every hass authority to 127.0.0.1:2325, use the LAN fallback.
 */
export function resolveVoiceSocketAuthority(
  pageProtocol: string,
  pageHost: string,
  hass?: HassLike | null,
): VoiceSocketAuthority {
  const page: VoiceSocketAuthority = { protocol: pageProtocol, host: pageHost, source: "page" };
  if (!pageHostNeedsHaIngressHost(pageHost)) {
    return page;
  }
  const usable = hass
    ? hassAuthorities(hass).find((candidate) => !isLoopbackHostname(hostnameOf(candidate.host)))
    : undefined;
  if (usable) {
    return usable;
  }
  const fallback = authorityFromUrl(LAN_HA_FALLBACK_URL);
  return fallback ? { ...fallback, source: "lan-fallback" } : page;
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

/**
 * Store installs register as `{repo_hash}_grok_voice_agent`. This HA host's
 * installed slug is `b4d5c281_grok_voice_agent`. TODO: discover the installed
 * slug from `/addons` instead of hardcoding one repo hash.
 */
export const VOICE_ADDON_SLUGS = ["b4d5c281_grok_voice_agent", "grok_voice_agent"] as const;

function supervisorDetail(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (error && typeof error === "object") {
    return JSON.stringify(error);
  }
  return String(error);
}

export async function resolveVoiceSocketUrl(hass: HassLike, pageProtocol: string, host: string): Promise<string> {
  let lastDetail = "";
  for (const slug of VOICE_ADDON_SLUGS) {
    try {
      const info = await hass.callWS({
        type: "supervisor/api",
        endpoint: `/addons/${slug}/info`,
        method: "get",
      });
      const entry = info.data?.ingress_entry || info.ingress_entry;
      if (entry) {
        const authority = resolveVoiceSocketAuthority(pageProtocol, host, hass);
        console.log(`[Grok Voice] duplex host ${authority.host} via ${authority.source}`);
        return voiceSocketUrl(authority.protocol, authority.host, entry);
      }
      lastDetail = "Grok Voice ingress is not available for this user";
    } catch (error) {
      lastDetail = supervisorDetail(error);
    }
  }
  if (lastDetail === "Grok Voice ingress is not available for this user") {
    throw new Error(lastDetail);
  }
  throw new Error(`Grok Voice ingress lookup failed: ${lastDetail}`);
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
