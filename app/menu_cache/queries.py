"""
Consultas de lectura contra el caché local (SQLite).

Funciones puras sobre una conexión sqlite3 ya abierta (con
row_factory = sqlite3.Row, ver schema.get_connection). Devuelven listas
de dicts planos, listas para json.dumps o para formatear como texto.

Todavía no están conectadas al agente ni a la web — este archivo es
parte del paso 1 (validar el caché solo). Cuando llegue el paso de
conectarlo, los adaptadores (Agent SDK para la web, MCP stdio para
Claude Desktop) deben ser envoltorios delgados sobre estas funciones,
no reimplementar la lógica.
"""
import sqlite3


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Paso 1 de propuesta-menu-semanal: catálogo de platos principales, con
# combo de ensalada sugerido y acompañamientos (salsas/sopas/pares
# simétricos) ya resueltos por JOIN — equivalente local a
# notion_direct.fetch_platos_principales().
# ---------------------------------------------------------------------------

def platos_principales(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, nombre, estado, veces_validado, proteina_principal,
               combo_ensalada_sugerido
        FROM platos
        WHERE tipo_plato = 'Plato principal'
        """
    ).fetchall()
    platos = _rows_to_dicts(rows)
    for p in platos:
        acomp = conn.execute(
            """
            SELECT a.id, a.nombre, a.tipo_sugerencia_acompanamiento
            FROM plato_acompanamientos pa
            JOIN platos a ON a.id = pa.acompanamiento_id
            WHERE pa.plato_id = ?
            """,
            (p["id"],),
        ).fetchall()
        p["acompanamientos"] = _rows_to_dicts(acomp)
    return platos


# ---------------------------------------------------------------------------
# Paso 2: últimas N semanas, con sus platos ya resueltos por nombre.
# ---------------------------------------------------------------------------

def semanas_recientes(conn: sqlite3.Connection, limit: int = 8) -> list[dict]:
    semanas = conn.execute(
        """
        SELECT id, semana, fecha, origen
        FROM semanas
        ORDER BY fecha DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    result = []
    for s in semanas:
        platos = conn.execute(
            """
            SELECT p.nombre
            FROM semana_platos sp
            JOIN platos p ON p.id = sp.plato_id
            WHERE sp.semana_id = ?
            """,
            (s["id"],),
        ).fetchall()
        # "Platos de la semana (real)" — agregado 2026-09-14. Vacío es un
        # valor válido ("se cumplió tal cual lo propuesto"), no "sin
        # datos" — ver paso 2c de propuesta-menu-semanal/SKILL.md.
        platos_real = conn.execute(
            """
            SELECT p.nombre
            FROM semana_platos_real spr
            JOIN platos p ON p.id = spr.plato_id
            WHERE spr.semana_id = ?
            """,
            (s["id"],),
        ).fetchall()
        result.append(
            {
                "id": s["id"],
                "semana": s["semana"],
                "fecha": s["fecha"],
                "origen": s["origen"],
                "platos": [p["nombre"] for p in platos],
                "platos_real": [p["nombre"] for p in platos_real],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Pasos 3b y 5: insumos de uno o varios platos, con nombre y unidad del
# insumo ya resueltos por el JOIN.
# ---------------------------------------------------------------------------

def receta_insumo_por_platos(conn: sqlite3.Connection, plato_ids: list[str]) -> list[dict]:
    if not plato_ids:
        return []
    placeholders = ",".join("?" for _ in plato_ids)
    rows = conn.execute(
        f"""
        SELECT
            ri.plato_id,
            p.nombre AS plato_nombre,
            ri.insumo_id,
            i.nombre AS insumo_nombre,
            i.unidad_estandar,
            i.origen_compra,
            ri.tipo,
            ri.confianza,
            ri.cantidad_referencia,
            ri.origen,
            ri.estado
        FROM receta_insumo ri
        JOIN insumos i ON i.id = ri.insumo_id
        JOIN platos p ON p.id = ri.plato_id
        WHERE ri.plato_id IN ({placeholders})
        """,
        plato_ids,
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Paso 4b: combos de Ensalada — insumo por combo, agrupado, equivalente
# local a notion_direct.fetch_ensalada_combos(). Excluye Estado =
# Descartado, igual que la consulta en vivo.
# ---------------------------------------------------------------------------

def combos_ensalada(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT i.nombre AS insumo_nombre, ri.combos
        FROM receta_insumo ri
        JOIN insumos i ON i.id = ri.insumo_id
        JOIN platos p ON p.id = ri.plato_id
        WHERE p.nombre = 'Ensalada' AND ri.estado != 'Descartado'
        """
    ).fetchall()
    combos_map: dict[str, list[str]] = {}
    for row in rows:
        combos = [c for c in (row["combos"] or "").split(",") if c]
        if not combos:
            combos_map.setdefault("(sin combo asignado — uso libre/genérico)", []).append(
                row["insumo_nombre"]
            )
        else:
            for combo in combos:
                combos_map.setdefault(combo, []).append(row["insumo_nombre"])
    return combos_map


# Compatibilidad con el nombre usado en la primera versión de este boceto
# (antes de conocer el campo Receta_Insumo.Combos real) — se mantiene sin
# el agrupado por combo, por si hace falta la lista plana de relaciones.
def receta_insumo_ensalada(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.id AS insumo_id, i.nombre AS insumo_nombre,
               ri.cantidad_referencia, ri.combos, ri.estado
        FROM receta_insumo ri
        JOIN insumos i ON i.id = ri.insumo_id
        JOIN platos p ON p.id = ri.plato_id
        WHERE p.nombre = 'Ensalada'
        """
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Paso 2b / 5b: frecuencia de insumos de consumo familiar en las últimas
# N semanas.
# ---------------------------------------------------------------------------

def frecuencia_consumo_familiar(conn: sqlite3.Connection, semanas: int = 6) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.nombre AS insumo_nombre, COUNT(DISTINCT sif.semana_id) AS semanas_distintas
        FROM semana_insumo_familiar sif
        JOIN insumos i ON i.id = sif.insumo_id
        WHERE sif.semana_id IN (
            SELECT id FROM semanas ORDER BY fecha DESC LIMIT ?
        )
        GROUP BY i.id
        ORDER BY semanas_distintas DESC
        """,
        (semanas,),
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Paso 3: coincidencia de un nombre coloquial contra el catálogo,
# buscando también en Notas.
# ---------------------------------------------------------------------------

def buscar_platos(conn: sqlite3.Connection, texto: str) -> list[dict]:
    like = f"%{texto}%"
    rows = conn.execute(
        """
        SELECT id, nombre, estado, tipo_plato, notas
        FROM platos
        WHERE nombre LIKE ? OR notas LIKE ?
        """,
        (like, like),
    ).fetchall()
    return _rows_to_dicts(rows)
