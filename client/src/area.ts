import { matchDeviceId, type HassDeviceRecord, type HassEntityRecord, type NativeAssistHass } from "./native-assist";

/** Last-resort name when this kiosk's HA area is not readable yet. Empty: do not invent a room. */
export const FALLBACK_AREA_NAME = "";

export type AreaSource = "explicit" | "kiosk" | "ha" | "fallback";

export interface KioskArea {
  id?: string;
  name: string;
  source: AreaSource;
}

export interface KioskDevice {
  name: string;
  id?: string;
}

export interface AreaRecord {
  area_id?: string | null;
  name?: string | null;
}

export interface DeviceAreaRecord extends HassDeviceRecord {
  area_id?: string | null;
}

export interface EntityAreaRecord extends HassEntityRecord {
  area_id?: string | null;
}

const KIOSK_AREA_ID_KEYS = ["area_id", "areaId", "ha_area_id", "assist_area_id"];
const KIOSK_AREA_NAME_KEYS = [
  "area",
  "area_name",
  "areaName",
  "ha_area",
  "assist_area",
  "assistArea",
];

function trimString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function firstString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = trimString(record[key]);
    if (value) {
      return value;
    }
  }
  return "";
}

function asRecords<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** Inject / page override: `GROK_VOICE_AREA` and `GROK_VOICE_AREA_ID`. */
export function areaFromExplicit(input?: { area?: string; areaId?: string } | null): KioskArea | null {
  const name = trimString(input?.area);
  const id = trimString(input?.areaId);
  if (!name && !id) {
    return null;
  }
  return { id: id || undefined, name: name || id, source: "explicit" };
}

/** Any area field Kiosk Satellite may expose on `getDeviceInfo()`. */
export function areaFromKioskInfo(info: unknown): KioskArea | null {
  if (!info || typeof info !== "object") {
    return null;
  }
  const record = info as Record<string, unknown>;
  const id = firstString(record, KIOSK_AREA_ID_KEYS);
  const name = firstString(record, KIOSK_AREA_NAME_KEYS);
  if (!id && !name) {
    return null;
  }
  return { id: id || undefined, name: name || id, source: "kiosk" };
}

export function areaNameForId(areaId: string, areas: AreaRecord[]): string {
  const match = areas.find((area) => trimString(area.area_id) === areaId);
  return trimString(match?.name) || areaId;
}

/**
 * Home Assistant area assigned to this kiosk device, or to its
 * `assist_satellite` entity when that entity has its own area.
 */
export function areaFromRegistries(input: {
  deviceName: string;
  devices: DeviceAreaRecord[];
  entities: EntityAreaRecord[];
  areas: AreaRecord[];
}): KioskArea | null {
  const deviceName = trimString(input.deviceName);
  if (!deviceName) {
    return null;
  }
  const deviceId = matchDeviceId(input.devices, deviceName, input.entities);
  if (!deviceId) {
    return null;
  }
  const device = input.devices.find((item) => item.id === deviceId);
  const deviceArea = trimString(device?.area_id);
  const owned = input.entities.filter((entity) => entity.device_id === deviceId);
  const satellite = owned.find((entity) => (entity.entity_id || "").startsWith("assist_satellite."));
  const satelliteArea = trimString(satellite?.area_id);
  const entityAreas = [
    ...new Set(owned.map((entity) => trimString(entity.area_id)).filter(Boolean)),
  ];
  const areaId = satelliteArea || deviceArea || (entityAreas.length === 1 ? entityAreas[0] : "");
  if (!areaId) {
    return null;
  }
  return { id: areaId, name: areaNameForId(areaId, input.areas), source: "ha" };
}

async function registryList<T>(hass: NativeAssistHass, type: string): Promise<T[]> {
  try {
    return asRecords<T>(await hass.callWS?.({ type }));
  } catch {
    return [];
  }
}

