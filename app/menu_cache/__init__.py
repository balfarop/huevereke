"""
Caché local (SQLite) de las 5 bases de Notion del menú semanal
(Platos, Insumos, Receta_Insumo, Semanas, Semana_Insumo_Familiar).

Este es el paso 1 de la migración planteada: validar que la sincronización
Notion -> SQLite funcione y refleje bien el esquema real, ANTES de tocar
nada de la web o del agente (agent_service.py / main.py no cambian en
este paso).

Piezas:
- schema.py  — DDL de las tablas y apertura de conexión. Espejo del
               esquema real vigente al 2026-09-07 (incluye los campos de
               acompañamientos estructurados de la Fase 1-6: Combo de
               ensalada sugerido, Acompañamientos sugeridos, Tipo de
               sugerencia de acompañamiento, Receta_Insumo.Combos).
- queries.py — lógica de consulta (funciones puras sobre una conexión
               sqlite3). Cuando lleguemos al paso de conectar esto al
               agente, tanto el adaptador de la app web (Agent SDK) como
               el de Claude Desktop (MCP por stdio) serán envoltorios
               delgados sobre estas mismas funciones.
- sync.py    — sincronización en bloque Notion -> SQLite, reutilizando
               los parsers ya existentes en notion_direct.py en vez de
               duplicarlos.

Validar este paso: correr build_cache.py (raíz del proyecto) contra tu
Notion real y revisar el resumen impreso.
"""
