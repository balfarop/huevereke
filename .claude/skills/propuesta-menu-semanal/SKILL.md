---
name: propuesta-menu-semanal
description: "Usa y mantiene los campos estructurados de acompañamientos (Combo de ensalada sugerido, Combos, Acompañamientos sugeridos, Tipo de sugerencia de acompañamiento) poblados por curar-combos-plato y mantenidos junto con analisis-plato-insumo, para armar las preguntas de ensalada/sopa/salsa del paso 4b con opciones reales en vez de inventadas — incorpora además los aprendizajes de la primera corrida completada: reconciliación final de stock/duplicados antes de cerrar, coincidencia de nombres coloquiales con el catálogo, re-preguntar acompañamientos al reemplazar un plato, manejo de recetas nuevas no catalogadas, límites reales de Notion en este workspace, el campo Origen en Semanas para distinguir propuestas automáticas de notas manuales, y el precargado bajo demanda (herramienta `mcp__web_context__precargar_contexto`, llamada por el propio agente al confirmar el flujo) de los pasos 1 y 2 cuando corre en el canal web de Huevereke."
---

# Propuesta de Menú Semanal

## Objetivo

Proponer, cada domingo, un menú de la semana (normalmente 5 platos principales) más su bebida, acompañamiento (ensalada u otro) y lista de compras consolidada, priorizando platos con insumos confiables sobre platos aún poco validados, y usando cada propuesta aceptada como una oportunidad más para cerrar el conocimiento de los platos que todavía están en refinamiento. Cuando la familia declare insumos que ya tienen en casa esa semana (paso 2b), úsalos como desempate para ahorrar en la compra, sin bajar por eso el estándar de validación.

Esta skill no solo arma los platos principales: también se encarga de las categorías que antes se omitían en silencio (bebida, ensalada/sopa de acompañamiento, insumos de desayuno, frutas de consumo familiar) preguntando directamente por ellas antes de cerrar la propuesta, en vez de asumir o inventar.

El flujo de validación tiene dos gates, no uno: primero la propuesta detallada (paso 6, con tablas) para que el usuario revise y corrija platos/cantidades, y después — solo cuando esa ya está aceptada — un reporte final minimalista en texto plano (paso 6b) que es el que realmente se usa para pedir y comprar, y el que habilita el guardado en Notion (paso 7).

## Requisito previo

Esta skill depende de 5 bases de Notion construidas y mantenidas por `analisis-plato-insumo`: `Platos`, `Insumos`, `Receta_Insumo`, `Semanas`, `Semana_Insumo_Familiar`. Si esas bases no existen o `Platos` está prácticamente vacía, avísale al usuario y no continúes — primero necesita procesar algunas semanas con esa otra skill.

**Todo lo relacionado a Notion (esquema de las 5 bases, el link para ubicarlas, límites de la plataforma, eficiencia de consultas, sintaxis SQL) vive en la skill compartida `notion-interaccion`, no aquí.** Antes de leer o escribir cualquiera de las 5 bases, consulta ahí: `notion-schema.md` (estructura completa + link de entrada) y `reglas-interaccion.md` (límites de JOIN/cuota, cómo batchear consultas, gotchas de sintaxis SQL confirmados en producción, y qué hacer si `Query Data Source` devuelve el error de cuota agotada). Esa skill debe estar adjuntada/instalada junto a esta — si no puedes encontrar esos archivos, avísale al usuario en vez de adivinar el esquema de memoria.

**La ruta para leerlos depende del entorno** (confirmado con una prueba real): en Claude Desktop usa la ruta relativa `../notion-interaccion/references/notion-schema.md` (o `reglas-interaccion.md`); si esa ruta falla con un error de formato ("file_path must be an absolute path"), estás en un Managed Agent — usa en su lugar `/workspace/skills/notion-interaccion/references/notion-schema.md` (o `reglas-interaccion.md`). Intenta la relativa primero y cae a la absoluta solo si falla por formato de ruta, nunca al revés.

**Antes de asumir el nombre o la existencia de un campo, verifica contra el esquema real fetcheado de Notion, no contra lo que diga de memoria esta guía — el esquema documentado puede quedarse desactualizado respecto a la base real.** (Esto ya pasó una vez: la guía mencionaba escribir `Origen` en `Semanas` cuando ese campo todavía no existía; se agregó a la base el 2026-08-31 y ahora sí es válido usarlo — ver paso 7.)

Dentro del flujo de abajo, cuando necesites el mismo dato para varios platos/días a la vez (pasos 3b y 4b) o agrupar escrituras nuevas (paso 7), aplica las reglas de "por lote, nunca por item" de `notion-interaccion/references/reglas-interaccion.md` — no repitas ninguna consulta que ya hiciste en esta misma corrida (ver esa misma referencia); el paso 6b en particular nunca necesita una consulta nueva, reformatea los mismos datos ya reunidos para el paso 6.

### Caché local de este proyecto (`mcp__menu_cache__*`) — usarlo explícitamente en los pasos 3b, 4b y 5

Este fork paralelo de Huevereke tiene, además del conector de Notion, un servidor MCP local en el mismo proceso (`app/menu_cache/`, ya conectado en `app/agent_service.py`) con un espejo SQLite de las 5 bases — se sincroniza corriendo `python build_cache.py`, no automáticamente en cada turno (ver README.md de este proyecto). Al ser una copia local, resuelve estas consultas de lectura sin gastar nada de la cuota compartida de "Query Data Source" de Notion, pero puede estar desactualizada respecto a algo que se acaba de guardar en Notion en esta misma conversación (paso 7) — `mcp__menu_cache__estado_cache` devuelve cuándo se sincronizó por última vez, útil si tienes dudas de frescura.

**En los pasos 3b, 4b y 5 (más abajo), elegí explícitamente estas herramientas en vez de una consulta en vivo a Notion** — son lecturas repetitivas, estructuralmente idénticas a las que ya se hacían por lote contra Notion, y el caché ya las resuelve con los JOIN correspondientes hechos. Las herramientas disponibles: `mcp__menu_cache__platos_principales` (catálogo completo, con combo de ensalada y acompañamientos ya resueltos), `mcp__menu_cache__receta_insumo_por_platos` (insumos de una lista de ids de plato, con nombre/unidad ya resueltos por JOIN), `mcp__menu_cache__combos_ensalada` (vocabulario de combos con sus insumos, excluyendo `Descartado`), `mcp__menu_cache__semanas_recientes` (incluye `origen` y `platos_real` de cada semana, agregados 2026-09-14 — útiles si necesitas resolver el paso 2c/3 desde el caché en vez del precargado o Notion en vivo), `mcp__menu_cache__frecuencia_consumo_familiar`, `mcp__menu_cache__buscar_platos` y `mcp__menu_cache__estado_cache`. Si alguna no está disponible (ej. corriendo en Claude Desktop, donde este servidor no existe) o devuelve un error, no te bloquees: cae de vuelta a la consulta normal contra Notion descrita en ese paso, como se hacía antes de este caché. **Ten en cuenta que este caché puede estar desactualizado respecto a una corrección de `Platos de la semana (real)` que tú mismo acabas de escribir en el paso 2c de esta misma conversación** (se sincroniza corriendo `build_cache.py`, no en cada turno) — si acabas de escribir ahí, usa el dato tal como lo escribiste, no lo que diga el caché todavía.

