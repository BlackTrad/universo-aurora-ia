// A cena é decorativa: nenhum destes parâmetros participa das análises.
export const UNIVERSE = Object.freeze({
  seed: 846219,
  targetFPS: 60,
  maxDPR: 2,
  mobileDPR: 1.25,
  maxPixels: 2_600_000,
  mobilePixels: 1_000_000,
  stars: 7400,
  mobileStars: 2200,
  dust: 72,
  mobileDust: 28,
  skyWidth: 1024,
  mobileSkyWidth: 640,
  skyRefreshSeconds: 3,
  planetRotation: .018,
  cloudRotation: .023,
  cometPeriod: 25,
  cometDuration: 1.8,
  cyan: '#86dfed',
  gold: '#d8ba83',
});

export type MotionState = {paused: boolean; reduced: boolean; renderer: 'webgl' | 'canvas' | 'static'};
export type UniverseController = {
  setPaused(value: boolean): void;
  destroy(): void;
  readonly paused: boolean;
  readonly reduced: boolean;
};

export function resolution(width: number, height: number, deviceDPR: number, mobile: boolean, level = 0) {
  const limit = (mobile ? UNIVERSE.mobilePixels : UNIVERSE.maxPixels) * Math.pow(.67, level);
  const ratio = Math.min(deviceDPR || 1, mobile ? UNIVERSE.mobileDPR : UNIVERSE.maxDPR,
    Math.sqrt(limit / Math.max(1, width * height)));
  return {width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)), ratio};
}

// Orçamento separado do tempo da cena: telas de 90/120/144 Hz não aceleram o fundo.
export class FrameClock {
  private previous: number | null = null;
  private budget = 0;
  private sincePaint = 0;
  time = 0;
  reset() {this.previous = null; this.budget = 0; this.sincePaint = 0;}
  advance(now: number) {
    const raw = this.previous === null ? 0 : Math.max(0, (now - this.previous) / 1000);
    this.previous = now;
    const dt = Math.min(raw, .05), period = 1 / UNIVERSE.targetFPS;
    this.time += dt; this.budget += dt; this.sincePaint += dt;
    if (this.budget + 1e-7 < period) return {paint: false, dt: 0, raw};
    this.budget = Math.max(0, this.budget - period);
    if (this.budget >= period) this.budget %= period;
    const elapsed = this.sincePaint; this.sincePaint = 0;
    return {paint: true, dt: elapsed, raw};
  }
}
