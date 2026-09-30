export interface Playable {
  stop(): void;
}

/**
 * Schedules Grok audio. A later `response.created` (tool follow-up, a second
 * TTS generation) must not cut the sentence already queued. Only user barge-in
 * (`speech_started`) flushes playback.
 */
export class PlaybackQueue {
  private sources: Playable[] = [];
  private acceptDeltas = false;

  responseStarted(): void {
    this.acceptDeltas = true;
  }

  enqueue(pcm: ArrayBuffer, play: (pcm: ArrayBuffer) => Playable): boolean {
    if (!this.acceptDeltas) {
      return false;
    }
    this.sources.push(play(pcm));
    return true;
  }

  speechStarted(): void {
    this.acceptDeltas = false;
    this.stopSources();
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
