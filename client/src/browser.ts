import { downsample, floatToPcm16, PcmPreroll, schedulePcm } from "./audio";
import { authHandshake } from "./ingress";
import { SessionEndWatch } from "./session-end";
import { VoiceSession, type ServerMessage, type WebSocketLike } from "./session";
import { scheduleTimeout } from "./timers";

export const SAMPLE_RATE = 24000;

/** Wait at most this long for a previous AudioContext.close() before opening a new one. */
export const CONTEXT_HANDOFF_MS = 250;

export const MIC_GRAPH_ABORTED = "session ended before mic graph";
export const AUDIO_CONTEXT_CLOSED = "audio context closed";

export interface DuplexSocket {
  binaryType: string;
  addEventListener(type: "open" | "message" | "close", handler: (event: { data?: unknown }) => void): void;
  send(data: string | ArrayBuffer): void;
  close(): void;
}

export function openSocket(
  url: string,
  token: string,
  session: VoiceSession,
  ingress = false,
  area?: { id?: string; name: string },
  device?: { id?: string; name: string },
  socketFactory: (url: string) => DuplexSocket = (socketUrl) => new WebSocket(socketUrl),
): WebSocketLike {
  const socket = socketFactory(url);
  const preroll = new PcmPreroll();
  const pendingControl: string[] = [];
  let audioOpen = false;
  const flushAudio = () => {
    if (audioOpen) {
      return;
    }
    audioOpen = true;
    for (const chunk of preroll.drain()) {
      socket.send(chunk);
    }
    for (const message of pendingControl) {
      socket.send(message);
    }
    pendingControl.length = 0;
  };
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify(authHandshake({ ingress, token, url, area, device })));
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      const message = JSON.parse(event.data) as ServerMessage;
      if (message.type === "ready") {
        flushAudio();
      }
      session.handleServerText(message);
      return;
    }
    session.handleServerBinary(event.data as ArrayBuffer);
  });
  socket.addEventListener("close", () => {
    session.finish("closed");
  });
  return {
    send(data) {
      if (!audioOpen) {
        if (typeof data === "string") {
          pendingControl.push(data);
          return;
        }
        preroll.push(data);
        return;
      }
      socket.send(data);
    },
    close() {
      socket.close();
    },
  };
}

export interface AudioContextLike {
  state: string;
  resume?: () => Promise<void>;
}

export function isAudioContextOpen(context: { state: string }): boolean {
  return context.state !== "closed";
}

/**
 * Resume a live context, or create a new one if the previous session already
 * closed (or is closing) it. Never build a mic graph on a closed context.
 */
export async function ensureOpenAudioContext<T extends AudioContextLike>(
  context: T | null | undefined,
  create: () => T,
): Promise<T> {
  let ctx = context && isAudioContextOpen(context) ? context : create();
  if (ctx.state === "suspended" && typeof ctx.resume === "function") {
    await ctx.resume();
  }
  if (!isAudioContextOpen(ctx)) {
    ctx = create();
    if (ctx.state === "suspended" && typeof ctx.resume === "function") {
      await ctx.resume();
    }
  }
  return ctx;
}

let previousContextClose: Promise<void> = Promise.resolve();

/** Test hook. Production boot does not call this. */
export function resetAudioContextGate(): void {
  previousContextClose = Promise.resolve();
}

export function scheduleContextClose(
  context: { state: string; close: () => unknown },
  delayMs: number,
): Promise<void> {
  previousContextClose = new Promise<void>((resolve) => {
    scheduleTimeout(() => {
      if (!isAudioContextOpen(context)) {
        resolve();
        return;
      }
      void Promise.resolve(context.close())
        .catch(() => undefined)
        .finally(resolve);
    }, Math.max(0, delayMs));
  });
  return previousContextClose;
}

export async function waitForPreviousAudioContext(maxMs = CONTEXT_HANDOFF_MS): Promise<void> {
  await Promise.race([
    previousContextClose,
    new Promise<void>((resolve) => {
      scheduleTimeout(resolve, maxMs);
    }),
  ]);
}

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) {
    track.stop();
  }
}

export interface CaptureMicOptions {
  isLive?: () => boolean;
  replaceContext?: (current: AudioContext) => Promise<AudioContext>;
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
}

function attachMicGraph(
  context: AudioContext,
  stream: MediaStream,
  onPcm: (pcm: ArrayBuffer) => void,
): { stop(): void } {
  if (!isAudioContextOpen(context)) {
    throw new Error(AUDIO_CONTEXT_CLOSED);
  }
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const mute = context.createGain();
  mute.gain.value = 0;
  processor.onaudioprocess = (event) => {
    if (!isAudioContextOpen(context)) {
      return;
    }
    const input = event.inputBuffer.getChannelData(0);
    onPcm(floatToPcm16(downsample(input, context.sampleRate, SAMPLE_RATE)));
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);
  return {
    stop() {
      try {
        processor.disconnect();
        source.disconnect();
        mute.disconnect();
      } catch {
        // Nodes may already be gone if the context closed.
      }
      stopTracks(stream);
    },
  };
}

