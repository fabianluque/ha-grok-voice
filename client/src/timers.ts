/**
 * Chrome and Android WebView throw `TypeError: Illegal invocation` when a
 * Web IDL method is pulled off its owner and called as a free function.
 * `SessionEndWatch` used to store `setTimeout` / `clearTimeout` that way, and
 * the idle re-arm ran for the whole duplex session.
 */
export function scheduleTimeout(fn: () => void, ms: number): ReturnType<typeof setTimeout> {
  return globalThis.setTimeout.call(globalThis, fn, ms);
}

export function cancelTimeout(id: ReturnType<typeof setTimeout>): void {
  globalThis.clearTimeout.call(globalThis, id);
}
