const chatEl = document.getElementById("chat");
const chatInnerEl = document.getElementById("chat-inner");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const themeToggleEl = document.getElementById("theme-toggle");
const tokensCounterEl = document.getElementById("tokens-counter");
const costCounterEl = document.getElementById("cost-counter");
const testModeToggleEl = document.getElementById("test-mode-toggle");
const newConversationBtnEl = document.getElementById("new-conversation-btn");
const welcomeScreenEl = document.getElementById("welcome-screen");
const welcomeInputSlotEl = document.getElementById("welcome-input-slot");
const chatAreaEl = document.querySelector(".chat-area");
const testModeRowEl = document.querySelector(".test-mode-row");

// El test-mode-row y el chat-form (formEl) son los mismos nodos del DOM en
// los dos lugares donde pueden vivir — nunca se clonan, solo se mueven con
// appendChild (eso preserva sus listeners tal cual, ya definidos más abajo
// contra las mismas variables formEl/inputEl/testModeToggleEl). Mientras no
// arrancó la conversación viven dentro de #welcome-input-slot, flotando en
// la tarjeta de bienvenida en vez de fijos al fondo de la pantalla — ver
// moveInputToWelcome()/moveInputToFooter() y su uso en sendMessage() y
// closeConversation().
function moveInputToWelcome() {
  if (welcomeInputSlotEl && testModeRowEl && formEl) {
    welcomeInputSlotEl.appendChild(testModeRowEl);
    welcomeInputSlotEl.appendChild(formEl);
  }
}

function moveInputToFooter() {
  if (chatAreaEl && testModeRowEl && formEl) {
    chatAreaEl.appendChild(testModeRowEl);
    chatAreaEl.appendChild(formEl);
  }
}

moveInputToWelcome();

// ---------- Contador de consumo (tokens/costo estimado de la sesión) ----------
// El backend manda un evento {"usage": {...}} con el acumulado de TODA esta
// sesión de chat (no solo el turno en curso), más una bandera `final`. Los
// TOKENS se actualizan en cada intercambio (cada pregunta/respuesta), no
// solo al terminar toda la propuesta — el backend los cuenta apenas llega
// cada AssistantMessage (final=false). El COSTO en USD, en cambio, solo es
// válido cuando termina TODO el turno (final=true, disparado por
// ResultMessage) — es un dato que el SDK recién calcula ahí, no antes, y es
// una estimación del lado del cliente (tabla de precios de lista), no el
// dato real de facturación de Anthropic. Por eso vive en su propio
// elemento ("costo aprox. propuesta"), separado del contador de tokens que
// sí se ve avanzar en vivo — así no da la impresión de estar roto o
// desincronizado.
//
// A propósito NO calculamos acá ningún "% de tu cuota": el consumo del
// Agent SDK sale de la MISMA ventana de 5 horas / límite semanal que ya
// ves en Configuración → Uso de Claude Desktop para el uso interactivo de
// Claude Code (confirmado probando esta misma app — ambas barras se mueven
// al usarla), y Anthropic no publica un equivalente en dólares de esos
// límites contra el cual calcular un %, ni un endpoint para leerlos desde
// acá. La forma real de saber cuánto le queda a tu cuenta sigue siendo
// mirar esas barras en Claude Desktop.
function formatTokenCount(n) {
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(n);
}

function renderUsage(usage) {
  if (!usage) return;
  if (tokensCounterEl) {
    const totalTokens =
      (usage.input_tokens || 0) +
      (usage.output_tokens || 0) +
      (usage.cache_creation_input_tokens || 0) +
      (usage.cache_read_input_tokens || 0);
    tokensCounterEl.textContent = `${formatTokenCount(totalTokens)} tokens`;
  }
  // El costo solo se refresca cuando el turno completo terminó de verdad
  // (ver comentario arriba) — mientras tanto se deja el último valor
  // mostrado, en vez de parpadear con un número parcial/engañoso.
  if (costCounterEl && usage.final) {
    const costText = usage.cost_usd != null ? `$${usage.cost_usd.toFixed(3)}` : "—";
    costCounterEl.textContent = `costo aprox. propuesta: ${costText}`;
  }
}

const THEME_KEY = "huevereke-theme";

function applyThemeIcon() {
  const isDark =
    document.documentElement.getAttribute("data-theme") === "dark" ||
    (!document.documentElement.getAttribute("data-theme") &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  themeToggleEl.textContent = isDark ? "☀️" : "🌙";
}

themeToggleEl.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  const isDarkNow =
    current === "dark" ||
    (!current && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const next = isDarkNow ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME_KEY, next);
  applyThemeIcon();
});

