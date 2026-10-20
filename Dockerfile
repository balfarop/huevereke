FROM python:3.12-slim

# El Agent SDK de Python invoca el CLI de Claude Code como subproceso,
# así que necesitamos Node.js + el paquete @anthropic-ai/claude-code.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY .claude ./.claude

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
