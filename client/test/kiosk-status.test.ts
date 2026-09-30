import { describe, expect, it, vi } from "vitest";
import {
  applyOverlaySpeech,
  attachOverlayDismiss,
  finalizeTranscript,
  isOverlayTap,
  mergeTranscript,
  overlayStyle,
  speakerLabel,
  statusLabel,
  upsertTranscript,
  voiceStatusFromMessage,
  type OverlaySpeechState,
} from "../src/kiosk-status";

function speechState(overrides: Partial<OverlaySpeechState> = {}): OverlaySpeechState {
  return { userSpeaking: false, heardStop: false, newUserUtterance: false, ...overrides };
}

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
    expect(css).toContain("pointer-events:auto");
    expect(css).toContain("cursor:pointer");
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

  it("replaces live user snapshots, including xAI revisions", () => {
    const messages = upsertTranscript([], "user", "turn on", false);
    upsertTranscript(messages, "user", "turn on the", false);
    upsertTranscript(messages, "user", "turn on the lights", false);
    expect(messages).toEqual([{ role: "user", text: "turn on the lights", final: false }]);
    upsertTranscript(messages, "user", "Hello?", false);
    expect(messages).toEqual([{ role: "user", text: "Hello?", final: false }]);
    upsertTranscript(messages, "user", "Hello, my name is", false);
    expect(messages).toEqual([{ role: "user", text: "Hello, my name is", final: false }]);
    expect(mergeTranscript("Hello?", "Hello, my name is", "replace")).toBe("Hello, my name is");
  });

  it("keeps one You: line when ASR revises wording after speech_stopped or completed", () => {
    const speech = speechState();
    const messages = upsertTranscript([], "user", "turn on the light", false, {
      itemId: "item_1",
      speech,
    });
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "turn on the lights", false, { itemId: "item_1", speech });
    applyOverlaySpeech(messages, speech, "speech_stopped");
    upsertTranscript(messages, "user", "turn on the lights", true, { itemId: "item_1", speech });
    upsertTranscript(messages, "user", "turn off the attic fan", false, { itemId: "item_1", speech });
    expect(messages).toEqual([
      { role: "user", text: "turn off the attic fan", final: false, itemId: "item_1" },
    ]);
  });

  it("replaces the same ASR item even after Grok has started speaking", () => {
    const messages = upsertTranscript([], "user", "what's happening", true, { itemId: "item_1" });
    upsertTranscript(messages, "assistant", "This weekend?", false);
    upsertTranscript(messages, "user", "what's the weather this weekend", false, { itemId: "item_1" });
    expect(messages).toEqual([
      { role: "user", text: "what's the weather this weekend", final: false, itemId: "item_1" },
      { role: "assistant", text: "This weekend?", final: false },
    ]);
  });

  it("starts a new user line after the previous snapshot is finalized", () => {
    const messages = upsertTranscript([], "user", "turn on the lights", false);
    finalizeTranscript(messages, "user");
    upsertTranscript(messages, "user", "and the fan", false);
    expect(messages).toEqual([
      { role: "user", text: "turn on the lights", final: true },
      { role: "user", text: "and the fan", final: false },
    ]);
  });

  it("starts a new You: line only after a true turn boundary", () => {
    const speech = speechState();
    const messages = upsertTranscript([], "user", "turn on", false, { itemId: "item_1", speech });
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "turn on the lights", false, { itemId: "item_1", speech });
    applyOverlaySpeech(messages, speech, "speech_stopped");
    upsertTranscript(messages, "user", "turn off the lights", false, { itemId: "item_1", speech });
    expect(messages).toEqual([
      { role: "user", text: "turn off the lights", final: false, itemId: "item_1" },
    ]);
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "and the fan", false, { itemId: "item_2", speech });
    expect(messages).toEqual([
      { role: "user", text: "turn off the lights", final: true, itemId: "item_1" },
      { role: "user", text: "and the fan", final: false, itemId: "item_2" },
    ]);
  });

  it("does not start a new You: line on extra speech_started mid-utterance", () => {
    const messages = upsertTranscript([], "user", "turn on", false);
    const speech = speechState();
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "turn on the", false, { speech });
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "turn on the lights", false, { speech });
    expect(messages).toEqual([{ role: "user", text: "turn on the lights", final: false }]);
    applyOverlaySpeech(messages, speech, "speech_stopped");
    expect(messages).toEqual([{ role: "user", text: "turn on the lights", final: true }]);
    applyOverlaySpeech(messages, speech, "speech_started");
    upsertTranscript(messages, "user", "and the fan", false, { speech });
    expect(messages).toEqual([
      { role: "user", text: "turn on the lights", final: true },
      { role: "user", text: "and the fan", final: false },
    ]);
  });

  it("treats a short pointer gesture as a dismiss tap, not a scroll", () => {
    expect(isOverlayTap({ x: 10, y: 10, t: 0 }, { x: 12, y: 11, t: 80 })).toBe(true);
    expect(isOverlayTap({ x: 10, y: 10, t: 0 }, { x: 10, y: 80, t: 120 })).toBe(false);
    expect(isOverlayTap(null, { x: 10, y: 10, t: 0 })).toBe(false);
  });

  it("dismisses the overlay on a tap, including during a follow-up", () => {
    const listeners: Record<string, Array<(event: PointerEvent) => void>> = {};
    const root = {
      addEventListener(type: string, listener: (event: PointerEvent) => void) {
        (listeners[type] ??= []).push(listener);
      },
    };
    const onDismiss = vi.fn();
    attachOverlayDismiss(root, onDismiss);
    const fire = (type: string, event: Partial<PointerEvent>) => {
      for (const listener of listeners[type] ?? []) {
        listener(event as PointerEvent);
      }
    };
    fire("pointerdown", { isPrimary: true, clientX: 40, clientY: 40, timeStamp: 1 });
    fire("pointerup", { isPrimary: true, clientX: 42, clientY: 41, timeStamp: 40 });
    expect(onDismiss).toHaveBeenCalledOnce();
    fire("pointerdown", { isPrimary: true, clientX: 40, clientY: 40, timeStamp: 100 });
    fire("pointerup", { isPrimary: true, clientX: 40, clientY: 90, timeStamp: 180 });
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
