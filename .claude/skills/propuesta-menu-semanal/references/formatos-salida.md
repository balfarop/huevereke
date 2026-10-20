# Formatos de salida — reglas completas

Esta skill puede correr en contextos distintos: una conversación de chat (Cowork, Claude.ai) donde el usuario puede leer tablas markdown y usar botones de pregunta, o un agente headless que habla por API a través de un canal de mensajería (WhatsApp, Telegram) donde no hay tablas ni botones — solo texto plano que se manda como mensaje, y donde cada carácter de más es texto que atraviesa el pipeline del agente (API + backend + Notion MCP) sin aportar nada al usuario.

Ver la tabla de decisión resumida en `SKILL.md` (sección "Formato de salida") para elegir el modo — este archivo tiene el detalle completo de cada uno.

## Detallado (por defecto)

Tablas markdown completas, con todas las columnas (Estado, Confianza, "se usa en", frecuencia observada, etc.). Úsalo salvo que algo indique lo contrario — es el formato de todos los ejemplos de los pasos 3 a 7 de `SKILL.md`.

## WhatsApp

Texto plano, sin tablas ni markdown de encabezado (`#`, `##`) ni negrita doble (`**`) — WhatsApp solo entiende `*negrita*` con un asterisco, `_cursiva_` y `~tachado~`. Sin columnas informativas de trabajo interno (Estado, Confianza, "se usa en", frecuencia observada). Usa este modo cuando: (a) el usuario lo pide explícitamente ("mándamelo para WhatsApp", "en texto plano", "algo que pueda copiar y pegar"), o (b) la sesión está corriendo como el agente de WhatsApp — en ese caso usa este formato por defecto en vez del detallado, sin que el usuario tenga que pedirlo cada vez.

**Esta prohibición no es solo sobre tablas.** Aplica también a la prosa: cuando tengas que explicar una relajación de reglas (ej. paso 3 punto 5) o cualquier otra decisión, hazlo en lenguaje corriente y nunca citando el nombre literal de un campo de Notion (`Estado`, `Veces validado`, etc.) — esos nombres son vocabulario interno de trabajo, no información que la familia necesite. La misma regla aplica en modo Detallado y Telegram: no es exclusiva de WhatsApp, solo se menciona aquí porque es donde más tienta usarlos como atajo.

Además, este modo comparte con Telegram las reglas de "no redundancia de proteína", "sin comentario de estado/confianza" y "sin narrar problemas técnicos internos" descritas en la sección de Telegram más abajo — aplican igual aquí, con la única diferencia de que WhatsApp sí acepta `*negrita*` simple.

## Telegram (compacto)

Mismo espíritu que el modo WhatsApp — texto plano para un canal sin tablas — pero deliberadamente más comprimido, porque en una arquitectura de agente vía API (Cloudflare + Notion MCP, por ejemplo) el texto de cada mensaje generado tiene un costo real de tokens en el pipeline, no solo ocupa espacio en pantalla. Reglas adicionales sobre el modo WhatsApp:

