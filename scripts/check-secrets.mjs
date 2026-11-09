#!/usr/bin/env node
// Escanea el repositorio en busca de secretos y datos privados antes de un commit.
// Uso: node scripts/check-secrets.mjs   → código 1 si encuentra algo.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SELF = fileURLToPath(import.meta.url);
const SKIP_DIRS = new Set(['.git', 'node_modules', '__pycache__']);
const BINARY_EXT = new Set(['.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.mp3', '.mp4', '.wav', '.zip']);
const ALLOWED_EMAIL = /@(example\.(com|org)|users\.noreply\.github\.com|anthropic\.com)$/i;
// Falsos positivos conocidos: decoradores de Python, paquetes npm con @ y similares
const EMAIL_LIKE_OK = /^@|node_modules|@anthropic-ai|@playwright|@types/;

const RULES = [
  ['Clave de OpenRouter', /\bsk-or-v1-[a-f0-9]{20,}/],
  ['Clave de OpenAI', /\bsk-(proj-)?[A-Za-z0-9]{20,}/],
  ['Clave de Anthropic', /\bsk-ant-[A-Za-z0-9_-]{20,}/],
  ['Clave de ElevenLabs', /\bsk_[a-f0-9]{40,}/],
  ['Token OAuth de Google', /\bya29\.[\w-]{20,}/],
  ['Refresh token de Google', /\b1\/\/0[\w-]{20,}/],
  ['Secreto de cliente OAuth de Google', /\bGOCSPX-[\w-]{10,}/],
  ['ID de cliente OAuth de Google', /\b\d{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com/],
  ['JWT', /\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}/],
  ['Cabecera Bearer con valor', /Bearer\s+[A-Za-z0-9._~+/-]{20,}/],
  ['IP privada', /\b(10\.\d{1,3}|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b/],
  // Rutas reales de un PC (las plantillas usan %USERPROFILE%, ~ o <usuario>)
  ['Ruta personal de Windows', /[A-Z]:[\\/]+(Users|home)[\\/]+(?![<%$~*{]|Public\b|usuario\b|yo\b)[\w.-]+/i],
  ['Ruta personal de Git Bash', /\/[a-z]\/Users\/(?![<%$~*{]|usuario\b|yo\b)[\w.-]+/i],
  ['ID de voz de ElevenLabs con valor', /voice_id\s*=\s*["'][A-Za-z0-9]{20}["']/],
];

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (!BINARY_EXT.has(path.extname(entry.name).toLowerCase()) && full !== SELF) yield full;
  }
}

function scanLine(line) {
  const hits = RULES.filter(([, re]) => re.test(line)).map(([name]) => name);
  const emails = line.match(/[\w.+-]+@[\w-]+\.[a-z]{2,}(\.[a-z]{2,})?/gi) ?? [];
  if (emails.some((e) => !ALLOWED_EMAIL.test(e) && !EMAIL_LIKE_OK.test(e))) hits.push('Correo electrónico');
  return hits;
}

const findings = [];
for (const file of walk(ROOT)) {
  fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
    for (const hit of scanLine(line)) findings.push(`${path.relative(ROOT, file)}:${i + 1}  ${hit}`);
  });
}

if (findings.length) {
  console.error(`✗ ${findings.length} posible(s) secreto(s) o dato(s) privado(s):\n  ${findings.join('\n  ')}`);
  process.exit(1);
}
console.log('✓ Sin secretos ni datos privados detectados.');