export async function areaFromHomeAssistant(
  hass: NativeAssistHass,
  deviceName: string,
): Promise<KioskArea | null> {
  if (!hass.callWS) {
    return null;
  }
  // Registries are independent. Sequential awaits on entity_registry/list
  // (often large) delayed listen-start by 1–2s when this ran on wake.
  const [devices, entities, areas] = await Promise.all([
    registryList<DeviceAreaRecord>(hass, "config/device_registry/list"),
    registryList<EntityAreaRecord>(hass, "config/entity_registry/list"),
    registryList<AreaRecord>(hass, "config/area_registry/list"),
  ]);
  return areaFromRegistries({ deviceName, devices, entities, areas });
}

/**
 * Prefer an explicit inject override, then Kiosk Satellite's own area fields,
 * then the Home Assistant area on this kiosk device. If none of those are
 * readable yet, send no room name (the add-on default_area is the next fallback).
 */
export async function resolveKioskArea(input: {
  kiosk?: { getDeviceInfo?: () => Promise<unknown> } | null;
  hass?: NativeAssistHass | null;
  explicit?: { area?: string; areaId?: string } | null;
  fallbackName?: string;
}): Promise<KioskArea> {
  let info: unknown;
  try {
    info = await input.kiosk?.getDeviceInfo?.();
  } catch {
    info = null;
  }
  rememberKioskDevice(deviceFromKioskInfo(info));

  const explicit = areaFromExplicit(input.explicit);
  if (explicit) {
    return explicit;
  }

  const fromKiosk = areaFromKioskInfo(info);
  if (fromKiosk) {
    return fromKiosk;
  }

  const fromInfo = info && typeof info === "object" ? trimString((info as { name?: unknown }).name) : "";
  const deviceName = fromInfo || trimString(peekCachedKioskDevice()?.name);
  if (deviceName && input.hass) {
    try {
      const fromHa = await areaFromHomeAssistant(input.hass, deviceName);
      if (fromHa) {
        return fromHa;
      }
    } catch {
      // Registry lookup is best-effort; fall through to an empty area.
    }
  }

  return fallbackArea(input.fallbackName);
}

function fallbackArea(name?: string): KioskArea {
  return { name: trimString(name) || FALLBACK_AREA_NAME, source: "fallback" };
}

let areaCache: KioskArea | null = null;
let areaPrefetch: Promise<KioskArea> | null = null;
let areaPrefetchHasHass = false;
let deviceCache: KioskDevice | null = null;
let generatedDeviceId: string | null = null;

/** Test hook. Production boot does not call this. */
export function resetKioskAreaCache(storage?: Storage | null): void {
  areaCache = null;
  areaPrefetch = null;
  areaPrefetchHasHass = false;
  deviceCache = null;
  generatedDeviceId = null;
  const store = storage === undefined ? defaultStorage() : storage;
  try {
    store?.removeItem(DEVICE_ID_STORAGE_KEY);
  } catch {
    // Ignore missing storage.
  }
}

export function peekCachedKioskArea(): KioskArea | null {
  return areaCache;
}

export function rememberKioskArea(area: KioskArea): KioskArea {
  // A slower hass-less prefetch must not clobber a later HA hit.
  if (area.source === "fallback" && areaCache && areaCache.source !== "fallback") {
    return areaCache;
  }
  areaCache = area;
  return area;
}

/**
 * Area for wake visuals / overlay. Must not await Home Assistant — that
 * lookup is what made listen-start feel 1–2s late after 0.2.6.
 */
export function immediateKioskArea(input: {
  explicit?: { area?: string; areaId?: string } | null;
  cached?: KioskArea | null;
  fallbackName?: string;
}): KioskArea {
  return areaFromExplicit(input.explicit) ?? input.cached ?? fallbackArea(input.fallbackName);
}

type AreaResolveInput = {
  kiosk?: { getDeviceInfo?: () => Promise<unknown> } | null;
  hass?: NativeAssistHass | null;
  explicit?: { area?: string; areaId?: string } | null;
  fallbackName?: string;
};

function hassWsReady(hass?: NativeAssistHass | null): boolean {
  return typeof hass?.callWS === "function";
}

