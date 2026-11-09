// [Nau] Datos del HUD: los lee de la API de Jarvis (/v1/hud/*) cada poco.
import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import type { HudState } from './hologram';

export type SystemData = {
  cpu: number;
  cpu_nucleo_max: number;
  ram: { usado_gb: number; total_gb: number; pct: number };
  swap_pct: number;
  disco: { usado_gb: number; total_gb: number; libre_gb: number; pct: number };
  bateria: { pct: number; cargando: boolean } | null;
  red: { subida_kbs: number; bajada_kbs: number };
  procesos: number;
  arranque: number;
  charlas_guardadas: number;
  wifi: { senal: number; red: string | null } | null;
  gpu: number | null;
  temperatura: number | null;
};

export type MachineData = {
  equipo: string;
  usuario: string;
  nombre?: string;
  zona?: string;
  so: string;
  modelo?: string;
  cpu?: string;
  nucleos?: number;
  gpu?: string;
};

export type ListenerData = {
  estado: HudState;
  texto: string;
  nivel?: number;
  latencia?: number | null;
  activo: boolean;
};

export type WeatherData = { lugar: string; temperatura: number; humedad: number | null; cielo: string };

const SYSTEM_EVERY_MS = 1500;
const LISTENER_EVERY_MS = 300;
const WEATHER_EVERY_MS = 15 * 60 * 1000;

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await apiFetch(path);
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null; // la API está reiniciándose: se reintenta en la siguiente vuelta
  }
}

/** Repite `load` cada `everyMs` y guarda el último resultado válido. */
function usePolling<T>(path: string, everyMs: number): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const next = await getJson<T>(path);
      if (!alive) return;
      if (next !== null) setData(next);
      timer = setTimeout(tick, everyMs);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [path, everyMs]);
  return data;
}

export function useHudData() {
  const system = usePolling<SystemData>('/v1/hud/sistema', SYSTEM_EVERY_MS);
  const listener = usePolling<ListenerData>('/v1/hud/oyente', LISTENER_EVERY_MS);
  const weather = usePolling<WeatherData>('/v1/hud/tiempo', WEATHER_EVERY_MS);
  const machine = usePolling<MachineData>('/v1/hud/equipo', WEATHER_EVERY_MS);
  return { system, listener, weather, machine };
}

/** «3 h 12 min» desde el arranque del PC. */
export function formatUptime(bootEpoch: number | undefined, now: number): string {
  if (!bootEpoch) return '--';
  const minutes = Math.max(0, Math.floor((now / 1000 - bootEpoch) / 60));
  const days = Math.floor(minutes / 1440);
  const rest = `${Math.floor((minutes % 1440) / 60)} h ${minutes % 60} min`;
  return days ? `${days} d ${rest}` : rest;
}
