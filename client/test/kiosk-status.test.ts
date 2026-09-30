import { describe, expect, it } from "vitest";
import { statusLabel, voiceStatusFromMessage } from "../src/kiosk-status";

describe("kiosk status pill", () => {
  it("maps duplex events to Listening or Speaking", () => {
    expect(voiceStatusFromMessage("ready")).toBe("listening");
    expect(voiceStatusFromMessage("speech_started")).toBe("listening");
    expect(voiceStatusFromMessage("speech_stopped")).toBe("listening");
    expect(voiceStatusFromMessage("response_done")).toBe("listening");
    expect(voiceStatusFromMessage("response_started")).toBe("speaking");
    expect(voiceStatusFromMessage("transcript")).toBeNull();
    expect(statusLabel("listening")).toBe("Listening");
    expect(statusLabel("speaking")).toBe("Speaking");
  });
});
