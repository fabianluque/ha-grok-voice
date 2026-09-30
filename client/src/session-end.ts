import { isClosingUtterance, isOpenFollowup } from "./end-phrase";
import type { ServerMessage } from "./session";
import { cancelTimeout, scheduleTimeout } from "./timers";

export const DEFAULT_IDLE_MS = 30_000;

/** Extra quiet time after a follow-up question so TTS drain + thinking fit. */
export const FOLLOWUP_IDLE_GRACE_MS = 15_000;

/** If a closer has no ack turn yet, wait this long for Grok to start speaking. */
export const ACK_GRACE_MS = 2_500;

export interface SessionEndWatchOptions {
  idleMs?: number;
  onEnd(reason: "done" | "idle"): void;
  setTimer?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (id: ReturnType<typeof setTimeout>) => void;
}

/**
 * Hang up after a goodbye phrase, or after configured silence once the
 * assistant has finished and the user is not mid-utterance.
 *
 * A closer does not hang up until Grok's ack turn has finished generating
 * (`response_done`). Playback drain happens in `VoiceSession.finish`.
 */
export class SessionEndWatch {
  userSpeaking = false;
  assistantBusy = false;
  private idleMs: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly onEnd: (reason: "done" | "idle") => void;
  private readonly setTimer: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  private readonly clearTimer: (id: ReturnType<typeof setTimeout>) => void;
  private armed = false;
  private pendingDone = false;
  private assistantText = "";

  constructor(options: SessionEndWatchOptions) {
    this.idleMs = options.idleMs ?? DEFAULT_IDLE_MS;
    this.onEnd = options.onEnd;
    this.setTimer = options.setTimer ?? scheduleTimeout;
    this.clearTimer = options.clearTimer ?? cancelTimeout;
  }

  setIdleMs(idleMs: number): void {
    if (!Number.isFinite(idleMs) || idleMs < 1000) {
      return;
    }
    this.idleMs = idleMs;
    if (this.armed) {
      this.arm();
    }
  }

  handle(message: ServerMessage): "done" | "idle" | null {
    if (message.type === "ready") {
      if (typeof message.idleTimeoutSeconds === "number") {
        this.setIdleMs(message.idleTimeoutSeconds * 1000);
      }
      this.armed = true;
      this.arm();
      return null;
    }
    if (message.type === "speech_started") {
      this.userSpeaking = true;
      this.assistantText = "";
      this.arm();
      return null;
    }
    if (message.type === "speech_stopped") {
      this.userSpeaking = false;
      this.arm();
      return null;
    }
    if (message.type === "response_started") {
      this.assistantBusy = true;
      this.assistantText = "";
      this.arm();
      return null;
    }
    if (message.type === "transcript" && message.role === "assistant" && message.text) {
      this.assistantText = message.text;
      if (isOpenFollowup(message.text) && this.pendingDone) {
        this.pendingDone = false;
        this.arm();
      }
      return null;
    }
    if (message.type === "response_done") {
      this.assistantBusy = false;
      if (this.pendingDone && !isOpenFollowup(this.assistantText)) {
        this.dispose();
        this.onEnd("done");
        return "done";
      }
      this.pendingDone = false;
      this.arm();
      return null;
    }
    if (
      message.type === "transcript" &&
      message.role === "user" &&
      message.final === true &&
      isClosingUtterance(message.text)
    ) {
      this.pendingDone = true;
      this.arm();
      return null;
    }
    return null;
  }

  dispose(): void {
    this.clear();
    this.armed = false;
  }

  private arm(): void {
    this.clear();
    if (!this.armed || this.userSpeaking || this.assistantBusy) {
      return;
    }
    const followup = !this.pendingDone && isOpenFollowup(this.assistantText);
    const ms = this.pendingDone ? ACK_GRACE_MS : this.idleMs + (followup ? FOLLOWUP_IDLE_GRACE_MS : 0);
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.onEnd(this.pendingDone ? "done" : "idle");
    }, ms);
  }

  private clear(): void {
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
  }
}
