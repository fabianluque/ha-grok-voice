export interface HassAuthLike {
  data?: { access_token?: string; hassUrl?: string };
  accessToken?: string;
  wsUrl?: string;
}

export interface HassLike {
  callWS(message: unknown): Promise<IngressInfo>;
  auth?: HassAuthLike;
  connection?: { host?: string; options?: { auth?: HassAuthLike; host?: string } };
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

export type VoiceAuthMode = "token" | "ingress";

export function usesIngressAuth(url: string): boolean {
  try {
    return isOpenWebUiPath(new URL(url, "http://localhost").pathname);
  } catch {
    return url.includes("/api/hassio_ingress/");
  }
}

export function authModeForUrl(url: string, ingress = false): VoiceAuthMode {
  if (ingress || usesIngressAuth(url)) {
    return "ingress";
  }
  return "token";
}

export function authHandshake(options: {
  ingress: boolean;
  token: string;
  url?: string;
  area?: { id?: string; name?: string } | null;
  device?: { id?: string; name?: string } | null;
}): {
  type: "auth";
  via?: "ingress";
  token?: string;
  area?: { id?: string; name: string };
  device?: { id?: string; name: string };
} {
  const areaName = options.area?.name?.trim();
  const areaId = options.area?.id?.trim();
  const area = areaName || areaId ? { name: areaName || areaId!, id: areaId || undefined } : undefined;
  const deviceName = options.device?.name?.trim();
  const deviceId = options.device?.id?.trim();
  const device =
    deviceName || deviceId ? { name: deviceName || deviceId!, id: deviceId || undefined } : undefined;
  const identity = {
    ...(area ? { area } : {}),
    ...(device ? { device } : {}),
  };
  if (authModeForUrl(options.url || "", options.ingress) === "ingress") {
    return { type: "auth", via: "ingress", ...identity };
  }
  return { type: "auth", token: options.token, ...identity };
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
 * Last-resort Home Assistant hostname when the kiosk page is loopback
 * (Kiosk Satellite :2325) and hassUrl / internal_url are also loopback.
 * Kiosk duplex never uses Core :8123 `/api/hassio_ingress/` — Lovelace has
 * no ingress_session cookie. Prefer add-on ``duplex_lan_host`` (or
 * ``GROK_VOICE_DUPLEX_LAN_HOST`` / the script origin) so Fire tablets
 * that cannot resolve mDNS do not dead-end on ``homeassistant.local``.
 */
export const LAN_HA_FALLBACK_HOST = "homeassistant.local";

/** Add-on / inject override for the duplex debug-port hostname. */
export const DUPLEX_LAN_HOST_KEY = "GROK_VOICE_DUPLEX_LAN_HOST";

export const KIOSK_CONFIG_PATH = "/kiosk-config";

/** Add-on debug port mapped on the HA host. Token auth; not ingress. */
export const VOICE_DEBUG_PORT = 8080;

export const LAN_VOICE_DEBUG_URL = `ws://${LAN_HA_FALLBACK_HOST}:${VOICE_DEBUG_PORT}/`;

/** Packaged kiosk IIFE served by the add-on on the debug (and ingress) HTTP ports. */
export const KIOSK_CLIENT_PATH = "/grok-voice.js";

export const LAN_VOICE_SCRIPT_URL = `http://${LAN_HA_FALLBACK_HOST}:${VOICE_DEBUG_PORT}${KIOSK_CLIENT_PATH}`;

export type VoiceSocketHostSource =
  | "page"
  | "auth.hassUrl"
  | "auth.wsUrl"
  | "connection.host"
  | "connection.hassUrl"
  | "connection.wsUrl"
  | "hass.hassUrl"
  | "config.internal_url"
  | "config.external_url"
  | "lan-fallback"
  | "explicit"
  | "addon"
  | "script";

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

/** Bare ``homeassistant.local`` — Fire tablets often fail this mDNS name. */
export function isMdnsFallbackHostname(host: string): boolean {
  return hostnameOf(host) === LAN_HA_FALLBACK_HOST;
}

/**
 * Hostname/IP from the add-on option or an inject override.
 * Accepts ``192.168.86.38``, ``http://192.168.86.38:8123``, or a host:port.
 */
export function cleanDuplexLanHost(value: unknown): string {
  if (value == null) {
    return "";
  }
  const raw = String(value).trim();
  if (!raw) {
    return "";
  }
  const authority = authorityFromUrl(raw);
  const host = authority ? hostnameOf(authority.host) : hostnameOf(raw);
  if (!host || isLoopbackHostname(host)) {
    return "";
  }
  return host;
}

export function kioskScriptHost(doc: { querySelectorAll?(selectors: string): ArrayLike<{ src?: string }> } | null | undefined): string {
  return hostnameOf(kioskScriptOrigin(doc));
}

/** Origin that served grok-voice.js / kiosk-boot.js, when it is a real LAN host. */
export function kioskScriptOrigin(doc: { querySelectorAll?(selectors: string): ArrayLike<{ src?: string }> } | null | undefined): string {
  const scripts = doc?.querySelectorAll?.("script[src]");
  if (!scripts) {
    return "";
  }
  for (let i = 0; i < scripts.length; i += 1) {
    const src = scripts[i]?.src || "";
    if (!src || !(src.includes("grok-voice.js") || src.includes("kiosk-boot.js"))) {
      continue;
    }
    try {
      const url = new URL(src, "http://localhost");
      const host = url.hostname.toLowerCase();
      if (!host || isLoopbackHostname(host) || isMdnsFallbackHostname(host)) {
        continue;
      }
      return `${url.protocol}//${url.host}`;
    } catch {
      continue;
    }
  }
  return "";
}

let cachedAddonLanHost = "";

export function rememberAddonLanHost(host: unknown): string {
  cachedAddonLanHost = cleanDuplexLanHost(host);
  return cachedAddonLanHost;
}

export function peekAddonLanHost(): string {
  return cachedAddonLanHost;
}

export function resetAddonLanHost(): void {
  cachedAddonLanHost = "";
}

export function configuredDuplexLanHost(explicit?: unknown): string {
  return cleanDuplexLanHost(explicit) || cachedAddonLanHost;
}

export async function prefetchKioskConfig(input: {
  origin?: string;
  fetch?: (url: string) => Promise<{ ok: boolean; json(): Promise<unknown> }>;
}): Promise<string> {
  const origin = (input.origin || "").replace(/\/$/, "");
  if (!origin) {
    return cachedAddonLanHost;
  }
  const fetcher = input.fetch || (globalThis.fetch as typeof input.fetch);
  if (!fetcher) {
    return cachedAddonLanHost;
  }
  try {
    const response = await fetcher(`${origin}${KIOSK_CONFIG_PATH}`);
    if (!response.ok) {
      return cachedAddonLanHost;
    }
    const body = (await response.json()) as { duplex_lan_host?: unknown };
    return rememberAddonLanHost(body?.duplex_lan_host);
  } catch {
    return cachedAddonLanHost;
  }
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
    ["connection.host", hass.connection?.host || hass.connection?.options?.host],
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

function usableHassAuthorities(hass?: HassLike | null): VoiceSocketAuthority[] {
  if (!hass) {
    return [];
  }
  return hassAuthorities(hass).filter((candidate) => !isLoopbackHostname(hostnameOf(candidate.host)));
}

/**
 * Find a non-loopback HA hostname for the add-on debug port. Never used to
 * open Core :8123 ingress — that path needs a cookie Lovelace does not have.
 *
 * Prefer an explicit add-on ``duplex_lan_host`` (or inject override). Then
 * existing HA connection / config hosts. Skip bare ``homeassistant.local``
 * when a real LAN IP or hostname is available (script origin or hass).
 */
export function resolveVoiceSocketAuthority(
  pageProtocol: string,
  pageHost: string,
  hass?: HassLike | null,
  lanHost?: unknown,
): VoiceSocketAuthority {
  return resolveVoiceSocketAuthorityFrom({
    pageProtocol,
    pageHost,
    hass,
    lanHost,
  });
}

export function resolveVoiceSocketAuthorityFrom(input: {
  pageProtocol: string;
  pageHost: string;
  hass?: HassLike | null;
  lanHost?: unknown;
  scriptHost?: unknown;
}): VoiceSocketAuthority {
  const configured = cleanDuplexLanHost(input.lanHost);
  if (configured) {
    return { protocol: "http:", host: configured, source: "addon" };
  }
  const page: VoiceSocketAuthority = { protocol: input.pageProtocol, host: input.pageHost, source: "page" };
  if (!pageHostNeedsHaIngressHost(input.pageHost) && !isMdnsFallbackHostname(input.pageHost)) {
    return page;
  }
  const discovered = usableHassAuthorities(input.hass);
  const smarter = discovered.find((candidate) => !isMdnsFallbackHostname(candidate.host));
  if (smarter) {
    return smarter;
  }
  const script = cleanDuplexLanHost(input.scriptHost);
  if (script && !isMdnsFallbackHostname(script)) {
    return { protocol: "http:", host: script, source: "script" };
  }
  const mdnsHass = discovered.find((candidate) => isMdnsFallbackHostname(candidate.host));
  if (mdnsHass) {
    return mdnsHass;
  }
  if (!pageHostNeedsHaIngressHost(input.pageHost)) {
    return page;
  }
  return { protocol: "http:", host: LAN_HA_FALLBACK_HOST, source: "lan-fallback" };
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

export function parseDebugPort(value: unknown): number {
  const n = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : VOICE_DEBUG_PORT;
}

/** Lovelace / kiosk inject talks to the add-on debug port. Ingress needs a cookie. */
export function debugVoiceSocketUrl(protocol: string, host: string, debugPort = VOICE_DEBUG_PORT): string {
  const proto = protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${hostnameOf(host)}:${debugPort}/`;
}

/** HTTP origin of the add-on debug port (static JS, not the duplex WebSocket). */
export function debugVoiceHttpOrigin(protocol: string, host: string, debugPort = VOICE_DEBUG_PORT): string {
  const proto = protocol === "https:" ? "https:" : "http:";
  return `${proto}//${hostnameOf(host)}:${debugPort}`;
}

/**
 * Stable kiosk client URL: ``http://<HA-LAN>:8080/grok-voice.js``.
 *
 * Lovelace has no Supervisor ingress cookie, so do not use
 * ``/api/hassio_ingress/...``. A classic ``<script src>`` cannot send a
 * Bearer token either; this GET is unauthenticated on purpose. Duplex still
 * authenticates on the WebSocket.
 */
export function debugVoiceScriptUrl(protocol: string, host: string, debugPort = VOICE_DEBUG_PORT): string {
  return `${debugVoiceHttpOrigin(protocol, host, debugPort)}${KIOSK_CLIENT_PATH}`;
}

export function resolveKioskClientScript(input: {
  hass?: HassLike | null;
  pageProtocol: string;
  pageHost: string;
  debugPort?: number;
  explicit?: string;
  lanHost?: unknown;
  scriptHost?: unknown;
}): { url: string; authority: VoiceSocketAuthority } {
  const explicit = (input.explicit || "").trim();
  if (explicit) {
    return {
      url: explicit,
      authority: { protocol: "", host: "", source: "explicit" },
    };
  }
  const socket = resolveKioskVoiceSocket(input);
  return {
    url: debugVoiceScriptUrl(socket.authority.protocol, socket.authority.host, socket.debugPort),
    authority: socket.authority,
  };
}

export interface KioskVoiceSocket {
  url: string;
  authority: VoiceSocketAuthority;
  debugPort: number;
  authMode: VoiceAuthMode;
}

export function resolveKioskVoiceSocket(input: {
  hass?: HassLike | null;
  pageProtocol: string;
  pageHost: string;
  debugPort?: number;
  lanHost?: unknown;
  scriptHost?: unknown;
}): KioskVoiceSocket {
  const authority = resolveVoiceSocketAuthorityFrom({
    pageProtocol: input.pageProtocol,
    pageHost: input.pageHost,
    hass: input.hass,
    lanHost: input.lanHost,
    scriptHost: input.scriptHost,
  });
  const debugPort = parseDebugPort(input.debugPort);
  return {
    url: debugVoiceSocketUrl(authority.protocol, authority.host, debugPort),
    authority,
    debugPort,
    authMode: "token",
  };
}

export function describeDuplexChoice(choice: {
  authority: Pick<VoiceSocketAuthority, "source">;
  host: string;
  authMode: VoiceAuthMode;
}): string {
  return `[Grok Voice] duplex host ${choice.host} via ${choice.authority.source} auth ${choice.authMode}`;
}

/**
 * Kiosk / dashboard inject: `ws(s)://<HA host>:8080/` with token auth.
 * Do not use `/api/hassio_ingress/` here — Lovelace has no ingress cookie.
 */
export async function resolveVoiceSocketUrl(
  hass: HassLike,
  pageProtocol: string,
  host: string,
  debugPort?: number,
  lanHost?: unknown,
): Promise<string> {
  const resolved = resolveKioskVoiceSocket({
    hass,
    pageProtocol,
    pageHost: host,
    debugPort,
    lanHost,
  });
  console.log(
    describeDuplexChoice({
      authority: resolved.authority,
      host: `${hostnameOf(resolved.authority.host)}:${resolved.debugPort}`,
      authMode: resolved.authMode,
    }),
  );
  return resolved.url;
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
