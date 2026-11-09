// [Nau] Página principal de Jarvis: HUD holográfico con datos reales del PC y del oyente de voz.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { apiFetch } from '@/lib/api';
import { Gauge } from '@/components/Hud/Gauge';
import { HudBackground } from '@/components/Hud/HudBackground';
import { HudLeft, HudRight } from '@/components/Hud/HudSides';
import { startHologram, STATE_NAMES } from '@/components/Hud/hologram';
import type { HudState } from '@/components/Hud/hologram';
import { useHudData } from '@/components/Hud/useHudData';
import { useFitToScreen } from '@/components/Hud/useFitToScreen';
import '@/components/Hud/hud.css';

const VOICE_MODEL = 'openrouter/anthropic/claude-haiku-5.5';
const IDLE_CAPTION = 'Llámalo por su nombre: «Hola Jarvis», «¿Qué pasa, Jarvis?»…';
const MIC_LEVEL_FULL = 0.1;  // nivel RMS que llena el círculo del micro

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** Pregunta escrita en el HUD: respuesta del modelo leída en voz alta. */
async function askJarvis(question: string): Promise<string> {
  const res = await apiFetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: VOICE_MODEL, stream: false, messages: [{ role: 'user', content: question }] }),
  });
  if (!res.ok) throw new Error(`La API respondió ${res.status}`);
  const data = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return (data.choices?.[0]?.message?.content ?? '').replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}

async function speak(text: string): Promise<void> {
  const res = await apiFetch('/v1/speech/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text.slice(0, 4000) }),
  });
  if (!res.ok) return;
  const url = URL.createObjectURL(await res.blob());
  const audio = new Audio(url);
  audio.addEventListener('ended', () => URL.revokeObjectURL(url));
  await audio.play().catch(() => URL.revokeObjectURL(url));
}

