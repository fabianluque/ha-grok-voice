import { isClosingUtterance } from "./end-phrase";
import type { ServerMessage } from "./session";
import { cancelTimeout, scheduleTimeout } from "./timers";

export const DEFAULT_IDLE_MS = 20_000;

export interface SessionEndWatchOptions {
  idleMs?: number;
  onEnd(reason: "done" | "idle"): void;
  setTimer?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (id: ReturnType<typeof setTimeout>) => void;
}

/**
 * Hang up after a goodbye phrase, or after configured silence once the
 * assistant has finished and the user is not mid-utterance.
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
      this.arm();
      return null;
    }
    if (message.type === "response_done") {
      this.assistantBusy = false;
      this.arm();
      return null;
    }
    if (
      message.type === "transcript" &&
      message.role === "user" &&
      message.final !== false &&
      isClosingUtterance(message.text)
    ) {
      this.dispose();
      this.onEnd("done");
      return "done";
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
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.onEnd("idle");
    }, this.idleMs);
  }

  private clear(): void {
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
  }
}