- Etiquetas de sección abreviadas en vez de frases completas: `D1:`…`D5:` en vez de "Día 1:", `Bebida:` y `Desayuno:` sin el título general, `Compra:` y `Familiar:` en vez de "Lista de compras — proveedor" y "Consumo familiar".
- Sin línea de título ("Propuesta de menú — semana del...") salvo que el usuario la pida — el propio chat de Telegram ya da contexto de que es la propuesta de esa semana.
- Acompañamiento del día va en la misma línea que el plato, unido con `+`, sin frase adicional explicando de dónde sale.
- **Nunca repitas la proteína aparte del nombre del plato si el nombre ya la deja clara** ("Lentejas con pescado", "Trigo con pollo a la olla", "Locro de zapallo con carne" — los tres ya dicen la proteína, no agregues "(pescado)", "(pollo)", "(carne de res)" detrás). Es redundante y en este modo cada palabra de más cuesta tokens. Solo agrégala, sin paréntesis, si el nombre del plato genuinamente no da ninguna pista (ej. "Ensalada", un nombre propio sin ingrediente reconocible) — y en ese caso intégrala a la frase en vez de ponerla entre paréntesis.
- **Nunca menciones el estado de validación, confianza, ni ningún otro dato de trabajo interno** (`Estado`, `Veces validado`, "cerrado", "en refinamiento", "bien validado", "baja confianza", etc.) en ningún mensaje al usuario en este modo — ni en la propuesta final ni en mensajes intermedios de progreso (ej. al confirmar qué platos quedaron seleccionados antes de pasar a las preguntas de bebida/ensalada). Esta información es para tu propio razonamiento interno (ver paso 3, regla del máximo de platos poco validados) — nunca para el texto que le mandas al usuario en Telegram/WhatsApp, ni siquiera en lenguaje corriente sin nombrar el campo. Si el usuario pregunta explícitamente por qué elegiste un plato, ahí sí puedes explicarlo en una frase corta.
- **No narres problemas técnicos internos** (límites de Notion, reintentos, errores de herramientas, "Notion me cortó por límite de consultas", etc.) — resuélvelos en silencio según las reglas de la sección "Límites reales de Notion" de `SKILL.md` (reintenta una vez, sigue con lo que tengas). Menciónaselo al usuario únicamente si el problema de verdad cambia algo que necesitas de él (ej. no pudiste confirmar un insumo y necesitas que te diga qué usaron) — y en ese caso, formúlalo como una pregunta normal de contenido ("¿me confirmas qué usaron para X?"), nunca como un reporte de incidente ni mencionando Notion, límites de consultas, o nombres de herramientas.
- Mismo contenido real que los demás modos — ningún plato, insumo, proteína o cantidad se omite. Lo único que se reduce es el texto que envuelve a esos datos, nunca los datos mismos.
- Usa este modo cuando: (a) el usuario lo pide explícitamente ("mándamelo para Telegram", "modo compacto", "resume esto para el bot"), o (b) el contexto de la sesión indica explícitamente que es el agente de Telegram — ver la nota siguiente sobre cómo debe llegar esa señal.
- **Estas reglas aplican a TODOS los mensajes de la conversación, no solo a la propuesta final (pasos 6/6b).** Cualquier mensaje intermedio (confirmar platos seleccionados, preguntar por bebida/ensalada/stock, avisar que falta algo) sigue el mismo estilo comprimido y las mismas prohibiciones de arriba — no solo el reporte final.
- **Los mensajes intermedios de progreso van sin introducción ni cierre — solo el dato.** Nada de "Ya tengo los N platos principales (sin repetir lo de las últimas 3 semanas, balanceando proteína):" antes de la lista, ni una frase de transición después. Directo a `D1: [Plato]` … `D5: [Plato]`, y de ahí a la siguiente pregunta (bebida/ensalada/stock) en el mismo mensaje si aplica. Ejemplo correcto de confirmación tras el paso 3 (nótese: sin explicar el criterio de selección, sin protéina redundante, sin estado):
  ```
  D1: Pollo al horno
  D2: Lentejas con pescado
  D3: Arroz tapado
  D4: Trigo con pollo a la olla
  D5: Locro de zapallo con carne

  [siguiente pregunta del flujo, ej. bebida]
  ```

## Cómo se elige el modo en la práctica (importante si esta skill corre en más de un entorno)

Claude no detecta por sí solo en qué aplicación o canal está corriendo — no hay ninguna señal automática de "esto es Claude Desktop" o "esto es mi agente de Telegram" solo por tener la skill cargada. La condición (b) de cada modo ("la sesión está corriendo como el agente de WhatsApp/Telegram") solo se cumple si algo en el contexto de esa conversación lo dice explícitamente. En la práctica, esto se traduce en dos comportamientos distintos según dónde se use la skill:

