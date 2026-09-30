import { describe, expect, it } from "vitest";
import { isClosingUtterance, isOpenFollowup } from "../src/end-phrase";

describe("closing utterances", () => {
  it("matches goodbye phrases and natural variants", () => {
    for (const text of [
      "thank you",
      "Thanks!",
      "thanks grok",
      "OK that's all",
      "that’s it",
      "that's it, thanks",
      "goodbye",
      "Good bye.",
      "stop listening",
      "please stop listening",
      "thank you so much",
      "oh, that's great, thank you",
      "that's great, thanks",
      "alright, goodbye",
      "ok bye",
      "that's all for now, thank you",
      "you can go now",
      "you can go",
      "thanks I'm done",
      "I'm done",
      "never mind",
      "all set",
    ]) {
      expect(isClosingUtterance(text), text).toBe(true);
    }
  });

  it("does not treat a request as a hang-up", () => {
    for (const text of [
      "thank you for turning on the lights",
      "thanks, now turn off the kitchen",
      "stop listening to the radio",
      "that's all the lights in the attic",
      "could you thank you later for me",
      "don't stop listening until I say so",
      "tell me when I'm done",
      "never mind the kitchen lights",
      "",
      undefined,
    ]) {
      expect(isClosingUtterance(text)).toBe(false);
    }
  });

  it("treats assistant questions and want-clauses as open follow-ups", () => {
    for (const text of [
      "The Mets won 4-2. Want last night's highlights?",
      "Want last night's highlights",
      "A concert Saturday. Want me to check Sunday too?",
      "Lights on. Anything else?",
      "Would you like the kitchen too?",
      "Should I dim them as well?",
    ]) {
      expect(isOpenFollowup(text), text).toBe(true);
    }
    for (const text of [
      "Lights on.",
      "You're welcome.",
      "Anytime.",
      "The Mets won 4-2 last night.",
      "I want to be careful with that lock.",
      "",
      undefined,
    ]) {
      expect(isOpenFollowup(text)).toBe(false);
    }
  });
});
