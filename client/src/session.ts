import { PlaybackQueue, type Playable } from "./playback";

export interface WebSocketLike {
  send(data: ArrayBuffer | string): void;
  close(): void;
}

export interface ServerMessage {
  type: string;
  reason?: string;
  role?: string;
  text?: string;
  name?: string;
  status?: string;
  final?: boolean;
  itemId?: string;
  idleTimeoutSeconds?: number;
}

/** Page teardown: close immediately. Do not wait for ack TTS. */
export function isUnloadHangup(reason: string): boolean {
  return reason === "unload";
}

/** Hang-up reasons that should let queued ack TTS finish first. */
export function hangupWaitsForPlayback(reason: string): boolean {
  if (isUnloadHangup(reason)) {
    return false;
  }
  return reason === "done" || reason === "idle" || reason === "stop" || reason === "closed";
}

export class VoiceSession {
  captureActive = false;
  private readonly playback = new PlaybackQueue();
  private socket: WebSocketLike | null = null;
  private readonly endHandlers: Array<(reason: string) => void> = [];
  private ended = false;
  private hangingUp = false;
  private endReason: string | null = null;

  constructor(
    private readonly play: (pcm: ArrayBuffer) => Playable,
    private readonly socketFactory: () => WebSocketLike,
  ) {}

  get isEnded(): boolean {
    return this.ended;
  }

  onEnd(handler: (reason: string) => void): void {
    this.endHandlers.push(handler);
    if (this.ended) {
      handler(this.endReason ?? "end");
    }
  }

  async start(): Promise<void> {
    if (this.ended || this.hangingUp) {
      return;
    }
    this.captureActive = true;
    if (!this.socket) {
      this.socket = this.socketFactory();
    }
  }

  /** Mic audio keeps flowing while a reply is playing. */
  sendMic(pcm: ArrayBuffer): void {
    if (!this.captureActive || !this.socket) {
      return;
    }
    this.socket.send(pcm);
  }

  handleServerText(message: ServerMessage): void {
    if (this.hangingUp && message.type === "speech_started") {
      return;
    }
    if (message.type === "response_started") {
      this.playback.responseStarted();
    } else if (message.type === "speech_started") {
      this.playback.speechStarted();
    } else if (message.type === "end") {
      this.finish(message.reason ?? "end");
    }
  }

  handleServerBinary(pcm: ArrayBuffer): boolean {
    return this.playback.enqueue(pcm, this.play);
  }

  finish(reason: string): void {
    if (this.ended) {
      return;
    }
    if (this.hangingUp) {
      if (reason === "closed") {
        return;
      }
      if (reason === "error" || reason === "unauthorized") {
        this.playback.speechStarted();
        this.completeHangup(reason);
      }
      return;
    }
    this.hangingUp = true;
    this.endReason = reason;
    this.captureActive = false;
    this.playback.stopAccepting();
    if (!hangupWaitsForPlayback(reason)) {
      this.playback.speechStarted();
      this.completeHangup(reason);
      return;
    }
    if (!this.playback.hasQueuedAudio()) {
      this.completeHangup(reason);
      return;
    }
    void this.playback.waitUntilDrained().then(() => this.completeHangup(reason));
  }

  private completeHangup(reason: string): void {
    if (this.ended) {
      return;
    }
    this.ended = true;
    this.endReason = reason;
    if (this.socket && (reason === "done" || reason === "idle" || reason === "stop" || reason === "unload")) {
      try {
        this.socket.send(JSON.stringify({ type: "stop", reason }));
      } catch {
        // Socket may already be closing.
      }
    }
    this.socket?.close();
    for (const handler of this.endHandlers) {
      handler(reason);
    }
  }
}
