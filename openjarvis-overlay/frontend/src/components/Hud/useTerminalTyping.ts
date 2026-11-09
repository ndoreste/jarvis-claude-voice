// [Nau] Efecto de terminal en las tarjetas laterales: al abrir el HUD, las líneas de cada tarjeta
// aparecen una tras otra, tecleadas de izquierda a derecha (animación .type-line de hud.css).
// Solo se escribe así la primera vez; después los datos se actualizan en vivo sin animación.
import { useEffect } from 'react';
import type { RefObject } from 'react';

const LINES = [
  '.panel-head', '.place', '.sky', '.meter-row > .k', '.meter-row > .v', '.meter-row > .bar', '.kv dt', '.kv dd',
  '.info .who', '.info .sep', '.info .row', '.swatches', '.prompt',
].join(', ');
const LINE_GAP_S = 0.07;   // segundos entre una línea y la siguiente
const PANEL_GAP_S = 0.25;  // pausa extra al pasar a la tarjeta siguiente

export function useTerminalTyping(sideRef: RefObject<HTMLElement | null>, startDelayS = 0): void {
  useEffect(() => {
    const side = sideRef.current;
    if (!side) return;
    let delay = startDelayS;
    side.querySelectorAll<HTMLElement>('.panel').forEach((panel) => {
      panel.querySelectorAll<HTMLElement>(LINES).forEach((line) => {
        line.classList.add('type-line');
        line.style.animationDelay = `${delay.toFixed(2)}s`;
        delay += LINE_GAP_S;
      });
      delay += PANEL_GAP_S;
    });
  }, [sideRef, startDelayS]);
}
