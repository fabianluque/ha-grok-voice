export type VoiceStatus = "listening" | "speaking";

export interface OverlayMessage {
  role: string;
  text: string;
  final: boolean;
  itemId?: string;
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
  cursor:pointer;
  background:rgba(6,8,14,.82);
  color:#f4f6fb;
  font:clamp(18px,2.9vw,28px)/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  backdrop-filter:blur(22px) saturate(1.15);
  -webkit-backdrop-filter:blur(22px) saturate(1.15);
}
#grok-voice-overlay .header{
  display:flex;align-items:center;gap:12px;flex-shrink:0;
  font-size:clamp(20px,3.2vw,32px);
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
  margin-left:auto;color:#9aa3b5;font-size:clamp(14px,1.8vw,18px);font-weight:700;
}
#grok-voice-overlay .messages{
  flex:1 1 auto;overflow:auto;min-height:0;
  display:flex;flex-direction:column;gap:clamp(10px,1.8vh,20px);
}
#grok-voice-overlay .messages:empty{display:none}
#grok-voice-overlay .msg{
  margin:0;white-space:pre-wrap;word-break:break-word;
  font-size:clamp(18px,2.9vw,28px);line-height:1.35;
}
#grok-voice-overlay .msg[data-final="false"]{opacity:.88}
#grok-voice-overlay .msg[data-role="user"]{color:#d7deea}
#grok-voice-overlay .msg[data-role="assistant"]{color:#7dffcf}
#grok-voice-overlay .who{font-weight:800;margin-right:.35em;color:#9aa3b5}
#grok-voice-overlay .msg[data-role="assistant"] .who{color:#18bc9c}
#grok-voice-overlay .body{font-weight:400}
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

/** Replace a live user snapshot, or merge an incremental assistant piece. */
export function mergeTranscript(current: string, incoming: string, mode: "merge" | "replace" = "merge"): string {
  if (mode === "replace") {
    return incoming || current;
  }
  if (!incoming) {
    return current;
  }
  if (!current) {
    return incoming;
  }
  if (incoming.startsWith(current)) {
    return incoming;
  }
  if (current.startsWith(incoming)) {
    return current;
  }
  return current + incoming;
}

function lastIndexWhere(messages: OverlayMessage[], match: (message: OverlayMessage) => boolean): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (match(messages[index])) {
      return index;
    }
  }
  return -1;
}

function applyTranscript(
  message: OverlayMessage,
  text: string,
  final: boolean,
  itemId?: string,
): void {
  message.text = mergeTranscript(message.text, text, message.role === "user" ? "replace" : "merge");
  message.final = final;
  if (itemId) {
    message.itemId = itemId;
  }
  if (final) {
    message.text = message.text.trim();
  }
}

export interface OverlaySpeechState {
  userSpeaking: boolean;
  /** VAD ended the current utterance; a later speech_started is a new You: line. */
  heardStop: boolean;
  /** Next user snapshot without a matching item id starts a new bubble. */
  newUserUtterance: boolean;
}

export interface UpsertTranscriptOptions {
  itemId?: string;
  speech?: OverlaySpeechState;
}

/**
 * Update the in-progress line for a role, otherwise append a new bubble.
 *
 * User ASR revisions (including after speech_stopped / completed) replace the
 * live You: line. A new You: line starts only at a true turn boundary: a new
 * conversation item id, or speech_started after speech_stopped.
 */
export function upsertTranscript(
  messages: OverlayMessage[],
  role: string,
  text: string,
  final: boolean,
  options: UpsertTranscriptOptions = {},
): OverlayMessage[] {
  const itemId = options.itemId?.trim() || undefined;
  const speech = options.speech;
  if (role === "user") {
    let index = itemId ? lastIndexWhere(messages, (message) => message.role === "user" && message.itemId === itemId) : -1;
    if (index < 0) {
      const last = messages[messages.length - 1];
      if (last?.role === "user" && (!last.itemId || !itemId || last.itemId === itemId)) {
        const sameTurn = speech
          ? speech.newUserUtterance !== true
          : !last.final || Boolean(itemId && last.itemId === itemId);
        if (sameTurn) {
          index = messages.length - 1;
        }
      }
    }
    if (index >= 0) {
      applyTranscript(messages[index], text, final, itemId);
      if (speech) {
        speech.newUserUtterance = false;
      }
      return messages;
    }
  } else {
    const last = messages[messages.length - 1];
    if (last && last.role === role && !last.final) {
      applyTranscript(last, text, final, itemId);
      return messages;
    }
  }
  const seed = text.trim();
  if (!seed) {
    return messages;
  }
  const next: OverlayMessage = {
    role,
    text: final ? seed : text.replace(/^\s+/, ""),
    final,
  };
  if (itemId) {
    next.itemId = itemId;
  }
  messages.push(next);
  if (role === "user" && speech) {
    speech.newUserUtterance = false;
  }
  return messages;
}

export function finalizeTranscript(messages: OverlayMessage[], role?: string): OverlayMessage[] {
  const last = messages[messages.length - 1];
  if (last && !last.final && (!role || last.role === role)) {
    last.final = true;
    last.text = last.text.trim();
  }
  return messages;
}

