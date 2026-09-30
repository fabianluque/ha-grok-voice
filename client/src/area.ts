import { matchDeviceId, type HassDeviceRecord, type HassEntityRecord, type NativeAssistHass } from "./native-assist";

/** Last-resort name when this kiosk's HA area is not readable yet. */
export const FALLBACK_AREA_NAME = "Attic";

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
  const deviceId = matchDeviceId(input.devices, deviceName);
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
 * then the Home Assistant area on this kiosk device. Fall back to Attic only
 * when none of those are readable yet.
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

  const deviceName = info && typeof info === "object" ? trimString((info as { name?: unknown }).name) : "";
  if (deviceName && input.hass) {
    try {
      const fromHa = await areaFromHomeAssistant(input.hass, deviceName);
      if (fromHa) {
        return fromHa;
      }
    } catch {
      // Registry lookup is best-effort; fall through to Attic.
    }
  }

  return fallbackArea(input.fallbackName);
}

function fallbackArea(name?: string): KioskArea {
  return { name: trimString(name) || FALLBACK_AREA_NAME, source: "fallback" };
}

let areaCache: KioskArea | null = null;
let areaPrefetch: Promise<KioskArea> | null = null;
let deviceCache: KioskDevice | null = null;

/** Test hook. Production boot does not call this. */
export function resetKioskAreaCache(): void {
  areaCache = null;
  areaPrefetch = null;
  deviceCache = null;
}

export function peekCachedKioskArea(): KioskArea | null {
  return areaCache;
}

export function rememberKioskArea(area: KioskArea): KioskArea {
  areaCache = area;
  return area;
}

/**
 * Area to send on duplex auth. Must not await Home Assistant — that lookup
 * is what made listen-start feel 1–2s late after 0.2.6.
 */
export function immediateKioskArea(input: {
  explicit?: { area?: string; areaId?: string } | null;
  cached?: KioskArea | null;
  fallbackName?: string;
}): KioskArea {
  return areaFromExplicit(input.explicit) ?? input.cached ?? fallbackArea(input.fallbackName);
}

/** Resolve and cache the kiosk area in the background (inject boot, not wake). */
export function prefetchKioskArea(input: {
  kiosk?: { getDeviceInfo?: () => Promise<unknown> } | null;
  hass?: NativeAssistHass | null;
  explicit?: { area?: string; areaId?: string } | null;
  fallbackName?: string;
}): Promise<KioskArea> {
  if (areaCache) {
    return Promise.resolve(areaCache);
  }
  if (areaPrefetch) {
    return areaPrefetch;
  }
  areaPrefetch = resolveKioskArea(input)
    .then((area) => rememberKioskArea(area))
    .catch(() => rememberKioskArea(areaCache ?? fallbackArea(input.fallbackName)))
    .finally(() => {
      areaPrefetch = null;
    });
  return areaPrefetch;
}

export function describeArea(area: KioskArea): string {
  const id = area.id ? ` id=${area.id}` : "";
  return `${area.name}${id} source=${area.source}`;
}

const DEVICE_ID_KEYS = ["id", "deviceId", "device_id", "ha_device_id", "serial"];

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

/** Cached satellite identity for the auth message. Never awaits HA. */
export function immediateKioskDevice(): KioskDevice | null {
  return deviceCache;
}