## Parámetros del sistema

Estos valores son el punto de partida razonable; ajústalos si el usuario lo pide explícitamente, y menciónalo si notas que las reglas no están dando una propuesta razonable con el catálogo actual.

- **Ventana de no-repetición (solo platos principales)**: no proponer un `Plato principal` que haya aparecido en las últimas 3 semanas. **Para cada una de esas semanas, usa `Semanas.Platos de la semana (real)` si tiene algo cargado; si está en blanco, usa `Platos de la semana`** (blank ahí significa "se cumplió tal cual lo propuesto", no "sin datos") — ver paso 2c para cómo y cuándo se llena ese campo. Esta ventana **no aplica** a Ensalada ni a Bebida — la familia los repite con normalidad semana a semana, y forzar variedad ahí sería inventar una restricción que no existe en la realidad.
- **Máximo de platos poco validados**: máximo 2 de 5 platos principales en la propuesta pueden estar en `Estado = Nuevo` o `En refinamiento`. El resto debe priorizar `Cerrado` o `En refinamiento` con mayor `Veces validado`.
- **Tamaño del menú**: 5 platos principales (lunes a viernes), salvo que el usuario indique otro número para esa semana.
- **Ventana de frecuencia para consumo familiar**: al sugerir frutas/consumo familiar, mira las últimas 4-6 semanas registradas en `Semana_Insumo_Familiar`.
- **Proteína siempre presente**: esta familia tiene niños pequeños y espera proteína en (casi) todos los platos principales. No confíes ciegamente en `Platos.Proteína principal` para decidir si un plato ya tiene proteína — ese campo puede estar desactualizado (ver paso 3b). Ante la duda, pregunta; no propongas en silencio un plato sin proteína identificable.
- **Ensalada/sopa se decide por plato, no por semana**: la familia puede acompañar un solo día con ensalada, todos los días, o ninguno — y la combinación de insumos de la ensalada suele variar según qué plato principal acompaña ese día (ver paso 4b).
- **Bebida: puede haber más de una en la misma semana.** No asumas que "bebida de la semana" significa una sola — es normal que la familia pida varias a la vez (ej. chicha morada, agua de maracuyá, jugo de naranja y jugo de lúcuma juntas). Cuando eso pase, pregunta igual en una sola tanda (paso 4a), pero acepta y registra todas las que confirmen: en el reporte minimalista van en una sola línea "Bebida:" separadas por coma, y todas entran a la relación `Platos de la semana` en el paso 7.
- **Insumos en stock como desempate, no como criterio de peso**: si la familia declara insumos que ya tienen en casa (paso 2b), úsalos solo para desempatar entre platos igual de confiables en el paso 3 y como sugerencia dirigida en bebida/ensalada (paso 4a/4b) — nunca para preferir un plato con menos historial de validación. La ventana para calcular qué insumos son "de uso frecuente" y sugerirlos es de 8 semanas (ver paso 2). **Importante: el stock declarado no vive solo en el paso 2b — el usuario puede declarar más insumos en stock en cualquier punto posterior de la conversación** (al pedir un cambio, al corregir una cantidad, al aclarar un acompañamiento). Cada vez que eso pase, trátalo con el mismo peso que la declaración inicial y recuerda restarlo de la lista de compras final (ver paso 5b).
- **Dos gates de validación, no uno**: el paso 6 (detallado) y el paso 6b (minimalista) son aprobaciones independientes. Aceptar el paso 6 solo habilita generar el paso 6b — no autoriza guardar en Notion. Solo la aceptación explícita del paso 6b habilita el paso 7.

## Formato de salida

Antes del paso 6, decide qué modo usar. El **Detallado** es el que aplica en la inmensa mayoría de usos (cualquier conversación de chat normal — Cowork, Claude.ai, Claude Desktop) y no requiere ninguna decisión de tu parte; los otros dos son para cuando esta skill corre detrás de un agente headless de mensajería (WhatsApp, Telegram vía API), donde no hay tablas ni botones.

| Modo | Cuándo usarlo |
|---|---|
| **Detallado (por defecto)** | Chat normal (Cowork, Claude.ai, Claude Desktop) — aplica sin que tengas que hacer nada. Tablas markdown completas, es el formato de los ejemplos de los pasos 3 a 7 de este documento. |
| **WhatsApp** | El usuario lo pide explícitamente, o la sesión corre como el agente de WhatsApp. Texto plano, sin tablas ni `##`, solo `*negrita*` simple. |
| **Telegram (compacto)** | El usuario lo pide explícitamente, o la sesión corre como el agente de Telegram. Texto plano aún más comprimido, cuidado con `parse_mode=MarkdownV2`. |

Ver `references/formatos-salida.md` para las reglas completas de cada modo (etiquetas abreviadas, plantillas de mensaje, cómo detectar el canal correcto cuando esta skill corre en un agente headless, y cómo manejar las preguntas de los pasos 3b/4 cuando no hay UI interactiva para hacerlas).

**Esta elección de modo (Detallado/WhatsApp/Telegram) solo aplica al paso 6.** El paso 6b tiene su propio formato fijo — minimalista, sin tablas, sin encabezados markdown — que no cambia según el canal.

## Flujo de trabajo

### 1. Consultar el catálogo de platos principales

**Antes de consultar Notion para esto, revisa si tienes disponible la herramienta `mcp__web_context__precargar_contexto`.** Solo existe en el canal web de Huevereke, y solo tiene sentido llamarla UNA vez que ya decidiste seguir el flujo de esta skill — es decir, después de aplicar el filtro de tema del canal (ver contexto del sistema): si la conversación resulta ser sobre otra cosa, no la llames. Ni bien confirmes que sí vas a armar una propuesta, llámala una sola vez, sin argumentos: trae de un solo golpe, por REST directo y sin gastar cuota de "Query Data Source" de Notion, el catálogo de platos principales de este paso 1 (ya con combo de ensalada y acompañamientos resueltos), las últimas semanas del paso 2, y el vocabulario de combos de Ensalada. Usa ese resultado tal cual para este paso, sin hacer ninguna consulta a Notion. Si esa herramienta no está en tu lista (Claude Desktop, Telegram, WhatsApp, una sesión de terminal vía Agent SDK) o la llamada falla, sigue con la consulta normal de abajo.

