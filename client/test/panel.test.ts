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

function mountPanel() {
  const html = readFileSync(panelPath, "utf8");
  const scripts = inlineScripts(html);
  expect(scripts.length).toBeGreaterThan(0);
  const markup = html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
  document.documentElement.innerHTML = markup
    .replace(/^[\s\S]*?<html[^>]*>/i, "")
    .replace(/<\/html>\s*$/i, "");
  (0, eval)(
    scripts[0] +
      "\n;globalThis.__panel={setS(v){S=v},setTab(v){tab=v},setOpen(v){openRow=v},render};",
  );
}

const emptyWindow = {
  cost: 0, sessions: 0, minutes: 0, realtime_cost: 0, summary_cost: 0,
  latency_median: null, latency_p90: null,
};

function stylePct(el: Element | null, key: string): number {
  expect(el).not.toBeNull();
  const style = el!.getAttribute("style") || "";
  const match = style.match(new RegExp(`${key}:\\s*([\\d.]+)%`));
  expect(match, `expected ${key} in ${style}`).not.toBeNull();
  return Number(match![1]);
}

function markFor(label: string): HTMLElement {
  const rows = [...document.querySelectorAll(".wf-row")];
  const row = rows.find((r) => {
    const body = r.querySelector(".wf-body");
    return body && (body.textContent || "").includes(label);
  });
  expect(row, `row for ${label}`).toBeTruthy();
  const mark = row!.querySelector(".wf-bar, .wf-tick") as HTMLElement | null;
  expect(mark, `mark for ${label}`).not.toBeNull();
  return mark!;
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
    mountPanel();
    const labels = [...document.querySelectorAll("#tabs button")].map((el) => el.textContent);
    expect(labels).toEqual(["Status", "Settings", "Skills", "Tablets", "Test"]);
  });

  it("renders a session timeline waterfall in the detail view", () => {
    mountPanel();
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
              { t: 500, kind: "tool", name: "skill", skill: "dashboard", action: "catalog", args: {}, result: { ok: true }, cached: true, call_to_dispatch_ms: 1, ha_ms: 250, result_to_audio_ms: 200 },
              { t: 700, kind: "first_audio", e2s_ms: 300 },
              { t: 900, kind: "response.done", e2s_to_first_audio_ms: 300, first_audio_to_done_ms: 200, total_ms: 500, audio_s: 0.4, usage: { input_tokens: 20, output_tokens: 10 }, status: "completed", output: [{ type: "message", status: "completed" }] },
              { t: 1200, kind: "provider_tool", name: "web_search", call_id: "c9", arguments: { query: "weather this weekend" } },
              { t: 4800, kind: "memory_summary", summary_kind: "summary", ms: 200 },
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
    expect(text).toContain("web search (xAI) · weather this weekend");
    expect(text).toContain("xAI reports this after the spoken answer");
    expect(text).toContain("xAI tools:");
    expect(text).toContain("web_search");
    expect(document.querySelector(".wf")).not.toBeNull();
    expect(document.querySelector(".wf-axis")).not.toBeNull();
    expect(document.querySelector(".wf-leg")).not.toBeNull();
  });

  it("positions waterfall bars by session offset and duration", () => {
    mountPanel();
    globalThis.__panel.setS({
      relay: { listening: true, port: 8766 },
      status: {
        ha_ready: true,
        sessions: { live: [], recent: [] },
        memory: [],
        stats: {
          totals: { today: emptyWindow, "7d": emptyWindow, "30d": emptyWindow },
          tablets: [],
          daily: [{ date: "2026-10-15", sessions: 0, minutes: 0, realtime_cost: 0, summary_cost: 0 }],
          recent: [{
            id: 1, tablet: "Kitchen", started: 1, ended: 6, duration_s: 5, ready_ms: 80, turns: 1,
            latencies: [300], latency_median: 300, latency_p90: 300,
            tools: [{ name: "skill", ok: true, ms: 250 }],
            usage: {},
            realtime_cost: 0.01, error: "", end_reason: "end_conversation",
            timeline: [
              { t: 0, kind: "connect" },
              { t: 100, kind: "speech_started" },
              { t: 400, kind: "speech_stopped" },
              { t: 500, kind: "tool", name: "skill", skill: "dashboard", action: "catalog", ha_ms: 250 },
              { t: 700, kind: "first_audio", e2s_ms: 300 },
              { t: 900, kind: "response.done", first_audio_to_done_ms: 200, status: "completed" },
              { t: 1200, kind: "provider_tool", name: "web_search", arguments: { query: "weather this weekend" } },
              { t: 4800, kind: "memory_summary", summary_kind: "summary", ms: 200 },
              { t: 5000, kind: "session_end", reason: "end_conversation" },
            ],
          }],
        },
      },
    });
    globalThis.__panel.setTab("Status");
    globalThis.__panel.setOpen(1);
    globalThis.__panel.render();

    const axis = document.querySelector(".wf-axis")?.textContent || "";
    expect(axis).toContain("0");
    expect(axis).toContain("2500 ms");
    expect(axis).toContain("5000 ms");
    expect(document.querySelector(".wf-leg")?.textContent).toMatch(/speech/);

    const speech = markFor("speech start (VAD)");
    expect(speech.className).toContain("wf-bar");
    expect(speech.getAttribute("data-kind")).toBe("speech");
    expect(stylePct(speech, "left")).toBe(2);
    expect(stylePct(speech, "width")).toBe(6);

    const latency = markFor("first audio");
    expect(latency.getAttribute("data-kind")).toBe("latency");
    expect(stylePct(latency, "left")).toBe(8);
    expect(stylePct(latency, "width")).toBe(6);

    const speak = markFor("response.done");
    expect(speak.getAttribute("data-kind")).toBe("speak");
    expect(stylePct(speak, "left")).toBe(14);
    expect(stylePct(speak, "width")).toBe(4);

    const ha = markFor("skill dashboard.catalog");
    expect(ha.getAttribute("data-kind")).toBe("ha");
    expect(stylePct(ha, "left")).toBe(5);
    expect(stylePct(ha, "width")).toBe(5);

    const mem = markFor("memory summary");
    expect(mem.getAttribute("data-kind")).toBe("mem");
    expect(stylePct(mem, "left")).toBe(92);
    expect(stylePct(mem, "width")).toBe(4);

    const connect = markFor("KS connected");
    expect(connect.className).toContain("wf-tick");
    expect(stylePct(connect, "left")).toBe(0);

    const search = markFor("web search (xAI)");
    expect(search.className).toContain("wf-tick");
    expect(stylePct(search, "left")).toBe(24);

    const end = markFor("session end");
    expect(end.className).toContain("wf-tick");
    expect(stylePct(end, "left")).toBe(100);

    const lefts = [...document.querySelectorAll(".wf-bar, .wf-tick")].map((el) =>
      stylePct(el, "left"),
    );
    expect(new Set(lefts).size).toBeGreaterThan(4);
  });

  it("shows a usage chart tooltip on hover and tap", () => {
    mountPanel();
    globalThis.__panel.setS({
      relay: { listening: true, port: 8766 },
      status: {
        ha_ready: true,
        sessions: { live: [], recent: [] },
        memory: [],
        stats: {
          totals: { today: emptyWindow, "7d": emptyWindow, "30d": emptyWindow },
          tablets: [],
          daily: [
            { date: "2026-10-13", sessions: 4, minutes: 12, realtime_cost: 0.08, summary_cost: 0.02 },
            { date: "2026-10-14", sessions: 1, minutes: 3, realtime_cost: 0.01, summary_cost: 0 },
            { date: "2026-10-15", sessions: 0, minutes: 0, realtime_cost: 0, summary_cost: 0 },
          ],
          recent: [],
        },
      },
    });
    globalThis.__panel.setTab("Status");
    globalThis.__panel.render();

    const wrap = document.querySelector(".chart");
    const tip = document.querySelector(".chart-tip") as HTMLElement | null;
    expect(wrap).not.toBeNull();
    expect(tip).not.toBeNull();
    expect(tip!.hidden).toBe(true);
    expect(document.querySelector("svg title")).toBeNull();

    const bar = wrap!.querySelector("[data-i='0']") as HTMLElement | null;
    expect(bar).not.toBeNull();
    bar!.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 20, clientY: 10 }));
    expect(tip!.hidden).toBe(false);
    expect(tip!.innerHTML).toContain("2026-10-13");
    expect(tip!.textContent).toContain("4 sessions");
    expect(tip!.textContent).toContain("12 min");
    expect(tip!.textContent).toMatch(/realtime/);
    expect(tip!.textContent).toMatch(/summaries/);
    expect(tip!.textContent).toMatch(/total/);
    expect(tip!.textContent).toContain("$0.0800");
    expect(tip!.textContent).toContain("$0.0200");
    expect(tip!.textContent).toContain("$0.1000");

    wrap!.dispatchEvent(new MouseEvent("mouseleave", { bubbles: true }));
    expect(tip!.hidden).toBe(true);

    const second = wrap!.querySelector("[data-i='1']") as HTMLElement | null;
    expect(second).not.toBeNull();
    second!.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: 40, clientY: 12 }));
    expect(tip!.hidden).toBe(false);
    expect(tip!.innerHTML).toContain("2026-10-14");
    expect(tip!.textContent).toContain("1 sessions");
    expect(tip!.textContent).toContain("3 min");
    expect(tip!.textContent).toContain("$0.0100");
  });

  it("labels empty ha_skill actions as pass-through (any args)", () => {
    const html = readFileSync(panelPath, "utf8");
    const scripts = inlineScripts(html);
    const markup = html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
    document.documentElement.innerHTML = markup
      .replace(/^[\s\S]*?<html[^>]*>/i, "")
      .replace(/<\/html>\s*$/i, "");
    (0, eval)(
      scripts[0] +
        "\n;globalThis.__panel={setS(v){S=v},setTab(v){tab=v},setDraft(v){draft=v},setSel(v){selSkill=v},render};",
    );
    const skill = {
      name: "helper", enabled: true, direct: false, index: "helper", help: "ask {request}", aliases: [],
      actions: [
        { name: "ask", aliases: [], backend: { type: "ha_skill", target: "", fixed: {} }, args: [] },
        { name: "lookup", aliases: [], backend: { type: "ha_skill", target: "", fixed: {} },
          args: [{ name: "food", required: true, aliases: [] }] },
      ],
    };
    globalThis.__panel.setS({
      relay: { listening: true, port: 8766 },
      backends: ["script", "service", "relay", "ha_skill"],
      coerce: [""],
      scripts: [],
      services: {},
      relay_handlers: [],
      preview: { tools: [], bytes: 0, tokens_estimate: 0 },
      skills: [skill],
      settings: {},
      status: { ha_ready: true, sessions: { live: [], recent: [] }, memory: [] },
    });
    globalThis.__panel.setDraft([JSON.parse(JSON.stringify(skill))]);
    globalThis.__panel.setSel(0);
    globalThis.__panel.setTab("Skills");
    globalThis.__panel.render();
    const names = [...document.querySelectorAll('.act [data-a="name"]')].map((el) => el.value);
    expect(names).toEqual(["ask", "lookup"]);
    const acts = [...document.querySelectorAll(".act")];
    expect(acts).toHaveLength(2);
    expect(acts[0].textContent).toContain("pass-through (any args)");
    expect(acts[1].textContent).not.toContain("pass-through (any args)");
  });
});
