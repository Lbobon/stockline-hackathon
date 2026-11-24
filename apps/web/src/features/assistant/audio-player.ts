/**
 * Plays one agent audio segment as it arrives. MediaSource when the browser can stream that mime (mp3 in Chromium/Firefox),
 * so the first sentence plays before the turn is finished; otherwise the chunks are collected and played as one Blob.
 */
export interface PlayerCallbacks { onPlaying: () => void; onEnded: () => void }

// Mobile Safari only lets an <audio> element play without a tap if it was first played inside a tap: the demo phone
// (/phone) unlocks one element on "Accepter" and every segment then plays through it.
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
let shared: HTMLAudioElement | undefined;
export function unlockAudio() {
  shared ??= new Audio();
  shared.src = SILENT;
  void shared.play().catch(() => undefined);
}

export class SegmentPlayer {
  private el: HTMLAudioElement | undefined;
  private url: string | undefined;
  private ms: MediaSource | undefined;
  private sb: SourceBuffer | undefined;
  private queue: ArrayBuffer[] = [];
  private chunks: ArrayBuffer[] = [];
  private ended = false;
  private stopped = false;
  private playingFired = false;

  constructor(private mime: string, private cb: PlayerCallbacks) {
    const canStream = typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(mime);
    if (!canStream) return;
    const ms = new MediaSource();
    this.ms = ms;
    this.open(URL.createObjectURL(ms));
    ms.addEventListener("sourceopen", () => {
      if (this.stopped) return;
      try {
        this.sb = ms.addSourceBuffer(this.mime);
        this.sb.addEventListener("updateend", () => this.pump());
        this.pump();
      } catch {
        this.ms = undefined; // fall back to Blob
        this.release();
        if (this.ended) this.end();
      }
    }, { once: true });
  }

  private open(url: string) {
    this.url = url;
    const el = shared ?? new Audio(url);
    if (shared) el.src = url;
    this.el = el;
    el.onplaying = () => { if (!this.playingFired) { this.playingFired = true; this.cb.onPlaying(); } };
    el.onended = () => this.finish();
    el.onerror = () => this.finish();
    el.play().catch(() => { /* autoplay blocked or aborted: onerror/stop handle it */ });
  }

  private pump() {
    const sb = this.sb;
    if (!sb || sb.updating || this.stopped) return;
    const next = this.queue.shift();
    if (next) {
      try { sb.appendBuffer(next); } catch { /* quota / closed */ }
      return;
    }
    if (this.ended && this.ms?.readyState === "open") {
      try { this.ms.endOfStream(); } catch { /* ignore */ }
    }
  }

  push(buf: ArrayBuffer) {
    if (this.stopped) return;
    this.chunks.push(buf);
    if (this.ms) { this.queue.push(buf); this.pump(); }
  }

  end() {
    if (this.stopped) return;
    this.ended = true;
    if (this.ms) { this.pump(); return; } // streaming (or sourceopen pending): pump() ends the stream once drained
    // Blob fallback (or MediaSource refused the mime)
    this.release();
    if (!this.chunks.length) { this.finish(); return; }
    this.ms = undefined;
    this.open(URL.createObjectURL(new Blob(this.chunks, { type: this.mime })));
  }

  stop() {
    if (this.stopped) return;
    this.el?.pause();
    this.finish();
  }

  private finish() {
    if (this.stopped) return;
    this.stopped = true;
    this.release();
    this.cb.onEnded();
  }

  private release() {
    if (this.el) { this.el.onended = this.el.onerror = this.el.onplaying = null; this.el.pause(); }
    if (this.url) URL.revokeObjectURL(this.url);
    this.el = undefined;
    this.url = undefined;
  }
}
