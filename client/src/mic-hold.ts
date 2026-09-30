/** 24 kHz PCM16 = 48 bytes per millisecond. */
export const PCM16_BYTES_PER_MS = 48;

/** Keep at most this much uplink audio while the socket / Grok session catches up. */
export const MIC_HOLD_MS = 1500;

export const MIC_HOLD_MAX_BYTES = PCM16_BYTES_PER_MS * MIC_HOLD_MS;

/**
 * FIFO of mic PCM captured before the duplex uplink can accept it.
 * Drops the oldest frames when the cap is reached so a slow MCP/tools
 * handshake cannot grow without bound.
 */
export class MicHold {
  private chunks: ArrayBuffer[] = [];
  private bytes = 0;

  constructor(private readonly maxBytes = MIC_HOLD_MAX_BYTES) {}

  get byteLength(): number {
    return this.bytes;
  }

  get size(): number {
    return this.chunks.length;
  }

  push(pcm: ArrayBuffer): void {
    if (pcm.byteLength === 0) {
      return;
    }
    this.chunks.push(pcm);
    this.bytes += pcm.byteLength;
    while (this.bytes > this.maxBytes && this.chunks.length > 0) {
      const dropped = this.chunks.shift();
      if (!dropped) {
        break;
      }
      this.bytes -= dropped.byteLength;
    }
    if (this.bytes < 0) {
      this.bytes = 0;
    }
  }

  drain(): ArrayBuffer[] {
    const out = this.chunks;
    this.chunks = [];
    this.bytes = 0;
    return out;
  }
}
