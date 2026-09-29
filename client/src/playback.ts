export interface Playable {
  stop(): void;
}

/** Schedules Grok audio and drops it the moment the user talks over a reply. */
export class PlaybackQueue {
  private sources: Playable[] = [];
  private acceptDeltas = false;

  responseStarted(): void {
    this.stopSources();
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
      source.stop();
    }
    this.sources = [];
  }
}
