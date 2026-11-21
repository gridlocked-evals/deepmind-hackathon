/** Short, explicit human pulses. Deduplicate DOM repeats and serialize provider controls. */
export class HumanControls {
  private held = new Set<string>();
  private queue: object[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private lastSent = -Infinity;
  constructor(private send: (value: object) => void) {}
  private flush = () => {
    this.timer = null;
    const value = this.queue.shift(); if (!value) return;
    this.send(value); this.lastSent = Date.now();
    if (this.queue.length) this.timer = setTimeout(this.flush, 35);
  };
  private enqueue(value: object) {
    this.queue.push(value);
    if (!this.timer) {
      const delay = Math.max(0, 35 - (Date.now() - this.lastSent));
      if (delay) this.timer = setTimeout(this.flush, delay); else this.flush();
    }
  }
  keyDown(key: string, repeat = false) {
    if (repeat || this.held.has(key)) return;
    this.held.add(key); this.enqueue({ kind: 'key', owner: 'human', key, hold_ms: 200 });
  }
  keyUp(key: string) { if (this.held.has(key)) this.release(); }
  release() {
    // Discard movement that has not reached the provider when the view loses focus.
    this.queue = this.queue.filter(value => (value as { kind: string }).kind !== 'key');
    if (!this.held.size) return;
    this.held.clear();
    if (!this.queue.some(value => (value as { kind: string }).kind === 'release')) this.enqueue({ kind: 'release' });
  }
  takeover() { this.release(); this.enqueue({ kind: 'takeover', owner: 'human' }); }
  stop() { this.clear(); this.send({ kind: 'stop' }); }
  clear() { if (this.timer) clearTimeout(this.timer); this.timer = null; this.queue = []; this.held.clear(); }
}
