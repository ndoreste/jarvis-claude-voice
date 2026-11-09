// [Nau] Columnas laterales del HUD: fecha, discos, equipo, energía y pregunta (izquierda);
// tiempo, sistema y ficha del equipo (derecha).
import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { apiFetch } from '@/lib/api';
import { Gauge } from './Gauge';
import { formatUptime } from './useHudData';
import { useTerminalTyping } from './useTerminalTyping';
import type { MachineData, SystemData, WeatherData } from './useHudData';

const p2 = (n: number) => String(n).padStart(2, '0');
const fMonth = new Intl.DateTimeFormat('es-ES', { month: 'long' });
const fWeekday = new Intl.DateTimeFormat('es-ES', { weekday: 'long' });
const SECONDS_ARC = 2 * Math.PI * 80;

function DateRing({ now }: { now: Date }) {
  const seconds = now.getSeconds();
  return (
    <div className="datering">
      <svg viewBox="0 0 200 200" aria-hidden="true">
        <circle cx="100" cy="100" r="97" fill="none" stroke="rgba(240,255,0,.3)" strokeWidth="1" />
        <circle cx="100" cy="100" r="90" fill="none" stroke="#f0ff00" strokeWidth="5" strokeDasharray="2 6.2" opacity=".45" />
        <circle cx="100" cy="100" r="80" fill="none" stroke="#f0ff00" strokeWidth="6" transform="rotate(-90 100 100)"
          strokeDasharray={`${((seconds / 60) * SECONDS_ARC).toFixed(1)} ${SECONDS_ARC.toFixed(1)}`} />
      </svg>
      <svg className="datering-spin" viewBox="0 0 200 200" aria-hidden="true">
        <circle cx="100" cy="100" r="70" fill="none" stroke="#f0ff00" strokeWidth="1.2" strokeDasharray="14 8" opacity=".6" />
      </svg>
      <div className="inner">
        <span className="month">{fMonth.format(now)}</span>
        <span className="day">{p2(now.getDate())}</span>
        <span className="wd">{fWeekday.format(now)}</span>
        <span className="time">{p2(now.getHours())}:{p2(now.getMinutes())}<small>:{p2(seconds)}</small></span>
      </div>
    </div>
  );
}

function Bar({ pct, alertAt = 90 }: { pct: number | undefined; alertAt?: number }) {
  const value = Math.max(0, Math.min(100, pct ?? 0));
  return <div className={`bar${value >= alertAt ? ' alert' : ''}`}><i style={{ width: `calc(${value}% - 2px)` }} /></div>;
}

type PowerAction = 'bloquear' | 'reiniciar' | 'apagar';
const POWER_LABELS: Record<PowerAction, string> = { bloquear: 'Bloquear', reiniciar: 'Reiniciar', apagar: 'Apagar' };

async function sendPower(accion: PowerAction | 'cancelar', confirmado: boolean): Promise<string> {
  const res = await apiFetch('/v1/hud/energia', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ accion, confirmado }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail ?? `Error ${res.status}`);
  }
  return accion;
}

function PowerControls({ onMessage }: { onMessage: (text: string) => void }) {
  const [pending, setPending] = useState<PowerAction | null>(null);
  const [scheduled, setScheduled] = useState<PowerAction | null>(null);

  const run = async (accion: PowerAction) => {
    setPending(null);
    try {
      await sendPower(accion, true);
      if (accion === 'bloquear') onMessage('Equipo bloqueado.');
      else { setScheduled(accion); onMessage(`${POWER_LABELS[accion]} en 30 segundos. Puedes cancelarlo.`); }
    } catch (err) {
      onMessage(`No se pudo ${POWER_LABELS[accion].toLowerCase()}: ${err instanceof Error ? err.message : 'error'}`);
    }
  };
  const cancel = async () => {
    try { await sendPower('cancelar', true); onMessage('Cancelado.'); } catch { onMessage('No se pudo cancelar.'); }
    setScheduled(null);
  };

  if (scheduled) {
    return (
      <div className="confirm">
        <span>{POWER_LABELS[scheduled]} en curso (30 s).</span>
        <div className="row"><button type="button" className="danger" onClick={cancel}>Cancelar</button></div>
      </div>
    );
  }
  if (pending) {
    return (
      <div className="confirm">
        <span>¿Seguro que quieres {POWER_LABELS[pending].toLowerCase()} el PC?</span>
        <div className="row">
          <button type="button" className="danger" onClick={() => run(pending)}>Sí, {POWER_LABELS[pending].toLowerCase()}</button>
          <button type="button" onClick={() => setPending(null)}>No</button>
        </div>
      </div>
    );
  }
  return (
    <div className="actions">
      <button type="button" onClick={() => setPending('bloquear')}>⏻ Bloquear</button>
      <button type="button" onClick={() => setPending('reiniciar')}>↻ Reiniciar</button>
      <button type="button" onClick={() => setPending('apagar')}>⏼ Apagar</button>
    </div>
  );
}

type LeftProps = {
  now: Date;
  system: SystemData | null;
  onAsk: (question: string) => void;
  onMessage: (text: string) => void;
  isAsking: boolean;
};

