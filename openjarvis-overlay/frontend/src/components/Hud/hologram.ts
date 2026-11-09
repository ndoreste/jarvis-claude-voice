// [Nau] Motor del holograma central del HUD (lienzo de 720×960, aparato centrado en 360,470).
// Aparato de neón amarillo fluorescente, proyector con haz de luz y efectos de interferencia.

import { isReducedMotion } from './motion';

export type HudState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface HologramLive {
  state: HudState;
  /** Nivel real del micro (0-1) mientras escucha; null para simularlo. */
  level: number | null;
}

const NEON = '#f0ff00';
const HOT = '#fdffd0';
const TAU = Math.PI * 2;
const DEG = Math.PI / 180;
const STATES: Record<HudState, { name: string; speed: number; glow: number }> = {
  idle: { name: 'En espera', speed: 1, glow: 0.85 },
  listening: { name: 'Escuchando', speed: 1.6, glow: 1.0 },
  thinking: { name: 'Pensando', speed: 4, glow: 0.95 },
  speaking: { name: 'Hablando', speed: 1.8, glow: 1.15 },
};
export const STATE_NAMES: Record<HudState, string> = {
  idle: STATES.idle.name, listening: STATES.listening.name, thinking: STATES.thinking.name, speaking: STATES.speaking.name,
};