export function HudPage() {
  const now = useNow();
  const { system, listener, weather, machine } = useHudData();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hudRef = useRef<HTMLDivElement>(null);
  useFitToScreen(hudRef);
  const [webState, setWebState] = useState<HudState | null>(null);
  const [webCaption, setWebCaption] = useState<string | null>(null);

  // Lo que pasa en la web (pregunta escrita) manda sobre el estado del oyente de voz
  const state: HudState = webState ?? listener?.estado ?? 'idle';
  const liveRef = useRef({ state, level: null as number | null });
  liveRef.current = {
    state,
    level: state === 'listening' && listener?.nivel != null ? Math.min(1, listener.nivel / MIC_LEVEL_FULL) : null,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    return startHologram(canvas, () => liveRef.current);
  }, []);

  const handleAsk = async (question: string) => {
    setWebState('thinking');
    setWebCaption(`Tú: «${question}»`);
    try {
      const reply = await askJarvis(question);
      setWebState('speaking');
      setWebCaption(reply);
      await speak(reply);
    } catch (err) {
      setWebState('idle');  // en reposo, para que el error sí se vea
      setWebCaption(`No he podido responder: ${err instanceof Error ? err.message : 'error'}`);
    } finally {
      setTimeout(() => setWebState(null), 4000);
    }
  };

  // Durante la conversación no se muestra texto: ni lo que dice el usuario ni lo que responde Jarvis
  const isTalking = state !== 'idle';
  const caption = isTalking ? ''
    : webCaption && webState ? webCaption
    : listener && !listener.activo ? 'El oyente de voz no está en marcha.'
    : IDLE_CAPTION;
  const micLevel = listener?.nivel ?? null;
  const bat = system?.bateria;

  return (
    <div className="jarvis-hud">
      <HudBackground />
      <div className="scanband" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />
      <div className="hud-frame">
      <div className="hud" ref={hudRef}>
        <nav className="ruler" aria-label="Días del mes">
          {Array.from({ length: new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate() }, (_, i) => {
            const d = i + 1, today = d === now.getDate();
            const cls = `${today ? 'today ' : ''}${Math.abs(d - now.getDate()) <= 3 ? 'near' : ''}`;
            return <span key={d} className={cls} aria-current={today ? 'date' : undefined}>{String(d).padStart(2, '0')}</span>;
          })}
        </nav>

        <HudLeft now={now} system={system} onAsk={handleAsk} isAsking={webState === 'thinking'}
          onMessage={(text) => { setWebCaption(text); setWebState('idle'); setTimeout(() => setWebState(null), 6000); }} />

        <main className="center">
          <h1 className="hud-title">J.A.R.V.I.S.</h1>
          <div className="subtitle">
            {machine?.nombre ? `Asistente personal de ${machine.nombre}` : 'Asistente personal'}
            {machine?.zona ? ` · ${machine.zona}` : ''}
          </div>
          <div className="stage">
            <div className="hud-col left">
              <Gauge name="CPU" unit="%" value={system?.cpu ?? null} alertAt={90} sub={machine?.cpu?.replace(/\(R\)|\(TM\)|Intel|Core/g, '').trim()} />
              <Gauge name="Temp. CPU" unit="°C" value={system?.temperatura ?? null} alertAt={85} sub={system && system.temperatura === null ? 'sin sensor' : 'Procesador'} />
              <Gauge name="RAM" unit="%" value={system?.ram.pct ?? null} alertAt={90} sub={system ? `${system.ram.usado_gb} / ${system.ram.total_gb} GB` : ''} />
            </div>
            <div className="core-wrap">
              <canvas ref={canvasRef} className="core" aria-label="Núcleo holográfico de Jarvis" />
              <div className="sr" aria-live="polite">{STATE_NAMES[state]}</div>
              <div className={`caption${state === 'speaking' ? ' speaking' : ''}`}>{caption}</div>
            </div>
            <div className="hud-col right">
              <Gauge name="GPU" unit="%" value={system?.gpu ?? null} alertAt={90} sub={machine?.gpu?.replace(/\(R\)/g, '')} />
              <Gauge name="Disco C:" unit="%" value={system?.disco.pct ?? null} alertAt={90} sub={system ? `${system.disco.usado_gb} / ${system.disco.total_gb} GB` : ''} />
              <Gauge name="Red" unit="KB/s" value={system ? system.red.bajada_kbs : null} max={2000} sub="↓ descarga" />
            </div>
          </div>
          <div className="links">
            <Link className="btn" to="/chat">Chat</Link>
            <Link className="btn" to="/dashboard">Panel</Link>
            <Link className="btn" to="/settings">Ajustes</Link>
          </div>
        </main>

        <HudRight now={now} system={system} weather={weather} machine={machine} />

        <section className="dock" aria-label="Indicadores del PC">
          <Gauge name="Batería" unit="%" value={bat?.pct ?? null} sub={bat ? (bat.cargando ? 'Cargando' : 'Con batería') : 'Sin batería'} />
          <Gauge name="Wi-Fi" unit="%" value={system?.wifi?.senal ?? null} sub={system?.wifi?.red ?? 'Sin Wi-Fi'} />
          <Gauge name="Núcleo máx." unit="%" value={system?.cpu_nucleo_max ?? null} sub="El más cargado" />
          <Gauge name="Micro" unit="nivel" value={micLevel} max={MIC_LEVEL_FULL} shown={micLevel === null ? undefined : micLevel.toFixed(3)} sub={listener?.activo ? 'Oyente activo' : 'Oyente parado'} />
          <Gauge name="Respuesta" unit="s" value={listener?.latencia ?? null} max={4} alertAt={3.5} shown={listener?.latencia?.toFixed(1)} sub="Última respuesta" />
          <Gauge name="Procesos" unit="activos" value={system?.procesos ?? null} max={500} />
          <Gauge name="Memoria" unit="charlas" value={system?.charlas_guardadas ?? null} max={50} sub="Guardadas" />
          <Gauge name="Swap" unit="%" value={system?.swap_pct ?? null} alertAt={80} sub="Memoria virtual" />
        </section>
      </div>
      </div>
    </div>
  );
}