applyThemeIcon();

function autoResizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + "px";
}

inputEl.addEventListener("input", autoResizeInput);

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    formEl.requestSubmit();
  }
});

function addMessage(text, role) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  chatInnerEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function showThinking() {
  const div = document.createElement("div");
  div.className = "msg agent thinking";
  div.innerHTML = `
    <img src="/static/img/huevereke_pensando.png" alt="" class="thinking-avatar" />
    <span class="thinking-label">Pensando</span>
    <span class="thinking-dots"><span></span><span></span><span></span></span>
  `;
  chatInnerEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

// Burbuja "pensando"/respuesta activa del turno en curso — compartida
// entre sendMessage (POST /chat) y el flujo de responder una pregunta
// (POST /chat/answer, cuando le toca retomar el streaming): ambas pueden
// terminar escribiendo en la misma burbuja, según cuál conexión sea la
// que efectivamente reciba la continuación del turno.
let activeAgentDiv = null;
let activeTurnStarted = false;

// True desde que se manda el primer mensaje de la conversación actual
// hasta que se cierra (automático vía el aviso de cierre, o manual con
// "Nueva conversación") — mientras esté en true, el toggle de "Modo
// prueba" queda deshabilitado: el modo se fija una sola vez, al crear la
// sesión en el backend (ver ConversationSession.test_mode), y permitir
// cambiarlo a mitad de la conversación solo generaría confusión sobre a
// qué mitad de los mensajes aplica.
let conversationStarted = false;

function ensureAgentBubble() {
  if (!activeAgentDiv) {
    activeAgentDiv = showThinking();
    activeTurnStarted = false;
  }
  return activeAgentDiv;
}

// Cierra la conversación actual del lado del backend (descarta el
// ClaudeSDKClient con todo su historial acumulado — ver POST /chat/close)
// y deja la pantalla lista para una conversación 100% nueva. La llaman
// tanto el botón "Nueva conversación" (en cualquier momento) como el botón
// del aviso de cierre que aparece cuando el propio agente señala que una
// propuesta (real o de prueba) ya terminó (ver payload.close_prompt en
// consumeStream).
async function closeConversation() {
  if (newConversationBtnEl) newConversationBtnEl.disabled = true;
  try {
    await fetch("/chat/close", { method: "POST" });
  } catch (err) {
    // Si falla el POST, igual limpiamos la pantalla — en el peor caso el
    // backend cierra solo esa sesión al ratito por inactividad (ver
    // SESSION_IDLE_MINUTES), y para el usuario lo importante es poder
    // seguir escribiendo sin quedarse trabado por un error de red acá.
    console.error("No se pudo cerrar la conversación en el backend:", err);
  }

  chatInnerEl.innerHTML = "";
  tokensCounterEl.textContent = "— tokens";
  costCounterEl.textContent = "costo aprox. propuesta: —";
  activeAgentDiv = null;
  activeTurnStarted = false;
  conversationStarted = false;
  if (testModeToggleEl) {
    testModeToggleEl.disabled = false;
    testModeToggleEl.checked = false;
  }
  if (newConversationBtnEl) newConversationBtnEl.disabled = false;
  // La conversación que empieza ahora es nueva — la pantalla de bienvenida
  // vuelve a tener sentido hasta que el usuario mande su primer mensaje, así
  // que el input real vuelve a mudarse a la tarjeta de bienvenida.
  if (welcomeScreenEl) welcomeScreenEl.style.display = "";
  moveInputToWelcome();
}

if (newConversationBtnEl) {
  newConversationBtnEl.addEventListener("click", closeConversation);
}

// Consume un stream SSE (de /chat o de /chat/answer cuando le toca
// retomar el turno) y despacha cada evento a la burbuja activa o a una
// tarjeta de pregunta nueva. Los heartbeats ({"ping": true}) se ignoran —
// solo existen para mantener la conexión con actividad y que no se corte
// sola en el camino mientras no hay nada real que mostrar.
async function consumeStream(resp) {
  if (resp.status === 401) {
    window.location.href = "/login";
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Esta llamada puede corresponder a una respuesta de /chat/answer que el
  // backend dejó totalmente vacía (solo el marcador de cierre), porque la
  // conexión original de /chat seguía viva y le tocaba a ELLA seguir
  // escuchando la continuación (ver _drain_events/listener_active en
  // agent_service.py) — en ese caso esta llamada no tuvo ningún rol real
  // en la conversación y NO debe tocar el estado compartido de la burbuja
  // activa (activeAgentDiv/activeTurnStarted). Antes se reseteaba ese
  // estado sin condición al final de cada llamada, así que la llamada
  // "vacía" (o la anticipada por ensureAgentBubble() en el click de
  // Responder) pisaba la referencia de la burbuja que SÍ estaba usando la
  // conexión real, provocando que esta última creara una burbuja nueva —
  // dejando huérfana, y para siempre en "pensando", la burbuja que sí se
  // había alcanzado a crear pero nunca recibió contenido.
  let sawRealEvent = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Los eventos SSE vienen separados por doble salto de línea
    const parts = buffer.split("\n\n");
    buffer = parts.pop(); // lo incompleto se queda para el siguiente chunk

    for (const part of parts) {
      // El marcador de cierre del stream ("event: done\ndata: {}") no es
      // contenido real — antes esto se colaba como si fuera un payload
      // vacío, y como no calzaba con ninguna rama (ni pregunta, ni texto,
      // ni error) igual disparaba ensureAgentBubble(), creando una
      // burbuja de "pensando" que ya no tenía nada que la resolviera
      // nunca. Pasaba sobre todo con las respuestas "vacías" de
      // /chat/answer (cuando la conexión original seguía viva y le tocó
      // a ella seguir escuchando, ver backend) — el ícono se quedaba
      // ahí para siempre.
      const eventLine = part.split("\n").find((l) => l.startsWith("event: "));
      if (eventLine && eventLine.slice(7).trim() === "done") continue;

      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const payload = JSON.parse(line.slice(6));

      if (payload.ping) continue; // heartbeat, nada que mostrar

      if (payload.usage) {
        renderUsage(payload.usage);
        continue;
      }

      sawRealEvent = true;

      if (payload.question) {
        // Si había una burbuja "pensando" activa que nunca llegó a mostrar
        // texto real (por ejemplo, la que se crea al hacer click en
        // Responder, anticipando la continuación) y lo que sigue es
        // directamente otra pregunta, sin narración de por medio, esa
        // burbuja ya no tiene nada que la vaya a resolver — la quitamos en
        // vez de dejarla congelada en "pensando" para siempre.
        if (activeAgentDiv && !activeTurnStarted) {
          activeAgentDiv.remove();
        }
        activeAgentDiv = null;
        activeTurnStarted = false;

        // Cada pregunta es su propia burbuja independiente — el modelo
        // puede disparar más de una AskUserQuestion en el mismo turno
        // (ej. más de 4 preguntas en total, límite duro de la
        // herramienta). Si compartieran la burbuja activa, la segunda
        // pisaría el DOM de la primera y esa pregunta quedaría huérfana.
        const questionDiv = document.createElement("div");
        questionDiv.className = "msg agent";
        chatInnerEl.appendChild(questionDiv);
        chatEl.scrollTop = chatEl.scrollHeight;
        renderQuestionCard(questionDiv, payload.question, payload.question_id);
        continue;
      }

      if (payload.close_prompt) {
        // El propio agente señaló (mcp__conversation__cerrar_conversacion)
        // que esta propuesta ya terminó — misma limpieza de burbuja
        // "pensando" huérfana que en payload.question, por la misma razón.
        if (activeAgentDiv && !activeTurnStarted) {
          activeAgentDiv.remove();
        }
        activeAgentDiv = null;
        activeTurnStarted = false;

        const closeDiv = document.createElement("div");
        closeDiv.className = "msg agent close-prompt";
        const guardado = !!payload.close_prompt.guardado;
        const motivo = payload.close_prompt.motivo || "";
        const headline = guardado
          ? "✅ Propuesta guardada en Notion."
          : "🧪 Conversación de prueba terminada — no se guardó nada.";
        closeDiv.innerHTML = `
          <p class="close-prompt-headline">${escapeHtml(headline)}</p>
          ${motivo ? `<p class="close-prompt-motivo">${escapeHtml(motivo)}</p>` : ""}
          <button type="button" class="close-prompt-btn">Cerrar conversación</button>
        `;
        chatInnerEl.appendChild(closeDiv);
        chatEl.scrollTop = chatEl.scrollHeight;
        closeDiv.querySelector(".close-prompt-btn").addEventListener("click", async () => {
          const btn = closeDiv.querySelector(".close-prompt-btn");
          btn.disabled = true;
          btn.textContent = "Cerrando…";
          await closeConversation();
        });
        continue;
      }

      const agentDiv = ensureAgentBubble();
      if (!activeTurnStarted && (payload.text || payload.error)) {
        // Primer contenido real: quitamos el indicador de "pensando"
        agentDiv.classList.remove("thinking");
        agentDiv.innerHTML = "";
        activeTurnStarted = true;
      }

      if (payload.error) {
        agentDiv.classList.add("error");
        agentDiv.textContent += `Error: ${payload.error}`;
      } else if (payload.text) {
        agentDiv.textContent += payload.text;
        chatEl.scrollTop = chatEl.scrollHeight;
      }
    }
  }

  // Si esta llamada no vio ningún evento real, no era ella quien tenía el
  // hilo de la conversación (ver comentario arriba) — no tocamos el estado
  // compartido, para no pisarle la burbuja a la conexión que sí lo tiene.
  if (!sawRealEvent) return;

  // El stream terminó de verdad (el turno llegó a _TURN_DONE del lado del
  // backend) — la próxima burbuja de "pensando" debe ser una nueva, no
  // esta reciclada. Si quedó una burbuja "pensando" sin resolver (el turno
  // terminó sin que llegara más texto después de la última pregunta, por
  // ejemplo), la quitamos en vez de dejarla congelada.
  if (activeAgentDiv && !activeTurnStarted) {
    activeAgentDiv.remove();
  }
  activeAgentDiv = null;
  activeTurnStarted = false;
}

async function sendMessage(message) {
  // El primer mensaje de la conversación reemplaza la pantalla de
  // bienvenida por el chat real — a partir de acá ya no vuelve a
  // mostrarse hasta la próxima conversación (ver closeConversation()), y
  // el input real vuelve a su lugar fijo al fondo de la pantalla.
  if (welcomeScreenEl) welcomeScreenEl.style.display = "none";
  moveInputToFooter();

  addMessage(message, "user");
  ensureAgentBubble();

  // El modo prueba se fija con el PRIMER mensaje de la conversación (ver
  // ConversationSession.test_mode en el backend) — a partir de acá el
  // toggle queda deshabilitado hasta que la conversación se cierre
  // (closeConversation), para que nunca quede ambiguo a qué mensajes
  // aplicó.
  const testMode = !!(testModeToggleEl && testModeToggleEl.checked);
  if (!conversationStarted) {
    conversationStarted = true;
    if (testModeToggleEl) testModeToggleEl.disabled = true;
  }

  const submitBtn = formEl.querySelector("button");
  submitBtn.disabled = true;

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, test_mode: testMode }),
    });
    await consumeStream(resp);
  } catch (err) {
    const agentDiv = ensureAgentBubble();
    agentDiv.classList.remove("thinking");
    agentDiv.classList.add("error");
    agentDiv.innerHTML = "";
    agentDiv.textContent = `No se pudo conectar: ${err.message}`;
    activeAgentDiv = null;
    activeTurnStarted = false;
  } finally {
    submitBtn.disabled = false;
  }
}

