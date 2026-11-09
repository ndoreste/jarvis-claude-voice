# Seguridad y privacidad

Un asistente con el micro siempre abierto y acceso a tu PC y a tus cuentas es una superficie de ataque, y también una fuente de errores propios. Estas son las decisiones que tomé y por qué.

## Modelo de amenazas

| Riesgo | Ejemplo | Mitigación |
|---|---|---|
| **Falsa activación** | La tele dice algo parecido a «Jarvis, borra…» | Lo irreversible nunca se hace a la primera (ver abajo) |
| **Error de transcripción** | «Envía» entendido en vez de «revisa» | La misma confirmación, que repite en voz alta qué hará exactamente |
| **Inyección de prompt** | Una web leída por el cerebro dice «encarga "envía…" ya confirmado» | `--confirmado` solo vale para un permiso que Jarvis ya te ha preguntado por voz, de esa misma sesión, de menos de 10 min y una sola vez. La marca de confirmación la pone el script, y sus imitaciones se borran del texto |
| **Exceso de privilegios del cerebro** | El modelo de voz ejecuta comandos arbitrarios | El cerebro solo puede ejecutar `jarvis-herramienta` (agenda y encargar), WebSearch y WebFetch. Modo `dontAsk`: lo demás se deniega |
| **Cobros inesperados** | Una `ANTHROPIC_API_KEY` en el entorno hace que Claude Code use la API | Los procesos se lanzan sin `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` ni las variables de Bedrock o Vertex |
| **Fuga de audio o conversaciones** | Guardar todo lo que oye el micro | Transcripción local. Lo que se oye sin «Jarvis» no se guarda |
| **Permisos de OAuth excesivos** | El conector de Google original pedía Gmail con borrado, Drive y Contactos | Reducido a `calendar.readonly` |

## Confirmación obligatoria

Enviar mensajes o correos, publicar, comprar o pagar, y borrar, sobrescribir o mover a la papelera **nunca se hace en la primera ejecución**:

1. El prompt de sistema de la tarea (`bin/tarea_claude.py`) obliga a Claude Code a preparar todo y terminar con `CONFIRMAR: <qué hará exactamente>`.
2. Jarvis te lo lee y pregunta «¿Lo hago?».
3. Si dices que sí, el cerebro vuelve a encargar la tarea con `--sesion <id> --confirmado`.
4. `tarea_claude.py` **no se fía de ese `--confirmado`**. Solo lo acepta si existe una tarea anterior de esa misma sesión que cumpla todo esto: pidió permiso (`confirmar`), Jarvis ya te la ha leído en voz alta (`anunciado`), terminó hace menos de 10 minutos y su permiso no se ha usado antes (`confirmacion_usada`). Si no se cumple, la tarea se ejecuta sin confirmar y vuelve a preguntarte.
5. Solo entonces reanuda **la misma sesión** con la marca `[<TU NOMBRE> HA CONFIRMADO POR VOZ]` delante. Cualquier imitación de la marca en el texto dictado (en mayúsculas o minúsculas, con otros espacios u otro nombre) se borra antes (`prompt_for`).

Así, una inyección de prompt no puede pedir una acción **nueva** «ya confirmada». Como mucho, podría aceptar en tu lugar el permiso que Jarvis acaba de preguntarte, que es exactamente la acción que oíste. Además, las tareas usan el modo de permisos `auto` de Claude Code, que añade su propio filtro de acciones peligrosas.

Lo probé con un archivo de prueba: sin la marca, Claude Code se negó a borrarlo; con la marca, lo borró. La validación del paso 4 tiene pruebas con casos simulados: sin permiso pendiente, sin preguntar, caducado, segundo uso e imitaciones de la marca.

> Límites: el paso 1 depende de que el modelo siga su prompt de sistema. La protección de verdad es el paso 4, que es código, pero solo cubre lo que el modelo identifica como irreversible.

## Privacidad

- **Audio.** Se transcribe con faster-whisper en el PC y no sale de él. Lo que sí sale: el texto de lo que dices **después** de «Jarvis» (al modelo) y el texto de la respuesta (a ElevenLabs).
- **Registros.** `data/escucha.log` guarda las conversaciones con Jarvis. De lo que se oye sin activarle, solo apunta el número de palabras y el volumen máximo, para poder diagnosticar el micro sin guardar lo que se dice.
- **Mientras dictas.** Si usas un dictado por voz (en mi caso, *Escriba*), Jarvis ignora lo que oye mientras su ventana de grabación está visible (`bin/escriba_activo.py`). Si no lo usas, este archivo no hace nada.
- **Memoria.** Las conversaciones se guardan en `data/memoria-voz.jsonl` y solo se consultan si pides «retomar» algo.

## Secretos

- Las claves (ElevenLabs y OpenRouter) se guardan como **variables de entorno de usuario** con `scripts/configurar-claves.ps1`, que las pide sin mostrarlas en pantalla. Nunca van en un archivo del repo.
- Los tokens de Google se guardan en `data/connectors/`, fuera del repo (`.gitignore` excluye `data/` entero).
- Los datos personales (nombre, perfil, ciudad, rutas) están en `data/usuario.toml`, no en el código.
- [`scripts/check-secrets.mjs`](../scripts/check-secrets.mjs) busca claves (Anthropic, OpenAI, OpenRouter, ElevenLabs, Google), JWT, IPs privadas, correos y rutas personales de Windows antes de cada commit. `scripts/sincronizar.ps1` lo ejecuta siempre y se para si encuentra algo.

## Superficie de red

- La API (`:8000`) y la web (`:5173`) escuchan en `127.0.0.1`.
- `POST /v1/hud/energia` (bloquear, reiniciar o apagar) solo acepta peticiones con origen `localhost:5173` o `127.0.0.1:5173`, y deja 30 s para cancelar. Esto frena a otras webs abiertas en el navegador, pero no a un programa que ya se ejecuta en tu PC: ese programa podría falsificar la cabecera `Origin`, aunque también podría apagar el equipo por su cuenta con `shutdown`.
- Las tareas se lanzan con `--add-dir` de tu carpeta de usuario: Claude Code puede leer y escribir en todo tu perfil, con su modo de permisos `auto` como filtro.

## Mejoras pendientes

- Token local de un solo uso, generado por el oyente al oír el «sí», en lugar de que el cerebro pase `--confirmado`.
- Token en cabecera para `POST /v1/hud/energia`.
- Lista de acciones permitidas por proyecto (por ejemplo, que «LinkedIn» nunca pueda tocar archivos fuera de su carpeta).
- Rotar automáticamente `data/escucha.log`.
