"""
Logging compartido de lo que hace el agente paso a paso — herramientas que
llama, resultados, costo del turno. Usado tanto por chat_cli.py (donde ES
la interfaz visible) como por la app web (donde va a la terminal de
uvicorn, para tener la misma visibilidad que en terminal directa).
"""
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def _safe_print(texto: str) -> None:
    """print() que nunca lanza por la codificación de la consola. Con la
    salida redirigida (o una consola cp1252 en Windows) los emojis del log
    reventaban con UnicodeEncodeError, y como esto corre dentro de la tarea
    del turno, el turno entero moría en silencio. El log es secundario: si
    no se puede escribir tal cual, se escribe con los caracteres dudosos
    reemplazados."""
    try:
        print(texto, flush=True)
    except UnicodeEncodeError:
        import sys

        codificacion = getattr(sys.stdout, "encoding", None) or "ascii"
        print(texto.encode(codificacion, errors="replace").decode(codificacion), flush=True)


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(texts) if texts else str(content)
    return str(content)


def log_message(message, prefix: str = "") -> None:
    """Imprime en consola el paso que representa este mensaje del SDK."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ThinkingBlock):
                _safe_print(f"{prefix}🤔 [pensando] {block.thinking}")
            elif isinstance(block, ToolUseBlock):
                _safe_print(f"{prefix}🔧 [herramienta] {block.name}({block.input})")
            elif isinstance(block, TextBlock):
                _safe_print(f"{prefix}💬 {block.text}")

    elif isinstance(message, UserMessage):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                marca = "❌" if block.is_error else "✅"
                resultado = _tool_result_text(block.content)
                preview = resultado[:300] + ("…" if len(resultado) > 300 else "")
                _safe_print(f"{prefix}{marca} [resultado] {preview}")

    elif isinstance(message, ResultMessage):
        costo = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "N/D"
        _safe_print(
            f"{prefix}— fin del turno · {message.num_turns} paso(s) · "
            f"{message.duration_ms}ms · costo aprox: {costo} · "
            f"session_id: {message.session_id} —\n"
        )
