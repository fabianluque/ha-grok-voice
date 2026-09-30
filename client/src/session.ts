import { PlaybackQueue, type Playable } from "./playback";
import { MicHold } from "./mic-hold";

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
  idleTimeoutSeconds?: number;
}

export class VoiceSession {
  captureActive = false;
  private readonly playback = new PlaybackQueue();
  private readonly hold = new MicHold();
  private socket: WebSocketLike | null = null;
  private endHandler: ((reason: string) => void) | null = null;
  private ended = false;
  private endReason: string | null = null;
  private live = false;

  constructor(
    private readonly play: (pcm: ArrayBuffer) => Playable,
    private readonly socketFactory: () => WebSocketLike,
  ) {}

  onEnd(handler: (reason: string) => void): void {
    this.endHandler = handler;
    if (this.ended) {
      handler(this.endReason ?? "end");
    }
  }

  /** Accept mic frames before the WebSocket exists (wake pre-roll). */
  armCapture(): void {
    this.captureActive = true;
  }

  /** Prepend PCM captured before this session owned the mic callback. */
  importHold(hold: MicHold): void {
    for (const chunk of hold.drain()) {
      this.sendMic(chunk);
    }
  }

  async start(): Promise<void> {
    this.captureActive = true;
    if (!this.socket) {
      this.socket = this.socketFactory();
    }
    this.flushIfLive();
  }

  /** Mic audio keeps flowing while a reply is playing. */
  sendMic(pcm: ArrayBuffer): void {
    if (this.ended || !this.captureActive) {
      return;
    }
    if (!this.live || !this.socket) {
      this.hold.push(pcm);
      return;
    }
    this.socket.send(pcm);
  }

  handleServerText(message: ServerMessage): void {
    if (message.type === "ready") {
      this.goLive();
    } else if (message.type === "response_started") {
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
    this.ended = true;
    this.endReason = reason;
    this.captureActive = false;
    this.live = false;
    this.hold.drain();
    this.playback.speechStarted();
    if (this.socket && (reason === "done" || reason === "idle" || reason === "stop")) {
      try {
        this.socket.send(JSON.stringify({ type: "stop", reason }));
      } catch {
        // Socket may already be closing.
      }
    }
    this.socket?.close();
    this.endHandler?.(reason);
  }

  private goLive(): void {
    if (this.ended) {
      return;
    }
    this.live = true;
    this.flushIfLive();
  }

  private flushIfLive(): void {
    if (!this.live || !this.socket || this.ended) {
      return;
    }
    for (const chunk of this.hold.drain()) {
      this.socket.send(chunk);
    }
  }
}
