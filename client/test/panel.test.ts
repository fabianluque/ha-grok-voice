/**
 * @vitest-environment jsdom
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const panelPath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../grok_voice_agent/app/ks_relay/www/index.html",
);

function inlineScripts(html: string): string[] {
  const scripts: string[] = [];
  const re = /<script(\s[^>]*)?>(.*?)<\/script>/gis;
  for (const match of html.matchAll(re)) {
    if (/\bsrc\s*=/i.test(match[1] || "")) continue;
    const body = (match[2] || "").trim();
    if (body) scripts.push(body);
  }
  return scripts;
}

describe("relay panel HTML", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    class FakeWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = FakeWebSocket.CONNECTING;
      url: string;
      onopen: ((ev: Event) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      constructor(url: string) {
        this.url = url;
      }
      close() {
        this.readyState = FakeWebSocket.CLOSED;
      }
      send() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.documentElement.innerHTML = "";
  });

  it("parses and renders the tab buttons", () => {
    const html = readFileSync(panelPath, "utf8");
    const scripts = inlineScripts(html);
    expect(scripts.length).toBeGreaterThan(0);
    const markup = html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
    document.documentElement.innerHTML = markup
      .replace(/^[\s\S]*?<html[^>]*>/i, "")
      .replace(/<\/html>\s*$/i, "");
    expect(document.querySelectorAll("#tabs button")).toHaveLength(0);
    (0, eval)(scripts[0]);
    const labels = [...document.querySelectorAll("#tabs button")].map((el) => el.textContent);
    expect(labels).toEqual(["Status", "Settings", "Skills", "Tablets", "Test"]);
  });
});
