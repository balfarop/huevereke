"""
Paso 1 de la migración al caché local: sincroniza Notion -> SQLite y
muestra un resumen — SIN tocar la web ni el agente (agent_service.py y
main.py de este proyecto son, por ahora, una copia sin cambios de la app
que ya funciona).

Uso:
    python build_cache.py            # sincroniza y muestra conteos
    python build_cache.py --sample   # además imprime filas de muestra

Requiere en tu .env (ver .env.example):
- NOTION_TOKEN (el mismo de siempre)
- Las 5 *_DATA_SOURCE_ID. Semanas, Platos y Receta_Insumo ya tienen
  default en config.py (los mismos que usa notion_direct.py hoy); te
  van a faltar INSUMOS_DATA_SOURCE_ID y SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID
  — sácalos con notion-fetch sobre el link de entrada en
  notion-interaccion/references/notion-schema.md, o pídeselos una vez al
  agente en Claude Desktop.

Qué revisar en el resultado antes de pasar al siguiente paso:
- Que "platos" dé algo cercano a tu catálogo real (33 y subiendo).
- Que "receta_insumo_omitidas_sin_relacion" NO aparezca (o sea 0) — si
  aparece, hay filas de Receta_Insumo con Plato o Insumo sin resolver,
  algo raro que vale la pena mirar antes de construir nada encima.
- Con --sample: que los combos de Ensalada y los acompañamientos de
  algún plato conocido (ej. "Arroz con pollo" -> Salsa huancaina) se vean
  como esperas.
"""
import argparse
import asyncio
import json

from app.config import settings
from app.menu_cache import queries
from app.menu_cache.schema import get_connection, init_db
from app.menu_cache.sync import refresh_cache


async def main(sample: bool) -> None:
    print(f"Inicializando esquema en {settings.MENU_CACHE_DB_PATH} ...")
    init_db(settings.MENU_CACHE_DB_PATH)

    print("Sincronizando desde Notion (puede tardar unos segundos)...")
    counts = await refresh_cache()

    print("\nResumen de la sincronización:")
    for tabla, n in counts.items():
        print(f"  {tabla}: {n}")

    if sample:
        conn = get_connection(settings.MENU_CACHE_DB_PATH)
        try:
            print("\n--- platos_principales() (primeros 5, con acompañamientos) ---")
            print(json.dumps(queries.platos_principales(conn)[:5], ensure_ascii=False, indent=2))

            print("\n--- semanas_recientes(limit=3) ---")
            print(json.dumps(queries.semanas_recientes(conn, limit=3), ensure_ascii=False, indent=2))

            print("\n--- combos_ensalada() ---")
            print(json.dumps(queries.combos_ensalada(conn), ensure_ascii=False, indent=2))
        finally:
            conn.close()

    print(
        "\nListo. Revisa los conteos y (si usaste --sample) los datos de "
        "muestra contra lo que sabes de tu catálogo real antes de seguir "
        "con el siguiente paso (conectar esto al agente)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", action="store_true", help="Imprime filas de muestra además del resumen"
    )
    args = parser.parse_args()
    asyncio.run(main(args.sample))
