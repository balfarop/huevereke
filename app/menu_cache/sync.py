"""
Sincronización en bloque: Notion -> SQLite local.

Mismo patrón que notion_direct.py (REST directo a la API pública de
Notion, con el token de integración interna — sin pasar por el
conector MCP ni por el agente), pero trayendo TODAS las filas y TODAS
las columnas que hacen falta de las 5 bases, no solo el puñado fijo que
se precarga hoy para los pasos 1 y 2.

Reutiliza los parsers ya existentes en notion_direct.py (_title_text,
_select_text, _number_value, _relation_ids, _multi_select_names) en vez
de duplicarlos — solo se agregan acá los que faltan (_rich_text,
_url_value, _date_value).

Son ~5 consultas en bloque (una por base, paginando con has_more/
next_cursor si hace falta) — muy lejos de la cuota compartida de "Query
Data Source". Pensado para correr a mano (build_cache.py, en la raíz)
mientras se valida este paso; más adelante, al arrancar el proceso y/o
desde un endpoint de refresco.

Nota sobre relaciones duales: al traer las filas por la API REST directa,
"Acompañamientos sugeridos" llega ya resuelta en ambas direcciones para
un par simétrico (Notion mantiene las relaciones duales a nivel de datos
en las dos páginas) — el gotcha de reglas-interaccion.md sobre el lado
recíproco no siendo consultable es específico de la herramienta SQL del
conector MCP, no de la API REST.
"""
import httpx

from ..config import settings
from ..notion_direct import (
    NOTION_API,
    _headers,
    _multi_select_names,
    _number_value,
    _relation_ids,
    _select_text,
    _title_text,
)
from .schema import get_connection


def _rich_text(prop: dict | None) -> str:
    if not prop:
        return ""
    parts = prop.get("rich_text") or []
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _url_value(prop: dict | None) -> str:
    if not prop:
        return ""
    return prop.get("url") or ""


def _date_value(prop: dict | None) -> str:
    if not prop:
        return ""
    date = prop.get("date") or {}
    return date.get("start") or ""


async def _query_all(client: httpx.AsyncClient, data_source_id: str, label: str = "") -> list[dict]:
    """Trae TODAS las filas de una fuente de datos, paginando hasta
    agotar has_more/next_cursor. A diferencia de las consultas puntuales
    de notion_direct.py, acá sí necesitamos el catálogo completo.

    `label` es solo para mensajes de error más útiles (ej. "Insumos") —
    un 404 acá casi siempre significa que tu integración interna todavía
    no está compartida con esa base en particular (ver .env.example).
    """
    results: list[dict] = []
    body: dict = {"page_size": 100}
    while True:
        resp = await client.post(
            f"{NOTION_API}/data_sources/{data_source_id}/query",
            headers=_headers(),
            json=body,
        )
        if resp.status_code >= 400:
            prefijo = f"[{label}] " if label else ""
            sugerencia = (
                " — probablemente falta compartir tu integración interna con "
                "esta base (Notion > la base > \"...\" > Conexiones)."
                if resp.status_code == 404
                else ""
            )
            raise RuntimeError(
                f"{prefijo}Notion respondió {resp.status_code}: {resp.text[:300]}{sugerencia}"
            )
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return results


def _require_ids() -> None:
    faltantes = [
        name
        for name in (
            "PLATOS_DATA_SOURCE_ID",
            "INSUMOS_DATA_SOURCE_ID",
            "RECETA_INSUMO_DATA_SOURCE_ID",
            "SEMANAS_DATA_SOURCE_ID",
            "SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID",
        )
        if not getattr(settings, name)
    ]
    if faltantes:
        raise RuntimeError(
            "Faltan data source ids en la config: "
            + ", ".join(faltantes)
            + " (ver .env.example)."
        )


