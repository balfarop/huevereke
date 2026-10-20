"""
Consultas FIJAS y determinísticas directo contra la API REST pública de
Notion — sin agente, sin tokens de LLM, sin la cuota compartida de
"Query Data Source" del conector MCP.

Esto es intencionalmente distinto al resto de la app: cuando el agente
necesita razonar (elegir platos, calcular insumos, etc.) usa el conector
oficial de Notion vía mcp-remote. Pero hay un puñado de consultas que son
siempre la misma pregunta, sin nada que razonar — esas se resuelven acá,
con HTTP directo y un token de integración interna aparte:

- El sidebar de "últimas semanas" (para mostrar en la web).
- El precargado de contexto al iniciar una conversación nueva: el paso 1
  (catálogo de platos principales, con su combo de ensalada sugerido y
  acompañamientos) y el paso 2 (últimas 8 semanas) de la skill
  `propuesta-menu-semanal` son, según su propio SKILL.md, siempre la
  misma consulta al principio de cada corrida — se le entregan ya
  resueltos al agente en el primer mensaje, en vez de que él mismo gaste
  turnos y cuota consultándolos.
- El vocabulario de combos del plato "Ensalada" (Fase 6 del proyecto de
  acompañamientos estructurados, 2026-09) — Ensalada es un plato único y
  fijo que casi no cambia, así que su desglose por combo (paso 4b de
  propuesta-menu-semanal) también califica como "siempre la misma
  consulta" y se resuelve acá en vez de en vivo por el agente.

La única EXCEPCIÓN a "solo lecturas" es create_test_notes_page(), al final
de este archivo: una escritura, pero deliberadamente acotada a un único
destino fijo (TEST_NOTES_PARENT_PAGE_ID) — el guardado opcional de notas
de conversación cuando el usuario corre una propuesta en modo prueba (ver
app/conversation_tools.py). Va acá, con el mismo cliente REST directo de
integración interna, y no a través del conector oficial de Notion que usa
el agente, para no depender de que el agente arme bien los argumentos de
una herramienta genérica de creación de páginas — mismo motivo por el que
fetch_context_preload vive acá y no como una consulta del agente.

Requisito de configuración (ver README): crear una integración interna en
Notion, compartirla con la página "Menu Semanal" (o al menos con las bases
Semanas, Platos y Receta_Insumo), y poner su secreto en NOTION_TOKEN en
el .env.
"""
import asyncio
from datetime import datetime

import httpx

from .config import settings

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"  # versión con soporte de "data sources"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _title_text(prop: dict | None) -> str:
    if not prop:
        return ""
    parts = prop.get("title") or []
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _select_text(prop: dict | None) -> str:
    if not prop:
        return ""
    select = prop.get("select")
    return (select or {}).get("name", "") or ""


def _multi_select_names(prop: dict | None) -> list[str]:
    if not prop:
        return []
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def _relation_ids(prop: dict | None) -> list[str]:
    if not prop:
        return []
    return [r["id"] for r in prop.get("relation", []) if r.get("id")]


def _number_value(prop: dict | None):
    if not prop:
        return None
    return prop.get("number")


def _require_config() -> None:
    if not settings.NOTION_TOKEN:
        raise RuntimeError(
            "NOTION_TOKEN no está configurado — hace falta para las "
            "consultas directas a Notion (ver README)."
        )


# ---------------------------------------------------------------------------
# Últimas semanas (sidebar web + precargado de contexto del paso 2)
# ---------------------------------------------------------------------------

# Nombres de plato ya resueltos (id de página -> "Nombre"). Vive mientras
# corra el proceso — los nombres de platos casi no cambian, así que no hace
# falta volver a pedirlos en cada refresh.
_plato_name_cache: dict[str, str] = {}
_weeks_cache: dict[int, list] = {}