Trae de `Platos` todos los registros con `Tipo de plato = Plato principal`, pidiendo `Estado`, `Veces validado`, `Proteína principal`, `Combo de ensalada sugerido` y `Acompañamientos sugeridos` (no la fila completa). Los últimos dos se agregaron en la Fase 5 del proyecto de acompañamientos estructurados — sin ellos, ni el paso 3 (afinidad semanal) ni el paso 4b (recomendación de combo/salsa) tienen con qué trabajar más adelante en la misma corrida. Una sola consulta — este catálogo se reutiliza en el resto de la conversación (ver "Eficiencia de consultas a Notion"), no lo vuelvas a traer más adelante en la misma corrida.

**El resultado de `mcp__web_context__precargar_contexto` trae, además, las URLs resueltas de "Ensalada", "Insumos para desayunos", y de cada plato-acompañamiento que aparezca en la columna `Acompañamientos sugeridos`** (salsas, sopas rotativas) — bajo el título "URLs ya resueltas". Si la llamaste, úsalas directo para cualquier join con `Receta_Insumo` o `Semanas.Platos de la semana` que necesite esas URLs — no hace falta un `notion-query-data-sources` para resolver ese nombre. En Claude Desktop/Cowork (sin esa herramienta) sí consúltalas normalmente, como hasta ahora.

### 2. Consultar semanas recientes

**Mismo chequeo que en el paso 1**: si ya llamaste a `mcp__web_context__precargar_contexto` en el paso 1, la sección "Últimas semanas (fecha y platos)" de ese resultado ya es el resultado de este paso — no vuelvas a llamarla ni a consultar Notion para esto (una sola llamada por conversación, cubre los pasos 1 y 2 juntos). Si no la llamaste (herramienta no disponible), sigue con la consulta normal de abajo.

Trae las últimas 8 entradas de `Semanas` (por fecha) y sus platos relacionados, en una sola consulta pidiendo solo las columnas necesarias (fecha, `Origen`, `Platos de la semana` y `Platos de la semana (real)`). Este mismo resultado se reutiliza para tres cosas — no lo vuelvas a consultar más adelante: (a) las 3 más recientes definen qué excluir por la ventana de no-repetición de platos principales (paso 3, prefiriendo `Platos de la semana (real)` sobre `Platos de la semana` cuando tenga algo cargado), (b) las 8 completas alimentan el cálculo de insumos de uso frecuente del paso 2b, y (c) la más reciente con `Origen = Propuesta automática` es la que revisa el paso 2c.

### 2b. Preguntar por insumos que ya tienen en stock (opcional)

Antes de seleccionar los platos (paso 3), pregúntale a la familia si hay insumos que ya tienen en casa esta semana y quieren aprovechar para ahorrar en la compra. Es una pregunta abierta de texto libre — nunca bloqueante: si no responden nada, sigue el resto del flujo sin este dato.

Para que no se les olvide nada, arma un checklist de sugerencia con los insumos de uso más frecuente en las últimas 8 semanas (del paso 2), contando por insumo en cuántas semanas *distintas* aparece, ordenando de mayor a menor y tomando el top 8-10.

Interpreta la respuesta contra `Insumos.Nombre` y `Insumos.Alias`: si algo no calza con ningún `Nombre`/`Alias` conocido, pregunta en vez de adivinar o crear uno nuevo. Ignora cualquier cantidad que mencionen — trátalo como señal binaria.

**Esta lista de insumos en stock es efímera para efectos de Notion** (se usa solo para priorizar dentro de esta conversación y se descarta al terminar — no crees ni actualices ninguna fila de Notion por esto), **pero no es efímera dentro de la conversación misma**: mantén mentalmente acumulada TODA declaración de stock que el usuario haga en cualquier punto posterior (no solo aquí), porque el paso 5b la necesita completa al cerrar.

### 2c. Confirmar si la semana inmediatamente anterior se cumplió tal cual (campo `Platos de la semana (real)`)

De las semanas recientes ya traídas en el paso 2, identifica la más reciente con `Origen = Propuesta automática` (si el precargado no trajo `Origen`, o estás en la consulta normal a Notion, tráelo junto con el resto de columnas de ese paso — no hace falta una consulta aparte). Si esa semana ya tiene algo cargado en `Platos de la semana (real)`, no preguntes nada — ya quedó registrada (posiblemente en la corrida anterior) y sigue con el paso 3. Si no existe ninguna semana con `Origen = Propuesta automática` todavía (primera corrida de esta skill), tampoco preguntes — sigue de largo.

En cualquier otro caso, antes de pasar al paso 3, pregúntale al usuario en una sola tanda (`AskUserQuestion`) si esa semana se preparó tal como se propuso (ej. "La semana pasada propusimos [lista de platos] — ¿se hizo tal cual, o hubo cambios (algún plato que no se llegó a hacer, o que cambiaron por otro)?"), con opciones simples tipo "Sí, tal cual" / "Hubo cambios, quiero contarte" — el texto libre ya cubre el detalle de qué cambió.

- Si confirma que se cumplió tal cual: **no escribas nada** — deja `Platos de la semana (real)` en blanco a propósito (blank ya significa "coincidió", no hace falta copiar la lista). No es un paso bloqueante: si el usuario no quiere detenerse en esto, una respuesta rápida de "sí, tal cual" alcanza y sigues.
- Si hubo cambios: identifica con el usuario la lista final de platos que sí se prepararon esa semana (mismo cuidado que en el paso 3 para calzar nombres coloquiales contra el catálogo — no inventes un plato nuevo por esto). Actualiza la página de esa `Semana` (ya existente, la tienes por su URL del paso 2/precargado) escribiendo esa lista completa en `Platos de la semana (real)` — una sola llamada de actualización, sin tocar `Platos de la semana` (que se queda igual, como registro histórico de lo que se propuso). Este es un ajuste puntual a una semana ya cerrada, no una escritura nueva de la semana en curso — no esperes al paso 7 para hacerlo.
- **Si el contexto de esta conversación viene marcado con MODO_PRUEBA** (ver paso 7 más abajo), tampoco escribas esta corrección — pregunta igual si quieres, pero avísale al usuario que en modo prueba no se guarda, mismo criterio que el resto de escrituras a Notion de esta skill.

### 3. Seleccionar los platos principales de la semana

Aplica en este orden:

1. Excluye los platos principales servidos dentro de la ventana de no-repetición — usando, semana por semana, `Platos de la semana (real)` cuando tenga algo cargado y `Platos de la semana` cuando esté en blanco (ver Parámetros del sistema y paso 2c).
2. De lo que queda, prioriza platos `Cerrado` y `En refinamiento` con mayor `Veces validado`. Si dos o más platos empatan en `Estado` y `Veces validado`, prioriza el que use más insumos de los declarados en stock (paso 2b) — ignorando insumos marcados como agregado opcional en `Notas`.
3. Completa hasta el máximo permitido (2 de 5) con platos `Nuevo` o `En refinamiento` de baja validación — márcalos explícitamente como de baja confianza.
4. Balancea proteína principal entre los días: evita repetir la misma proteína en días consecutivos cuando el catálogo lo permita.
5. Si no alcanzan platos para completar el menú, afloja la ventana de no-repetición de a un paso (nunca el límite de poco-validados).

