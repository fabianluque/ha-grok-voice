import { downsample, floatToPcm16, schedulePcm } from "./audio";
import { accessToken, resolveVoiceSocketUrl, type HassLike } from "./ingress";
import { VoiceSession } from "./session";
import { installGrokVoice, type KioskApi } from "./wake";

interface KioskWindow extends Window {
  kioskSatellite?: KioskApi;
  GROK_VOICE_URL?: string;
}

const SAMPLE_RATE = 24000;

function pageHass(): HassLike | null {
  const root = document.querySelector("home-assistant") as { hass?: HassLike } | null;
  return root?.hass ?? null;
}

function showLine(overlay: HTMLElement, role: string, text: string): void {
  const line = document.createElement("p");
  line.textContent = `${role === "user" ? "You" : "Grok"}: ${text}`;
  overlay.appendChild(line);
}

function openSocket(url: string, token: string, session: VoiceSession): { send(data: ArrayBuffer | string): void; close(): void } {
  const socket = new WebSocket(url);
  const pending: Array<ArrayBuffer | string> = [];
  let open = false;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "auth", token }));
    open = true;
    for (const chunk of pending) {
      socket.send(chunk);
    }
    pending.length = 0;
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      session.handleServerText(JSON.parse(event.data));
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

async function captureMic(
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

function boot(): void {
  const kiosk = (window as KioskWindow).kioskSatellite;
  installGrokVoice({
    kiosk,
    addEventListener: (type, handler) => window.addEventListener(type, handler),
    openSession: async () => {
      const hass = pageHass();
      const explicit = (window as KioskWindow).GROK_VOICE_URL;
      const url = explicit || (hass ? await resolveVoiceSocketUrl(hass, location.protocol, location.host) : "");
      if (!url || !hass) {
        throw new Error("Home Assistant ingress is not available on this page");
      }
      const token = accessToken(hass);
      const overlay = document.createElement("div");
      overlay.id = "grok-voice-overlay";
      overlay.style.cssText = "position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;color:white;font:16px sans-serif;text-shadow:0 1px 2px black;";
      document.body.appendChild(overlay);
      const context = new AudioContext({ sampleRate: SAMPLE_RATE });
      const nextTime = { t: 0 };
      let mic: { stop(): void } | null = null;
      let closed = false;
      const session = new VoiceSession(
        (pcm) => schedulePcm(context, pcm, nextTime),
        () => openSocket(url, token, session),
      );
      const originalFinish = session.finish.bind(session);
      session.finish = (reason) => {
        if (closed) {
          return;
        }
        closed = true;
        mic?.stop();
        overlay.remove();
        void context.close();
        originalFinish(reason);
      };
      const wrapped = session.handleServerText.bind(session);
      session.handleServerText = (message) => {
        if (message.type === "transcript" && message.text) {
          showLine(overlay, message.role || "assistant", message.text);
        }
        if (message.type === "response_started") {
          nextTime.t = context.currentTime;
        }
        if (message.type === "speech_started") {
          nextTime.t = context.currentTime;
        }
        wrapped(message);
      };
      mic = await captureMic(context, (pcm) => session.sendMic(pcm));
      return session;
    },
  });
}

boot();