async def _resolve_plato_names(client: httpx.AsyncClient, page_ids: set[str]) -> dict[str, str]:
    missing = [pid for pid in page_ids if pid not in _plato_name_cache]
    if missing:
        # Notion pide no pasarse de ~3 requests/segundo sostenidos; un
        # semáforo chico alcanza para esta cantidad de páginas (platos por
        # semana, con bastante repetición entre semanas).
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(page_id: str) -> None:
            async with semaphore:
                resp = await client.get(f"{NOTION_API}/pages/{page_id}", headers=_headers())
                if resp.status_code == 200:
                    page = resp.json()
                    name = _title_text(page.get("properties", {}).get("Nombre"))
                    _plato_name_cache[page_id] = name or "(sin nombre)"
                else:
                    _plato_name_cache[page_id] = "(no disponible)"

        await asyncio.gather(*(fetch_one(pid) for pid in missing))

    return {pid: _plato_name_cache.get(pid, "(no disponible)") for pid in page_ids}


async def fetch_recent_weeks(limit: int = 6, force_refresh: bool = False) -> list[dict]:
    """
    Devuelve las últimas `limit` semanas: [{"label": "2026-08-31",
    "platos": ["Tallarines...", "Chicha morada", ...], "platos_real": [...],
    "origen": "Propuesta automática", "url": "https://www.notion.so/...",
    "page_id": "..."}, ...]

    `platos_real` viene de `Semanas.Platos de la semana (real)` (agregado
    2026-09-14) — lista vacía si el campo está en blanco, lo cual **no**
    significa "sin datos": significa que esa semana se cumplió tal cual lo
    propuesto (ver paso 2c de propuesta-menu-semanal). `platos` (de
    `Platos de la semana`) nunca se corrige retroactivamente y siempre
    refleja la propuesta/nota original.

    `url` es la URL pública de la página de esa Semana en Notion (viene
    gratis en la misma respuesta de la consulta, sin gastar otra llamada) —
    la usa el sidebar de la web para enlazar "Ver en Notion", donde vive
    cualquier sub-página de notas de esa semana (ver paso 7 de
    propuesta-menu-semanal) sin tener que traer ni renderizar ese contenido
    acá. `page_id` (con guiones, igual que lo devuelve la API) es el mismo
    dato en el formato que necesita una actualización de página posterior
    (ej. el propio paso 2c, si el agente prefiere resolverlo desde acá en
    vez de buscarlo de nuevo).

    Se cachea en memoria del proceso por `limit` (dura mientras corra
    uvicorn, no por tiempo) — no hace falta volver a pedirle esto a Notion
    en cada carga. Usa force_refresh=True para refrescarlo.
    """
    if not force_refresh and limit in _weeks_cache:
        return _weeks_cache[limit]

    _require_config()
    if not settings.SEMANAS_DATA_SOURCE_ID:
        raise RuntimeError("SEMANAS_DATA_SOURCE_ID no está configurado — ver README.")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{NOTION_API}/data_sources/{settings.SEMANAS_DATA_SOURCE_ID}/query",
            headers=_headers(),
            json={
                "sorts": [{"property": "Fecha", "direction": "descending"}],
                "page_size": limit,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
        rows = resp.json().get("results", [])

        weeks = []
        all_plato_ids: set[str] = set()
        for row in rows:
            props = row.get("properties", {})
            label = _title_text(props.get("Semana")) or "(sin nombre)"
            relation = props.get("Platos de la semana", {}).get("relation", [])
            plato_ids = [r["id"] for r in relation]
            real_relation = props.get("Platos de la semana (real)", {}).get("relation", [])
            real_plato_ids = [r["id"] for r in real_relation]
            all_plato_ids.update(plato_ids)
            all_plato_ids.update(real_plato_ids)
            weeks.append(
                {
                    "label": label,
                    "plato_ids": plato_ids,
                    "real_plato_ids": real_plato_ids,
                    "origen": _select_text(props.get("Origen")),
                    "url": row.get("url"),
                    "page_id": row.get("id"),
                }
            )

        names = await _resolve_plato_names(client, all_plato_ids)

    for week in weeks:
        week["platos"] = [names[pid] for pid in week.pop("plato_ids")]
        week["platos_real"] = [names[pid] for pid in week.pop("real_plato_ids")]

    _weeks_cache[limit] = weeks
    return weeks


# ---------------------------------------------------------------------------
# Catálogo de platos principales (paso 1 de propuesta-menu-semanal)
# ---------------------------------------------------------------------------

_platos_principales_cache: list[dict] | None = None

# Nombre + Tipo de sugerencia de acompañamiento de cada plato que aparece
# como DESTINO de una relación Acompañamientos sugeridos (salsas, sopas
# rotativas, u otro plato principal en un par simétrico). Aparte de
# _plato_name_cache porque acá necesitamos un campo extra (el Select), no
# solo el nombre.
_acompanamiento_target_cache: dict[str, dict] = {}


async def _resolve_acompanamiento_targets(
    client: httpx.AsyncClient, page_ids: set[str]
) -> dict[str, dict]:
    missing = [pid for pid in page_ids if pid not in _acompanamiento_target_cache]
    if missing:
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(page_id: str) -> None:
            async with semaphore:
                resp = await client.get(f"{NOTION_API}/pages/{page_id}", headers=_headers())
                if resp.status_code == 200:
                    props = resp.json().get("properties", {})
                    nombre = _title_text(props.get("Nombre")) or "(sin nombre)"
                    tipo = _select_text(props.get("Tipo de sugerencia de acompañamiento"))
                    url_id = page_id.replace("-", "")
                    _acompanamiento_target_cache[page_id] = {
                        "nombre": nombre,
                        "tipo": tipo or "(sin definir)",
                        "url": f"https://app.notion.com/{url_id}",
                    }
                else:
                    _acompanamiento_target_cache[page_id] = {
                        "nombre": "(no disponible)",
                        "tipo": "",
                        "url": "",
                    }

        await asyncio.gather(*(fetch_one(pid) for pid in missing))

    return {
        pid: _acompanamiento_target_cache.get(pid, {"nombre": "(no disponible)", "tipo": "", "url": ""})
        for pid in page_ids
    }


# Platos "especiales" — fijos y permanentes en el catálogo (no cambian de
# semana a semana), consultados por nombre en prácticamente cada corrida
# (Ensalada para los combos, Insumos para desayunos para el paso 4c) — se
# resuelven una sola vez por nombre, igual que Ensalada en
# _resolve_ensalada_id, para no obligar al agente a repetir esa misma
# búsqueda por nombre en cada conversación.
_SPECIAL_PLATOS = ["Ensalada", "Insumos para desayunos"]
_special_platos_cache: dict[str, str] = {}


async def _resolve_special_platos(client: httpx.AsyncClient) -> dict[str, str]:
    missing = [n for n in _SPECIAL_PLATOS if n not in _special_platos_cache]
    if missing:
        resp = await client.post(
            f"{NOTION_API}/data_sources/{settings.PLATOS_DATA_SOURCE_ID}/query",
            headers=_headers(),
            json={
                "filter": {
                    "or": [{"property": "Nombre", "title": {"equals": n}} for n in missing]
                },
                "page_size": len(missing),
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
        for row in resp.json().get("results", []):
            nombre = _title_text(row.get("properties", {}).get("Nombre"))
            page_id = row.get("id", "").replace("-", "")
            if nombre:
                _special_platos_cache[nombre] = f"https://app.notion.com/{page_id}"
    return {n: _special_platos_cache[n] for n in _SPECIAL_PLATOS if n in _special_platos_cache}


async def fetch_special_platos(force_refresh: bool = False) -> dict[str, str]:
    """Wrapper con su propio cliente HTTP, para poder llamarse desde
    fetch_context_preload igual que las otras piezas del precargado."""
    if not force_refresh and len(_special_platos_cache) == len(_SPECIAL_PLATOS):
        return dict(_special_platos_cache)
    _require_config()
    async with httpx.AsyncClient(timeout=20) as client:
        return await _resolve_special_platos(client)


async def fetch_platos_principales(force_refresh: bool = False) -> list[dict]:
    """
    Paso 1, fijo, de la skill propuesta-menu-semanal: "Trae de Platos todos
    los registros con Tipo de plato = Plato principal, pidiendo Estado,
    Veces validado, Proteína principal, Combo de ensalada sugerido y
    Acompañamientos sugeridos" (los últimos dos, agregados en la Fase 5 del
    proyecto de acompañamientos estructurados). Cada acompañamiento sugerido
    viene resuelto con su propio Nombre y Tipo de sugerencia de
    acompañamiento (Mismo día / Misma semana / sin definir).
    """
    global _platos_principales_cache
    if not force_refresh and _platos_principales_cache is not None:
        return _platos_principales_cache

    _require_config()
    if not settings.PLATOS_DATA_SOURCE_ID:
        raise RuntimeError("PLATOS_DATA_SOURCE_ID no está configurado — ver README.")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{NOTION_API}/data_sources/{settings.PLATOS_DATA_SOURCE_ID}/query",
            headers=_headers(),
            json={
                "filter": {
                    "property": "Tipo de plato",
                    "select": {"equals": "Plato principal"},
                },
                "page_size": 100,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
        rows = resp.json().get("results", [])

        platos = []
        all_target_ids: set[str] = set()
        for row in rows:
            props = row.get("properties", {})
            page_id = row.get("id", "").replace("-", "")
            target_ids = _relation_ids(props.get("Acompañamientos sugeridos"))
            all_target_ids.update(target_ids)
            platos.append(
                {
                    "nombre": _title_text(props.get("Nombre")) or "(sin nombre)",
                    "estado": _select_text(props.get("Estado")),
                    "veces_validado": _number_value(props.get("Veces validado")),
                    "proteina_principal": _select_text(props.get("Proteina principal")),
                    "combo_ensalada": _select_text(props.get("Combo de ensalada sugerido"))
                    or "(sin revisar)",
                    "_acompanamiento_ids": target_ids,
                    "url": f"https://app.notion.com/{page_id}" if page_id else "",
                }
            )

        targets = await _resolve_acompanamiento_targets(client, all_target_ids)

    for p in platos:
        p["acompanamientos"] = [targets[tid] for tid in p.pop("_acompanamiento_ids")]

    _platos_principales_cache = platos
    return platos


# ---------------------------------------------------------------------------
# Combos de Ensalada (paso 4b de propuesta-menu-semanal) — Fase 6
# ---------------------------------------------------------------------------

# Ensalada es un plato único y permanente del catálogo — resolvemos su
# page_id una sola vez por nombre en vez de hardcodearlo, así no se rompe
# si algún día cambia de página (aunque en la práctica no debería pasar).
_ensalada_page_id_cache: dict = {"id": None}
_insumo_name_cache: dict[str, str] = {}
_ensalada_combos_cache: dict = {"data": None}


async def _resolve_ensalada_id(client: httpx.AsyncClient) -> str:
    if _ensalada_page_id_cache["id"]:
        return _ensalada_page_id_cache["id"]

    resp = await client.post(
        f"{NOTION_API}/data_sources/{settings.PLATOS_DATA_SOURCE_ID}/query",
        headers=_headers(),
        json={
            "filter": {"property": "Nombre", "title": {"equals": "Ensalada"}},
            "page_size": 1,
        },
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
    rows = resp.json().get("results", [])
    if not rows:
        raise RuntimeError('No se encontró el plato "Ensalada" en Platos.')

    page_id = rows[0]["id"]
    _ensalada_page_id_cache["id"] = page_id
    return page_id


async def _resolve_insumo_names(client: httpx.AsyncClient, page_ids: set[str]) -> dict[str, str]:
    missing = [pid for pid in page_ids if pid not in _insumo_name_cache]
    if missing:
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(page_id: str) -> None:
            async with semaphore:
                resp = await client.get(f"{NOTION_API}/pages/{page_id}", headers=_headers())
                if resp.status_code == 200:
                    name = _title_text(resp.json().get("properties", {}).get("Nombre"))
                    _insumo_name_cache[page_id] = name or "(sin nombre)"
                else:
                    _insumo_name_cache[page_id] = "(no disponible)"

        await asyncio.gather(*(fetch_one(pid) for pid in missing))

    return {pid: _insumo_name_cache.get(pid, "(no disponible)") for pid in page_ids}


async def fetch_ensalada_combos(force_refresh: bool = False) -> dict[str, list[str]]:
    """
    Combo -> [insumos], leído de Receta_Insumo.Combos para las relaciones
    del plato "Ensalada" (excluyendo Estado = Descartado). El vocabulario de
    combos es abierto (ver notion-schema.md) — por eso esto se resuelve en
    vivo contra Notion en cada refresh de caché, en vez de hardcodear acá la
    lista de nombres, que se desactualizaría apenas se agregue un combo
    nuevo (ya pasó una vez con "Coleslaw").
    """
    if not force_refresh and _ensalada_combos_cache["data"] is not None:
        return _ensalada_combos_cache["data"]

    _require_config()
    if not settings.RECETA_INSUMO_DATA_SOURCE_ID:
        raise RuntimeError("RECETA_INSUMO_DATA_SOURCE_ID no está configurado — ver README.")

    async with httpx.AsyncClient(timeout=20) as client:
        ensalada_id = await _resolve_ensalada_id(client)

        resp = await client.post(
            f"{NOTION_API}/data_sources/{settings.RECETA_INSUMO_DATA_SOURCE_ID}/query",
            headers=_headers(),
            json={
                "filter": {
                    "and": [
                        {"property": "Plato", "relation": {"contains": ensalada_id}},
                        {"property": "Estado", "select": {"does_not_equal": "Descartado"}},
                    ]
                },
                "page_size": 100,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
        rows = resp.json().get("results", [])

        all_insumo_ids: set[str] = set()
        parsed: list[tuple[str, list[str]]] = []
        for row in rows:
            props = row.get("properties", {})
            insumo_ids = _relation_ids(props.get("Insumo"))
            if not insumo_ids:
                continue
            insumo_id = insumo_ids[0]
            all_insumo_ids.add(insumo_id)
            parsed.append((insumo_id, _multi_select_names(props.get("Combos"))))

        names = await _resolve_insumo_names(client, all_insumo_ids)

    combos_map: dict[str, list[str]] = {}
    for insumo_id, combos in parsed:
        nombre = names.get(insumo_id, "(no disponible)")
        if not combos:
            combos_map.setdefault("(sin combo asignado — uso libre/genérico)", []).append(nombre)
        else:
            for combo in combos:
                combos_map.setdefault(combo, []).append(nombre)

    _ensalada_combos_cache["data"] = combos_map
    return combos_map


# ---------------------------------------------------------------------------
# Precargado de contexto para el primer mensaje de cada conversación nueva
# ---------------------------------------------------------------------------


def _format_context_preload(
    platos: list[dict],
    weeks: list[dict],
    combos: dict[str, list[str]],
    special_platos: dict[str, str],
    combos_note: str | None = None,
) -> str:
    lines = [
        "[Datos precargados de Notion — paso 1 y paso 2 de "
        "propuesta-menu-semanal ya resueltos, más el vocabulario de combos "
        "de Ensalada (paso 4b) — Fase 6 del proyecto de acompañamientos "
        "estructurados. NO vuelvas a consultar esto por Notion, ya lo "
        "tienes acá tal cual saldría de esas consultas. La columna URL de "
        "cada plato es la misma URL que vas a encontrar en las relaciones "
        "de Receta_Insumo y Semanas.Platos de la semana — úsala directo "
        "para esos joins, no hace falta una consulta aparte para resolver "
        "nombre -> URL.]",
        "",
        f"## Catálogo de platos principales ({len(platos)})",
        "| Nombre | Estado | Veces validado | Proteína principal | Combo de ensalada sugerido | Acompañamientos sugeridos | URL |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in platos:
        acomp = (
            "; ".join(f"{a['nombre']} ({a['tipo']})" for a in p["acompanamientos"])
            if p["acompanamientos"]
            else "—"
        )
        lines.append(
            f"| {p['nombre']} | {p['estado']} | {p['veces_validado']} | "
            f"{p['proteina_principal']} | {p['combo_ensalada']} | {acomp} | {p['url']} |"
        )
    lines.append("")
    lines.append(f"## Últimas {len(weeks)} semanas (fecha, origen y platos)")
    lines.append(
        "`platos` es la propuesta/nota original de `Platos de la semana` — nunca se "
        "corrige. `real` es `Platos de la semana (real)`; en blanco significa "
        "\"nadie confirmó todavía, o ya se confirmó que se cumplió tal cual lo "
        "propuesto\" (ambos casos se ven igual — no \"sin datos\") — para el paso 3, "
        "usa `real` en vez de `platos` en las semanas donde `real` no esté vacío. La "
        "más reciente con origen `Propuesta automática` es la que revisa el paso 2c: "
        "si `real` ya tiene contenido ahí, no preguntes de nuevo (ya se registró una "
        "desviación); si está vacía, corresponde preguntar salvo que esta misma "
        "conversación ya haya confirmado con el usuario que esa semana se cumplió tal "
        "cual (no vuelvas a preguntar dos veces por la misma semana en la misma corrida)."
    )
    most_recent_auto_idx = next(
        (i for i, w in enumerate(weeks) if w["origen"] == "Propuesta automática"), None
    )
    for i, w in enumerate(weeks):
        origen = w["origen"] or "(sin origen registrado)"
        detalle = f"platos: {', '.join(w['platos']) or '(sin platos)'}"
        if w["platos_real"]:
            detalle += f" | real: {', '.join(w['platos_real'])}"
        marca = ""
        if i == most_recent_auto_idx and not w["platos_real"]:
            marca = " — ⚠️ paso 2c: pregúntale al usuario si esta semana se cumplió tal cual antes de seguir al paso 3"
        lines.append(f"- {w['label']} [{origen}]: {detalle}{marca}")
    lines.append("")

    # URLs de los platos-acompañamiento (salsas, sopas rotativas) que
    # aparecieron como destino en la tabla de arriba, más los platos
    # especiales fijos (Ensalada, Insumos para desayunos) — resueltos acá
    # para que el agente no tenga que volver a buscarlos por nombre en
    # Notion cuando necesite su URL (ej. para crear/confirmar una relación
    # Receta_Insumo, o incluirlos en Semanas.Platos de la semana).
    acomp_urls: dict[str, str] = {}
    for p in platos:
        for a in p["acompanamientos"]:
            if a.get("url"):
                acomp_urls[a["nombre"]] = a["url"]
    if special_platos or acomp_urls:
        lines.append("## URLs ya resueltas — platos especiales y de acompañamiento")
        lines.append(
            "No hace falta un `notion-query-data-sources` para resolver "
            "nombre → URL de ninguno de estos, ya están acá:"
        )
        for nombre, url in special_platos.items():
            lines.append(f"- {nombre}: {url}")
        for nombre, url in acomp_urls.items():
            lines.append(f"- {nombre}: {url}")
        lines.append("")

    lines.append('## Combos de Ensalada (Receta_Insumo.Combos del plato "Ensalada")')
    if combos_note:
        lines.append(
            f"⚠️ No se pudo precargar esto ({combos_note}). Consulta Notion "
            "en vivo si necesitas el vocabulario de combos de Ensalada para "
            "esta conversación (revisa que la integración interna tenga "
            "acceso a Receta_Insumo, no solo a Platos/Semanas)."
        )
    else:
        lines.append(
            "Vocabulario abierto — esta es la lista vigente al momento de "
            "precargar; puede haber crecido desde entonces si el catálogo "
            "cambió, no la trates como cerrada de por vida. "
            '"(sin combo asignado — uso libre/genérico)" son insumos de '
            "Ensalada sin etiqueta de combo específico."
        )
        for combo, insumos in combos.items():
            lines.append(f"- **{combo}**: {', '.join(insumos)}")
    lines.append("")
    lines.append(
        "Esto cubre el paso 1 (catálogo de platos principales, con su "
        "combo de ensalada sugerido y acompañamientos ya resueltos), el "
        "paso 2 (últimas semanas, con origen y `Platos de la semana (real)` "
        "ya incluidos para el paso 2c), y el vocabulario de combos del "
        "paso 4b de propuesta-menu-semanal. Para cualquier otro dato "
        "(insumos específicos de un plato que no sea Ensalada, otras "
        "categorías de plato como bebida/postre, crear o confirmar "
        "relaciones nuevas, actualizar `Platos de la semana (real)` en el "
        "paso 2c, etc.) sí consulta o escribe en Notion normalmente."
    )
    return "\n".join(lines)


_preload_cache: dict = {"text": None}


async def fetch_context_preload(force_refresh: bool = False) -> str:
    """
    Arma el bloque de texto con el catálogo + últimas 8 semanas + combos de
    Ensalada, listo para anteponer al primer mensaje de una conversación
    nueva. Cacheado por proceso (mismo criterio que las otras consultas de
    este módulo).

    Los combos de Ensalada se resuelven por separado y con manejo de error
    propio: si esa consulta falla (ej. la integración interna todavía no
    tiene acceso a Receta_Insumo — pasa si solo compartiste la página con
    Platos y Semanas), no tiramos abajo el catálogo de platos ni las
    semanas, que sí suelen funcionar. Mejor un precargado incompleto que
    ninguno.
    """
    if not force_refresh and _preload_cache["text"] is not None:
        return _preload_cache["text"]

    platos = await fetch_platos_principales(force_refresh=force_refresh)
    weeks = await fetch_recent_weeks(limit=8, force_refresh=force_refresh)
    special_platos = await fetch_special_platos(force_refresh=force_refresh)

    try:
        combos = await fetch_ensalada_combos(force_refresh=force_refresh)
        combos_note = None
    except Exception as exc:  # noqa: BLE001
        combos = {}
        combos_note = str(exc)

    text = _format_context_preload(platos, weeks, combos, special_platos, combos_note)

    _preload_cache["text"] = text
    return text


# ---------------------------------------------------------------------------
# Guardado de notas de conversación en modo prueba (ver conversation_tools.py)
# ---------------------------------------------------------------------------

_NOTION_TEXT_BLOCK_LIMIT = 1900  # margen bajo el límite real de 2000 de Notion


def _paragraph_blocks(texto: str) -> list[dict]:
    """
    Parte `texto` en bloques "paragraph" de Notion, respetando saltos de
    línea dobles como separadores de párrafo cuando existen, y sin superar
    _NOTION_TEXT_BLOCK_LIMIT caracteres por bloque de texto (la API de
    Notion rechaza un solo rich_text.content de más de 2000 caracteres).
    """
    parrafos = [p for p in texto.split("\n\n") if p.strip()]
    if not parrafos:
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "(sin contenido)"}}]
                },
            }
        ]
    blocks = []
    for parrafo in parrafos:
        for i in range(0, len(parrafo), _NOTION_TEXT_BLOCK_LIMIT) or [0]:
            chunk = parrafo[i : i + _NOTION_TEXT_BLOCK_LIMIT] or parrafo
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
                }
            )
            if not parrafo:
                break
    return blocks or [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "(sin contenido)"}}]},
        }
    ]