async def refresh_cache(db_path: str | None = None) -> dict[str, int]:
    """Sincroniza las 5 bases y devuelve un resumen {tabla: cantidad}."""
    _require_ids()
    db_path = db_path or settings.MENU_CACHE_DB_PATH
    conn = get_connection(db_path)
    counts: dict[str, int] = {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Borramos TODO de una sola vez acá arriba, en orden hijo -> padre,
            # antes de traer ni insertar nada de Notion. Antes cada bloque
            # (Platos/Insumos/Receta_Insumo/Semanas/Semana_Insumo_Familiar)
            # borraba su propia tabla justo antes de reinsertarla — funcionaba
            # en un caché recién creado (todas las tablas vacías, nada que
            # violara ninguna FK), pero en una RE-sincronización (caché ya
            # poblado por una corrida anterior) reventaba con
            # "sqlite3.IntegrityError: FOREIGN KEY constraint failed" apenas
            # se llegaba a "DELETE FROM platos": receta_insumo y
            # semana_platos (tablas hijas, con FK a platos) todavía tenían
            # filas viejas sin borrar en ese momento — su DELETE recién
            # llegaba más abajo, en sus propios bloques. Mismo problema le
            # tocaba después a "DELETE FROM insumos" (hijas: receta_insumo,
            # semana_insumo_familiar). Borrando todo primero, en el orden
            # correcto, evitamos el problema sin tener que desactivar
            # PRAGMA foreign_keys (que sigue protegiendo el resto del
            # sync — ver los contadores de "omitidas"/"omitidos" más abajo).
            conn.execute("DELETE FROM plato_acompanamientos")
            conn.execute("DELETE FROM receta_insumo")
            conn.execute("DELETE FROM semana_platos")
            conn.execute("DELETE FROM semana_platos_real")
            conn.execute("DELETE FROM semana_insumo_familiar")
            conn.execute("DELETE FROM platos")
            conn.execute("DELETE FROM insumos")
            conn.execute("DELETE FROM semanas")

            # --- Platos ---
            rows = await _query_all(client, settings.PLATOS_DATA_SOURCE_ID, "Platos")
            plato_ids_conocidos: set[str] = set()
            for row in rows:
                props = row["properties"]
                conn.execute(
                    """INSERT INTO platos
                       (id, nombre, estado, veces_validado, tipo_plato,
                        proteina_principal, fuente_referencia, notas,
                        combo_ensalada_sugerido, tipo_sugerencia_acompanamiento)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        _title_text(props.get("Nombre")),
                        _select_text(props.get("Estado")),
                        _number_value(props.get("Veces validado")),
                        _select_text(props.get("Tipo de plato")),
                        _select_text(props.get("Proteina principal")),
                        _url_value(props.get("Fuente de referencia")),
                        _rich_text(props.get("Notas")),
                        _select_text(props.get("Combo de ensalada sugerido")),
                        _select_text(props.get("Tipo de sugerencia de acompañamiento")),
                    ),
                )
                plato_ids_conocidos.add(row["id"])
            counts["platos"] = len(rows)

            # Segunda pasada para las relaciones "Acompañamientos sugeridos":
            # como es una relación self-referencial (Plato -> Plato), no
            # podíamos insertarla en la misma pasada de arriba — si el plato
            # destino todavía no se había insertado (el orden de las filas
            # que devuelve Notion no está garantizado), la FK fallaba con
            # "FOREIGN KEY constraint failed". Ahora que TODOS los platos ya
            # existen, esto no puede pasar por orden — solo queda el caso
            # (raro) de que el destino esté archivado/no vino en esta
            # consulta, que se cuenta pero no rompe el sync.
            acomp_omitidos = 0
            for row in rows:
                props = row["properties"]
                for acomp_id in _relation_ids(props.get("Acompañamientos sugeridos")):
                    if acomp_id not in plato_ids_conocidos:
                        acomp_omitidos += 1
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO plato_acompanamientos (plato_id, acompanamiento_id) "
                        "VALUES (?, ?)",
                        (row["id"], acomp_id),
                    )
            if acomp_omitidos:
                counts["acompanamientos_omitidos_destino_no_encontrado"] = acomp_omitidos

            # --- Insumos ---
            rows = await _query_all(client, settings.INSUMOS_DATA_SOURCE_ID, "Insumos")
            for row in rows:
                props = row["properties"]
                conn.execute(
                    """INSERT INTO insumos
                       (id, nombre, alias, unidad_estandar, uso, origen_compra, notas)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        _title_text(props.get("Nombre")),
                        _rich_text(props.get("Alias")),
                        _select_text(props.get("Unidad estandar")),
                        _select_text(props.get("Uso")),
                        _select_text(props.get("Origen de compra")),
                        _rich_text(props.get("Notas")),
                    ),
                )
            counts["insumos"] = len(rows)

            # --- Receta_Insumo ---
            rows = await _query_all(client, settings.RECETA_INSUMO_DATA_SOURCE_ID, "Receta_Insumo")
            omitidas = 0
            for row in rows:
                props = row["properties"]
                platos_rel = _relation_ids(props.get("Plato"))
                insumos_rel = _relation_ids(props.get("Insumo"))
                if not platos_rel or not insumos_rel:
                    omitidas += 1
                    continue
                conn.execute(
                    """INSERT INTO receta_insumo
                       (id, plato_id, insumo_id, tipo, confianza,
                        cantidad_referencia, origen, estado, veces_confirmado, combos)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        platos_rel[0],
                        insumos_rel[0],
                        _select_text(props.get("Tipo")),
                        _select_text(props.get("Confianza")),
                        _rich_text(props.get("Cantidad de referencia")),
                        _select_text(props.get("Origen")),
                        _select_text(props.get("Estado")),
                        _number_value(props.get("Veces confirmado")),
                        ",".join(_multi_select_names(props.get("Combos"))),
                    ),
                )
            counts["receta_insumo"] = len(rows) - omitidas
            if omitidas:
                counts["receta_insumo_omitidas_sin_relacion"] = omitidas

            # --- Semanas (+ relación semana_platos) ---
            rows = await _query_all(client, settings.SEMANAS_DATA_SOURCE_ID, "Semanas")
            for row in rows:
                props = row["properties"]
                conn.execute(
                    """INSERT INTO semanas
                       (id, semana, fecha, estado_procesamiento, texto_original, origen)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        _title_text(props.get("Semana")),
                        _date_value(props.get("Fecha")),
                        _select_text(props.get("Estado de procesamiento")),
                        _rich_text(props.get("Texto original")),
                        _select_text(props.get("Origen")),
                    ),
                )
                for plato_id in _relation_ids(props.get("Platos de la semana")):
                    conn.execute(
                        "INSERT OR IGNORE INTO semana_platos (semana_id, plato_id) VALUES (?, ?)",
                        (row["id"], plato_id),
                    )
                # "Platos de la semana (real)" — agregado 2026-09-14, ver
                # notion-schema.md y propuesta-menu-semanal/SKILL.md paso 2c.
                # Vacío es un valor válido (significa "se cumplió tal cual"),
                # no hay nada especial que hacer acá si no trae relaciones.
                for plato_id_real in _relation_ids(props.get("Platos de la semana (real)")):
                    conn.execute(
                        "INSERT OR IGNORE INTO semana_platos_real (semana_id, plato_id) VALUES (?, ?)",
                        (row["id"], plato_id_real),
                    )
            counts["semanas"] = len(rows)

            # --- Semana_Insumo_Familiar ---
            rows = await _query_all(client, settings.SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID, "Semana_Insumo_Familiar")
            for row in rows:
                props = row["properties"]
                semana_rel = _relation_ids(props.get("Semana"))
                insumo_rel = _relation_ids(props.get("Insumo"))
                if not semana_rel or not insumo_rel:
                    continue
                conn.execute(
                    """INSERT INTO semana_insumo_familiar
                       (id, nombre, semana_id, insumo_id, cantidad, unidad)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        _title_text(props.get("Nombre")),
                        semana_rel[0],
                        insumo_rel[0],
                        _rich_text(props.get("Cantidad")),
                        _select_text(props.get("Unidad")),
                    ),
                )
            counts["semana_insumo_familiar"] = len(rows)

        conn.execute(
            "INSERT OR REPLACE INTO _sync_meta (key, value) VALUES ('last_synced_at', datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()
    return counts
