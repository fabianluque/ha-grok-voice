import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelTimeout, scheduleTimeout } from "../src/timers";

describe("bound timers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls setTimeout and clearTimeout with globalThis as this", () => {
    const originalSet = globalThis.setTimeout;
    const originalClear = globalThis.clearTimeout;
    const setThis: unknown[] = [];
    const clearThis: unknown[] = [];
    vi.spyOn(globalThis, "setTimeout").mockImplementation(function (this: unknown, fn, ms) {
      setThis.push(this);
      return originalSet.call(globalThis, fn as () => void, ms);
    });
    vi.spyOn(globalThis, "clearTimeout").mockImplementation(function (this: unknown, id) {
      clearThis.push(this);
      originalClear.call(globalThis, id);
    });
    const id = scheduleTimeout(() => undefined, 5);
    cancelTimeout(id);
    expect(setThis).toEqual([globalThis]);
    expect(clearThis).toEqual([globalThis]);
  });
});
