// [Nau] Círculo del HUD: un dato del PC con su arco de progreso.
const ARC_R = 42;
const ARC_LEN = 2 * Math.PI * ARC_R;

type GaugeProps = {
  name: string;
  unit: string;
  /** Valor numérico para el arco; null si el dato no está disponible. */
  value: number | null;
  /** Texto del centro (por defecto, el valor redondeado). */
  shown?: string;
  sub?: string;
  max?: number;
  alertAt?: number;
};

export function Gauge({ name, unit, value, shown, sub = '', max = 100, alertAt = Infinity }: GaugeProps) {
  const fraction = value === null ? 0 : Math.max(0, Math.min(1, value / max));
  const isAlert = value !== null && value >= alertAt;
  const text = value === null ? '--' : (shown ?? String(Math.round(value)));
  const label = `${name}: ${text} ${unit}${sub ? `, ${sub}` : ''}`;
  return (
    <div className={`hg${isAlert ? ' alert' : ''}`} role="img" aria-label={label}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle className="ring-out" cx="60" cy="60" r="57" />
        <circle className="ticks" cx="60" cy="60" r="51" />
        <circle className="track" cx="60" cy="60" r={ARC_R} />
        <circle className="bar-arc" cx="60" cy="60" r={ARC_R}
          strokeDasharray={`${(fraction * ARC_LEN).toFixed(1)} ${ARC_LEN.toFixed(1)}`} transform="rotate(-90 60 60)" />
        <text className="val" x="60" y="57">{text}</text>
        <text className="unit" x="60" y="76">{unit}</text>
      </svg>
      {/* El anillo que gira va en su propia capa: así el brillo del SVG de arriba no se recalcula en cada fotograma */}
      <svg className="hg-spin" viewBox="0 0 120 120" aria-hidden="true">
        <circle className="spin" cx="60" cy="60" r="33" />
      </svg>
      {name && <div className="name">{name}</div>}
      <div className="sub">{sub}</div>
    </div>
  );
}