export async function captureMic(
  context: AudioContext,
  onPcm: (pcm: ArrayBuffer) => void,
  options?: CaptureMicOptions,
): Promise<{ stop(): void; context: AudioContext }> {
  const getUserMedia =
    options?.getUserMedia ?? ((constraints: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(constraints));
  const stream = await getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const live = options?.isLive ?? (() => true);
  if (!live()) {
    stopTracks(stream);
    throw new Error(MIC_GRAPH_ABORTED);
  }
  let ctx = context;
  try {
    ctx = options?.replaceContext
      ? await options.replaceContext(ctx)
      : await ensureOpenAudioContext(ctx, () => new AudioContext({ sampleRate: SAMPLE_RATE }));
  } catch (error) {
    stopTracks(stream);
    throw error;
  }
  if (!live()) {
    stopTracks(stream);
    throw new Error(MIC_GRAPH_ABORTED);
  }
  if (!isAudioContextOpen(ctx)) {
    stopTracks(stream);
    throw new Error(AUDIO_CONTEXT_CLOSED);
  }
  try {
    const graph = attachMicGraph(ctx, stream, onPcm);
    return { ...graph, context: ctx };
  } catch (error) {
    stopTracks(stream);
    throw error;
  }
}

export interface BrowserSessionOptions {
  url: string;
  token: string;
  ingress?: boolean;
  area?: { id?: string; name: string };
  device?: { id?: string; name: string };
  onTranscript?: (role: string, text: string, final?: boolean, itemId?: string) => void;
  onServerText?: (message: ServerMessage) => void;
  socketFactory?: (url: string) => DuplexSocket;
  createAudioContext?: () => AudioContext;
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
}

export async function createBrowserSession(
  options: BrowserSessionOptions,
): Promise<{ session: VoiceSession }> {
  await waitForPreviousAudioContext();
  const createAudioContext = options.createAudioContext ?? (() => new AudioContext({ sampleRate: SAMPLE_RATE }));
  let context = await ensureOpenAudioContext(null, createAudioContext);
  const nextTime = { t: 0 };
  let mic: { stop(): void } | null = null;
  let closed = false;
  let capturing = true;
  const session = new VoiceSession(
    (pcm) => schedulePcm(context, pcm, nextTime),
    () =>
      openSocket(
        options.url,
        options.token,
        session,
        options.ingress === true,
        options.area,
        options.device,
        options.socketFactory,
      ),
  );
  const originalFinish = session.finish.bind(session);
  const endWatch = new SessionEndWatch({
    onEnd: (reason) => session.finish(reason),
  });
  session.finish = (reason) => {
    endWatch.dispose();
    originalFinish(reason);
  };
  const closeContextSoon = (delayMs: number) => {
    scheduleContextClose(context, delayMs);
  };
  session.onEnd((reason) => {
    if (closed) {
      return;
    }
    closed = true;
    mic?.stop();
    if (capturing) {
      // getUserMedia is still in flight. captureMic will abort without
      // attaching to this context; close after that promise settles.
      return;
    }
    const remainingMs =
      reason === "error" || reason === "unauthorized"
        ? 0
        : Math.max(0, Math.ceil((nextTime.t - context.currentTime) * 1000));
    closeContextSoon(remainingMs + 80);
  });
  const wrapped = session.handleServerText.bind(session);
  session.handleServerText = (message) => {
    if (message.type === "transcript" && message.text) {
      options.onTranscript?.(message.role || "assistant", message.text, message.final === true, message.itemId);
    }
    // Only barge-in jumps the playback cursor. A tool follow-up
    // `response_started` must append after audio already scheduled.
    // Hang-up has already stopped capture so barge-in cannot cut ack TTS.
    if (message.type === "speech_started" && session.captureActive) {
      nextTime.t = context.currentTime;
    }
    options.onServerText?.(message);
    if (endWatch.handle(message)) {
      return;
    }
    wrapped(message);
  };
  try {
    // Open the duplex socket while getUserMedia is in flight so VAD/ready
    // is not serialized behind the mic prompt.
    await session.start();
    const captured = await captureMic(context, (pcm) => session.sendMic(pcm), {
      isLive: () => !closed && !session.isEnded,
      getUserMedia: options.getUserMedia,
      replaceContext: async (current) => {
        if (closed || session.isEnded) {
          return current;
        }
        const next = await ensureOpenAudioContext(current, createAudioContext);
        if (next !== current) {
          context = next;
          nextTime.t = 0;
        }
        return next;
      },
    });
    context = captured.context;
    mic = captured;
    capturing = false;
    if (closed || session.isEnded) {
      mic.stop();
      closeContextSoon(0);
      throw new Error(MIC_GRAPH_ABORTED);
    }
  } catch (error) {
    capturing = false;
    if (!closed) {
      session.finish("error");
    } else {
      closeContextSoon(0);
    }
    throw error;
  }
  return { session };
}
