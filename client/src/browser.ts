import { downsample, floatToPcm16, schedulePcm } from "./audio";
import { authHandshake } from "./ingress";
import { MicHold } from "./mic-hold";
import { SessionEndWatch } from "./session-end";
import { VoiceSession, type ServerMessage, type WebSocketLike } from "./session";

export const SAMPLE_RATE = 24000;

export function openSocket(
  url: string,
  token: string,
  session: VoiceSession,
  ingress = false,
  area?: { id?: string; name: string },
): WebSocketLike {
  const socket = new WebSocket(url);
  const pending: Array<ArrayBuffer | string> = [];
  let open = false;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify(authHandshake({ ingress, token, url, area })));
    open = true;
    for (const chunk of pending) {
      socket.send(chunk);
    }
    pending.length = 0;
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      session.handleServerText(JSON.parse(event.data) as ServerMessage);
    } else {
      session.handleServerBinary(event.data as ArrayBuffer);
    }
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
): Promise<{ stop(): void; setOnPcm(next: (pcm: ArrayBuffer) => void): void }> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(2048, 1, 1);
  const mute = context.createGain();
  mute.gain.value = 0;
  let handler = onPcm;
  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    handler(floatToPcm16(downsample(input, context.sampleRate, SAMPLE_RATE)));
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);
  return {
    setOnPcm(next) {
      handler = next;
    },
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

export interface PrimedMic {
  context: AudioContext;
  hold: MicHold;
  stop(): void;
  setOnPcm(next: (pcm: ArrayBuffer) => void): void;
}

let priming: Promise<PrimedMic> | null = null;

async function openPrimedMic(): Promise<PrimedMic> {
  const hold = new MicHold();
  const context = new AudioContext({ sampleRate: SAMPLE_RATE });
  if (context.state === "suspended") {
    await context.resume();
  }
  const mic = await captureMic(context, (pcm) => hold.push(pcm));
  return {
    context,
    hold,
    stop: () => mic.stop(),
    setOnPcm: (next) => mic.setOnPcm(next),
  };
}

/**
 * Start getUserMedia as soon as wake fires (in parallel with Assist cancel
 * settle) so the first syllables are in the hold before the duplex socket.
 */
export function primeMicCapture(): Promise<PrimedMic> {
  if (!priming) {
    priming = openPrimedMic().catch((error) => {
      priming = null;
      throw error;
    });
  }
  return priming;
}

function takePrimedMic(): Promise<PrimedMic> | null {
  const current = priming;
  priming = null;
  return current;
}

/** Test hook. */
export function resetPrimedMic(): void {
  priming = null;
}

export interface BrowserSessionOptions {
  url: string;
  token: string;
  ingress?: boolean;
  area?: { id?: string; name: string };
  onTranscript?: (role: string, text: string, final?: boolean) => void;
  onServerText?: (message: ServerMessage) => void;
}

export async function createBrowserSession(
  options: BrowserSessionOptions,
): Promise<{ session: VoiceSession }> {
  let primed: PrimedMic | null = null;
  const pendingPrime = takePrimedMic();
  if (pendingPrime) {
    try {
      primed = await pendingPrime;
    } catch {
      primed = null;
    }
  }

  const context = primed?.context ?? new AudioContext({ sampleRate: SAMPLE_RATE });
  if (context.state === "suspended") {
    await context.resume();
  }
  const nextTime = { t: 0 };
  let micStop = primed ? () => primed!.stop() : null;
  let closed = false;
  const session = new VoiceSession(
    (pcm) => schedulePcm(context, pcm, nextTime),
    () => openSocket(options.url, options.token, session, options.ingress === true, options.area),
  );
  session.armCapture();
  const originalFinish = session.finish.bind(session);
  const endWatch = new SessionEndWatch({
    onEnd: (reason) => session.finish(reason),
  });
  session.finish = (reason) => {
    if (closed) {
      return;
    }
    closed = true;
    endWatch.dispose();
    micStop?.();
    void context.close();
    originalFinish(reason);
  };
  const wrapped = session.handleServerText.bind(session);
  session.handleServerText = (message) => {
    if (message.type === "transcript" && message.text) {
      options.onTranscript?.(message.role || "assistant", message.text, message.final !== false);
    }
    // Only barge-in jumps the playback cursor. A tool follow-up
    // `response_started` must append after audio already scheduled.
    if (message.type === "speech_started") {
      nextTime.t = context.currentTime;
    }
    options.onServerText?.(message);
    if (endWatch.handle(message)) {
      return;
    }
    wrapped(message);
  };
  try {
    if (primed) {
      primed.setOnPcm((pcm) => session.sendMic(pcm));
      session.importHold(primed.hold);
    }
    // Open the duplex socket without waiting for getUserMedia when the mic
    // was not already primed. Frames queue in VoiceSession until `ready`.
    await session.start();
    if (!primed) {
      const mic = await captureMic(context, (pcm) => session.sendMic(pcm));
      micStop = () => mic.stop();
    }
  } catch (error) {
    session.finish("error");
    throw error;
  }
  return { session };
}
