// [Nau] El HUD se anima siempre, aunque Windows tenga desactivados los «Efectos de animación»
// (el navegador lo traduce a prefers-reduced-motion y lo dejaba casi quieto). el usuario lo quiere en
// movimiento. Para volver a respetar el ajuste del sistema, devuelve el matchMedia comentado.
export function isReducedMotion(): boolean {
  return false; // matchMedia('(prefers-reduced-motion: reduce)').matches
}
