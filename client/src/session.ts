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
  idleTimeoutSeconds?: number;
}

export class VoiceSession {
  captureActive = false;
  private readonly playback = new PlaybackQueue();
  private socket: WebSocketLike | null = null;
  private endHandler: ((reason: string) => void) | null = null;
  private ended = false;

  constructor(
    private readonly play: (pcm: ArrayBuffer) => Playable,
    private readonly socketFactory: () => WebSocketLike,
  ) {}

  onEnd(handler: (reason: string) => void): void {
    this.endHandler = handler;
  }

  async start(): Promise<void> {
    this.captureActive = true;
    this.socket = this.socketFactory();
  }

  /** Mic audio keeps flowing while a reply is playing. */
  sendMic(pcm: ArrayBuffer): void {
    if (!this.captureActive || !this.socket) {
      return;
    }
    this.socket.send(pcm);
  }

  handleServerText(message: ServerMessage): void {
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
    this.ended = true;
    this.captureActive = false;
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
}
