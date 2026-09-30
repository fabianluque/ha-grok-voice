import { describe, expect, it } from "vitest";
import {
  overlayStyle,
  speakerLabel,
  statusLabel,
  upsertTranscript,
  voiceStatusFromMessage,
} from "../src/kiosk-status";

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

  it("replaces in-progress assistant text as deltas arrive", () => {
    const messages = upsertTranscript([], "assistant", "The lights", false);
    upsertTranscript(messages, "assistant", "The lights are on", false);
    upsertTranscript(messages, "assistant", "The lights are on.", true);
    expect(messages).toEqual([{ role: "assistant", text: "The lights are on.", final: true }]);
  });

  it("covers the dashboard with large type", () => {
    const css = overlayStyle();
    expect(css).toContain("#grok-voice-overlay{");
    expect(css).toContain("inset:0");
    expect(css).toContain("backdrop-filter:blur(22px)");
    expect(css).toContain("rgba(6,8,14,.82)");
    expect(css).toContain("clamp(26px,4.2vw,40px)");
    expect(css).toContain("clamp(28px,4.6vw,44px)");
  });
});