export function HudLeft({ now, system, onAsk, onMessage, isAsking }: LeftProps) {
  const [question, setQuestion] = useState('');
  const sideRef = useRef<HTMLElement>(null);
  useTerminalTyping(sideRef);
  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || isAsking) return;
    onAsk(q);
    setQuestion('');
  };
  const disk = system?.disco;
  return (
    <aside className="side left" ref={sideRef} aria-label="Fecha, discos, equipo y energía">
      <DateRing now={now} />

      <section className="panel holo" aria-label="Discos">
        <div className="panel-head"><span className="label">Discos</span><span className="code">DSK</span></div>
        <div className="meter-row">
          <span className="k">C: total</span><span /><span className="v">{disk ? `${disk.total_gb} GB` : '--'}</span>
          <Bar pct={disk?.pct} />
          <span className="k">Libre</span><span /><span className="v">{disk ? `${disk.libre_gb} GB` : '--'}</span>
        </div>
      </section>

      <section className="panel holo" aria-label="Equipo">
        <div className="panel-head"><span className="label">Equipo</span><span className="code">PWR</span></div>
        <dl className="kv">
          <dt>Encendido</dt><dd>{formatUptime(system?.arranque, now.getTime())}</dd>
          <dt>Procesos</dt><dd>{system?.procesos ?? '--'}</dd>
          <dt>Wi-Fi</dt><dd>{system?.wifi?.red ?? '--'}</dd>
          <dt>Red ↑</dt><dd>{system ? `${Math.round(system.red.subida_kbs)} KB/s` : '--'}</dd>
          <dt>Red ↓</dt><dd>{system ? `${Math.round(system.red.bajada_kbs)} KB/s` : '--'}</dd>
        </dl>
        <PowerControls onMessage={onMessage} />
      </section>

      <form className="ask" onSubmit={handleSubmit} autoComplete="off">
        <label className="sr" htmlFor="hud-ask">Pregúntale a Jarvis</label>
        <input id="hud-ask" placeholder="Pregúntale a Jarvis…" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <button type="submit" disabled={isAsking}>{isAsking ? '…' : 'Enviar'}</button>
      </form>
    </aside>
  );
}

type RightProps = { now: Date; system: SystemData | null; weather: WeatherData | null; machine: MachineData | null };
const SWATCHES = ['#f0ff00', '#d3e800', '#b5c700', '#8e9a00', '#fdffd0', '#f8ffd9', '#5c6400', '#2b3000'];

export function HudRight({ now, system, weather, machine }: RightProps) {
  const sideRef = useRef<HTMLElement>(null);
  useTerminalTyping(sideRef, 0.3);
  const ram = system?.ram;
  const disk = system?.disco;
  const rows: [string, string][] = [
    ['Equipo', machine?.modelo ?? '--'],
    ['SO', machine?.so ?? '--'],
    ['Encendido', formatUptime(system?.arranque, now.getTime())],
    ['CPU', machine?.cpu ? `${machine.cpu}${machine.nucleos ? ` (${machine.nucleos} núcleos)` : ''}` : '--'],
    ['GPU', machine?.gpu ?? '--'],
    ['RAM', ram ? `${ram.usado_gb} / ${ram.total_gb} GB` : '--'],
    ['Disco C:', disk ? `${disk.usado_gb} / ${disk.total_gb} GB` : '--'],
    ['Jarvis', 'núcleo de voz activo'],
    ['Modelo', 'Claude Sonnet · suscripción'],
    ['Voz', 'ElevenLabs «Jarvis» · Whisper local'],
  ];
  const who = machine ? `${machine.usuario}@${machine.equipo.toLowerCase()}` : 'jarvis';
  return (
    <aside className="side right" ref={sideRef} aria-label="Tiempo, sistema y ficha del equipo">
      <section className="panel holo weather" aria-label="Tiempo">
        <div>
          <div className="place">{weather?.lugar ?? '—'}</div>
          <div className="sky">{weather ? `${weather.cielo}${weather.humedad != null ? ` · ${weather.humedad}% hum.` : ''}` : 'Cargando…'}</div>
        </div>
        <Gauge name="" unit="°C" value={weather?.temperatura ?? null} max={40} />
      </section>

      <section className="panel holo" aria-label="Sistema">
        <div className="panel-head"><span className="label">Sistema</span><span className="code">SYS</span></div>
        <div className="meter-row"><span className="k">CPU</span><span /><span className="v">{system ? `${Math.round(system.cpu)}%` : '--'}</span><Bar pct={system?.cpu} /></div>
        <div className="meter-row"><span className="k">RAM</span><span /><span className="v">{ram ? `${Math.round(ram.pct)}%` : '--'}</span><Bar pct={ram?.pct} /></div>
        <div className="meter-row"><span className="k">Swap</span><span /><span className="v">{system ? `${Math.round(system.swap_pct)}%` : '--'}</span><Bar pct={system?.swap_pct} /></div>
      </section>

      <section className="panel holo" aria-label="Ficha del equipo">
        <div className="panel-head"><span className="label">jarvis fetch</span><span className="code">SYS-00</span></div>
        <div className="rule" />
        <div className="info">
          <div className="who">{who}</div>
          <div className="sep">────────────────────────</div>
          {rows.map(([k, v]) => (
            <div className="row" key={k}><span className="k">{k}</span><span>{v}</span></div>
          ))}
          <div className="swatches" aria-hidden="true">{SWATCHES.map((c) => <i key={c} style={{ background: c }} />)}</div>
        </div>
        <div className="prompt"><span className="ps">{who}</span>:<span className="path">~</span>$ <span className="cursor" /></div>
      </section>
    </aside>
  );
}