**Aprovecha `Platos.Acompañamientos sugeridos` cuando el plato destino tiene `Tipo de sugerencia de acompañamiento: Misma semana`** (distinto de `Mismo día`, que se maneja en el paso 4b — ver la nota de migración 2026-09-05 de `notion-schema.md`; casos conocidos: Menestron, ligado a varios platos de carne de res por insumos compartidos; y pares simétricos entre dos `Plato principal` que la familia identificó explícitamente como buena combinación semanal, ej. Arroz con pollo ↔ Seco con frejoles — pero la consulta es genérica, no depende de conocer esos nombres). **Estos pares simétricos tienen el tipo seteado en ambos lados** — al resolver el destino de un plato ya seleccionado, no asumas que el otro lado está en blanco solo porque el plato ya seleccionado también aparece como origen de otras relaciones (mismo día) hacia salsas/sopas; son campos independientes en el mismo plato. Resuelve los platos destino de las relaciones de los platos ya seleccionados esta semana (Nombre + `Tipo de sugerencia de acompañamiento`, en una sola consulta) y, si dos o más comparten el mismo destino `Misma semana`, ofrécelo como sugerencia opcional para completar el menú (ej. "ya que llevan Quinua al pesto con bistec frito, ¿agregamos Menestrón otro día? comparten insumos") — es una sugerencia a confirmar, nunca una selección automática.

**Si el usuario ya te indicó los platos directamente** (ej. "haremos X, Y, Z" o pide ajustar/reemplazar un plato de una propuesta ya en curso), usa esos en vez de seleccionar — pero antes de asumir que un nombre coloquial corresponde a un plato del catálogo o que es genuinamente nuevo:

- **Busca coincidencias por Notas, no solo por título.** Un plato descrito con detalle por el usuario (ej. "seco de pollo con frejoles y chicharrita") puede no coincidir textualmente con ningún `Nombre` de `Platos`, pero sí calzar con las `Notas` de un plato existente de nombre distinto o parecido — caso real: "seco de pollo con frejoles y chicharrita" no era el plato "Seco de pollo" (que existe aparte, sin frejoles) sino "Seco con frejoles", cuyas `Notas` mencionan explícitamente la chicharrita. Cuando haya dos platos de nombre parecido en el catálogo (ej. "Seco de X" vs. "X con frejoles"), consulta las `Notas` de ambos candidatos antes de elegir — adivinar por el nombre más parecido puede seleccionar el plato equivocado y arruinar toda la lista de compras de ese día.
- Si genuinamente no calza con ningún plato ni sus `Notas`, no inventes uno nuevo — pregunta si es alias de uno existente o si es realmente nuevo (ver nota sobre recetas nuevas no catalogadas, más abajo).

**Si el usuario reemplaza un plato ya seleccionado en la conversación en curso** (ej. "no hagamos X, en su lugar hagamos Y"), trata cualquier decisión de acompañamiento (ensalada/sopa, paso 4b) que ya hubieras propuesto u obtenido para el plato **removido** como inválida — no la arrastres al plato nuevo asumiendo que sigue aplicando. Vuelve a preguntar explícitamente el acompañamiento para el día afectado, incluso si tu propia sugerencia anterior queda repetida por error del usuario (puede pasar, y está bien pedir que lo confirme en vez de asumir en cualquier dirección). Caso real: al reemplazar "Pollo al horno" por "Seco con frejoles" en un día, seguía sobre la mesa la sugerencia de "ensalada rusa" (asociada solo al plato viejo) y generó confusión que solo se resolvió preguntando de nuevo.

**Antes de seguir con el paso 4 (acompañamientos), confirma el menú de platos actualizado completo — no solo el día que cambió.** Esto aplica cada vez que el usuario pide un cambio sobre una propuesta que tú ya habías armado (no hace falta si el usuario te dio los platos directamente desde el principio, ahí ya está confirmando su propia elección). Usa `AskUserQuestion` mostrando el listado día por día ya actualizado (ej. "Con el cambio, la semana queda: Lunes Seco con frejoles, Martes Arroz con pollo, ... ¿seguimos así o hay otro ajuste?"), con opciones simples tipo "Sí, sigamos así" / "Quiero cambiar otro plato" — el campo de texto libre ya cubre cualquier ajuste puntual. **No avances a las preguntas de acompañamiento (paso 4/4b) de un plato que el usuario todavía no confirmó que le gusta** — puede que quiera seguir ajustando el menú antes de llegar a esa parte, y preguntarle por la ensalada de un plato que capaz todavía no quiere es trabajo que después hay que repetir.

### 3b. Verificar que cada plato seleccionado tenga proteína (o preguntar)

**Usa `mcp__menu_cache__receta_insumo_por_platos`**, pasando de una sola vez los ids de Notion de todos los platos seleccionados en el paso 3 (`plato_ids`), para traer sus filas de `Receta_Insumo` — ya viene con `insumo_nombre` y `unidad_estandar` resueltos por JOIN. Si la herramienta no está disponible o el caché está vacío/desactualizado (revisa `mcp__menu_cache__estado_cache` si tienes dudas), cae a la consulta normal por lote contra `Receta_Insumo` y `Notas` de cada plato seleccionado (sin cambios respecto a la versión anterior) antes de asumir que `Proteína principal` es correcto.

### 4. Preguntar por los acompañamientos de la semana — antes de armar la lista de insumos

Una sola tanda de preguntas, no una por una: bebida (recordando que puede ser más de una, ver Parámetros del sistema), ensalada/sopa por día, desayuno, frutas de consumo familiar.

**Antes de preguntar por desayuno, trae el patrón habitual vigente e inclúyelo en la propia pregunta — no preguntes a ciegas.** Los insumos de desayuno cuelgan de `Receta_Insumo` bajo el plato colector genérico "Insumos para desayunos" (ver `notion-interaccion/references/notion-schema.md`). Usa `mcp__menu_cache__receta_insumo_por_platos` con el id de ese plato (si corriste `mcp__web_context__precargar_contexto` en el paso 1, su URL ya viene resuelta bajo "URLs ya resueltas"; si no, resuélvela buscando en `Platos` por `Nombre = "Insumos para desayunos"`) para traer los insumos y cantidades vigentes — o la consulta normal a `Receta_Insumo`/`Insumos` si el caché no está disponible. Menciónalos en la propia pregunta (ej. "¿mantenemos el patrón habitual de desayuno — huevo, pan, palta, mantequilla — o cambiamos algo esta semana?") en vez de preguntar "¿algo para el desayuno?" sin contexto — así el usuario decide con la información delante, sin depender de recordarla de memoria. Si "Insumos para desayunos" todavía no tiene ninguna fila en `Receta_Insumo` (catálogo nuevo), pregunta igual pero sin inventar un patrón que no existe.

