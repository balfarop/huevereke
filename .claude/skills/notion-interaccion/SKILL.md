---
name: notion-interaccion
description: "Esquema completo y reglas de interacción (límites de la plataforma, eficiencia de consultas, sintaxis SQL, política de reintento ante cuota agotada) para las 5 bases de Notion que usan propuesta-menu-semanal y analisis-plato-insumo. Fuente única compartida — ninguna de las otras dos mantiene su propia copia del esquema."
---

# Interacción con Notion — esquema y reglas compartidas

Esta skill no tiene flujo de trabajo propio — es una base de referencia que otras skills consultan antes de leer o escribir en las 5 bases de Notion del sistema de menús semanales (`Platos`, `Insumos`, `Receta_Insumo`, `Semanas`, `Semana_Insumo_Familiar`).

## Contenido

- `references/notion-schema.md` — estructura completa de las 5 bases, el link de la página de Notion que las agrupa, y notas de migración de cambios de esquema.
- `references/reglas-interaccion.md` — límites reales de la plataforma en este workspace (sin JOIN entre bases, cuota compartida de `Query Data Source`), reglas de eficiencia de consultas, gotchas de sintaxis SQL confirmados en producción, qué hacer cuando se agota la cuota, y cómo leer los archivos de esta skill desde otra skill co-adjuntada (la sintaxis de ruta cambia según el entorno).

## Quién la usa y cómo

`propuesta-menu-semanal` (lee las 5 bases para armar propuestas de menú) y `analisis-plato-insumo` (escribe las 5 bases al procesar un archivo semanal real) dependen de esta skill para todo lo relacionado a Notion — ambas deben tenerla adjuntada (Managed Agents) o instalada junto a ellas (Claude Desktop) para poder leer estos archivos. Ninguna de las dos mantiene ya su propia copia del esquema.

Antes de leer o escribir cualquiera de las 5 bases, consulta primero `references/notion-schema.md` (estructura) y `references/reglas-interaccion.md` (cómo consultar sin desperdiciar cuota ni tokens). Ver la sección "Cómo se lee este archivo desde otra skill co-adjuntada" en `reglas-interaccion.md` para la sintaxis de ruta exacta según el entorno (Claude Desktop vs. Managed Agents) — no son la misma.

Si necesitas corregir el esquema o una regla, edítalo aquí — esta es la única fuente; ya no debería existir ninguna copia dentro de otra skill.
