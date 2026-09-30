export interface Playable {
  stop(): void;
  /** Resolves when this chunk has finished playing (or was stopped). */
  ended?: Promise<void>;
}

/**
 * Schedules Grok audio. A later `response.created` (tool follow-up, a second
 * TTS generation) must not cut the sentence already queued. Only user barge-in
 * (`speech_started`) flushes playback. Hang-up waits for `waitUntilDrained`
 * so an end-of-session ack can finish; mid-session tool TTS does not.
 */
export class PlaybackQueue {
  private sources: Playable[] = [];
  private acceptDeltas = false;
  private inflight = 0;
  private drainWaiters: Array<() => void> = [];

  responseStarted(): void {
    this.acceptDeltas = true;
  }

  enqueue(pcm: ArrayBuffer, play: (pcm: ArrayBuffer) => Playable): boolean {
    if (!this.acceptDeltas) {
      return false;
    }
    const source = play(pcm);
    this.sources.push(source);
    if (source.ended) {
      this.inflight += 1;
      void source.ended.then(
        () => this.sourceSettled(),
        () => this.sourceSettled(),
      );
    }
    return true;
  }

  speechStarted(): void {
    this.acceptDeltas = false;
    this.stopSources();
  }

  /** Keep playing queued audio, but do not accept further TTS (hang-up drain). */
  stopAccepting(): void {
    this.acceptDeltas = false;
  }

  /** True while a Playable.ended promise is still outstanding. */
  hasQueuedAudio(): boolean {
    return this.inflight > 0;
  }

  /** Resolves when every queued/playing chunk has finished (or been flushed). */
  waitUntilDrained(): Promise<void> {
    if (this.inflight === 0) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.drainWaiters.push(resolve);
    });
  }

  private sourceSettled(): void {
    this.inflight = Math.max(0, this.inflight - 1);
    if (this.inflight === 0) {
      const waiters = this.drainWaiters;
      this.drainWaiters = [];
      for (const waiter of waiters) {
        waiter();
      }
    }
  }

  private stopSources(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // Buffer sources throw if they already finished.
      }
    }
    this.sources = [];
  }
}
