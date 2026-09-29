import { downsample, floatToPcm16, schedulePcm } from "./audio";
import { authHandshake } from "./ingress";
import { VoiceSession, type ServerMessage, type WebSocketLike } from "./session";

export const SAMPLE_RATE = 24000;

export function openSocket(
  url: string,
  token: string,
  session: VoiceSession,
  ingress = false,
): WebSocketLike {
  const socket = new WebSocket(url);
  const pending: Array<ArrayBuffer | string> = [];
  let open = false;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify(authHandshake({ ingress, token })));
    open = true;
    for (const chunk of pending) {
      socket.send(chunk);
    }
    pending.length = 0;
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      session.handleServerText(JSON.parse(event.data) as ServerMessage);
      return;
    }
    session.handleServerBinary(event.data as ArrayBuffer);
  });
  socket.addEventListener("close", () => {
    session.finish("closed");
  });
  return {
    send(data) {
      if (!open) {
        pending.push(data);
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
  onTranscript?: (role: string, text: string) => void;
  onServerText?: (message: ServerMessage) => void;
}

export async function resumeAudioContext(
  context: { state: string; resume(): Promise<void> },
  attempts = 3,
): Promise<void> {
  for (let attempt = 0; attempt < attempts && context.state === "suspended"; attempt += 1) {
    try {
      await context.resume();
    } catch (error) {
      if (attempt === attempts - 1) {
        throw error;
      }
      await new Promise((resolve) => {
        setTimeout(resolve, 200);
      });
    }
  }
}

export async function createBrowserSession(
  options: BrowserSessionOptions,
): Promise<{ session: VoiceSession }> {
  const context = new AudioContext({ sampleRate: SAMPLE_RATE });
  await resumeAudioContext(context);
  const nextTime = { t: 0 };
  let mic: { stop(): void } | null = null;
  let closed = false;
  const session = new VoiceSession(
    (pcm) => schedulePcm(context, pcm, nextTime),
    () => openSocket(options.url, options.token, session, options.ingress === true),
  );
  const originalFinish = session.finish.bind(session);
  session.finish = (reason) => {
    if (closed) {
      return;
    }
    closed = true;
    mic?.stop();
    void context.close();
    originalFinish(reason);
  };
  const wrapped = session.handleServerText.bind(session);
  session.handleServerText = (message) => {
    if (message.type === "transcript" && message.text) {
      options.onTranscript?.(message.role || "assistant", message.text);
    }
    if (message.type === "response_started" || message.type === "speech_started") {
      nextTime.t = context.currentTime;
    }
    options.onServerText?.(message);
    wrapped(message);
  };
  try {
    mic = await captureMic(context, (pcm) => session.sendMic(pcm));
  } catch (error) {
    void context.close();
    throw error;
  }
  return { session };
}
