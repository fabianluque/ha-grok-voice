export type VoiceStatus = "listening" | "speaking";

const STATUS_ID = "grok-voice-status";
const STYLE_ID = "grok-voice-status-style";

const STYLE = `
#grok-voice-status{
  position:fixed;left:50%;bottom:28px;transform:translateX(-50%);
  z-index:9999;display:flex;align-items:center;gap:8px;
  padding:8px 14px;border-radius:999px;pointer-events:none;
  background:rgba(17,19,24,.78);color:#f4f6fb;
  font:600 14px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  letter-spacing:.01em;box-shadow:0 4px 20px rgba(0,0,0,.28);
  backdrop-filter:blur(8px);
}
#grok-voice-status[data-status="speaking"]{color:#c9ddff}
#grok-voice-status .dot{
  width:8px;height:8px;border-radius:50%;background:#18bc9c;
  box-shadow:0 0 0 0 rgba(24,188,156,.45);
  animation:grok-voice-pulse 1.6s ease-out infinite;
}
#grok-voice-status[data-status="speaking"] .dot{
  background:#5b9dff;box-shadow:0 0 0 0 rgba(91,157,255,.45);
}
@keyframes grok-voice-pulse{
  0%{box-shadow:0 0 0 0 currentColor;opacity:1}
  70%{box-shadow:0 0 0 8px transparent;opacity:.85}
  100%{box-shadow:0 0 0 0 transparent;opacity:1}
}
`;

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

export interface KioskStatus {
  set(status: VoiceStatus): void;
  remove(): void;
}

export function mountKioskStatus(doc: Document): KioskStatus {
  if (!doc.getElementById(STYLE_ID) && (doc.head || doc.documentElement)) {
    const style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STYLE;
    (doc.head || doc.documentElement).appendChild(style);
  }
  let root = doc.getElementById(STATUS_ID);
  if (!root) {
    root = doc.createElement("div");
    root.id = STATUS_ID;
    root.setAttribute("role", "status");
    root.setAttribute("aria-live", "polite");
    const dot = doc.createElement("span");
    dot.className = "dot";
    const label = doc.createElement("span");
    label.className = "label";
    root.appendChild(dot);
    root.appendChild(label);
    doc.body.appendChild(root);
  }
  const labelEl = root.querySelector(".label") ?? root;
  const set = (status: VoiceStatus) => {
    root.dataset.status = status;
    labelEl.textContent = statusLabel(status);
  };
  set("listening");
  return {
    set,
    remove() {
      root.remove();
    },
  };
}
