// [Nau] Fondo del HUD igual que el de la web de CorGon: partículas cian que flotan despacio,
// rebotan en los bordes y se unen con líneas finas cuando están cerca. Con el ratón cerca,
// las partículas se acercan, brillan más y se unen al puntero con una línea violeta.
// Encima van la rejilla de puntos y el degradado (hud.css, .jarvis-hud::before).
// Ligero a propósito: 30 fotogramas por segundo, sin desenfoques y a resolución 1×.
import { useEffect, useRef } from 'react';
import { isReducedMotion } from './motion';

const FRAME_MS = 1000 / 30;
const PARTICLES = 45;
const PARTICLES_MOBILE = 20;
const MOBILE_MAX_W = 768;
const SPEED = 0.4 * 60;        // píxeles por segundo (máximo por eje)
const LINK_DIST = 120;         // distancia máxima para unir dos partículas
const MOUSE_PULL_DIST = 180;   // las partículas se acercan al ratón dentro de este radio
const MOUSE_GLOW_DIST = 100;   // y brillan más dentro de este
const CYAN = '0, 240, 255';
const VIOLET = '168, 85, 247';
const VEIL = 'rgba(10, 15, 30, 0.4)';  // velo azul marino sobre el fondo

interface Particle { x: number; y: number; vx: number; vy: number; r: number; bright: number }

function makeParticles(w: number, h: number): Particle[] {
  const n = w < MOBILE_MAX_W ? PARTICLES_MOBILE : PARTICLES;
  return Array.from({ length: n }, () => ({
    x: Math.random() * w, y: Math.random() * h,
    vx: (Math.random() - 0.5) * SPEED, vy: (Math.random() - 0.5) * SPEED,
    r: 1 + Math.random() * 1.5, bright: 0.3 + Math.random() * 0.4,
  }));
}

export function HudBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const reduce = isReducedMotion();
    const mouse = { x: -1000, y: -1000, active: false };
    let w = 0, h = 0, ps: Particle[] = [], raf = 0, last = performance.now(), acc = 0;

    const move = (p: Particle, dt: number) => {
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
      if (!mouse.active) return;
      const dx = mouse.x - p.x, dy = mouse.y - p.y, d = Math.hypot(dx, dy);
      if (d > 0 && d < MOUSE_PULL_DIST) {
        const pull = ((MOUSE_PULL_DIST - d) / MOUSE_PULL_DIST) * 0.25 * dt * 60;
        p.x += (dx / d) * pull; p.y += (dy / d) * pull;
      }
    };

    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = VEIL; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        const md = mouse.active ? Math.hypot(mouse.x - p.x, mouse.y - p.y) : Infinity;
        const bright = md < MOUSE_GLOW_DIST ? Math.min(1, p.bright + (MOUSE_GLOW_DIST - md) / MOUSE_GLOW_DIST) : p.bright;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${CYAN}, ${0.4 * bright})`; ctx.fill();
        ctx.lineWidth = 0.5;
        for (let j = i + 1; j < ps.length; j++) {
          const q = ps[j], d = Math.hypot(p.x - q.x, p.y - q.y);
          if (d >= LINK_DIST) continue;
          ctx.strokeStyle = `rgba(${CYAN}, ${((LINK_DIST - d) / LINK_DIST) * 0.15})`;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
        }
        if (md < MOUSE_PULL_DIST) {
          ctx.strokeStyle = `rgba(${VIOLET}, ${((MOUSE_PULL_DIST - md) / MOUSE_PULL_DIST) * 0.2})`; ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke();
        }
      }
    };

    const resize = () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
      ps = makeParticles(w, h);
      draw();
    };
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      acc += now - last; last = now;
      if (acc < FRAME_MS) return;
      const dt = Math.min(0.1, acc / 1000); acc = 0;
      for (const p of ps) move(p, dt);
      draw();
    };
    const onMove = (e: MouseEvent) => { mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true; };
    const onLeave = () => { mouse.x = mouse.y = -1000; mouse.active = false; };

    resize();
    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMove, { passive: true });
    document.addEventListener('mouseleave', onLeave);
    if (!reduce) raf = requestAnimationFrame(step);
    return () => {
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseleave', onLeave);
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas ref={canvasRef} className="hud-bg" aria-hidden="true" />;
}