const rgb = (hex: string): [number, number, number] => {
  const n = parseInt(hex.slice(1), 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
};
const clamp01 = (a: number) => Math.max(0, Math.min(1, a));
const [nr, ng, nb] = rgb(NEON);
const [hr, hg, hb] = rgb(HOT);
const Y = (a: number) => `rgba(${nr},${ng},${nb},${clamp01(a)})`;
const W = (a: number) => `rgba(${hr},${hg},${hb},${clamp01(a)})`;

const EQ_BARS = 56;          // barras del ecualizador de voz alrededor de la lente
const SHOCK_MIN_GAP = 0.16;  // segundos mínimos entre dos ondas de choque
// Rendimiento: el resplandor se hace reduciendo la imagen (no con blur ni shadowBlur),
// y en reposo se dibuja a 30 fps; con voz, a la tasa completa de la pantalla.
const MAX_DPR = 1;
const BLOOM_DOWNSCALE = 6;   // el resplandor se calcula a 1/6 de resolución
const IDLE_FRAME_MS = 1000 / 30;

export function startHologram(canvas: HTMLCanvasElement, getLive: () => HologramLive): () => void {
  const ctx = canvas.getContext('2d');
  const buf = document.createElement('canvas');
  const b = buf.getContext('2d');
  const bloom = document.createElement('canvas');
  const bl = bloom.getContext('2d');
  if (!ctx || !b || !bl) return () => {};
  const reduce = isReducedMotion();

  let state: HudState = 'idle';
  let amp = 0, t = 0, last = performance.now(), glow = 0.85;
  let scale = 1, dpr = 1, raf = 0;
  const rot = new Float32Array(6);
  const motes = Array.from({ length: 40 }, () => ({
    x: (Math.random() - 0.5) * 300, y: Math.random() * 480, s: 12 + Math.random() * 30, r: 0.6 + Math.random() * 1.4,
  }));
  // Voz: sílabas sintéticas, ecualizador radial y ondas de choque en los golpes de voz
  let voice = 0, syllableTarget = 0, nextSyllable = 0, lastShock = 0;
  const shocks: { r: number; a: number }[] = [];
  const eqSeeds = Array.from({ length: EQ_BARS }, () => ({ p: Math.random() * TAU, f: 5 + Math.random() * 9 }));

  function resize() {
    dpr = Math.min(devicePixelRatio || 1, MAX_DPR);
    const r = canvas.getBoundingClientRect();
    canvas.width = buf.width = Math.round(r.width * dpr);
    canvas.height = buf.height = Math.round(r.height * dpr);
    bloom.width = Math.max(1, Math.round(canvas.width / BLOOM_DOWNSCALE));
    bloom.height = Math.max(1, Math.round(canvas.height / BLOOM_DOWNSCALE));
    bl!.imageSmoothingQuality = 'high';
    scale = canvas.width / 720;
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  // ── Primitivas ──
  // Sin shadowBlur: es lo más caro del lienzo. El brillo lo pone el resplandor de project().
  function arc(r: number, a0: number, a1: number, w: number, color: string) {
    b!.beginPath(); b!.arc(0, 0, r, a0, a1); b!.lineWidth = w; b!.strokeStyle = color; b!.stroke();
  }
  function neon(r: number, a0: number, a1: number, w: number, k: number) {
    arc(r, a0, a1, w * 5, Y(0.05 * k)); arc(r, a0, a1, w * 2.6, Y(0.12 * k));
    arc(r, a0, a1, w, Y(0.95 * k)); arc(r, a0, a1, w * 0.38, W(0.95 * k));
  }
  function sector(r0: number, r1: number, a0: number, a1: number, fill: string, stroke: string) {
    b!.beginPath(); b!.arc(0, 0, r1, a0, a1); b!.arc(0, 0, r0, a1, a0, true); b!.closePath();
    b!.fillStyle = fill; b!.fill(); b!.lineWidth = 1; b!.strokeStyle = stroke; b!.stroke();
  }
  function shape(points: [number, number][], fill: string | CanvasGradient | null, stroke: string, w = 1) {
    b!.beginPath(); points.forEach(([x, y], i) => (i ? b!.lineTo(x, y) : b!.moveTo(x, y))); b!.closePath();
    if (fill) { b!.fillStyle = fill; b!.fill(); }
    b!.lineWidth = w; b!.strokeStyle = stroke; b!.stroke();
  }
  function text(str: string, x: number, y: number, size: number, alpha: number, weight = 500) {
    b!.font = `${weight} ${size}px "Orbitron", sans-serif`; b!.fillStyle = Y(alpha);
    b!.textAlign = 'center'; b!.textBaseline = 'middle'; b!.fillText(str, x, y);
  }
  function bars(x: number, y: number, n: number, alpha: number) {
    for (let i = 0; i < n; i++) { const w = 18 + ((i * 37) % 50); b!.fillStyle = Y(alpha); b!.fillRect(x, y + i * 9, w, 3); }
  }

  /** Voz: destello central, ecualizador radial y ondas de choque. Escala con `voice` (0..1). */
  function drawVoice(dt: number, k: number) {
    const B = b!;
    if (voice > 0.62 && t - lastShock > SHOCK_MIN_GAP && !reduce) { shocks.push({ r: 70, a: 0.35 + voice * 0.65 }); lastShock = t; }
    for (let i = shocks.length - 1; i >= 0; i--) {
      const s = shocks[i];
      s.r += dt * 420; s.a -= dt * 1.25;
      if (s.a <= 0 || s.r > 300) { shocks.splice(i, 1); continue; }
      arc(s.r, 0, TAU, 2 + 4 * s.a, Y(s.a * 0.75));
    }
    if (voice < 0.02) return;
    const flash = B.createRadialGradient(0, 0, 0, 0, 0, 170);
    flash.addColorStop(0, Y(0.4 * voice)); flash.addColorStop(0.5, Y(0.12 * voice)); flash.addColorStop(1, Y(0));
    B.fillStyle = flash; B.beginPath(); B.arc(0, 0, 170, 0, TAU); B.fill();
    B.lineCap = 'round';
    for (let i = 0; i < EQ_BARS; i++) {
      const a = (i / EQ_BARS) * TAU - Math.PI / 2, s = eqSeeds[i];
      const wobble = 0.35 + 0.65 * Math.abs(Math.sin(t * s.f + s.p));
      const len = 6 + voice * wobble * 58, r0 = 84;
      B.beginPath(); B.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); B.lineTo(Math.cos(a) * (r0 + len), Math.sin(a) * (r0 + len));
      B.lineWidth = 3.2; B.strokeStyle = Y((0.35 + 0.65 * wobble) * voice * k); B.stroke();
    }
    B.lineCap = 'butt';
  }

  function drawHologram(dt: number) {
    if (!buf.width || !buf.height) return;
    const st = STATES[state], sp = reduce ? 0.15 : st.speed;
    [0.05, -0.14, 0.4, -0.7, 2.4, 0.9].forEach((v, i) => { rot[i] += dt * v * sp; });
    glow += ((st.glow + amp * 0.4 + voice * 0.9) - glow) * Math.min(1, dt * 12);
    const k = glow * (reduce ? 1 : 0.9 + 0.1 * Math.sin(t * 2.3));
    const B = b!;

    B.setTransform(1, 0, 0, 1, 0, 0); B.clearRect(0, 0, buf.width, buf.height);
    B.setTransform(scale, 0, 0, scale, 0, 0);
    B.globalCompositeOperation = 'lighter';

    // Proyector: base y haz de luz
    const cone = B.createLinearGradient(0, 940, 0, 300);
    cone.addColorStop(0, Y(0.16)); cone.addColorStop(0.5, Y(0.05)); cone.addColorStop(1, Y(0));
    shape([[290, 935], [430, 935], [640, 300], [80, 300]], cone, Y(0));
    B.save(); B.translate(360, 935); B.scale(1, 0.14);
    B.beginPath(); B.arc(0, 0, 80, 0, TAU); B.fillStyle = Y(0.25); B.fill();
    B.lineWidth = 6; B.strokeStyle = Y(0.9); B.stroke();
    B.beginPath(); B.arc(0, 0, 110, 0, TAU); B.lineWidth = 3; B.strokeStyle = Y(0.35); B.stroke();
    B.restore();
    for (const m of motes) {
      m.y += dt * m.s * (reduce ? 0.2 : 1); if (m.y > 620) m.y = 0;
      const y = 935 - m.y, spread = 0.25 + m.y / 620;
      B.beginPath(); B.arc(360 + m.x * spread, y, m.r, 0, TAU); B.fillStyle = Y(0.5 * (1 - m.y / 620)); B.fill();
    }

    // Paneles flotantes
    const P = Y(0.05), PS = Y(0.32);
    shape([[0, 150], [95, 150], [112, 167], [112, 238], [0, 238]], P, PS); bars(14, 168, 6, 0.28);
    shape([[608, 150], [720, 150], [720, 242], [625, 242], [608, 225]], P, PS); bars(640, 168, 6, 0.28);
    shape([[0, 670], [100, 670], [116, 686], [116, 752], [0, 752]], P, PS); bars(14, 690, 5, 0.22);
    shape([[604, 668], [720, 668], [720, 750], [620, 750], [604, 734]], P, PS); bars(636, 688, 5, 0.22);

    B.save(); B.translate(360, 470);
    const thump = 1 + 0.07 * voice;  // todo el aparato late con la voz
    B.scale(thump, thump);


    // Placa superior y hombros
    shape([[-72, -372], [72, -372], [88, -356], [88, -322], [-88, -322], [-88, -356]], Y(0.06), Y(0.45));
    for (let i = 0; i < 9; i++) { B.fillStyle = Y(i % 3 === 2 ? 0.25 : 0.7); B.fillRect(-42 + i * 10, -350, 7, 5); }
    shape([[-88, -330], [-150, -282], [-190, -232], [-176, -224], [-136, -270], [-80, -312]], Y(0.04), Y(0.3));
    shape([[88, -330], [150, -282], [190, -232], [176, -224], [136, -270], [80, -312]], Y(0.04), Y(0.3));

    // Carcasa exterior
    sector(255, 292, 0, TAU, Y(0.05), Y(0.38));
    for (let a = 0; a < 360; a += 6) {
      const r = a * DEG; B.beginPath(); B.moveTo(Math.cos(r) * 258, Math.sin(r) * 258); B.lineTo(Math.cos(r) * 289, Math.sin(r) * 289);
      B.strokeStyle = Y(0.1); B.lineWidth = 1; B.stroke();
    }
    sector(292, 334, -50 * DEG, -22 * DEG, Y(0.07), Y(0.45));
    sector(292, 352, -19 * DEG, 19 * DEG, Y(0.08), Y(0.5));
    sector(292, 330, 22 * DEG, 42 * DEG, Y(0.07), Y(0.45));
    for (let a = -14; a <= 14; a += 7) {
      const r = a * DEG; B.beginPath(); B.moveTo(Math.cos(r) * 300, Math.sin(r) * 300); B.lineTo(Math.cos(r) * 345, Math.sin(r) * 345);
      B.strokeStyle = Y(0.35); B.stroke();
    }
    sector(292, 312, 160 * DEG, 200 * DEG, Y(0.06), Y(0.35));

    // Trazos de neón exteriores
    neon(246, -128 * DEG, -52 * DEG, 3, k * 0.9);
    for (let a = 148; a <= 214; a += 5.5) {
      const r = a * DEG, lit = state === 'listening' ? 0.4 + 0.6 * Math.max(0, Math.sin(a * 0.2 - t * 8)) : 0.75;
      B.beginPath(); B.moveTo(Math.cos(r) * 264, Math.sin(r) * 264); B.lineTo(Math.cos(r) * 276, Math.sin(r) * 276);
      B.strokeStyle = Y(lit); B.lineWidth = 3; B.stroke();
    }
    B.save(); B.rotate(rot[0]); B.setLineDash([18, 9]); arc(263, 58 * DEG, 122 * DEG, 2.5, Y(0.75)); B.setLineDash([]); B.restore();
    neon(276, 28 * DEG, 46 * DEG, 2.5, k * 0.9);
    B.beginPath(); B.arc(150, -236, 3.5, 0, TAU); B.fillStyle = W(0.9); B.fill();
    text('B4', 152, -205, 11, 0.6); text('1/8', 150, 230, 11, 0.55);

    // Anillo de neón principal
    const ring = 220, gap = 15 * DEG;
    for (let q = 0; q < 4; q++) { const a = -90 * DEG + q * 90 * DEG; neon(ring, a + gap, a + 90 * DEG - gap, 9, k); }
    if (state === 'thinking') { B.save(); B.rotate(rot[4]); neon(ring, 0, 0.45, 11, 1.3); B.restore(); }
    arc(ring - 14, 0, TAU, 1, Y(0.25));
    for (const sx of [-1, 1]) {
      shape([[sx * 252, -40], [sx * 268, 0], [sx * 252, 40], [sx * 246, 40], [sx * 261, 0], [sx * 246, -40]], Y(0.35 * k), Y(0.95 * k), 2);
    }
    const tabs: [string, string, number][] = [['SD', 'VOZ', -90], ['MEM', '', 0], ['', '', 90], ['NET', '', 180]];
    for (const [l1, l2, a] of tabs) {
      B.save(); B.rotate((a + 90) * DEG); B.translate(0, -206);
      shape([[-40, -24], [40, -24], [48, -12], [48, 18], [-48, 18], [-48, -12]], Y(0.12 * k), Y(0.95 * k), 2.2);
      shape([[-26, -12], [26, -12], [30, -4], [30, 8], [-30, 8], [-30, -4]], Y(0.05), Y(0.45 * k), 1);
      if (a === 0 || a === 180) B.rotate(-(a + 90) * DEG);
      if (l1) text(l1, 0, l2 ? -6 : 0, 10, 0.95, 700);
      if (l2) text(l2, 0, 8, 8, 0.75);
      if (!l1) { B.beginPath(); B.arc(0, -2, 4, 0, TAU); B.fillStyle = W(0.9 * k); B.fill(); }
      B.restore();
    }

    // Anillo de puntos
    B.save(); B.rotate(rot[1]);
    for (let i = 0; i < 64; i++) {
      const a = (i / 64) * TAU;
      let alpha = 0.6;
      if (state === 'listening') alpha = 0.3 + 0.7 * Math.max(0, Math.sin(a * 3 - t * 6)) * (0.5 + amp);
      if (state === 'speaking') alpha = 0.45 + 0.55 * amp;
      const dr = 185 + voice * 16 * Math.sin(a * 6 + t * 14), ds = 2.2 * (1 + voice * 0.9);
      B.beginPath(); B.arc(Math.cos(a) * dr, Math.sin(a) * dr, ds, 0, TAU); B.fillStyle = Y(alpha); B.fill();
    }
    B.restore();

    // LED en las diagonales
    for (let q = 0; q < 4; q++) {
      const a = (45 + q * 90) * DEG; B.save(); B.translate(Math.cos(a) * 166, Math.sin(a) * 166); B.rotate(a + Math.PI / 2);
      shape([[-14, -10], [14, -10], [16, 10], [-16, 10]], Y(0.06), Y(0.45));
      const on = state === 'idle' ? 0.55 + 0.45 * Math.sin(t * 2 + q) : 0.7 + 0.3 * Math.sin(t * 9 + q * 1.7);
      B.beginPath(); B.arc(0, 0, 3.5, 0, TAU); B.fillStyle = W(on); B.fill();
      B.restore();
    }

    // Disco intermedio
    sector(98, 142, 0, TAU, Y(0.07), Y(0.5));
    for (let i = 0; i < 24; i++) {
      const r = i * 15 * DEG; B.beginPath(); B.moveTo(Math.cos(r) * 134, Math.sin(r) * 134); B.lineTo(Math.cos(r) * 141, Math.sin(r) * 141);
      B.strokeStyle = Y(0.5); B.stroke();
    }
    B.save(); B.rotate(rot[2] * 0.3); arc(121, 205 * DEG, 335 * DEG, 1.6, Y(0.8)); B.restore();
    text('D2', -70, -76, 11, 0.7); text('D2', -112, 26, 11, 0.55); text('B4', 0, 120, 11, 0.7); text('A2', 76, 98, 11, 0.6);

    // Aro y lente
    sector(62, 80, 0, TAU, Y(0.08), Y(0.6));
    B.save(); B.rotate(rot[3]); for (let q = 0; q < 3; q++) { const a = q * TAU / 3; arc(71, a, a + 1.1, 2, Y(0.7)); } B.restore();
    drawVoice(dt, k);
    const pulse = 1 + amp * 0.3 + voice * 0.75;
    B.save(); B.rotate(-rot[5]);
    neon(44 * pulse, 0.3, Math.PI - 0.3, 3.2 * (1 + voice), k * 1.1); neon(44 * pulse, Math.PI + 0.3, TAU - 0.3, 3.2 * (1 + voice), k * 1.1);
    B.restore();
    B.save(); B.rotate(rot[5] * 0.7); arc(30 * pulse, 0, Math.PI * 1.55, 1.6, Y(0.9)); B.restore();
    const pr = 9 * (1 + amp * 0.6 + voice * 2.6), pg = B.createRadialGradient(0, 0, 0, 0, 0, pr * 3);
    pg.addColorStop(0, W(1)); pg.addColorStop(0.35, Y(0.9)); pg.addColorStop(1, Y(0));
    B.fillStyle = pg; B.beginPath(); B.arc(0, 0, pr * 3, 0, TAU); B.fill();

    // Placa inferior con el estado
    shape([[-58, 292], [58, 292], [64, 306], [-64, 306]], Y(0.06), Y(0.35));
    shape([[-96, 306], [96, 306], [124, 342], [104, 430], [-104, 430], [-124, 342]], Y(0.07), Y(0.55), 1.4);
    shape([[-80, 318], [80, 318], [100, 344], [86, 412], [-86, 412], [-100, 344]], null, Y(0.2));
    B.save(); B.translate(-14, 352); B.lineWidth = 2; B.strokeStyle = Y(0.95);
    B.beginPath(); B.arc(0, 0, 8, 0, TAU); B.moveTo(-8, 0); B.lineTo(8, 0); B.moveTo(0, -8); B.lineTo(0, 8);
    B.moveTo(12, 6); B.lineTo(18, -6); B.lineTo(23, 3); B.lineTo(28, -2); B.lineTo(33, 6); B.stroke(); B.restore();
    text(STATES[state].name.toUpperCase(), 0, 390, 15, 0.98, 700);


    B.restore();
    B.globalCompositeOperation = 'source-over';
  }

  // ── Proyección: parpadeo, resplandor, imagen fantasma, cortes y barrido ──
  let glitchUntil = 0, nextGlitch = 2.5;
  let scan: CanvasPattern | null = null;
  function scanPattern(): CanvasPattern | null {
    const p = document.createElement('canvas'); p.width = 4; p.height = 3;
    const pc = p.getContext('2d'); if (!pc) return null;
    pc.fillStyle = 'rgba(0,0,0,0.42)'; pc.fillRect(0, 2, 4, 1);
    return ctx!.createPattern(p, 'repeat');
  }
  function project() {
    const C = ctx!, w = canvas.width, h = canvas.height;
    if (!w || !h) return;
    C.globalCompositeOperation = 'source-over'; C.clearRect(0, 0, w, h);
    C.globalAlpha = reduce ? 1 : 0.82 + 0.18 * Math.random() * Math.random();
    C.drawImage(buf, 0, 0);
    if (!reduce) {
      C.globalCompositeOperation = 'lighter';
      C.globalAlpha = 0.28; C.drawImage(buf, 3 * dpr, 0);
      // Resplandor barato: se reduce la imagen a 1/6 y se vuelve a ampliar con suavizado
      bl!.globalCompositeOperation = 'copy'; bl!.drawImage(buf, 0, 0, bloom.width, bloom.height);
      C.imageSmoothingEnabled = true; C.imageSmoothingQuality = 'high';
      C.globalAlpha = Math.min(1, 0.85 + 0.6 * voice); C.drawImage(bloom, 0, 0, w, h);
      C.globalAlpha = Math.min(1, 0.35 + 0.5 * voice); C.drawImage(bloom, -4 * dpr, -4 * dpr, w + 8 * dpr, h + 8 * dpr);
      C.globalAlpha = 1; C.globalCompositeOperation = 'source-over';
      if (t > nextGlitch) { glitchUntil = t + 0.14; nextGlitch = t + 3 + Math.random() * 4; }
      if (t < glitchUntil) {
        for (let i = 0; i < 4; i++) {
          const y = Math.random() * h, sh = 6 + Math.random() * 26, dx = (Math.random() - 0.5) * 30 * dpr;
          C.clearRect(0, y, w, sh); C.drawImage(buf, 0, y, w, sh, dx, y, w, sh);
        }
      }
      const band = ((t * 0.18) % 1.3) * h - 0.15 * h;
      const g = C.createLinearGradient(0, band, 0, band + 0.12 * h);
      g.addColorStop(0, Y(0)); g.addColorStop(0.5, Y(0.07)); g.addColorStop(1, Y(0));
      C.globalCompositeOperation = 'lighter'; C.fillStyle = g; C.fillRect(0, band, w, 0.12 * h);
    }
    C.globalCompositeOperation = 'source-over'; C.globalAlpha = 1;
    scan = scan || scanPattern();
    // Líneas de barrido solo sobre el holograma (source-atop), no sobre el fondo transparente
    if (scan) { C.globalCompositeOperation = 'source-atop'; C.fillStyle = scan; C.fillRect(0, 0, w, h); C.globalCompositeOperation = 'source-over'; }
  }

  function frame(now: number) {
    raf = requestAnimationFrame(frame);
    const live = getLive();
    // En reposo o pensando basta con 30 fps; con voz se anima a la tasa completa
    const isVoice = live.state === 'speaking' || live.state === 'listening';
    if (!isVoice && now - last < IDLE_FRAME_MS - 1) return;
    const dt = Math.min(0.05, (now - last) / 1000); last = now; t += dt;
    state = live.state;
    let target: number;
    if (t > nextSyllable) {
      // Sílabas: golpes de 80-220 ms, con alguna pausa breve entre palabras
      const pause = Math.random() < 0.18;
      syllableTarget = pause ? 0.05 : 0.45 + 0.55 * Math.random();
      nextSyllable = t + (pause ? 0.08 + Math.random() * 0.12 : 0.08 + Math.random() * 0.14);
    }
    let voiceTarget = 0;
    if (state === 'speaking') { target = syllableTarget; voiceTarget = syllableTarget; }
    else if (state === 'listening') {
      const lvl = live.level ?? 0.25 + 0.45 * Math.abs(Math.sin(t * 5.7) * Math.cos(t * 2.3));
      target = lvl;
      voiceTarget = Math.min(1, lvl * (0.7 + 0.6 * syllableTarget)) * 0.8;
    }
    else if (state === 'thinking') target = 0.15;
    else target = 0.04 + 0.03 * Math.sin(t * 1.4);
    amp += (target - amp) * Math.min(1, dt * 10);
    // Ataque rápido y caída algo más lenta, como una voz
    voice += (voiceTarget - voice) * Math.min(1, dt * (voiceTarget > voice ? 28 : 11));
    drawHologram(dt);
    project();
  }
  raf = requestAnimationFrame(frame);

  return () => { cancelAnimationFrame(raf); observer.disconnect(); };
}
