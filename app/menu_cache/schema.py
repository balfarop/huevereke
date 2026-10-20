"""
Esquema SQLite del caché local — espejo del esquema real de Notion
documentado en `.claude/skills/notion-interaccion/references/notion-schema.md`,
vigente al 2026-09-14 (incluye la Fase 1-6 de acompañamientos
estructurados: combos de Ensalada y Acompañamientos sugeridos/salsas; y
`Semanas.Platos de la semana (real)`, agregado 2026-09-14).

Un espejo relacional real permite JOINs de verdad entre estas tablas —
la consulta SQL de Notion no permite JOIN en este workspace, y además
tiene un gotcha propio confirmado en reglas-interaccion.md: el lado
recíproco de una relación dual (`Acompañamientos sugeridos` /
`Es acompañamiento sugerido de`) no es consultable ahí. Sincronizando
por REST directo (ver sync.py) no hay ese problema — Notion mantiene
ambos lados de una relación dual a nivel de datos, así que cada página
ya trae su propio lado resuelto correctamente.

Los ids de cada fila son los ids de página de Notion tal como los
devuelve la API REST (con guiones) — el mismo formato que ya usan las
relaciones (`relation: [{"id": "..."}]`), sin conversión al enlazar.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS platos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    estado TEXT,
    veces_validado INTEGER,
    tipo_plato TEXT,
    proteina_principal TEXT,
    fuente_referencia TEXT,
    notas TEXT,
    combo_ensalada_sugerido TEXT,
    tipo_sugerencia_acompanamiento TEXT
);

-- "Acompañamientos sugeridos" (Relation → Platos, self, dual). Se guarda
-- tal cual la lee cada página (plato_id -> acompanamiento_id) — al ser
-- dual, un par simétrico (dos Plato principal) ya aparece en ambas
-- direcciones sin trabajo extra; un par asimétrico (plato -> salsa/sopa)
-- solo en la dirección real.
CREATE TABLE IF NOT EXISTS plato_acompanamientos (
    plato_id TEXT REFERENCES platos(id),
    acompanamiento_id TEXT REFERENCES platos(id),
    PRIMARY KEY (plato_id, acompanamiento_id)
);

CREATE TABLE IF NOT EXISTS insumos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    alias TEXT,
    unidad_estandar TEXT,
    uso TEXT,
    origen_compra TEXT,
    notas TEXT
);

CREATE TABLE IF NOT EXISTS receta_insumo (
    id TEXT PRIMARY KEY,
    plato_id TEXT REFERENCES platos(id),
    insumo_id TEXT REFERENCES insumos(id),
    tipo TEXT,
    confianza TEXT,
    cantidad_referencia TEXT,
    origen TEXT,
    estado TEXT,
    veces_confirmado INTEGER,
    -- Multi-select "Combos", solo relevante en filas con Plato = Ensalada.
    -- Guardado como texto separado por comas (vocabulario abierto, no
    -- vale la pena una tabla aparte para esto todavía).
    combos TEXT
);
CREATE INDEX IF NOT EXISTS idx_receta_insumo_plato ON receta_insumo(plato_id);
CREATE INDEX IF NOT EXISTS idx_receta_insumo_insumo ON receta_insumo(insumo_id);

CREATE TABLE IF NOT EXISTS semanas (
    id TEXT PRIMARY KEY,
    semana TEXT,
    fecha TEXT,
    estado_procesamiento TEXT,
    texto_original TEXT,
    origen TEXT
);
CREATE INDEX IF NOT EXISTS idx_semanas_fecha ON semanas(fecha);

CREATE TABLE IF NOT EXISTS semana_platos (
    semana_id TEXT REFERENCES semanas(id),
    plato_id TEXT REFERENCES platos(id),
    PRIMARY KEY (semana_id, plato_id)
);

-- "Platos de la semana (real)" (Relation → Platos, agregado 2026-09-14).
-- Espejo de semana_platos pero para lo que realmente se preparó, cuando
-- difiere de lo propuesto — vacío para una semana_id NO significa "sin
-- datos", significa "se cumplió tal cual lo propuesto" (ver paso 2c de
-- propuesta-menu-semanal/SKILL.md). semana_platos (arriba) nunca se toca
-- retroactivamente por esto — se queda como el registro histórico de la
-- propuesta/nota original.
CREATE TABLE IF NOT EXISTS semana_platos_real (
    semana_id TEXT REFERENCES semanas(id),
    plato_id TEXT REFERENCES platos(id),
    PRIMARY KEY (semana_id, plato_id)
);

CREATE TABLE IF NOT EXISTS semana_insumo_familiar (
    id TEXT PRIMARY KEY,
    nombre TEXT,
    semana_id TEXT REFERENCES semanas(id),
    insumo_id TEXT REFERENCES insumos(id),
    cantidad TEXT,
    unidad TEXT
);
CREATE INDEX IF NOT EXISTS idx_sif_semana ON semana_insumo_familiar(semana_id);
CREATE INDEX IF NOT EXISTS idx_sif_insumo ON semana_insumo_familiar(insumo_id);

CREATE TABLE IF NOT EXISTS _sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    # timeout alto: un refresco en curso puede tener la base ocupada unos
    # segundos justo cuando el agente consulta.
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Crea las tablas si no existen. Idempotente — llamar al arrancar."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