// Renderiza 1-4 preguntas de AskUserQuestion como tarjeta interactiva
// dentro de `container`. Cada pregunta tiene, además de sus botones de
// opción (2-4, definidos por el agente), un campo de texto libre SIEMPRE
// visible — no cuenta contra el límite de opciones de la herramienta, es
// puramente de nuestra UI. Elegir una opción borra el texto libre y
// viceversa (son excluyentes). El botón "Responder" queda deshabilitado
// hasta que TODAS las preguntas tengan una respuesta (opción u otro texto),
// para que nunca se pueda enviar una respuesta a medias que descoloque el
// flujo del agente.
// Al confirmar, manda la respuesta a /chat/answer junto con questionId (para
// que el backend sepa cuál de las posibles preguntas pendientes está
// resolviendo — puede haber más de una a la vez en el mismo turno). Si esa
// respuesta era la última pendiente, /chat/answer retoma el streaming de
// la continuación (ver continue_reply en el backend) — por eso también se
// consume acá igual que en sendMessage.
function renderQuestionCard(container, questionPayload, questionId) {
  const questions = questionPayload.questions || [];
  const state = questions.map(() => ({ selected: new Set(), otherText: "" }));

  container.classList.add("question-card");

  function isAnswered(qi) {
    return state[qi].selected.size > 0 || state[qi].otherText.trim().length > 0;
  }

  // Recalcula solo si el botón "Responder" debe habilitarse — sin tocar
  // el resto del DOM, para no perder el foco mientras se escribe.
  function updateSubmitState() {
    const submitEl = container.querySelector(".question-submit");
    if (submitEl) submitEl.disabled = !questions.every((_, qi) => isAnswered(qi));
  }

  function render() {
    const blocks = questions
      .map((q, qi) => {
        const options = (q.options || [])
          .map((opt, oi) => {
            const isSelected = state[qi].selected.has(oi);
            return `
              <button type="button" class="option-btn${isSelected ? " selected" : ""}" data-qi="${qi}" data-oi="${oi}">
                <span class="option-label">${escapeHtml(opt.label)}</span>
                ${opt.description ? `<span class="option-desc">${escapeHtml(opt.description)}</span>` : ""}
              </button>`;
          })
          .join("");
        return `
          <div class="question-block" data-qi="${qi}">
            <p class="question-text">${escapeHtml(q.question)}</p>
            <div class="options">${options}</div>
            <div class="option-other">
              <span class="option-other-label">o escribe tu propia respuesta</span>
              <input
                type="text"
                class="option-other-input"
                data-qi="${qi}"
                placeholder="Tu respuesta…"
                value="${escapeHtml(state[qi].otherText)}"
              />
            </div>
          </div>
        `;
      })
      .join("");

    container.innerHTML =
      blocks + `<button type="button" class="question-submit" disabled>Responder</button>`;

    container.querySelectorAll(".option-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const qi = Number(btn.dataset.qi);
        const oi = Number(btn.dataset.oi);
        const isMultiSelect = !!questions[qi].multiSelect;
        state[qi].otherText = ""; // elegir una opción descarta el texto libre
        if (!isMultiSelect) state[qi].selected.clear();
        if (state[qi].selected.has(oi)) state[qi].selected.delete(oi);
        else state[qi].selected.add(oi);
        render();
      });
    });

    container.querySelectorAll(".option-other-input").forEach((input) => {
      // Escuchamos "input" (no volvemos a pintar toda la tarjeta) para no
      // perder el foco ni la posición del cursor mientras el usuario escribe.
      // A propósito NO tocamos state[qi].selected ni la clase "selected" de
      // ningún botón acá — antes escribir en este campo borraba visualmente
      // la opción ya elegida, dando la impresión de que esa selección se
      // había perdido (aunque en realidad seguía guardada). Ahora ambas
      // conviven en pantalla; al enviar, el texto libre sigue ganando si
      // no está vacío (ver más abajo), pero elegir SÍ sigue borrando el
      // texto libre — es una acción deliberada del usuario, no algo que
      // pase solo por seguir escribiendo.
      input.addEventListener("input", () => {
        const qi = Number(input.dataset.qi);
        state[qi].otherText = input.value;
        updateSubmitState();
      });
    });

    updateSubmitState();

    container.querySelector(".question-submit").addEventListener("click", async () => {
      const answers = {};
      questions.forEach((q, qi) => {
        const otherText = state[qi].otherText.trim();
        if (otherText) {
          answers[q.question] = otherText;
        } else {
          const labels = [...state[qi].selected].map((oi) => q.options[oi].label);
          answers[q.question] = labels.join(", ");
        }
      });

      const submitEl = container.querySelector(".question-submit");
      submitEl.disabled = true;
      submitEl.textContent = "Enviando…";

      // Igual que sendMessage(): mostramos "pensando" de una vez, al
      // responder, en vez de esperar a que llegue el primer texto/pregunta
      // de la continuación. Antes, entre responder una pregunta y que
      // apareciera la siguiente (o más texto), no había ningún indicador
      // en pantalla — daba la impresión de que el hilo se cortaba.
      ensureAgentBubble();

      try {
        const resp = await fetch("/chat/answer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers, question_id: questionId }),
        });
        if (!resp.ok) {
          const errBody = await resp.json().catch(() => ({}));
          throw new Error(errBody.error || `HTTP ${resp.status}`);
        }
        container.classList.remove("question-card");
        // Antes acá se reemplazaba TODO el contenido de la tarjeta por
        // solo las respuestas (Object.values(answers).join(" · ")),
        // borrando la pregunta misma del DOM — en la web quedaba una
        // lista de textos sueltos sin su pregunta, como si el usuario
        // hablara solo. Ahora se conserva cada pregunta junto a su
        // respuesta al colapsar la tarjeta.
        container.innerHTML = questions
          .map((q) => {
            const answerText = answers[q.question] || "—";
            return `
              <p class="question-answered">
                <span class="question-answered-q">${escapeHtml(q.question)}</span>
                <span class="question-answered-a">${escapeHtml(answerText)}</span>
              </p>`;
          })
          .join("");
        // Si esta era la última pregunta pendiente de la tanda, el backend
        // retoma acá mismo el streaming de la continuación (más texto,
        // otra pregunta, o el cierre del turno) — si todavía quedaba otra
        // pregunta sin responder, esto es solo un {"status":"ok"} plano y
        // consumeStream no encuentra nada que despachar, sin problema.
        await consumeStream(resp);
      } catch (err) {
        // Si falla, dejamos el botón reactivado para reintentar y avisamos
        // en pantalla — antes esto quedaba silencioso (fetch no lanza error
        // por sí solo con un 4xx/5xx) y la pregunta se daba por respondida
        // en la interfaz aunque el backend nunca la haya recibido bien.
        submitEl.disabled = false;
        submitEl.textContent = "Responder";
        const errorEl = document.createElement("p");
        errorEl.className = "question-error";
        errorEl.textContent = `No se pudo enviar: ${err.message}. Intenta de nuevo.`;
        container.appendChild(errorEl);
        // La burbuja "pensando" que se creó arriba (ensureAgentBubble())
        // quedaría huérfana para siempre si no la resolvemos acá — nada
        // más la va a limpiar porque consumeStream() nunca llegó a correr
        // (mismo patrón que el catch de sendMessage()).
        if (activeAgentDiv) {
          activeAgentDiv.remove();
          activeAgentDiv = null;
          activeTurnStarted = false;
        }
      }
    });
  }

  render();
  chatEl.scrollTop = chatEl.scrollHeight;
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = inputEl.value.trim();
  if (!message) return;
  inputEl.value = "";
  autoResizeInput();
  sendMessage(message);
});

