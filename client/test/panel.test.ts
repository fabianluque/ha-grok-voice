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
    delete globalThis.__panel;
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

  it("renders a session timeline waterfall in the detail view", () => {
    const html = readFileSync(panelPath, "utf8");
    const scripts = inlineScripts(html);
    const markup = html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
    document.documentElement.innerHTML = markup
      .replace(/^[\s\S]*?<html[^>]*>/i, "")
      .replace(/<\/html>\s*$/i, "");
    (0, eval)(
      scripts[0] +
        "\n;globalThis.__panel={setS(v){S=v},setTab(v){tab=v},setOpen(v){openRow=v},render};",
    );
    const emptyWindow = {
      cost: 0, sessions: 0, minutes: 0, realtime_cost: 0, summary_cost: 0,
      latency_median: null, latency_p90: null,
    };
    const daily = Array.from({ length: 3 }, (_, i) => ({
      date: `2026-10-1${i}`, sessions: 0, minutes: 0, realtime_cost: 0, summary_cost: 0,
    }));
    globalThis.__panel.setS({
      relay: { listening: true, port: 8766 },
      status: {
        ha_ready: true,
        sessions: { live: [], recent: [] },
        memory: [],
        stats: {
          totals: { today: emptyWindow, "7d": emptyWindow, "30d": emptyWindow },
          tablets: [],
          daily,
          recent: [{
            id: 1, tablet: "Kitchen", started: 1, ended: 6, duration_s: 5, ready_ms: 80, turns: 1,
            latencies: [300], latency_median: 300, latency_p90: 300,
            tools: [{ name: "skill", ok: true, ms: 12, cached: true }],
            usage: { input_tokens: 20, output_tokens: 10 },
            realtime_cost: 0.01, error: "", end_reason: "end_conversation",
            timeline: [
              { t: 0, kind: "connect" },
              { t: 10, kind: "session.update", tools: 11 },
              { t: 40, kind: "upstream_open" },
              { t: 80, kind: "session.updated", ready_ms: 80 },
              { t: 100, kind: "speech_started" },
              { t: 400, kind: "speech_stopped" },
              { t: 410, kind: "transcript", role: "user", text: "open the dashboard" },
              { t: 500, kind: "tool", name: "skill", skill: "dashboard", action: "catalog", args: {}, result: { ok: true }, cached: true, call_to_dispatch_ms: 1, ha_ms: 0, result_to_audio_ms: 200 },
              { t: 700, kind: "first_audio", e2s_ms: 300 },
              { t: 900, kind: "response.done", e2s_to_first_audio_ms: 300, first_audio_to_done_ms: 200, total_ms: 500, audio_s: 0.4, usage: { input_tokens: 20, output_tokens: 10 } },
              { t: 5000, kind: "session_end", reason: "end_conversation" },
            ],
          }],
        },
      },
    });
    globalThis.__panel.setTab("Status");
    globalThis.__panel.setOpen(1);
    globalThis.__panel.render();
    const text = document.body.textContent || "";
    expect(text).toContain("KS connected (auth ok)");
    expect(text).toContain("session.updated (ready)");
    expect(text).toContain("speech start (VAD)");
    expect(text).toContain("open the dashboard");
    expect(text).toContain("skill dashboard.catalog");
    expect(text).toContain("cache hit");
    expect(text).toContain("end_conversation");
    expect(text).toContain("end-of-speech → first audio");
    expect(document.querySelector(".wf")).not.toBeNull();
  });
});
