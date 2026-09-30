/**
 * Kiosk Satellite `voice.runtime=native` starts Assist inside the app, then
 * pauses the dashboard WebView. Page hooks on `__vsSession` never see that
 * pipeline. `voiceCancel` is not on the page JS bridge; the same command is
 * the ESPHome action `esphome.<this-kiosk>_vs_cancel`.
 */

const MAX_SLUG = 40;

const FOLD: Record<string, string> = {
  à: "a",
  á: "a",
  â: "a",
  ã: "a",
  ä: "a",
  å: "a",
  è: "e",
  é: "e",
  ê: "e",
  ë: "e",
  ì: "i",
  í: "i",
  î: "i",
  ï: "i",
  ò: "o",
  ó: "o",
  ô: "o",
  õ: "o",
  ö: "o",
  ù: "u",
  ú: "u",
  û: "u",
  ü: "u",
  ñ: "n",
  ç: "c",
  ý: "y",
  ÿ: "y",
  ß: "ss",
};

/** Object ids Kiosk Satellite always registers, used to recover the node name. */
const NODE_SUFFIXES = ["_reload", "_screenshot", "_bring_to_front", "_load_start_url", "_clear_cache"];

export interface HassDeviceRecord {
  id?: string;
  name?: string | null;
  name_by_user?: string | null;
  area_id?: string | null;
}

export interface HassEntityRecord {
  entity_id?: string;
  device_id?: string | null;
  platform?: string;
  area_id?: string | null;
}

export interface NativeAssistHass {
  callService?(domain: string, service: string, data?: Record<string, unknown>): Promise<unknown>;
  callWS?(message: unknown): Promise<unknown>;
  services?: { esphome?: Record<string, unknown> };
}

export function esphomeNodeSlug(name: string): string {
  let slug = "";
  let pendingHyphen = false;
  const lower = name.trim().toLowerCase();
  for (const source of lower) {
    const folded = FOLD[source] ?? source;
    for (const char of folded) {
      const keep = (char >= "a" && char <= "z") || (char >= "0" && char <= "9");
      if (keep) {
        if (pendingHyphen && slug.length > 0) {
          slug += "-";
        }
        pendingHyphen = false;
        slug += char;
        if (slug.length >= MAX_SLUG) {
          return slug;
        }
      } else {
        pendingHyphen = true;
      }
    }
  }
  return slug;
}

export function vsCancelServiceFromNode(node: string): string {
  return `${node.replace(/-/g, "_")}_vs_cancel`;
}

/** Service names a fresh install derives from the kiosk device name. */
export function vsCancelCandidatesFromDeviceName(name: string): string[] {
  const slug = esphomeNodeSlug(name);
  if (!slug) {
    return [];
  }
  const prefixed = slug === "ks" || slug.startsWith("ks-") ? slug : `ks-${slug}`;
  const services: string[] = [];
  for (const candidate of [prefixed, slug]) {
    const service = vsCancelServiceFromNode(candidate);
    if (!services.includes(service)) {
      services.push(service);
    }
  }
  return services;
}

export function nodeFromKioskEntityId(entityId: string): string | null {
  const dot = entityId.indexOf(".");
  if (dot < 0) {
    return null;
  }
  const objectId = entityId.slice(dot + 1);
  for (const suffix of NODE_SUFFIXES) {
    if (objectId.endsWith(suffix) && objectId.length > suffix.length) {
      return objectId.slice(0, -suffix.length);
    }
  }
  return null;
}

function deviceLabel(device: HassDeviceRecord): string {
  return (device.name_by_user || device.name || "").trim();
}

function sameName(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}

export function matchDeviceId(devices: HassDeviceRecord[], deviceName: string): string | null {
  const matches = devices.filter((device) => device.id && sameName(deviceLabel(device), deviceName));
  if (matches.length !== 1) {
    return null;
  }
  return matches[0].id ?? null;
}

function belongsToOtherDevice(service: string, devices: HassDeviceRecord[], deviceName: string): boolean {
  for (const device of devices) {
    const label = deviceLabel(device);
    if (!label || sameName(label, deviceName)) {
      continue;
    }
    if (vsCancelCandidatesFromDeviceName(label).includes(service)) {
      return true;
    }
  }
  return false;
}

/**
 * Pick `esphome.<node>_vs_cancel` for this kiosk. A satellite entity that
 * migrated from another room is ignored. A service that belongs to a
 * different device name is never returned.
 */
export function cancelServiceForDevice(
  deviceName: string,
  devices: HassDeviceRecord[],
  entities: HassEntityRecord[],
  esphomeServices: Record<string, unknown> | undefined,
): string | null {
  const known = esphomeServices ? new Set(Object.keys(esphomeServices)) : null;
  const accept = (service: string | null): string | null => {
    if (!service) {
      return null;
    }
    if (known && !known.has(service)) {
      return null;
    }
    if (belongsToOtherDevice(service, devices, deviceName)) {
      return null;
    }
    return service;
  };

  const deviceId = matchDeviceId(devices, deviceName);
  const nodes = new Set<string>();
  if (deviceId) {
    for (const entity of entities) {
      if (entity.device_id !== deviceId || !entity.entity_id) {
        continue;
      }
      if (entity.platform && entity.platform !== "esphome") {
        continue;
      }
      const node = nodeFromKioskEntityId(entity.entity_id);
      if (node) {
        nodes.add(node);
      }
    }
  }

  const preferred = vsCancelCandidatesFromDeviceName(deviceName);
  const fromNodes = [...nodes].map((node) => vsCancelServiceFromNode(node));
  for (const candidate of preferred) {
    if (fromNodes.includes(candidate)) {
      const accepted = accept(candidate);
      if (accepted) {
        return accepted;
      }
    }
  }
  if (nodes.size === 1) {
    const accepted = accept(fromNodes[0] ?? null);
    if (accepted) {
      return accepted;
    }
  }
  for (const candidate of preferred) {
    const accepted = accept(candidate);
    if (accepted) {
      return accepted;
    }
  }
  return null;
}

function asRecords<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export async function resolveNativeCancelService(
  hass: NativeAssistHass,
  deviceName: string,
): Promise<string | null> {
  let devices: HassDeviceRecord[] = [];
  let entities: HassEntityRecord[] = [];
  try {
    devices = asRecords<HassDeviceRecord>(await hass.callWS?.({ type: "config/device_registry/list" }));
  } catch {
    devices = [];
  }
  try {
    entities = asRecords<HassEntityRecord>(await hass.callWS?.({ type: "config/entity_registry/list" }));
  } catch {
    entities = [];
  }
  let services = hass.services?.esphome;
  if (!services) {
    try {
      const listed = await hass.callWS?.({ type: "get_services" });
      if (listed && typeof listed === "object" && "esphome" in listed) {
        services = (listed as { esphome?: Record<string, unknown> }).esphome;
      }
    } catch {
      services = undefined;
    }
  }
  return cancelServiceForDevice(deviceName, devices, entities, services);
}

export async function cancelNativeAssist(hass: NativeAssistHass | null | undefined, service: string | null): Promise<boolean> {
  if (!hass?.callService || !service) {
    return false;
  }
  try {
    await hass.callService("esphome", service, {});
    return true;
  } catch {
    return false;
  }
}