// ---------- Sidebar: últimas semanas ----------

const sidebarContentEl = document.getElementById("sidebar-content");
const sidebarRefreshEl = document.getElementById("sidebar-refresh");

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function renderWeeks(weeks) {
  if (!weeks || weeks.length === 0) {
    sidebarContentEl.innerHTML =
      '<p class="sidebar-status">No hay semanas registradas todavía.</p>';
    return;
  }

  sidebarContentEl.innerHTML = weeks
    .map((week, i) => {
      const platos = (week.platos || [])
        .map((p) => `<li>${escapeHtml(p)}</li>`)
        .join("");
      // El link a Notion es opcional y a propósito NO trae ni renderiza acá
      // ninguna nota/resumen de esa semana (ver paso 7 de
      // propuesta-menu-semanal, que puede dejar una sub-página con el
      // resumen de la conversación colgando de esta misma página de
      // Notion) — evita construir un render de bloques de Notion en la
      // app, y mantiene ese dato fuera del contexto de nuevas propuestas.
      const notionLink = week.url
        ? `<a class="week-card-notion-link" href="${escapeHtml(week.url)}" target="_blank" rel="noopener">Ver en Notion ↗</a>`
        : "";
      return `
        <details class="week-card" ${i === 0 ? "open" : ""}>
          <summary>${escapeHtml(week.label || "Semana")}</summary>
          <ul>${platos}</ul>
          ${notionLink}
        </details>
      `;
    })
    .join("");
}

async function loadWeeks(refresh = false) {
  sidebarContentEl.innerHTML = '<p class="sidebar-status">Cargando…</p>';
  try {
    const resp = await fetch(`/weeks/recent${refresh ? "?refresh=1" : ""}`);
    if (resp.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await resp.json();
    if (data.error) {
      sidebarContentEl.innerHTML = `<p class="sidebar-status">No se pudo cargar: ${escapeHtml(
        data.error
      )}</p>`;
      return;
    }
    renderWeeks(data.weeks);
  } catch (err) {
    sidebarContentEl.innerHTML = `<p class="sidebar-status">No se pudo cargar: ${escapeHtml(
      err.message
    )}</p>`;
  }
}

sidebarRefreshEl.addEventListener("click", () => loadWeeks(true));
loadWeeks(false);
