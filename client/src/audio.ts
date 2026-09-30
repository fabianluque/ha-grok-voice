export function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) {
    return input;
  }
  const ratio = fromRate / toRate;
  const length = Math.max(0, Math.floor(input.length / ratio));
  const output = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let sample = start; sample < end; sample += 1) {
      sum += input[sample];
      count += 1;
    }
    output[index] = count === 0 ? 0 : sum / count;
  }
  return output;
}

export function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return buffer;
}

export function pcm16ToFloat(pcm: ArrayBuffer): Float32Array {
  const view = new DataView(pcm);
  const samples = new Float32Array(pcm.byteLength / 2);
  for (let index = 0; index < samples.length; index += 1) {
    const value = view.getInt16(index * 2, true);
    samples[index] = value < 0 ? value / 0x8000 : value / 0x7fff;
  }
  return samples;
}

export interface AudioClock {
  currentTime: number;
  createBuffer(channels: number, length: number, sampleRate: number): AudioBufferLike;
  createBufferSource(): BufferSourceLike;
  destination: unknown;
}

export interface AudioBufferLike {
  duration: number;
  copyToChannel(samples: Float32Array, channel: number): void;
}

export interface BufferSourceLike {
  buffer: AudioBufferLike | null;
  connect(destination: unknown): void;
  start(when: number): void;
  stop(): void;
}

export function schedulePcm(
  context: AudioClock,
  pcm: ArrayBuffer,
  nextTime: { t: number },
): { stop(): void } {
  const samples = pcm16ToFloat(pcm);
  const buffer = context.createBuffer(1, samples.length, 24000);
  buffer.copyToChannel(samples, 0);
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(context.destination);
  const startAt = Math.max(context.currentTime, nextTime.t);
  source.start(startAt);
  nextTime.t = startAt + buffer.duration;
  return {
    stop() {
      try {
        source.stop();
      } catch {
        // Already finished or never started.
      }
    },
  };
}