async def create_test_notes_page(resumen: str) -> str:
    """
    Crea una sub-página hija de TEST_NOTES_PARENT_PAGE_ID con `resumen`
    como contenido — el equivalente, en modo prueba, a la sub-página real
    "Notas de la conversación" que el paso 7 de propuesta-menu-semanal
    crea colgando de la Semana recién guardada. Acá NUNCA cuelga de una
    Semana (real ni de prueba): siempre del mismo destino fijo, así que
    borrarla después (a mano, en Notion) no deja ningún rastro en datos
    reales. Devuelve la URL de la página creada.

    Lanza RuntimeError si TEST_NOTES_PARENT_PAGE_ID no está configurado o
    si Notion responde con un error (ej. la integración interna no está
    compartida con esa página todavía) — quien llama (ver
    conversation_tools.guardar_notas_de_prueba) se encarga de convertir
    eso en un mensaje legible para el agente, no para el usuario final
    directamente.
    """
    _require_config()
    if not settings.TEST_NOTES_PARENT_PAGE_ID:
        raise RuntimeError(
            "TEST_NOTES_PARENT_PAGE_ID no está configurado — ver .env.example."
        )

    titulo = f"[PRUEBA] Notas de conversación — {datetime.now():%Y-%m-%d %H:%M}"
    body = {
        "parent": {"page_id": settings.TEST_NOTES_PARENT_PAGE_ID},
        "properties": {"title": [{"type": "text", "text": {"content": titulo}}]},
        "children": _paragraph_blocks(resumen),
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{NOTION_API}/pages", headers=_headers(), json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion respondió {resp.status_code}: {resp.text[:300]}")
        return resp.json().get("url") or ""
