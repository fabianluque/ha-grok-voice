import { describe, expect, it } from "vitest";
import { speakerLabel, statusLabel, upsertTranscript, voiceStatusFromMessage } from "../src/kiosk-status";

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
    expect(speakerLabel("user")).toBe("You");
    expect(speakerLabel("assistant")).toBe("Grok");
  });

  it("updates a streaming line then appends the next turn", () => {
    const messages = upsertTranscript([], "user", "thank", false);
    upsertTranscript(messages, "user", "thank you", true);
    upsertTranscript(messages, "assistant", "You're welcome", true);
    expect(messages).toEqual([
      { role: "user", text: "thank you", final: true },
      { role: "assistant", text: "You're welcome", final: true },
    ]);
  });
});
