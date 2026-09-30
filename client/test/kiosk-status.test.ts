import { describe, expect, it } from "vitest";
import {
  mergeTranscript,
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

  it("covers the dashboard with readable conversation type", () => {
    const css = overlayStyle();
    expect(css).toContain("#grok-voice-overlay{");
    expect(css).toContain("inset:0");
    expect(css).toContain("backdrop-filter:blur(22px)");
    expect(css).toContain("rgba(6,8,14,.82)");
    expect(css).toContain("clamp(18px,2.9vw,28px)");
    expect(css).toContain("clamp(20px,3.2vw,32px)");
    expect(css).not.toContain("clamp(26px,4.2vw,40px)");
  });

  it("merges incremental pieces and cumulative snapshots", () => {
    expect(mergeTranscript("The lights", "The lights are on")).toBe("The lights are on");
    expect(mergeTranscript("The lights", " are on")).toBe("The lights are on");
    expect(mergeTranscript("Hello", "Hell")).toBe("Hello");
  });

  it("appends small streaming pieces without waiting for a full replace", () => {
    const messages = upsertTranscript([], "assistant", "The", false);
    upsertTranscript(messages, "assistant", " lights", false);
    upsertTranscript(messages, "assistant", " are", false);
    upsertTranscript(messages, "assistant", " on.", true);
    expect(messages).toEqual([{ role: "assistant", text: "The lights are on.", final: true }]);
  });
});
