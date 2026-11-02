// Framing a picture: a square window over it. Drag to move the picture, zoom with the slider,
// the mouse wheel or two fingers; arrow keys move it, + and - zoom. Sends "change" with
// {x, y, size} (see profiles.Frame) once a move or a zoom ends.
export default {
  template: `
    <div class="frame-editor">
      <div ref="viewport" class="frame-viewport" tabindex="0" role="application"
           aria-label="The picture's framing: drag to move it, use the slider or two fingers to zoom"
           @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up"
           @wheel.prevent="wheel" @keydown="key">
        <img :src="src" alt="" draggable="false" @load="loaded" :style="imageStyle">
        <div class="frame-grid" aria-hidden="true"></div>
      </div>
      <div class="frame-zoom">
        <span class="material-icons" aria-hidden="true">zoom_out</span>
        <input type="range" min="0" max="100" :value="zoomValue" aria-label="Zoom" @input="slide" @change="send">
        <span class="material-icons" aria-hidden="true">zoom_in</span>
      </div>
    </div>`,
  props: { src: String, frame: Object },
  data() {
    return { w: 0, h: 0, left: 0, top: 0, side: 0, view: 300, pointers: new Map(), pinch: null, timer: null };
  },
  computed: {
    imageStyle() {
      if (!this.side) return { visibility: 'hidden' };
      const k = this.view / this.side;
      return { width: `${this.w * k}px`, height: `${this.h * k}px`,
               transform: `translate(${-this.left * k}px, ${-this.top * k}px)` };
    },
    // the slider: 0 = the whole of the shorter side, 100 = zoomed in 5 times
    zoomValue() { return this.side ? Math.round(100 * Math.log(this.short / this.side) / Math.log(5)) : 0; },
    short() { return Math.min(this.w, this.h); },
  },
  mounted() {
    this.observer = new ResizeObserver(() => (this.view = this.$refs.viewport.clientWidth || 300));
    this.observer.observe(this.$refs.viewport);
  },
  unmounted() {
    this.observer?.disconnect();
    clearTimeout(this.timer);
  },
  methods: {
    loaded(event) {
      this.w = event.target.naturalWidth;
      this.h = event.target.naturalHeight;
      const f = this.frame || { x: 0, y: 0, size: 1 };
      this.side = Math.max(1, f.size * this.short);
      this.left = f.x * this.w;
      this.top = f.y * this.h;
      this.clamp();
    },
    clamp() {
      this.side = Math.min(Math.max(this.side, this.short / 5), this.short);
      this.left = Math.min(Math.max(this.left, 0), this.w - this.side);
      this.top = Math.min(Math.max(this.top, 0), this.h - this.side);
    },
    // zoom so the picture's side is `side`, keeping the point under (fx, fy) of the window in place
    zoomTo(side, fx = 0.5, fy = 0.5) {
      const px = this.left + fx * this.side, py = this.top + fy * this.side;
      this.side = side;
      this.clamp();
      this.left = px - fx * this.side;
      this.top = py - fy * this.side;
      this.clamp();
    },
    panBy(dx, dy) {  // screen pixels
      const k = this.view / this.side;
      this.left -= dx / k;
      this.top -= dy / k;
      this.clamp();
    },
    down(event) {
      try {
        this.$refs.viewport.setPointerCapture(event.pointerId);  // keep following it outside the window
      } catch (e) { /* not a live pointer (e.g. made up by a script): fine without */ }
      this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (this.pointers.size === 2) this.pinch = this.twoFingers();
    },
    move(event) {
      const last = this.pointers.get(event.pointerId);
      if (!last) return;
      const now = { x: event.clientX, y: event.clientY };
      if (this.pointers.size === 1) {
        this.panBy(now.x - last.x, now.y - last.y);
      }
      this.pointers.set(event.pointerId, now);
      if (this.pointers.size === 2 && this.pinch) {
        const next = this.twoFingers();
        const rect = this.$refs.viewport.getBoundingClientRect();
        this.zoomTo(this.side * this.pinch.distance / next.distance,
                    (next.x - rect.left) / rect.width, (next.y - rect.top) / rect.height);
        this.panBy(next.x - this.pinch.x, next.y - this.pinch.y);
        this.pinch = next;
      }
    },
    up(event) {
      if (!this.pointers.delete(event.pointerId)) return;
      this.pinch = this.pointers.size === 2 ? this.twoFingers() : null;
      if (this.pointers.size === 0) this.send();
    },
    twoFingers() {
      const [a, b] = [...this.pointers.values()];
      return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)) };
    },
    wheel(event) {
      const rect = this.$refs.viewport.getBoundingClientRect();
      this.zoomTo(this.side * Math.exp(event.deltaY * 0.0015),
                  (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
      this.sendSoon();
    },
    slide(event) {
      this.zoomTo(this.short / Math.pow(5, event.target.value / 100));
    },
    key(event) {
      const step = this.view * 0.05;
      const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
      if (moves[event.key]) this.panBy(...moves[event.key]);
      else if (event.key === '+' || event.key === '=') this.zoomTo(this.side / 1.15);
      else if (event.key === '-') this.zoomTo(this.side * 1.15);
      else return;
      event.preventDefault();
      this.sendSoon();
    },
    sendSoon() {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.send(), 250);
    },
    send() {
      if (!this.w) return;
      this.$emit('change', { x: this.left / this.w, y: this.top / this.h, size: this.side / this.short });
    },
  },
};
