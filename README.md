# Lab Agent — Bot de Telegram para Interpretación de Exámenes de Laboratorio

Un agente LangGraph que interpreta resultados de exámenes de laboratorio
contra un PDF de valores de referencia (RAG sobre FAISS) y responde a través
de un bot de Telegram, servido mediante un webhook de FastAPI.

El agente clasifica cada mensaje en una de tres rutas:

- **ANALIZAR_EXAMEN** — se proporcionó un valor de laboratorio → recupera los rangos de referencia (RAG) → evalúa y marca valores críticos.
- **PEDIR_INFO** — se mencionó un examen de laboratorio pero falta información clave (unidades, estado de ayuno, etc.) → la solicita.
- **FUERA_DE_ALCANCE** — cualquier cosa fuera de la interpretación de exámenes de laboratorio (síntomas, preguntas médicas generales) → declina cortésmente y redirige a un médico.

## Estructura del proyecto

```
.
├── src/lab_agent/
│   ├── config.py              # env-driven settings
│   ├── rag/
│   │   └── ingest.py           # PDF loading, chunking, FAISS build/persist/load
│   ├── agent/
│   │   ├── state.py             # MedicalAgentState (TypedDict)
│   │   ├── prompts.py           # all ChatPromptTemplate definitions
│   │   ├── nodes.py             # triage / out_of_scope / ask_info / rag / evaluate nodes
│   │   └── graph.py             # builds and compiles the LangGraph StateGraph
│   ├── telegram/
│   │   └── bot.py               # sendMessage / setWebhook calls to the Telegram API
│   └── api/
│       └── main.py              # FastAPI app: /health and /telegram/webhook
├── scripts/
│   └── set_webhook.py          # script único para registrar el webhook en Telegram
├── data/
│   ├── pdf/                     # PDFs de referencia originales (versionados)
│   └── faiss_index/             # índice vectorial persistido (ignorado por git, se construye en la primera ejecución)
├── notebooks/                  # notebooks exploratorios originales (se mantienen como referencia)
├── Dockerfile
├── docker-compose.yml           # api + Caddy (HTTPS automático) para el despliegue en OCI
├── Caddyfile
├── requirements.txt
├── .env.example
└── .gitignore
```

## Configuración local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# completa GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL
```

Ejecuta la API localmente:

```bash
PYTHONPATH=src uvicorn lab_agent.api.main:app --reload
```

La primera solicitud construye el índice FAISS a partir de `data/pdf/*.pdf` y
lo persiste en `data/faiss_index/`; las siguientes ejecuciones lo cargan desde
disco en lugar de volver a generar los embeddings.

## Integración con Telegram — lo que realmente necesitas

Elegiste el **modo webhook**, lo que significa:

1. **Un token de bot de Telegram.** Créalo mediante [@BotFather](https://t.me/BotFather) → `/newbot`. Colócalo en `TELEGRAM_BOT_TOKEN`.
2. **Un endpoint HTTPS público.** Telegram solo entrega actualizaciones a una URL `https://` con un certificado válido — nada de certificados autofirmados ni HTTP plano. Por eso el despliegue usa Caddy: obtiene automáticamente un certificado gratuito de Let's Encrypt para tu dominio.
3. **Un nombre de dominio apuntando a la IP pública de tu instancia OCI** (un registro A). Una IP desnuda no obtendrá un certificado válido de Let's Encrypt.
4. **Los puertos 80 y 443 abiertos** en la lista de seguridad / firewall de la instancia OCI (Oracle Cloud los bloquea por defecto — debes abrirlos en la lista de seguridad de la VCN *y* en el `firewalld` de la instancia si está activo).
5. Registrar el webhook una vez que la app sea alcanzable (ver más abajo).

**No** necesitas un "servicio de API" separado además de esta app FastAPI — ella
*es* la API. Telegram la llama directamente; no se requiere ninguna capa adicional.

## Despliegue en Oracle Cloud (OCI)

Esta guía asume la imagen **Oracle-Linux-9.8-2026.07.20-0** (Oracle Linux 9, basada en RHEL, usa `dnf`).

En la instancia de cómputo de OCI:

```bash
# 1. Actualizar el sistema
sudo dnf update -y

# 2. Instalar git
sudo dnf install -y git

# 3. Instalar Docker Engine + plugin de Compose
sudo dnf install -y dnf-utils
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

# (opcional) usar Docker sin sudo: agrega tu usuario al grupo docker
sudo usermod -aG docker $USER
newgrp docker

# 4. Clonar tu repositorio
git clone <your-repo-url> lab-agent && cd lab-agent

# 5. Configurar el entorno
cp .env.example .env
# edita .env con los valores reales; TELEGRAM_WEBHOOK_URL = https://tu-dominio.com/telegram/webhook

# 6. Apuntar el Caddyfile a tu dominio real
#    edita Caddyfile: reemplaza "your-domain.com" por tu dominio real

# 7. Levantar los contenedores
sudo docker compose up -d --build
```

Abre los puertos necesarios:

- **Consola de OCI** → VCN de tu instancia → Security Lists (o Network Security Group) → agrega reglas de ingreso para TCP 80 y 443 desde `0.0.0.0/0`.
- En la propia instancia, Oracle Linux 9 trae `firewalld` activo por defecto — ábrelos también ahí:

```bash
sudo firewall-cmd --permanent --add-port=80/tcp
sudo firewall-cmd --permanent --add-port=443/tcp
sudo firewall-cmd --reload
```

Una vez que el contenedor esté activo y tu dominio resuelva a la instancia,
registra el webhook con Telegram:

```bash
PYTHONPATH=src python3 scripts/set_webhook.py
```

Verifica que esté funcionando:

```bash
curl https://tu-dominio.com/health
```

Luego envía un mensaje a tu bot en Telegram — el webhook entrega la
actualización a `/telegram/webhook`, el grafo se ejecuta y el bot responde.

## Notas

- El índice FAISS se persiste en un volumen de Docker (`faiss_index`) para que sobreviva a los reinicios del contenedor y no se reconstruya (ni se vuelvan a generar los embeddings) en cada despliegue. Elimina el volumen si actualizas los PDFs de origen.
- `GEMINI_API_KEY` y `TELEGRAM_BOT_TOKEN` son secretos — nunca hagas commit de `.env`; solo `.env.example` está versionado.
