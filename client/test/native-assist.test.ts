import { describe, expect, it, vi } from "vitest";
import {
  cancelNativeAssist,
  cancelServiceForDevice,
  esphomeNodeSlug,
  matchDeviceId,
  nodeFromKioskEntityId,
  vsCancelCandidatesFromDeviceName,
} from "../src/native-assist";

const attic = { id: "attic", name: "Attic Dashboard" };
const dining = { id: "dining", name: "Dining Room" };

const entities = [
  { entity_id: "button.ks_attic_dashboard_reload", device_id: "attic", platform: "esphome" },
  { entity_id: "button.ks_dining_room_reload", device_id: "dining", platform: "esphome" },
  {
    entity_id: "assist_satellite.dining_room_dining_room_dashboard",
    device_id: "attic",
    platform: "esphome",
  },
];

const services = {
  ks_attic_dashboard_vs_cancel: {},
  ks_dining_room_vs_cancel: {},
};

describe("native assist cancel target", () => {
  it("slugs a device name the way Kiosk Satellite names a new ESPHome node", () => {
    expect(esphomeNodeSlug("Attic Dashboard")).toBe("attic-dashboard");
    expect(vsCancelCandidatesFromDeviceName("Attic Dashboard")).toEqual([
      "ks_attic_dashboard_vs_cancel",
      "attic_dashboard_vs_cancel",
    ]);
  });

  it("reads the node from this kiosk's button, not a migrated satellite entity", () => {
    expect(nodeFromKioskEntityId("button.ks_attic_dashboard_reload")).toBe("ks_attic_dashboard");
    expect(nodeFromKioskEntityId("assist_satellite.dining_room_dining_room_dashboard")).toBeNull();
    expect(cancelServiceForDevice("Attic Dashboard", [attic, dining], entities, services)).toBe(
      "ks_attic_dashboard_vs_cancel",
    );
  });

  it("uses a custom node name when it is the only ESPHome node on this device", () => {
    const custom = [
      { entity_id: "button.kiosk_satellite_ab12_reload", device_id: "attic", platform: "esphome" },
    ];
    expect(
      cancelServiceForDevice("Attic Dashboard", [attic, dining], custom, {
        kiosk_satellite_ab12_vs_cancel: {},
        ks_dining_room_vs_cancel: {},
      }),
    ).toBe("kiosk_satellite_ab12_vs_cancel");
  });

  it("does not call a service that belongs to another device name", () => {
    const contaminated = [
      { entity_id: "button.ks_dining_room_reload", device_id: "attic", platform: "esphome" },
    ];
    expect(cancelServiceForDevice("Attic Dashboard", [attic, dining], contaminated, services)).toBe(
      "ks_attic_dashboard_vs_cancel",
    );
    expect(cancelServiceForDevice("Attic Dashboard", [attic, dining], [], { ks_dining_room_vs_cancel: {} })).toBeNull();
  });

  it("matches a Dashboard kiosk name to HA name_by_user, name, or the room name", () => {
    expect(
      matchDeviceId(
        [
          { id: "dining", name: "Kiosk Satellite", name_by_user: "Dining Room Dashboard" },
          { id: "attic", name: "Attic Dashboard" },
        ],
        "Dining Room Dashboard",
      ),
    ).toBe("dining");
    expect(
      matchDeviceId(
        [
          { id: "dining", name: "Dining Room Dashboard", name_by_user: "Dining Room" },
          { id: "attic", name: "Attic Dashboard" },
        ],
        "Dining Room Dashboard",
      ),
    ).toBe("dining");
    expect(matchDeviceId([attic, dining], "Dining Room Dashboard")).toBe("dining");
  });

  it("matches via this kiosk's button entity when the HA device name differs", () => {
    expect(
      matchDeviceId(
        [
          { id: "dining", name: "Fire tablet" },
          { id: "attic", name: "Attic Dashboard" },
        ],
        "Dining Room Dashboard",
        [
          { entity_id: "button.ks_dining_room_dashboard_reload", device_id: "dining", platform: "esphome" },
          { entity_id: "button.ks_attic_dashboard_reload", device_id: "attic", platform: "esphome" },
        ],
      ),
    ).toBe("dining");
  });

  it("cancels native Assist for a Dashboard kiosk whose HA device uses the room name", () => {
    expect(cancelServiceForDevice("Dining Room Dashboard", [attic, dining], entities, services)).toBe(
      "ks_dining_room_vs_cancel",
    );
  });

  it("calls only the resolved service", async () => {
    const callService = vi.fn(async () => undefined);
    await expect(cancelNativeAssist({ callService }, "ks_attic_dashboard_vs_cancel")).resolves.toBe(true);
    expect(callService).toHaveBeenCalledTimes(1);
    expect(callService).toHaveBeenCalledWith("esphome", "ks_attic_dashboard_vs_cancel", {});
    await expect(cancelNativeAssist({ callService }, null)).resolves.toBe(false);
    expect(callService).toHaveBeenCalledTimes(1);
  });
});
