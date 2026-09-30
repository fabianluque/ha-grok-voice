export type VoiceStatus = "listening" | "speaking";

export interface OverlayMessage {
  role: string;
  text: string;
  final: boolean;
}

const OVERLAY_ID = "grok-voice-overlay";
const STYLE_ID = "grok-voice-overlay-style";

const STYLE = `
#grok-voice-overlay{
  position:fixed;inset:0;z-index:10000;
  display:flex;flex-direction:column;gap:clamp(12px,2vh,24px);
  box-sizing:border-box;
  padding:clamp(18px,4vh,40px) clamp(18px,4.5vw,48px) clamp(22px,4vh,44px);
  pointer-events:auto;
  background:rgba(6,8,14,.82);
  color:#f4f6fb;
  font:clamp(26px,4.2vw,40px)/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  backdrop-filter:blur(22px) saturate(1.15);
  -webkit-backdrop-filter:blur(22px) saturate(1.15);
}
#grok-voice-overlay .header{
  display:flex;align-items:center;gap:12px;flex-shrink:0;
  font-size:clamp(28px,4.6vw,44px);
}
#grok-voice-overlay .dot{
  width:14px;height:14px;border-radius:50%;background:#18bc9c;
  box-shadow:0 0 0 0 rgba(24,188,156,.45);
  animation:grok-voice-pulse 1.6s ease-out infinite;
}
#grok-voice-overlay[data-status="speaking"] .dot{
  background:#5b9dff;box-shadow:0 0 0 0 rgba(91,157,255,.45);
}
#grok-voice-overlay .status{
  font-weight:800;letter-spacing:.01em;
}
#grok-voice-overlay[data-status="speaking"] .status{color:#c9ddff}
#grok-voice-overlay .brand{
  margin-left:auto;color:#9aa3b5;font-size:clamp(18px,2.6vw,26px);font-weight:700;
}
#grok-voice-overlay .messages{
  flex:1 1 auto;overflow:auto;min-height:0;
  display:flex;flex-direction:column;gap:clamp(10px,1.8vh,20px);
}
#grok-voice-overlay .messages:empty{display:none}
#grok-voice-overlay .msg{
  margin:0;white-space:pre-wrap;word-break:break-word;
  font-size:clamp(26px,4.2vw,40px);line-height:1.35;
}
#grok-voice-overlay .msg[data-final="false"]{opacity:.88}
#grok-voice-overlay .msg[data-role="user"]{color:#d7deea}
#grok-voice-overlay .msg[data-role="assistant"]{color:#7dffcf}
#grok-voice-overlay .who{font-weight:800;margin-right:.35em;color:#9aa3b5}
#grok-voice-overlay .msg[data-role="assistant"] .who{color:#18bc9c}
@keyframes grok-voice-pulse{
  0%{box-shadow:0 0 0 0 currentColor;opacity:1}
  70%{box-shadow:0 0 0 10px transparent;opacity:.85}
  100%{box-shadow:0 0 0 0 transparent;opacity:1}
}
`;

export function overlayStyle(): string {
  return STYLE;
}

export function voiceStatusFromMessage(type: string): VoiceStatus | null {
  if (
    type === "ready" ||
    type === "speech_started" ||
    type === "speech_stopped" ||
    type === "response_done"
  ) {
    return "listening";
  }
  if (type === "response_started") {
    return "speaking";
  }
  return null;
}

export function statusLabel(status: VoiceStatus): string {
  return status === "speaking" ? "Speaking" : "Listening";
}

export function speakerLabel(role: string): string {
  return role === "user" ? "You" : "Grok";
}

/** Update the in-progress line for a role, otherwise append a new bubble. */
export function upsertTranscript(
  messages: OverlayMessage[],
  role: string,
  text: string,
  final: boolean,
): OverlayMessage[] {
  const trimmed = text.trim();
  if (!trimmed) {
    return messages;
  }
  const last = messages[messages.length - 1];
  if (last && last.role === role && !last.final) {
    last.text = trimmed;
    last.final = final;
    return messages;
  }
  messages.push({ role, text: trimmed, final });
  return messages;
}

export interface KioskOverlay {
  set(status: VoiceStatus): void;
  addMessage(role: string, text: string, final?: boolean): void;
  remove(): void;
}

export function mountKioskStatus(doc: Document): KioskOverlay {
  if (!doc.getElementById(STYLE_ID) && (doc.head || doc.documentElement)) {
    const style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STYLE;
    (doc.head || doc.documentElement).appendChild(style);
  }
  doc.getElementById(OVERLAY_ID)?.remove();
  doc.getElementById("grok-voice-status")?.remove();
  const root = doc.createElement("div");
  root.id = OVERLAY_ID;
  root.setAttribute("role", "status");
  root.setAttribute("aria-live", "polite");
  const header = doc.createElement("div");
  header.className = "header";
  const dot = doc.createElement("span");
  dot.className = "dot";
  const statusEl = doc.createElement("span");
  statusEl.className = "status";
  const brand = doc.createElement("span");
  brand.className = "brand";
  brand.textContent = "Grok";
  header.appendChild(dot);
  header.appendChild(statusEl);
  header.appendChild(brand);
  const log = doc.createElement("div");
  log.className = "messages";
  root.appendChild(header);
  root.appendChild(log);
  doc.body.appendChild(root);

  const messages: OverlayMessage[] = [];

  const paintLog = () => {
    log.textContent = "";
    for (const message of messages) {
      const line = doc.createElement("p");
      line.className = "msg";
      line.dataset.role = message.role === "user" ? "user" : "assistant";
      line.dataset.final = message.final ? "true" : "false";
      const who = doc.createElement("span");
      who.className = "who";
      who.textContent = `${speakerLabel(message.role)}:`;
      line.appendChild(who);
      line.appendChild(doc.createTextNode(` ${message.text}`));
      log.appendChild(line);
    }
    log.scrollTop = log.scrollHeight;
  };

  const set = (status: VoiceStatus) => {
    root.dataset.status = status;
    statusEl.textContent = statusLabel(status);
  };
  set("listening");
  return {
    set,
    addMessage(role: string, text: string, final = true) {
      upsertTranscript(messages, role, text, final);
      paintLog();
    },
    remove() {
      root.remove();
    },
  };
}