**Mismo problema, mismo arreglo, para frutas/consumo familiar — y ojo, el límite de 2-4 opciones de `AskUserQuestion` es un límite de la INTERFAZ (botones), no una guía de cuántas frutas mencionar.** Antes de preguntar, usa `mcp__menu_cache__frecuencia_consumo_familiar` (ventana de 4-6 semanas, ver Parámetros del sistema — o la consulta normal a `Semana_Insumo_Familiar` si el caché no está disponible) y trae la lista COMPLETA de insumos que aparecieron en esa ventana, no solo 2-4. Escribe esa lista completa dentro del TEXTO de la pregunta, a modo de guía (ej. "¿pedimos lo de siempre — manzana, plátano, papaya, mandarina, uva, pera — o cambiamos/agregamos algo esta semana?"), y reserva las 2-4 opciones de botón para respuestas simples (ej. "Sí, lo de siempre" / "Quiero ajustar algo") — el campo de texto libre ya cubre cualquier fruta puntual que quieran agregar o quitar. Preguntar "¿algo de fruta esta semana?" sin mostrar el patrón es lo que ha estado dejando fuera, sin que nadie lo note, frutas que la familia sí suele pedir pero que no entran en un puñado corto de opciones de botón.

**Si el usuario describe una receta de acompañamiento completa que no existe en el catálogo** (ej. una crema/sopa con su lista de insumos, sin que corresponda a ningún `Plato` ya registrado ni a una variante conocida): avísale que no está en el catálogo y pregunta si quiere que quede registrada como plato nuevo (trabajo de `analisis-plato-insumo`) o si prefiere usarla solo para esta semana sin catalogarla. Si elige NO catalogarla:

- Usa igual sus insumos para la lista de compras de esta semana (paso 5).
- Menciónala en el reporte minimalista (paso 6b) como parte de la línea de la comida de ese día, en lenguaje corriente.
- **No la incluyas en la relación `Platos de la semana`** al guardar (paso 7) — esa relación solo acepta platos que existen en el catálogo `Platos`.
- Deja una nota explícita en la sección "Bitácora técnica" de la sub-página "Notas de la conversación" (paso 7) señalando que esta receta quedó sin catalogar, con sus insumos, para que `analisis-plato-insumo` la evalúe si se repite en semanas futuras — no en `Semanas.Texto original` (ver paso 7, esa columna se dejó acotada al reporte final para evitar que la llamada que crea la Semana se vuelva demasiado grande).

### 4b. Acompañamiento por día (ensalada/sopa/salsa) — arma la pregunta desde los datos estructurados, no la inventes

Para cada día/plato principal seleccionado, antes de preguntar nada:

1. **Consulta `Platos.Combo de ensalada sugerido` del plato principal de ese día** (ya lo tienes del catálogo del paso 1, o tráelo si falta — preferí `mcp__menu_cache__platos_principales`, que trae `combo_ensalada_sugerido` ya resuelto para todo el catálogo de una sola vez, sin filtrar nada aparte):
   - `No lleva ensalada` → no preguntes por ensalada ese día. Si el usuario pide una igual esa semana, trátalo como una excepción puntual sin tocar el default (ver paso 7).
   - Un combo específico (ej. `Ensalada rusa`) → inclúyelo como opción recomendada ("(Recomendado)") en el `AskUserQuestion`, junto con 2-3 alternativas del vocabulario de combos (vocabulario abierto y creciente — fetchea las opciones vigentes de ese Select en Notion, no asumas una lista fija de memoria). No agregues tú una opción tipo "Otro"/"Otra combinación" si sabes que el cliente ya ofrece un campo de texto libre aparte (confirmado así en el canal web de Huevereke); si no tienes certeza de que el cliente lo ofrezca (Claude Desktop, Cowork, Telegram, WhatsApp), sí conviene dejar una opción de texto libre entre las alternativas para no dejar sin salida a una combinación distinta.
   - `Variable / sin patrón fijo`, o en blanco (plato nuevo, todavía sin revisar) → pregunta igual, sin marcar ninguna opción como recomendada.
2. **Consulta también `Platos.Acompañamientos sugeridos` del mismo plato, y filtra por `Tipo de sugerencia de acompañamiento: Mismo día`** (Salsa huancaina, Sopa de kion, etc. — ver la nota de migración 2026-09-05 de `notion-schema.md`; el mismo `mcp__menu_cache__platos_principales` del punto 1 ya trae, por plato, la lista `acompanamientos` con su `tipo_sugerencia_acompanamiento` — no hace falta una segunda llamada, solo filtrar esa lista por `Mismo día`). Ofrécelo como parte de la misma tanda de preguntas (ej. "¿la acompañamos con Salsa huancaina, como de costumbre?") en vez de omitirlo en silencio como se hacía antes. Los destinos marcados `Misma semana` (hoy: Menestron) no se ofrecen acá — esos son una decisión de qué platos incluir en la semana, no de acompañamiento de un día puntual, y ya se manejan en el paso 3.
3. Antes de crear relaciones `Receta_Insumo` nuevas sobre `Ensalada` por la combinación que confirme el usuario, **consulta primero cuáles ya existen** — preferí `mcp__menu_cache__receta_insumo_por_platos` con el id de Notion del plato `Ensalada` (mismo patrón que en el paso 3b); si la herramienta no está disponible, cae a la consulta normal (`Plato LIKE '%url_de_ensalada%'` en una sola consulta) — y compara insumo por insumo — en la práctica, casi todos los insumos mencionados ya tienen su relación `Transversal` de semanas anteriores, solo crea las que genuinamente falten. **Etiqueta la relación (nueva o ya existente) con el `Combos` correspondiente del vocabulario** al confirmarla (vocabulario abierto y creciente — fetchea las opciones vigentes de ese Select en Notion, no asumas una lista fija de memoria). Si la combinación no calza en ninguno de los combos con nombre ya existentes, no le asignes `Ensalada genérica` por tu cuenta — pregúntale al usuario si es un caso genérico o si merece un combo nuevo con nombre propio (el vocabulario es abierto, pero agregar uno es su decisión, no algo que decidas sola).

### 5. Agregar insumos según su confianza

**Usa `mcp__menu_cache__receta_insumo_por_platos`** con una sola llamada que incluya los ids de Notion de TODOS los platos que vas a necesitar esta semana: los platos principales (paso 3b), la ensalada/sopa de cada día (paso 4b), y cualquier acompañamiento catalogado que participe (paso 4, si aplica). A diferencia de la consulta en vivo, esta herramienta ya devuelve `insumo_nombre`, `unidad_estandar` **y `origen_compra`** resueltos por JOIN junto con cada fila de `Receta_Insumo` — no hace falta un segundo paso contra `Insumos`. Arma la lista de ids antes de llamarla (no vayas llamándola de a un plato a la vez) para no repetir el mismo patrón que producía, en la versión anterior contra Notion en vivo, varias consultas sueltas donde una sola por lote habría bastado.

