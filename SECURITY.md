# Política de seguridad

## Versiones

Solo se mantiene la rama `main`.

## Avisar de una vulnerabilidad

Si encuentras un fallo de seguridad (una forma de saltarse la confirmación por voz, de hacer que el cerebro ejecute algo distinto de sus herramientas, de filtrar datos o de exponer la API fuera de `127.0.0.1`…), **no abras un issue público**.

Usa el aviso privado de GitHub: pestaña **Security → Report a vulnerability** de este repositorio. Indica:

- Qué has encontrado y cómo reproducirlo.
- El impacto que crees que tiene.
- Si lo has probado en tu propia instalación.

Respondo en unos 7 días.

## Diseño de seguridad

El modelo de amenazas, la confirmación obligatoria y el tratamiento de secretos y datos personales están en [docs/seguridad.md](docs/seguridad.md).