/** Resolve and cache the kiosk area in the background (inject boot, not wake). */
export function prefetchKioskArea(input: AreaResolveInput): Promise<KioskArea> {
  // A first-boot fallback must not lock the attic (or any kiosk) out of a
  // later HA registry lookup once hass/callWS is actually available.
  if (areaCache && areaCache.source !== "fallback") {
    return Promise.resolve(areaCache);
  }
  const hasHass = hassWsReady(input.hass);
  if (areaPrefetch && (areaPrefetchHasHass || !hasHass)) {
    return areaPrefetch;
  }
  areaPrefetchHasHass = hasHass;
  const pending = resolveKioskArea(input)
    .then((area) => rememberKioskArea(area))
    .catch(() => rememberKioskArea(areaCache ?? fallbackArea(input.fallbackName)))
    .finally(() => {
      if (areaPrefetch === pending) {
        areaPrefetch = null;
        areaPrefetchHasHass = false;
      }
    });
  areaPrefetch = pending;
  return pending;
}

/**
 * Area to send on duplex auth. Listening is already painted; a short wait
 * for HA registries once `callWS` is ready does not delay wake visuals.
 */
export async function sessionKioskArea(input: AreaResolveInput): Promise<KioskArea> {
  const explicit = areaFromExplicit(input.explicit);
  if (explicit) {
    return rememberKioskArea(explicit);
  }
  const cached = peekCachedKioskArea();
  if (cached && cached.source !== "fallback") {
    return cached;
  }
  if (!hassWsReady(input.hass)) {
    return cached ?? fallbackArea(input.fallbackName);
  }
  return prefetchKioskArea(input);
}

export function describeArea(area: KioskArea): string {
  const id = area.id ? ` id=${area.id}` : "";
  const label = area.name.trim() || "(none)";
  return `${label}${id} source=${area.source}`;
}

const DEVICE_ID_KEYS = ["id", "deviceId", "device_id", "ha_device_id", "serial"];
export const DEVICE_ID_STORAGE_KEY = "grok-voice-device-id";

function defaultStorage(): Storage | null {
  try {
    const storage = (globalThis as { localStorage?: Storage }).localStorage;
    return storage ?? null;
  } catch {
    return null;
  }
}

function randomDeviceId(): string {
  const bytes = new Uint8Array(8);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return `kiosk-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** Stable per-browser id so two kiosks never share conversation memory. */
export function readOrCreateDeviceId(storage?: Storage | null): string {
  const store = storage === undefined ? defaultStorage() : storage;
  const existing = store?.getItem(DEVICE_ID_STORAGE_KEY)?.trim();
  if (existing) {
    generatedDeviceId = existing;
    return existing;
  }
  if (generatedDeviceId) {
    try {
      store?.setItem(DEVICE_ID_STORAGE_KEY, generatedDeviceId);
    } catch {
      // Ignore missing storage.
    }
    return generatedDeviceId;
  }
  const id = randomDeviceId();
  generatedDeviceId = id;
  try {
    store?.setItem(DEVICE_ID_STORAGE_KEY, id);
  } catch {
    // Private mode / missing storage: still return a session-local id.
  }
  return id;
}

export function deviceFromKioskInfo(info: unknown): KioskDevice | null {
  if (!info || typeof info !== "object") {
    return null;
  }
  const record = info as Record<string, unknown>;
  const name = trimString(record.name);
  const id = firstString(record, DEVICE_ID_KEYS);
  if (!name && !id) {
    return null;
  }
  return { name: name || id, id: id || undefined };
}

export function peekCachedKioskDevice(): KioskDevice | null {
  return deviceCache;
}

export function rememberKioskDevice(device: KioskDevice | null): KioskDevice | null {
  if (device) {
    deviceCache = device;
  }
  return deviceCache;
}

/**
 * Identity sent on duplex auth. Never awaits HA. The id is a per-tablet
 * localStorage value so the first wake (before getDeviceInfo) and later
 * wakes share conversation memory. Kiosk name is best-effort for logs.
 */
export function immediateKioskDevice(storage?: Storage | null): KioskDevice {
  const id = readOrCreateDeviceId(storage);
  const cached = deviceCache;
  return { name: cached?.name || "kiosk", id };
}
