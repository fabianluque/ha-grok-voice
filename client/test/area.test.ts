import { afterEach, describe, expect, it } from "vitest";
import {
  FALLBACK_AREA_NAME,
  areaFromExplicit,
  areaFromKioskInfo,
  areaFromRegistries,
  describeArea,
  deviceFromKioskInfo,
  immediateKioskArea,
  immediateKioskDevice,
  peekCachedKioskArea,
  peekCachedKioskDevice,
  prefetchKioskArea,
  resetKioskAreaCache,
  resolveKioskArea,
} from "../src/area";

describe("kiosk area", () => {
  afterEach(() => {
    resetKioskAreaCache();
  });

  it("prefers an explicit inject override", async () => {
    expect(areaFromExplicit({ area: "Kitchen", areaId: "kitchen" })).toEqual({
      id: "kitchen",
      name: "Kitchen",
      source: "explicit",
    });
    const area = await resolveKioskArea({
      explicit: { area: "Office" },
      kiosk: { getDeviceInfo: async () => ({ name: "Attic Dashboard", area: "Attic" }) },
      fallbackName: "Attic",
    });
    expect(area).toEqual({ name: "Office", source: "explicit" });
  });

  it("reads area fields from Kiosk Satellite getDeviceInfo", () => {
    expect(
      areaFromKioskInfo({ name: "Attic Dashboard", area_name: "Attic", area_id: "attic" }),
    ).toEqual({ id: "attic", name: "Attic", source: "kiosk" });
    expect(areaFromKioskInfo({ name: "Attic Dashboard" })).toBeNull();
  });

  it("uses the Home Assistant area on this kiosk device", () => {
    const area = areaFromRegistries({
      deviceName: "Dining Room Dashboard",
      devices: [
        { id: "dining", name: "Dining Room Dashboard", area_id: "dining_room" },
        { id: "attic", name: "Attic Dashboard", area_id: "attic" },
      ],
      entities: [
        { entity_id: "light.ignored", device_id: "attic", area_id: "attic" },
        {
          entity_id: "assist_satellite.dining_room_dashboard",
          device_id: "dining",
          area_id: "dining_room",
        },
      ],
      areas: [
        { area_id: "attic", name: "Attic" },
        { area_id: "dining_room", name: "Dining Room" },
      ],
    });
    expect(area).toEqual({ id: "dining_room", name: "Dining Room", source: "ha" });
  });

  it("falls back to Attic when nothing is readable yet", async () => {
    const area = await resolveKioskArea({
      kiosk: { getDeviceInfo: async () => ({ name: "Attic Dashboard" }) },
      hass: { callWS: async () => [] },
    });
    expect(area).toEqual({ name: FALLBACK_AREA_NAME, source: "fallback" });
    expect(describeArea(area)).toBe("Attic source=fallback");
  });

  it("resolves the HA area from this kiosk's device name", async () => {
    const area = await resolveKioskArea({
      kiosk: { getDeviceInfo: async () => ({ name: "Attic Dashboard" }) },
      hass: {
        callWS: async (message: unknown) => {
          const type = (message as { type?: string }).type;
          if (type === "config/device_registry/list") {
            return [{ id: "attic", name: "Attic Dashboard", area_id: "attic" }];
          }
          if (type === "config/entity_registry/list") {
            return [];
          }
          if (type === "config/area_registry/list") {
            return [{ area_id: "attic", name: "Attic" }];
          }
          return [];
        },
      },
    });
    expect(area).toEqual({ id: "attic", name: "Attic", source: "ha" });
  });

  it("does not await Home Assistant on the wake path", () => {
    const started = Date.now();
    const area = immediateKioskArea({
      explicit: null,
      cached: peekCachedKioskArea(),
    });
    expect(Date.now() - started).toBeLessThan(20);
    expect(area).toEqual({ name: FALLBACK_AREA_NAME, source: "fallback" });
  });

  it("uses a cached HA area immediately without waiting on prefetch", async () => {
    const delayed = prefetchKioskArea({
      kiosk: { getDeviceInfo: async () => ({ name: "Attic Dashboard" }) },
      hass: {
        callWS: async (message: unknown) => {
          await new Promise((resolve) => setTimeout(resolve, 40));
          const type = (message as { type?: string }).type;
          if (type === "config/device_registry/list") {
            return [{ id: "attic", name: "Attic Dashboard", area_id: "attic" }];
          }
          if (type === "config/area_registry/list") {
            return [{ area_id: "attic", name: "Attic" }];
          }
          return [];
        },
      },
    });
    expect(immediateKioskArea({ cached: peekCachedKioskArea() }).source).toBe("fallback");
    const resolved = await delayed;
    expect(resolved).toEqual({ id: "attic", name: "Attic", source: "ha" });
    expect(immediateKioskArea({ cached: peekCachedKioskArea() })).toEqual(resolved);
  });

  it("loads device, entity, and area registries in parallel", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const area = await resolveKioskArea({
      kiosk: { getDeviceInfo: async () => ({ name: "Attic Dashboard" }) },
      hass: {
        callWS: async () => {
          inFlight += 1;
          maxInFlight = Math.max(maxInFlight, inFlight);
          await new Promise((resolve) => setTimeout(resolve, 25));
          inFlight -= 1;
          return [];
        },
      },
    });
    expect(area.source).toBe("fallback");
    expect(maxInFlight).toBe(3);
  });

  it("caches kiosk device identity during area prefetch without blocking wake", async () => {
    expect(immediateKioskDevice()).toBeNull();
    expect(
      deviceFromKioskInfo({ name: "Attic Dashboard", deviceId: "attic-tablet" }),
    ).toEqual({ name: "Attic Dashboard", id: "attic-tablet" });
    const delayed = prefetchKioskArea({
      kiosk: {
        getDeviceInfo: async () => {
          await new Promise((resolve) => setTimeout(resolve, 30));
          return { name: "Attic Dashboard", id: "attic-tablet" };
        },
      },
    });
    expect(immediateKioskDevice()).toBeNull();
    await delayed;
    expect(peekCachedKioskDevice()).toEqual({ name: "Attic Dashboard", id: "attic-tablet" });
    expect(immediateKioskDevice()).toEqual({ name: "Attic Dashboard", id: "attic-tablet" });
  });
});