- **En una conversación de chat normal (Cowork, Claude.ai, Claude Desktop):** nadie menciona un canal de mensajería, así que ninguna condición (b) se cumple y el modo por defecto (**Detallado**) aplica sin que tengas que hacer nada.
- **En un agente headless propio (por ejemplo, el agente de Telegram vía API + Cloudflare + Notion MCP):** el desarrollador del agente debe incluir una línea explícita indicando el canal — en el *system prompt* que se manda junto con la skill, o como primer mensaje fijo de cada sesión. Sin esa línea, Claude no tiene manera de saber que debe usar modo Telegram y por defecto va a responder en modo Detallado dentro de tu pipeline, que es justo el resultado que este modo busca evitar.

Línea recomendada para el system prompt del agente de Telegram (cópiala tal cual o adapta a tu integración):

```
Este agente responde a usuarios a través de Telegram. Al usar la skill
propuesta-menu-semanal, usa siempre el modo "Telegram (compacto)" definido
en su sección "Formato de salida" — nunca el modo Detallado ni el de WhatsApp.
```

Si en algún momento tienes el mismo agente atendiendo más de un canal (ej. Telegram y WhatsApp desde el mismo backend), la señal debe indicar cuál de los dos es, no solo "modo compacto" genérico — los dos modos tienen reglas de formato distintas (WhatsApp acepta `*negrita*` libremente; Telegram no, salvo confirmación explícita del `parse_mode`).

## Plantillas de referencia

Plantilla de referencia para el modo WhatsApp (ver paso 6 de `SKILL.md` para el contenido completo que va en cada línea):

```
*Propuesta de menú — semana*

*Día 1:* [Plato] [+ acompañamiento, o nada si no hay acompañamiento] — agrega la proteína solo si el nombre del plato no la deja clara (ver regla de no-redundancia)
*Día 2:* ...

*Bebida:* [respuesta 4a, o "sin definir"]
*Desayuno:* [respuesta 4c, o "nada especial esta semana"]

*Lista de compras — proveedor*
- Insumo (cantidad simple si hay una clara; si es compartida entre varios platos sin un número único, escribe "cantidad habitual" en vez de listar cada nota)
- ...

*Consumo familiar*
- Insumo — cantidad
- ...
```

Plantilla de referencia para el modo Telegram (texto plano; agrega `*` alrededor de las etiquetas solo si el usuario confirmó `parse_mode=Markdown` legacy u HTML):

```
Menú semana [fecha]
D1: [Plato] [+ acompañamiento, o nada si no hay acompañamiento] — agrega la proteína solo si el nombre del plato no la deja clara (ver regla de no-redundancia)
D2: ...

Bebida: [respuesta 4a, o "sin definir"]
Desayuno: [respuesta 4c, o "nada especial esta semana"]

Compra:
- Insumo: cantidad
- ...

Familiar:
- Insumo: cantidad
- ...
```

La sección "Compra directa (carnicería/súper)" se omite por defecto en los modos WhatsApp y Telegram salvo que el usuario la pida explícitamente — es información más útil para revisar en el catálogo que para una lista de compras rápida. Si el usuario la pide, agrégala con el mismo estilo de guiones simples, sin tabla (y en modo Telegram, con la misma etiqueta abreviada: `Directa:`).

## Preguntas del paso 3b y 4 cuando no hay herramienta de preguntas interactivas

Como en un agente de WhatsApp o Telegram vía API, sin botones de opción múltiple: no te saltes las preguntas — conviértelas en un solo mensaje de texto claro con las opciones numeradas o con guiones, y espera la respuesta del usuario en el siguiente mensaje antes de continuar. En modo Telegram, aplica la misma compresión de texto a estas preguntas (opciones cortas, sin repetir contexto ya obvio por el hilo de la conversación) — pero nunca te saltes una pregunta solo por ahorrar texto. No asumas una respuesta por defecto solo porque no hay UI para preguntar; la ausencia de un mensaje explícito del usuario respondiendo la pregunta cuenta como "sin definir", igual que en el modo detallado.
