// [Nau] Ajusta el HUD a cualquier pantalla: se maqueta sobre un lienzo de referencia
// (1536×864, el portátil del usuario) y se escala para llenar la ventana sin cortes.
// Estructura esperada: .jarvis-hud > .hud-frame > .hud
import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

const DESIGN_W = 1536;
const DESIGN_H = 864;
const MOBILE_MAX_W = 760;  // en móviles el HUD se apila y se desplaza, sin escalar

/** Escala `.hud-frame` para que `.hud` quepa entero en la ventana, sea cual sea su tamaño. */
export function useFitToScreen(hudRef: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const hud = hudRef.current;
    const frame = hud?.parentElement;
    const root = frame?.parentElement;
    if (!hud || !frame || !root) return;
    let raf = 0;

    const applyScale = (scale: number, vw: number, vh: number) => {
      const w = vw / scale, h = vh / scale;
      frame.style.width = `${w}px`;
      frame.style.height = `${h}px`;
      frame.style.transform = `scale(${scale})`;
      root.style.setProperty('--frame-h', `${h}px`);
    };

    const measure = () => {
      raf = 0;
      const vw = window.innerWidth, vh = window.innerHeight;
      const isFit = vw > MOBILE_MAX_W;
      root.classList.toggle('fit', isFit);
      if (!isFit) {
        frame.style.width = frame.style.height = frame.style.transform = '';
        root.style.removeProperty('--frame-h');
        return;
      }
      let scale = Math.min(vw / DESIGN_W, vh / DESIGN_H);
      applyScale(scale, vw, vh);
      // Si el contenido aún no cabe (fuentes, textos largos), se reduce lo justo
      for (let i = 0; i < 3; i++) {
        const overflow = hud.scrollHeight / Math.max(1, hud.clientHeight);
        if (overflow <= 1.002) break;
        scale /= overflow;
        applyScale(scale, vw, vh);
      }
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(measure); };

    measure();
    window.addEventListener('resize', schedule);
    const observer = new ResizeObserver(schedule);
    Array.from(hud.children).forEach((child) => observer.observe(child));
    document.fonts?.ready.then(schedule).catch(() => {});
    return () => {
      window.removeEventListener('resize', schedule);
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
      root.classList.remove('fit');
    };
  }, [hudRef]);
}