**Guarda el `origen_compra` de cada insumo tal como viene en esta llamada — es el dato que separa "Lista de compras — proveedor" de "Compra directa" en el paso 6.** No lo estimes ni lo deduzcas por intuición de qué "parece" carne o verdura: si en algún momento tienes que armar esa separación y no tienes `origen_compra` a mano para algún insumo (ej. porque llegó por otra vía, como una receta no catalogada del paso 4), es señal de que falta consultarlo — antes de cerrar la propuesta, verifica esa columna contra Notion para los insumos que la tengan en blanco, en vez de adivinar y avisar después que fue estimado.

Si la herramienta no está disponible o el caché está vacío/desactualizado (revisa `mcp__menu_cache__estado_cache` si tienes dudas), cae al flujo anterior contra Notion en vivo: arma una sola lista consolidada con las URLs de TODOS los insumos que vas a necesitar esta semana, juntando lo que ya trajiste de `Receta_Insumo` en los pasos anteriores. Revisa el resultado completo de cada una de esas consultas a `Receta_Insumo` — si alguna trajo `has_more: true` o varias páginas, asegúrate de haber extraído la URL de insumo de cada fila antes de armar la lista, no solo de las primeras — y solo entonces haz **una única consulta por lote** a `Insumos` (`WHERE url LIKE '%url1%' OR url LIKE '%url2%' OR ...`) para traer `Nombre` y `Unidad estandar` de todos a la vez. Evita el patrón de ir consultando `Insumos` a medida que aparece cada grupo de platos/acompañamientos — eso es lo que produjo, en una corrida real, tres consultas separadas (un lote de 32, un insumo suelto que se quedó fuera del lote por no revisar bien el resultado completo de `Receta_Insumo`, y luego 4 insumos más por nombre en vez de URL) donde una sola consulta con la lista completa habría bastado. Si de todas formas se te escapa alguno y lo detectas recién en el paso 5b (reconciliación final), ahí sí haz una consulta puntual para ese insumo — no vale la pena posponer todo el paso 5 por eso — pero repite el chequeo de que la lista esté completa la próxima vez, para no repetir el mismo patrón.

(sin cambios respecto a la versión anterior: citar literalmente `Cantidad de referencia`, no inventar ni promediar cantidades, marcar como pendiente lo que no tenga cantidad confirmada.)

**Si el usuario corrige una cantidad ya citada en `Receta_Insumo`** (ej. dice que la cebolla semanal habitual es 1 kilo cuando el catálogo tiene citado "0.5 kg semanal"), esta skill **no edita `Receta_Insumo` directamente** — esa corrección de datos históricos es trabajo de `analisis-plato-insumo`. En su lugar: usa la cantidad que el usuario acaba de indicar para la lista de compras de esta semana (la validación humana en el momento gana), y deja una nota en la sección "Bitácora técnica" de la sub-página "Notas de la conversación" (paso 7) señalando la discrepancia para que se revise después — no en `Semanas.Texto original`.

### 5b. Reconciliación final de insumos — antes de mostrar el paso 6 y de nuevo antes del 6b

Este paso es obligatorio y es la causa más común de correcciones tardías del usuario. Antes de presentar la lista de compras (paso 6) y otra vez antes de cerrar el reporte minimalista (paso 6b), revisa insumo por insumo:

1. **Un insumo, una sola línea.** Si el mismo insumo se usa en más de un plato o acompañamiento del mismo día o de la semana (ej. palta en la ensalada y en el desayuno), únelo en una sola línea con la cantidad total y menciona todos los usos — nunca lo repitas en dos líneas con cantidades separadas.
2. **Vuelve a escanear TODA la conversación en busca de declaraciones de stock**, no solo la respuesta inicial del paso 2b. El usuario puede haber dicho "ya tenemos X" en cualquier momento posterior (al pedir un cambio, al corregir una cantidad, al aclarar un acompañamiento). Cualquier insumo así declarado se resta o se elimina por completo del "Pedido al proveedor" o "Compra directa", aunque el patrón habitual o la receta sugieran pedirlo.
3. **Respeta las restricciones de dónde se compra que el usuario indique** (ej. "eso lo compramos en el mercado, no al proveedor") aunque `Insumos.Origen de compra` diga otra cosa para ese insumo en general — es una decisión puntual de esta semana, no corrige el catálogo.
4. Si detectas un insumo que quedó sin ningún plato asociado esta semana (ej. algo que estaba en el stock declarado pero no se terminó usando en ningún plato ni acompañamiento), pregúntalo en vez de omitirlo o de forzarlo en algún lado — puede que el usuario quiera guardarlo para otra semana.

### 6. Presentar la propuesta detallada (primer gate)

Modo Detallado (ver "Formato de salida") — tablas markdown completas, con todas las columnas de trabajo (`Estado`, `Confianza`, "se usa en", frecuencia observada). Estructura:

```
## Menú de la semana

| Día | Plato | Acompañamiento | Proteína | Estado | Veces validado |
|---|---|---|---|---|---|
| Lunes | ... | ... | ... | ... | ... |
| ... | | | | | |

**Bebida(s):** ...
**Desayuno:** ...
**Frutas de consumo familiar:** ...

## Lista de compras — proveedor de frutas y verduras

| Insumo | Cantidad | Confianza | Se usa en |
|---|---|---|---|
| ... | ... | ... | ... |

## Compra directa (carnicería/súper)

| Insumo | Cantidad | Confianza | Se usa en |
|---|---|---|---|
| ... | ... | ... | ... |

## Consumo familiar

| Insumo | Cantidad |
|---|---|
| ... | ... |
```

**Las dos listas de compras (proveedor / directa) nunca van combinadas en una sola tabla, ni siquiera para "simplificar" la vista.** Sepáralas exclusivamente por `Insumos.Origen de compra` (`Proveedor frutas-verduras` vs. `Carnicería-supermercado`) — nunca por intuición de qué "parece" carne o verdura, porque el catálogo puede tener excepciones. Un insumo puede aparecer en como mucho una de las dos, nunca en ambas. La sección "Consumo familiar" es una tercera lista aparte, ni proveedor ni directa (ver paso 4d) — no la mezcles con ninguna de las dos aunque a veces se pida al mismo proveedor de frutas-verduras.

**Para pedir el visto bueno de este gate, usa `AskUserQuestion`** (ej. "¿Todo bien así?" con opciones "Sí, pasa al reporte final" / "Quiero cambiar algo") — nunca lo preguntes como texto plano al final del mensaje, aunque sea una decisión simple. El campo de texto libre de la tarjeta ya cubre cualquier ajuste puntual que quieran pedir.

### 6b. Generar el reporte final minimalista (segundo gate — el que realmente habilita Notion)

Texto plano, sin tablas ni encabezados markdown (`#`, `##`), sin las columnas de trabajo interno (`Estado`, `Confianza`, etc. — esas son solo para el paso 6). Mismo contenido que el paso 6, reformateado — no vuelve a limpiar la lista, la reconciliación del paso 5b ya debería estar aplicada antes de llegar aquí. Este formato es fijo y no cambia según el canal (Detallado/WhatsApp/Telegram — ver "Formato de salida"). Estructura:

