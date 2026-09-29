import { createBrowserSession } from "./browser";
import {
  DEBUG_TOKEN_KEY,
  discoverHass,
  pageVoiceSocketUrl,
  resolveAccessToken,
  saveDebugToken,
  tokenFromHassConnection,
  type HassLike,
  type TokenSource,
} from "./ingress";
import type { ServerMessage } from "./session";

const statusEl = document.getElementById("status") as HTMLParagraphElement;
const sourceEl = document.getElementById("token-source") as HTMLParagraphElement;
const logEl = document.getElementById("log") as HTMLElement;
const startBtn = document.getElementById("start") as HTMLButtonElement;
const stopBtn = document.getElementById("stop") as HTMLButtonElement;
const tokenInput = document.getElementById("token") as HTMLInputElement;
const remember = document.getElementById("remember") as HTMLInputElement;
const tokenPanel = document.getElementById("token-panel") as HTMLElement;
const errorEl = document.getElementById("error") as HTMLParagraphElement;
const secureEl = document.getElementById("secure-note") as HTMLParagraphElement;

let active: { session: { finish(reason: string): void } } | null = null;
let autoToken = "";
let autoSource: TokenSource = "none";

function setStatus(text: string, tone: "idle" | "live" | "talk" | "error" = "idle"): void {
  statusEl.textContent = text;
  statusEl.dataset.tone = tone;
}

function setError(text: string): void {
  errorEl.textContent = text;
  errorEl.hidden = !text;
}

function describeSource(source: TokenSource): string {
  switch (source) {
    case "hass":
      return "Using the Home Assistant session from this page.";
    case "hassConnection":
      return "Using the Home Assistant connection token.";
    case "hassTokens":
      return "Using the Home Assistant token stored in this browser.";
    case "saved":
      return "Using a long-lived token saved in this browser.";
    case "explicit":
      return "Using the long-lived token in the field below.";
    default:
      return "Paste a Home Assistant long-lived token to talk from this Mac.";
  }
}

function appendLine(role: string, text: string): void {
  const line = document.createElement("p");
  line.className = role === "user" ? "user" : "assistant";
  line.textContent = `${role === "user" ? "You" : "Grok"}: ${text}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function currentToken(): { token: string; source: TokenSource } {
  const explicit = tokenInput.value.trim();
  if (explicit) {
    return { token: explicit, source: "explicit" };
  }
  if (autoToken) {
    return { token: autoToken, source: autoSource };
  }
  const resolved = resolveAccessToken({
    hass: discoverHass(),
    storage: localStorage,
  });
  return resolved;
}

function refreshTokenUi(): void {
  const resolved = currentToken();
  sourceEl.textContent = describeSource(resolved.source);
  tokenPanel.hidden = resolved.source === "hass" || resolved.source === "hassConnection" || resolved.source === "hassTokens";
  if (!window.isSecureContext) {
    secureEl.hidden = false;
  }
}

function endReasonText(reason: string): string {
  switch (reason) {
    case "unauthorized":
      return "Home Assistant rejected this token.";
    case "idle":
      return "Session ended after silence. Press Start to talk again.";
    case "stop":
      return "Session stopped.";
    case "error":
      return "The voice session failed. Check the add-on log.";
    case "closed":
      return "The voice socket closed.";
    default:
      return `Session ended (${reason}).`;
  }
}

function onServerText(message: ServerMessage): void {
  if (message.type === "ready") {
    setStatus("Listening — talk anytime", "live");
    return;
  }
  if (message.type === "response_started") {
    setStatus("Grok is speaking", "talk");
    return;
  }
  if (message.type === "speech_started") {
    setStatus("Listening — talk anytime", "live");
    return;
  }
  if (message.type === "tool") {
    appendLine("assistant", `${message.name || "tool"} ${message.status || "done"}`);
  }
}

async function loadToken(): Promise<void> {
  const hass: HassLike | null = discoverHass();
  const fromConnection = await tokenFromHassConnection(window);
  const resolved = resolveAccessToken({
    hass,
    storage: localStorage,
  });
  if (resolved.token) {
    autoToken = resolved.token;
    autoSource = resolved.source;
  } else if (fromConnection) {
    autoToken = fromConnection;
    autoSource = "hassConnection";
  }
  if (autoSource === "saved" && !tokenInput.value) {
    tokenInput.value = autoToken;
    remember.checked = true;
  }
  refreshTokenUi();
}

async function start(): Promise<void> {
  setError("");
  const { token, source } = currentToken();
  if (!token) {
    setError("Add a Home Assistant long-lived access token first.");
    tokenPanel.hidden = false;
    tokenInput.focus();
    return;
  }
  if (source === "explicit" && remember.checked) {
    saveDebugToken(token, localStorage);
  }
  startBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus("Allow microphone access…", "idle");
  const url = pageVoiceSocketUrl(location.protocol, location.host, location.pathname);
  try {
    const { session } = await createBrowserSession({
      url,
      token,
      onTranscript: (role, text) => appendLine(role, text),
      onServerText,
    });
    active = { session };
    session.onEnd((reason) => {
      active = null;
      startBtn.disabled = false;
      stopBtn.disabled = true;
      const tone = reason === "unauthorized" || reason === "error" ? "error" : "idle";
      setStatus(endReasonText(reason), tone);
    });
    setStatus("Connecting to Grok Voice…", "idle");
    await session.start();
  } catch (error) {
    active = null;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    const message = error instanceof Error ? error.message : "Could not start voice";
    if (/not allowed|permission|secure|getUserMedia|NotAllowed|NotFound/i.test(message) || !window.isSecureContext) {
      setError(
        window.isSecureContext
          ? "The browser blocked the microphone. Allow it for this page and try again."
          : "This origin is not a secure context, so the browser will not open the microphone. Use HA ingress over HTTPS, or see the Mac testing notes.",
      );
      setStatus("Microphone blocked", "error");
    } else {
      setError(message);
      setStatus("Could not start", "error");
    }
  }
}

function stop(): void {
  active?.session.finish("stop");
}

startBtn.addEventListener("click", () => {
  void start();
});
stopBtn.addEventListener("click", stop);
tokenInput.addEventListener("input", refreshTokenUi);
remember.addEventListener("change", () => {
  if (!remember.checked) {
    localStorage.removeItem(DEBUG_TOKEN_KEY);
  }
});

void loadToken();
refreshTokenUi();