/**
 * Close the previous You: bubble only when a *new* utterance starts.
 *
 * Extra `speech_started` mid-turn must not finalize. `speech_stopped` /
 * `completed` also must not block later ASR revisions of the same item —
 * those still replace the live You: line. A following `speech_started` after
 * a stop is the turn boundary for a new You: line.
 */
export function applyOverlaySpeech(
  messages: OverlayMessage[],
  state: OverlaySpeechState,
  type: string,
): OverlayMessage[] {
  if (type === "speech_started") {
    if (!state.userSpeaking) {
      finalizeTranscript(messages, "user");
      if (state.heardStop) {
        state.newUserUtterance = true;
      }
      state.heardStop = false;
    }
    state.userSpeaking = true;
    return messages;
  }
  if (type === "speech_stopped") {
    state.userSpeaking = false;
    state.heardStop = true;
    finalizeTranscript(messages, "user");
  }
  return messages;
}

export interface KioskOverlay {
  set(status: VoiceStatus): void;
  addMessage(role: string, text: string, final?: boolean, itemId?: string): void;
  handleDuplex(type: string): void;
  finalize(role?: string): void;
  remove(): void;
}

export const OVERLAY_TAP_SLOP_PX = 24;
export const OVERLAY_TAP_MAX_MS = 700;

export interface OverlayPointer {
  x: number;
  y: number;
  t: number;
}

/** True for a tap, false for a scroll/drag across the overlay. */
export function isOverlayTap(start: OverlayPointer | null, end: OverlayPointer): boolean {
  if (!start) {
    return false;
  }
  if (end.t - start.t > OVERLAY_TAP_MAX_MS) {
    return false;
  }
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  return dx * dx + dy * dy <= OVERLAY_TAP_SLOP_PX * OVERLAY_TAP_SLOP_PX;
}

export function attachOverlayDismiss(
  root: {
    addEventListener(type: string, listener: (event: PointerEvent) => void): void;
  },
  onDismiss: () => void,
): void {
  let start: OverlayPointer | null = null;
  root.addEventListener("pointerdown", (event) => {
    if (event.isPrimary === false) {
      return;
    }
    start = { x: event.clientX, y: event.clientY, t: event.timeStamp };
  });
  root.addEventListener("pointerup", (event) => {
    if (event.isPrimary === false) {
      return;
    }
    const origin = start;
    start = null;
    if (!isOverlayTap(origin, { x: event.clientX, y: event.clientY, t: event.timeStamp })) {
      return;
    }
    onDismiss();
  });
  root.addEventListener("pointercancel", () => {
    start = null;
  });
}

export interface MountKioskStatusOptions {
  onDismiss?: () => void;
}

export function mountKioskStatus(doc: Document, options: MountKioskStatusOptions = {}): KioskOverlay {
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
  root.setAttribute("aria-label", "Grok conversation. Tap to hang up.");
  if (options.onDismiss) {
    attachOverlayDismiss(root, options.onDismiss);
  }
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
  const speech: OverlaySpeechState = { userSpeaking: false, heardStop: false, newUserUtterance: false };

  const paintLine = (message: OverlayMessage, index: number) => {
    const existing = log.children[index] as HTMLParagraphElement | undefined;
    if (existing) {
      existing.dataset.final = message.final ? "true" : "false";
      existing.dataset.role = message.role === "user" ? "user" : "assistant";
      const body = existing.querySelector(".body");
      if (body) {
        body.textContent = message.text;
      }
      log.scrollTop = log.scrollHeight;
      return;
    }
    const line = doc.createElement("p");
    line.className = "msg";
    line.dataset.role = message.role === "user" ? "user" : "assistant";
    line.dataset.final = message.final ? "true" : "false";
    const who = doc.createElement("span");
    who.className = "who";
    who.textContent = `${speakerLabel(message.role)}:`;
    const body = doc.createElement("span");
    body.className = "body";
    body.textContent = message.text;
    line.appendChild(who);
    line.appendChild(doc.createTextNode(" "));
    line.appendChild(body);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  };

  const set = (status: VoiceStatus) => {
    root.dataset.status = status;
    statusEl.textContent = statusLabel(status);
  };
  set("listening");
  return {
    set,
    addMessage(role: string, text: string, final = false, itemId?: string) {
      upsertTranscript(messages, role, text, final, { itemId, speech });
      let index = messages.length - 1;
      const id = itemId?.trim();
      if (role === "user" && id) {
        const found = lastIndexWhere(messages, (message) => message.role === "user" && message.itemId === id);
        if (found >= 0) {
          index = found;
        }
      }
      if (index < 0) {
        return;
      }
      paintLine(messages[index], index);
    },
    handleDuplex(type: string) {
      applyOverlaySpeech(messages, speech, type);
      const index = messages.length - 1;
      if (index < 0) {
        return;
      }
      paintLine(messages[index], index);
    },
    finalize(role?: string) {
      const index = messages.length - 1;
      if (index < 0) {
        return;
      }
      finalizeTranscript(messages, role);
      paintLine(messages[index], index);
    },
    remove() {
      root.remove();
    },
  };
}