```
Menú de la semana — [fecha]

Lunes: [Plato] [+ acompañamiento, si aplica]
...
Viernes: [Plato] [+ acompañamiento, si aplica]

Bebida: ...
Desayuno: ...
Frutas de consumo familiar: ...

Lista de compras — proveedor:
- Insumo (cantidad)
- ...

Compra directa (carnicería/súper):
- Insumo (cantidad)
- ...

Consumo familiar:
- Insumo — cantidad
- ...
```

**Mantén las tres secciones de compra separadas aquí también, sin excepción** — a diferencia de los modos WhatsApp/Telegram (donde "Compra directa" se omite por defecto salvo pedido explícito, ver `formatos-salida.md`), este reporte es el que la familia usa para pedir y comprar de verdad, así que **"Compra directa (carnicería/súper)" siempre va incluida aquí**, nunca se omite por brevedad.

**La confirmación final (la que dispara la escritura en Notion, paso 7) también va con `AskUserQuestion`** — ej. "¿Confirmas este reporte final?" con opciones "Sí, guarda en Notion" / "Todavía quiero ajustar algo". Igual que en el gate anterior: nunca la pidas como pregunta de texto plano, ni siquiera por ser la última del proceso — es la decisión más importante de toda la corrida (dispara escrituras reales), no la que menos merece una tarjeta clara.

### 7. Guardar en Notion tras la validación del reporte final (paso 6b)

- **Al crear la entrada de la semana, la llamada a `notion-create-pages` DEBE incluir `parent: {"data_source_id": "<id de la fuente de datos de Semanas>"}` — nunca la omitas.** Esto ya pasó una vez en una corrida real: la llamada de creación se hizo sin `parent` (probablemente porque más arriba solo se había hecho `notion-fetch` sobre la base para confirmar el esquema, y esa consulta se confundió con "ya tengo lo que hace falta para crear la página"), y Notion creó una página suelta fuera de cualquier base ("Nueva página", sin ancestro, sin ninguna de las propiedades enviadas — ni `Semana`, ni `Fecha`, ni `Platos de la semana`, ni `Texto original`, ni `Origen`, todas se perdieron en silencio, la llamada igual devolvió éxito con un `id`/`url` válidos). La semana completa desapareció sin ningún error visible — el usuario recién lo notó al no encontrarla en la tabla. Antes de esta llamada, asegúrate de tener resuelto el `data_source_id` de `Semanas` (del `notion-fetch` sobre la página agrupadora del "Punto de entrada a las 5 bases" en `notion-schema.md`, ya hecho normalmente en el paso 1/2 o al verificar el esquema un poco más arriba en este mismo paso 7) y pásalo explícitamente — el mismo patrón que ya usas correctamente más abajo en este paso 7 para crear filas de `Semana_Insumo_Familiar` (`parent: {"data_source_id": "<id de Semana_Insumo_Familiar>"}`). Un `id`/`url` de vuelta con "éxito" no es prueba de que la página quedó dentro de la base — si tienes cualquier duda, verifica con `notion-fetch` sobre el `url` devuelto que la página tenga las propiedades esperadas antes de seguir con los pasos siguientes (sub-página de notas, actualización de contadores, etc.), que si acumulan sobre una página huérfana quedan igual de perdidos.
- Crea la entrada de la semana en `Semanas` con los platos finalmente confirmados (principales + bebida(s) + ensalada/sopa por día si aplica) en `Platos de la semana`, `Estado de procesamiento = Validado`, y **`Origen = Propuesta automática`** (campo Select agregado a `Semanas` el 2026-08-31 específicamente para esto — distingue las semanas armadas por esta skill de las que `analisis-plato-insumo` procesa desde la nota manual real de la esposa; usa `Nota manual` solo si alguna vez esta skill terminara registrando algo a partir de un texto ya escrito por la familia, lo cual hoy no hace). **No incluyas en `Platos de la semana`** ninguna receta que el usuario haya pedido explícitamente no catalogar (ver paso 4) — esas quedan fuera aunque se hayan usado esa semana.
- Sigue verificando el esquema real antes de asumir que otros campos existen — el hecho de que `Origen` ya exista no garantiza que otros campos mencionados en guías anteriores también existan.
- **`Semanas.Texto original` lleva SOLO el reporte final minimalista (paso 6b), tal cual, sin agregarle nada más — ni siquiera una línea corta de "discrepancia detectada" o "variante sin catalogar".** Antes se le pedía además acumular ahí insumos de stock usados, recetas sin catalogar y discrepancias de cantidad — eso hacía crecer mucho el JSON de ESTA llamada puntual (la que crea la página de `Semanas`) en semanas con harta bitácora extra, y se confirmó en un caso real que provocó que la llamada a `notion-create-pages` se generara incompleta y fallara con "input que no se pudo parsear como JSON" (el texto se cortaba a mitad de una oración, antes de cerrar el JSON). Para evitar repetir ese error, esa bitácora técnica ahora va en la sub-página del punto siguiente, NUNCA en esta misma llamada — **cualquier mención a "discrepancia", "revisar", "sin catalogar" o similar es una señal de que se está colando bitácora técnica donde no va; muévela a la sub-página en vez de acortarla y dejarla igual acá.**
- **Después de crear la entrada de `Semanas` (en una llamada aparte, ya con la página de la Semana existente), crea una sub-página (child page) colgando de esa misma página**, con el título "Notas de la conversación". Esta sub-página cumple dos roles, en dos secciones separadas:
  1. **Resumen curado para consulta humana** (prosa breve o viñetas, no el transcript completo ni el detalle de llamadas a herramientas): qué acompañamientos se eligieron distintos al default y por qué (si el usuario dio una razón), cualquier pedido o ajuste puntual del usuario en el camino (ej. "esta semana quería algo más ligero"), platos que se reemplazaron respecto a lo sugerido inicialmente, y cualquier otra decisión conversacional que no quede reflejada en las columnas estructuradas ni en el reporte de `Texto original`.
  2. **Bitácora técnica** (sección aparte, con su propio encabezado tipo "Bitácora técnica" dentro de la misma sub-página): (a) qué insumos de stock se usaron y no se pidieron, (b) cualquier receta nueva sin catalogar que se usó esa semana con sus insumos, y (c) cualquier discrepancia de cantidad que el usuario haya corregido respecto a lo citado en `Receta_Insumo`. **Nota: `analisis-plato-insumo` no lee esto ni ninguna otra parte de las entradas `Origen = Propuesta automática`** — esa skill parte siempre de un archivo `.txt` manual aparte que la esposa escribe, y sus propias entradas de `Semanas` van con `Origen = Nota manual` (ver su propio SKILL.md). Esta sección es solo para que el usuario la tenga a mano al revisar la semana — si algo de acá (ej. una receta sin catalogar) realmente necesita llegar al catálogo, el usuario tendría que trasladarlo él mismo a una corrida de `analisis-plato-insumo`, no pasa automáticamente.

  **Esta sub-página es solo para consulta manual desde Notion o desde el link "Ver en Notion" de la tarjeta de esa semana en la app — nunca la leas ni la cites como parte del precargado de los pasos 1/2 de una corrida futura.** El objetivo es que quede disponible como historial opcional, sin sumarse al contexto (ni al costo en tokens) de las propuestas siguientes. Al ser una llamada de creación de página separada de la de `Semanas`, si esta bitácora técnica también llegara a crecer mucho (semana con muchas discrepancias/insumos sin catalogar), divídela en varios bloques de párrafo en vez de un solo bloque de texto larguísimo — más robusto y más legible en Notion de todas formas.
