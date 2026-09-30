import { downsample, floatToPcm16, PcmPreroll, schedulePcm } from "./audio";
import { authHandshake } from "./ingress";
import { SessionEndWatch } from "./session-end";
import { VoiceSession, type ServerMessage, type WebSocketLike } from "./session";
import { scheduleTimeout } from "./timers";

export const SAMPLE_RATE = 24000;

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

export async function captureMic(
  context: AudioContext,
  onPcm: (pcm: ArrayBuffer) => void,
): Promise<{ stop(): void }> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const mute = context.createGain();
  mute.gain.value = 0;
  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    onPcm(floatToPcm16(downsample(input, context.sampleRate, SAMPLE_RATE)));
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);
  return {
    stop() {
      processor.disconnect();
      source.disconnect();
      mute.disconnect();
      for (const track of stream.getTracks()) {
        track.stop();
      }
    },
  };
}

export interface BrowserSessionOptions {
  url: string;
  token: string;
  ingress?: boolean;
  area?: { id?: string; name: string };
  device?: { id?: string; name: string };
  onTranscript?: (role: string, text: string, final?: boolean, itemId?: string) => void;
  onServerText?: (message: ServerMessage) => void;
}

export async function createBrowserSession(
  options: BrowserSessionOptions,
): Promise<{ session: VoiceSession }> {
  const context = new AudioContext({ sampleRate: SAMPLE_RATE });
  if (context.state === "suspended") {
    await context.resume();
  }
  const nextTime = { t: 0 };
  let mic: { stop(): void } | null = null;
  let closed = false;
  const session = new VoiceSession(
    (pcm) => schedulePcm(context, pcm, nextTime),
    () => openSocket(options.url, options.token, session, options.ingress === true, options.area, options.device),
  );
  const originalFinish = session.finish.bind(session);
  const endWatch = new SessionEndWatch({
    onEnd: (reason) => session.finish(reason),
  });
  session.finish = (reason) => {
    endWatch.dispose();
    originalFinish(reason);
  };
  session.onEnd((reason) => {
    if (closed) {
      return;
    }
    closed = true;
    mic?.stop();
    const remainingMs =
      reason === "error" || reason === "unauthorized"
        ? 0
        : Math.max(0, Math.ceil((nextTime.t - context.currentTime) * 1000));
    scheduleTimeout(() => {
      void context.close();
    }, remainingMs + 80);
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
    mic = await captureMic(context, (pcm) => session.sendMic(pcm));
  } catch (error) {
    session.finish("error");
    throw error;
  }
  return { session };
}
