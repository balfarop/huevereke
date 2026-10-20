"""
Adaptador delgado: expone las funciones de lectura de queries.py como
herramientas del Agent SDK (`mcp__menu_cache__*`), corriendo en el mismo
proceso (sin subprocess, sin red — ver create_sdk_mcp_server en la doc
oficial: https://code.claude.com/docs/en/agent-sdk/custom-tools).

Paso 2 de la migración al caché: este archivo NO decide cuándo el agente
debe usar el caché en vez de Notion en vivo — eso lo dice el texto de la
skill (`.claude/skills/propuesta-menu-semanal/SKILL.md`, pasos 3b/4b/5).
Acá solo se pone a disposición la herramienta; cada función abre y cierra
su propia conexión sqlite3 (conexiones cortas, sin estado compartido entre
llamadas — coherente con que schema.get_connection ya fija
row_factory = sqlite3.Row y PRAGMA foreign_keys = ON en cada apertura).

Si el caché todavía no se sincronizó nunca (o el archivo no existe), las
consultas simplemente devuelven listas/diccionarios vacíos — no rompen la
conversación. `estado_cache` existe justamente para que el agente pueda
darse cuenta de eso y decidir si le conviene avisar o volver a Notion.
"""
import json
import sqlite3

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..config import settings
from . import queries
from .schema import get_connection


def _conn() -> sqlite3.Connection:
    return get_connection(settings.MENU_CACHE_DB_PATH)


def _text_result(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


@tool(
    "estado_cache",
    (
        "Devuelve cuándo se sincronizó por última vez el caché local "
        "(app/menu_cache) contra Notion, y si está vacío. Útil para "
        "decidir si conviene avisar que los datos podrían estar "
        "desactualizados antes de usarlos en los pasos 3b/4b/5."
    ),
    {},
)
async def estado_cache(args: dict) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT value FROM _sync_meta WHERE key = 'last_synced_at'"
        ).fetchone()
        n_platos = conn.execute("SELECT COUNT(*) AS n FROM platos").fetchone()["n"]
    finally:
        conn.close()
    return _text_result(
        {
            "last_synced_at_utc": row["value"] if row else None,
            "platos_en_cache": n_platos,
            "caché_vacío": n_platos == 0,
        }
    )


@tool(
    "platos_principales",
    (
        "Catálogo completo de platos principales del caché local, con "
        "combo de ensalada sugerido y acompañamientos (salsas/sopas/pares "
        "simétricos) ya resueltos — equivalente local al precargado de "
        "Notion. Úsalo en vez de consultar Notion en vivo para el "
        "catálogo de platos."
    ),
    {},
)
async def platos_principales(args: dict) -> dict:
    conn = _conn()
    try:
        data = queries.platos_principales(conn)
    finally:
        conn.close()
    return _text_result(data)


@tool(
    "semanas_recientes",
    (
        "Últimas N semanas del caché local (por defecto 8), con sus "
        "platos ya resueltos por nombre. Incluye `origen` y `platos_real` "
        "(Platos de la semana (real), agregado 2026-09-14 — lista vacía "
        "significa 'se cumplió tal cual lo propuesto', no 'sin datos', ver "
        "paso 2c de propuesta-menu-semanal). Úsalo para revisar historial "
        "reciente sin consultar Notion en vivo."
    ),
    {"limit": int},
)
async def semanas_recientes(args: dict) -> dict:
    limit = int(args.get("limit") or 8)
    conn = _conn()
    try:
        data = queries.semanas_recientes(conn, limit=limit)
    finally:
        conn.close()
    return _text_result(data)


@tool(
    "receta_insumo_por_platos",
    (
        "Insumos de uno o varios platos (por id de Notion), con nombre, "
        "unidad y origen de compra del insumo ya resueltos por JOIN "
        "(campo origen_compra = Insumos.Origen de compra — úsalo para "
        "separar proveedor/directa en el paso 6, no lo estimes). Pensado "
        "para el paso 3b (verificar proteína/insumos de los platos ya "
        "seleccionados) y el paso 5 (agregar insumos según su confianza) "
        "— consulta en batch, nunca por item."
    ),
    {"plato_ids": list},
)
async def receta_insumo_por_platos(args: dict) -> dict:
    plato_ids = args.get("plato_ids") or []
    conn = _conn()
    try:
        data = queries.receta_insumo_por_platos(conn, plato_ids)
    finally:
        conn.close()
    return _text_result(data)


@tool(
    "combos_ensalada",
    (
        "Vocabulario de combos de Ensalada del caché local: insumos "
        "agrupados por combo (Receta_Insumo.Combos), excluyendo Estado = "
        "Descartado — equivalente local a fetch_ensalada_combos(). Úsalo "
        "en el paso 4b para armar la pregunta de acompañamiento desde "
        "datos estructurados."
    ),
    {},
)
async def combos_ensalada(args: dict) -> dict:
    conn = _conn()
    try:
        data = queries.combos_ensalada(conn)
    finally:
        conn.close()
    return _text_result(data)


@tool(
    "frecuencia_consumo_familiar",
    (
        "Frecuencia de insumos de consumo familiar (Semana_Insumo_Familiar) "
        "en las últimas N semanas (por defecto 6), contando en cuántas "
        "semanas distintas aparece cada insumo. Útil como apoyo en el "
        "paso 5."
    ),
    {"semanas": int},
)
async def frecuencia_consumo_familiar(args: dict) -> dict:
    semanas = int(args.get("semanas") or 6)
    conn = _conn()
    try:
        data = queries.frecuencia_consumo_familiar(conn, semanas=semanas)
    finally:
        conn.close()
    return _text_result(data)


@tool(
    "buscar_platos",
    (
        "Busca platos del caché local por coincidencia de texto en el "
        "nombre o en las notas (LIKE %texto%). Útil para resolver un "
        "nombre coloquial contra el catálogo real antes de crear un plato "
        "nuevo por error."
    ),
    {"texto": str},
)
async def buscar_platos(args: dict) -> dict:
    texto = args.get("texto") or ""
    conn = _conn()
    try:
        data = queries.buscar_platos(conn, texto)
    finally:
        conn.close()
    return _text_result(data)


menu_cache_server = create_sdk_mcp_server(
    name="menu_cache",
    version="0.1.0",
    tools=[
        estado_cache,
        platos_principales,
        semanas_recientes,
        receta_insumo_por_platos,
        combos_ensalada,
        frecuencia_consumo_familiar,
        buscar_platos,
    ],
)