- Antes de crear relaciones `Receta_Insumo` nuevas para la ensalada del día (paso 4b), confirma que no existan ya (ver paso 4b) — no dupliques.
- **Actualiza `Platos.Combo de ensalada sugerido`** del plato principal de cada día, según lo confirmado en el paso 4b: si estaba en blanco, escríbelo directo como su primer default. Si ya estaba en `Variable / sin patrón fijo`, no hace falta tocarlo — un combo distinto cada vez es justo lo que ese valor anticipa. **Si ya tenía un valor específico (o `No lleva ensalada`) y esta semana se sirvió algo distinto, no lo sobreescribas sin preguntar** — confírmalo con el usuario en el momento (¿cambió el patrón, o fue solo esta vez?) y aplica lo que decida.
- **Si el usuario menciona explícitamente que dos platos principales (ya seleccionados o no) son buena combinación para la misma semana** (ej. "esto y X los solemos hacer la misma semana"), crea la relación `Acompañamientos sugeridos` entre ambos con `Tipo de sugerencia de acompañamiento: Misma semana` **en los dos platos** (par simétrico, ver nota de migración 2026-09-05 de `notion-schema.md`). Nunca la crees por tu cuenta solo porque compartan insumos — dos platos con mucho aderezo en común pueden ser justo lo contrario, redundantes, y esa distinción solo la sabe el usuario.
- Por cada plato `Nuevo` o `En refinamiento` que el usuario confirmó tal cual (sin corregir sus insumos), suma +1 a `Veces validado` en `Platos` y +1 a `Veces confirmado` en las filas de `Receta_Insumo` correspondientes. Los platos ya `Cerrado` no se incrementan solo por haberse servido de nuevo.
- Si el usuario confirmó una cantidad de proteína o de un insumo de desayuno que estaba pendiente, actualiza esa fila de `Receta_Insumo` a `Estado = Validado` con la cantidad indicada.
- Si el usuario agregó un insumo de desayuno nuevo que no estaba en el catálogo, créalo en `Insumos` y vincúlalo al plato colector "Insumos para desayunos" en `Receta_Insumo` como `Transversal`, `Origen = Validación humana`.
- Para los insumos de consumo familiar confirmados en el paso 4d, créalos TODOS en una sola llamada de creación de páginas en `Semana_Insumo_Familiar` — pero **solo los que efectivamente se van a pedir esta semana**. Si el usuario dice que ya tiene un insumo del patrón habitual en casa y no lo va a pedir (ej. "ya tenemos lúcuma y papaya"), no crees una fila para él — esta tabla registra lo que se pidió, no todo lo que se va a consumir.
- **MODO_PRUEBA (canal web de Huevereke, ver CHANNEL_CONTEXT del backend):** si el contexto de esta conversación viene marcado con MODO_PRUEBA, este paso 7 completo (Semana, sub-página de notas, y cualquier otra escritura a Notion) queda bloqueado a nivel técnico — no un pedido que dependa de que lo recuerdes. No lo intentes: arma la propuesta completa igual, pero al llegar a este punto dile al usuario en una frase que es una prueba y que no se guardó nada, sin sonar como un error. Justo ahí, preguntale con AskUserQuestion si de todas formas quiere que guardes un resumen de esta prueba en una página aparte de Notion (nunca colgando de una Semana real) para poder revisarlo — si acepta, llama a `mcp__conversation__guardar_notas_de_prueba` con ese resumen y pásale la URL que te devuelve tal cual, para que sepa dónde borrarla después.
- **Al terminar de verdad esta conversación** — ya sea que este paso 7 se completó con éxito (Semana + sub-página de notas creadas), o que estás en MODO_PRUEBA y el usuario da por cerrada la corrida — llama una sola vez, como tu última acción, a `mcp__conversation__cerrar_conversacion` con `guardado=true` (solo si de verdad escribiste en Notion en esta corrida) o `guardado=false` (MODO_PRUEBA, o el usuario nunca llegó a confirmar el guardado), y un `motivo` de una frase corta. Esto le avisa al navegador que puede liberar el contexto de esta conversación — no lo llames a mitad de una propuesta todavía en curso.

## Notas importantes

- **No inventes platos nuevos** que no existan en `Platos`. Esta skill solo selecciona entre lo ya catalogado; si el usuario quiere sumar un plato nuevo al repertorio, eso corresponde a `analisis-plato-insumo`, no a esta skill — salvo el caso ya cubierto en el paso 4 donde el usuario pide explícitamente usar la receta solo para esa semana sin registrarla.
- **No inventes cantidades.** Toda cantidad que muestres — en el paso 6 o en el 6b — debe venir citada de Notion o marcada explícitamente como pendiente, salvo que el usuario mismo te dé una cantidad nueva en la conversación (esa sí se usa tal cual para esa semana, ver paso 5).
- Si el catálogo tiene menos de ~10 platos principales utilizables, avísale al usuario que la variedad de la propuesta será limitada antes de generarla.
- Si el usuario no llega a confirmar la propuesta completa, no guardes nada parcial en Notion.
- Si el usuario pide explícitamente que no quiere que guardes nada todavía, respeta eso en toda la sesión.
- **`Platos.Proteína principal` no es una fuente de verdad confiable por sí sola** — ver el paso 3b.
- **`Semanas.Platos de la semana` nunca se corrige retroactivamente**, ni siquiera cuando el paso 2c detecta que la familia se desvió de lo propuesto — esa corrección va aparte, en `Platos de la semana (real)` (ver paso 2c y Parámetros del sistema). La primera se queda como el registro histórico de qué se propuso; la segunda, cuando existe, es lo que realmente se preparó.
- **Un plato puede ser una "base combinable"** en vez de una receta completa — tratarlo como dos elecciones, no como un solo plato cerrado.
- **No confíes de memoria en qué campos existen en cada base de Notion.** Verifica contra el esquema fetcheado antes de escribir una propiedad que "debería" existir según esta guía. Si genuinamente falta un campo que aportaría señal real (como pasó con `Origen`), se puede agregar en vivo con `notion-update-data-source` previa confirmación del usuario — no hay que limitarse a documentar la ausencia y trabajar alrededor de ella para siempre.
- Los modos WhatsApp y Telegram (paso 6) no son versiones "menos completas" de la propuesta — solo cambia cuánto texto envolvente rodea los mismos datos.