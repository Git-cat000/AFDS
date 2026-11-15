class FlowResizeObserver implements ResizeObserver {
  constructor(private listener: ResizeObserverCallback) {}
  observe(target: Element) {
    queueMicrotask(() =>
      this.listener([{ target } as ResizeObserverEntry], this),
    );
  }
  unobserve() {}
  disconnect() {}
}
class FlowMatrix {
  m22: number;
  constructor(transform = "") {
    this.m22 = Number(/scale\(([^,)]+)/.exec(transform)?.[1] || 1);
  }
}
Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: FlowResizeObserver,
});
Object.defineProperty(globalThis, "DOMMatrixReadOnly", {
  configurable: true,
  value: FlowMatrix,
});
Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
  configurable: true,
  get() {
    return Number.parseFloat(this.style.width) || 800;
  },
});
Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
  configurable: true,
  get() {
    return Number.parseFloat(this.style.height) || 400;
  },
});
Object.defineProperty(SVGElement.prototype, "getBBox", {
  configurable: true,
  value: () => ({ x: 0, y: 0, width: 270, height: 180 }),
});
