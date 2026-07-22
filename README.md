# Lab Agent — Bot de Telegram para Interpretación de Exámenes de Laboratorio

Un agente [LangGraph](https://langchain-ai.github.io/langgraph/) que interpreta
resultados de exámenes de laboratorio contra un PDF de valores de referencia
(RAG sobre FAISS, con embeddings y LLM de Gemini) y responde a través de un
bot de Telegram, servido mediante un webhook de FastAPI detrás de HTTPS
(Caddy + Let's Encrypt), desplegado en una instancia de Oracle Cloud
Infrastructure (OCI).

**Bot en producción:** [@LabTestAgentbot](https://t.me/LabTestAgentbot)

## Índice

- [Arquitectura del agente](#arquitectura-del-agente)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Configuración local](#configuración-local)
- [Integración con Telegram](#integración-con-telegram--lo-que-realmente-necesitas)
- [Infraestructura: OCI + Docker + Caddy](#infraestructura-oci--docker--caddy)
- [DNS gratuito con DuckDNS](#dns-gratuito-con-duckdns)
- [Despliegue paso a paso](#despliegue-paso-a-paso)
- [Registrar el webhook de Telegram](#registrar-el-webhook-de-telegram)
- [Troubleshooting real (bitácora del despliegue)](#troubleshooting-real-bitácora-del-despliegue)
- [Notas y decisiones de diseño](#notas-y-decisiones-de-diseño)

## Arquitectura del agente

El agente es un grafo de estados (`StateGraph` de LangGraph) que clasifica
cada mensaje entrante y lo enruta por uno de tres caminos. El estado
compartido entre nodos es `MedicalAgentState`
([state.py](src/lab_agent/agent/state.py)):

```python
question: str                    # Pregunta original del usuario
telegram_chat_id: Optional[int]  # ID de chat de Telegram
triage_decision: str             # ANALIZAR_EXAMEN | PEDIR_INFO | FUERA_DE_ALCANCE
missing_fields: Optional[str]    # Datos faltantes si la información está incompleta
extracted_values: List[str]      # Exámenes y valores detectados
rag_context: str                 # Rangos de referencia extraídos del PDF
answer: Optional[str]            # Respuesta final estructurada
final_action: str                # Nodo o ruta final ejecutada
```

### Flujo del grafo

```
                        ┌───────────────┐
                        │  triage_node  │  clasifica el mensaje con el LLM
                        └───────┬───────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
    ANALIZAR_EXAMEN        PEDIR_INFO        FUERA_DE_ALCANCE
              │                 │                 │
              ▼                 ▼                 ▼
        ┌───────────┐   ┌────────────────┐  ┌──────────────────┐
        │ rag_node  │   │ ask_info_node  │  │ out_of_scope_node │
        └─────┬─────┘   └────────┬───────┘  └─────────┬─────────┘
              │                  │                     │
              ▼                  ▼                     ▼
      ┌───────────────┐        END                   END
      │ evaluate_node │
      └───────┬───────┘
              │
              ▼
             END
```

**Nodos** ([nodes.py](src/lab_agent/agent/nodes.py)):

1. **`triage_node`** — envía la pregunta al LLM (`TRIAGE_PROMPT`) y clasifica
   la intención en una de tres rutas, extrayendo además los valores de
   examen mencionados (`extracted_values`) y cualquier dato faltante
   (`missing_fields`). Si el LLM falla, degrada a `FUERA_DE_ALCANCE` en vez
   de romper la conversación.
2. **`rag_node`** — solo se alcanza si la ruta es `ANALIZAR_EXAMEN`. Construye
   la query a partir de `extracted_values` (o de la pregunta completa si no
   se extrajo nada) y consulta el retriever FAISS para traer los fragmentos
   relevantes del PDF de valores de referencia (`rag_context`).
3. **`evaluate_node`** — compara los valores del usuario contra el
   `rag_context` recuperado y redacta la respuesta final, marcando si algún
   valor está fuera de rango o es crítico.
4. **`ask_info_node`** — se alcanza si la ruta es `PEDIR_INFO`; redacta una
   respuesta pidiendo los datos que faltan (unidades, ayuno, etc.) para poder
   evaluar el examen.
5. **`out_of_scope_node`** — se alcanza si la ruta es `FUERA_DE_ALCANCE`
   (síntomas, preguntas médicas generales, cualquier cosa que no sea
   interpretación de un examen de laboratorio); declina cortésmente y
   redirige a un médico.

### Modelos usados (Gemini)

| Uso | Variable de entorno | Valor por defecto |
|---|---|---|
| LLM (triage, respuestas) | `LLM_MODEL` | `gemini-3.1-flash-lite` |
| Embeddings (RAG) | `EMBEDDING_MODEL` | `models/gemini-embedding-001` |

Ambos se autentican con `GEMINI_API_KEY` contra la Gemini API pública
(`generativelanguage.googleapis.com`), **no** contra Vertex AI — ver la nota
sobre esto en [Troubleshooting](#troubleshooting-real-bitácora-del-despliegue).

### RAG: de PDF a respuesta

[`rag/ingest.py`](src/lab_agent/rag/ingest.py) hace todo el pipeline:

1. Carga los PDFs en `data/pdf/` con `PyMuPDFLoader`.
2. Los divide en chunks con `RecursiveCharacterTextSplitter`
   (`CHUNK_SIZE=300`, `CHUNK_OVERLAP=30` por defecto).
3. Genera embeddings con `GoogleGenerativeAIEmbeddings` y construye un índice
   `FAISS`.
4. Persiste el índice en `data/faiss_index/`. En arranques posteriores, si el
   índice ya existe en disco, se carga directamente sin volver a generar
   embeddings (ahorra tiempo y cuota de API).
5. Expone un retriever con `search_type="similarity_score_threshold"`
   (`RETRIEVER_SCORE_THRESHOLD=0.3`, `RETRIEVER_K=4` por defecto).

## Estructura del proyecto

```
.
├── src/lab_agent/
│   ├── config.py               # settings vía variables de entorno (.env)
│   ├── rag/
│   │   └── ingest.py            # carga de PDF, chunking, build/persist/load de FAISS
│   ├── agent/
│   │   ├── state.py              # MedicalAgentState (TypedDict)
│   │   ├── prompts.py            # ChatPromptTemplate de cada nodo
│   │   ├── nodes.py              # triage / out_of_scope / ask_info / rag / evaluate
│   │   └── graph.py              # construye y compila el StateGraph de LangGraph
│   ├── telegram/
│   │   └── bot.py                # sendMessage / setWebhook contra la API de Telegram
│   └── api/
│       └── main.py               # FastAPI: /health y /telegram/webhook
├── scripts/
│   └── set_webhook.py           # script para registrar el webhook en Telegram
├── data/
│   ├── pdf/                      # PDFs de referencia originales (versionados)
│   └── faiss_index/              # índice vectorial persistido (no versionado)
├── notebooks/                    # notebooks exploratorios originales (curso)
├── Dockerfile                    # imagen de la API (copia solo src/ y data/pdf/)
├── docker-compose.yml             # servicios api + caddy
├── Caddyfile                      # reverse proxy + HTTPS automático
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

1. **Un token de bot de Telegram.** Se crea con [@BotFather](https://t.me/BotFather)
   → `/newbot`. Va en `TELEGRAM_BOT_TOKEN`. El bot de este proyecto es
   [@LabTestAgentbot](https://t.me/LabTestAgentbot).
2. **Un endpoint HTTPS público.** Telegram solo entrega actualizaciones a una
   URL `https://` con certificado válido — nada de certificados autofirmados
   ni HTTP plano. Por eso el despliegue usa Caddy: obtiene automáticamente un
   certificado gratuito de Let's Encrypt para el dominio configurado.
3. **Un nombre de dominio apuntando a la IP pública de la instancia OCI** (un
   registro A). Una IP desnuda no obtendrá un certificado válido de Let's
   Encrypt, y Let's Encrypt necesita resolver el dominio para validar el reto
   ACME. Este proyecto usa **DuckDNS**, gratuito — ver la sección
   [DNS gratuito con DuckDNS](#dns-gratuito-con-duckdns).
4. **Los puertos 80 y 443 abiertos**, en dos capas distintas: la lista de
   seguridad / NSG de la VCN de OCI, *y* el firewall del sistema operativo
   dentro de la instancia (`firewalld` en Oracle Linux). Faltar cualquiera de
   las dos deja el puerto cerrado igual.
5. **Un secreto de webhook** (`TELEGRAM_WEBHOOK_SECRET`) — no lo entrega
   Telegram ni Google, lo genera uno mismo (ver más abajo) y sirve para que
   la API pueda verificar que cada request realmente viene de Telegram.
6. Registrar el webhook una vez que la app sea alcanzable por HTTPS (ver
   [Registrar el webhook de Telegram](#registrar-el-webhook-de-telegram)).

**No** se necesita un "servicio de API" separado además de esta app FastAPI —
ella *es* la API. Telegram la llama directamente; no se requiere ninguna capa
adicional.

## Infraestructura: OCI + Docker + Caddy

```
Telegram ──HTTPS──▶ Caddy (:80/:443, TLS automático) ──HTTP──▶ api (FastAPI, :8000)
                        │                                          │
                        │ certificado Let's Encrypt                │ LangGraph
                        │ (reto HTTP-01 vía DuckDNS)                │  + FAISS
                        ▼                                          ▼
                  lab-agent.duckdns.org                     Gemini API (LLM + embeddings)
```

- **Instancia de cómputo OCI**: Oracle Linux 9 (`Oracle-Linux-9.8-2026.07.20-0`),
  con una única IP pública (no elástica en este caso).
- **Docker Compose** levanta dos servicios ([docker-compose.yml](docker-compose.yml)):
  - `api`: construye la imagen desde el `Dockerfile` local, lee secretos de
    `.env` vía `env_file`, y persiste el índice FAISS en el volumen nombrado
    `faiss_index` para que sobreviva a recreaciones del contenedor.
  - `caddy`: imagen oficial `caddy:2-alpine`, expone 80/443 al host, lee
    [Caddyfile](Caddyfile) y hace reverse proxy hacia `api:8000` dentro de la
    red interna de Compose. Gestiona el ciclo de vida completo del
    certificado TLS (emisión y renovación) sin configuración manual.
- El contenedor `api` **no** expone puertos al host directamente
  (`expose: "8000"`, no `ports:`) — solo es alcanzable a través de Caddy, que
  es el único servicio con puertos públicos.

## DNS gratuito con DuckDNS

No se compró un dominio; se usó [DuckDNS](https://www.duckdns.org), un
servicio gratuito de subdominios pensado justo para este caso (servidores
personales con IP fija pero sin dominio propio).

1. Entrar a duckdns.org e iniciar sesión (con cuenta de Google, GitHub, etc.).
2. En el campo "sub domain" registrar el nombre deseado — en este proyecto,
   `lab-agent`, quedando `lab-agent.duckdns.org`.
3. En el campo IP de esa fila, escribir la IP pública de la instancia OCI y
   pulsar "update ip".
4. Verificar la propagación:
   ```bash
   nslookup lab-agent.duckdns.org
   ```
   Debe devolver la IP pública de la instancia.

**Limitación a tener en cuenta:** si la instancia OCI pierde su IP pública
(por ejemplo, al detenerla y volver a iniciarla sin una IP reservada), DuckDNS
no se entera solo — hay que volver a la página y pulsar "update ip" con la IP
nueva, o el certificado y el webhook dejarán de ser alcanzables.

## Despliegue paso a paso

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

# 4. Clonar el repositorio
git clone <url-del-repo> lab-agent && cd lab-agent

# 5. Configurar el entorno
cp .env.example .env
nano .env
# GEMINI_API_KEY=            -> API key real de https://aistudio.google.com/apikey (empieza con AIzaSy...)
# TELEGRAM_BOT_TOKEN=        -> token entregado por @BotFather
# TELEGRAM_WEBHOOK_SECRET=   -> cadena aleatoria propia, ver más abajo cómo generarla
# TELEGRAM_WEBHOOK_URL=      -> https://lab-agent.duckdns.org/telegram/webhook

# 6. Apuntar el Caddyfile al dominio real (ya versionado en este repo como
#    lab-agent.duckdns.org; si usas otro dominio, edítalo):
#    lab-agent.duckdns.org {
#        reverse_proxy api:8000
#    }

# 7. Levantar los contenedores
docker compose up -d --build
```

Abrir los puertos necesarios (**ambas** capas, o el tráfico externo no llega):

- **Consola de OCI** → VCN de la instancia → Security Lists (o Network
  Security Group) → agregar reglas de ingreso TCP 80 y 443 desde `0.0.0.0/0`.
- **En la instancia**, Oracle Linux 9 trae `firewalld` activo por defecto:

```bash
sudo firewall-cmd --permanent --add-port=80/tcp
sudo firewall-cmd --permanent --add-port=443/tcp
sudo firewall-cmd --reload
```

Verificar que la app responde:

```bash
curl https://lab-agent.duckdns.org/health
# {"status":"ok"}
```

## Registrar el webhook de Telegram

`TELEGRAM_WEBHOOK_SECRET` no se "obtiene" de Telegram ni de Google: es una
cadena aleatoria que uno mismo define. Telegram la reenvía en cada request
(header `X-Telegram-Bot-Api-Secret-Token`) para que la API pueda verificar
que la petición viene realmente de Telegram. Se genera así:

```bash
openssl rand -hex 32
```

`TELEGRAM_WEBHOOK_URL` es la URL pública HTTPS de la ruta
`POST /telegram/webhook` de la propia API:

```
TELEGRAM_WEBHOOK_URL=https://lab-agent.duckdns.org/telegram/webhook
```

Con ambos valores ya en `.env` y el contenedor `api` recreado
(`docker compose up -d --force-recreate api`), queda registrar el webhook.
El `Dockerfile` solo copia `src/` y `data/pdf/` a la imagen — `scripts/` no
viaja dentro del contenedor — así que la forma más simple es invocar
`set_webhook()` directamente dentro del contenedor `api`, donde el módulo
`lab_agent` y las variables de `.env` ya están disponibles:

```bash
docker compose exec api python3 -c "
import asyncio
from lab_agent.telegram.bot import set_webhook
print(asyncio.run(set_webhook()))
"
# {'ok': True, 'result': True, 'description': 'Webhook was set'}
```

(Alternativa, si se ejecuta fuera de Docker con Python instalado localmente y
las dependencias del `requirements.txt`: `PYTHONPATH=src python3
scripts/set_webhook.py`, exportando antes las variables de `.env` al shell.)

Verificar el estado del webhook en cualquier momento:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

Luego basta con escribirle a [@LabTestAgentbot](https://t.me/LabTestAgentbot)
en Telegram — el webhook entrega la actualización a `/telegram/webhook`, el
grafo se ejecuta y el bot responde en el mismo chat.

## Troubleshooting real (bitácora del despliegue)

Problemas concretos que aparecieron durante el despliegue en OCI, por si se
repiten:

**1. `401 UNAUTHENTICATED — ACCESS_TOKEN_TYPE_UNSUPPORTED` al construir el
índice FAISS.** El contenedor `api` fallaba en el arranque al llamar a
`generativelanguage.googleapis.com` para generar embeddings. Causa: la
variable `GEMINI_API_KEY` tenía un **access token OAuth temporal** (prefijo
`AQ.Ab8...`, el mismo formato que devuelve `gcloud auth print-access-token`
o una sesión de Colab/Cloud Shell), no una **API key** de Gemini. Ambas cosas
autentican contra Google pero son mecanismos distintos: `GoogleGenerativeAIEmbeddings`
y `ChatGoogleGenerativeAI` con `google_api_key=` esperan una key permanente
generada en [aistudio.google.com/apikey](https://aistudio.google.com/apikey),
que siempre empieza con `AIzaSy...`. La solución fue generar la key ahí (no
copiar ningún token de sesión) y usar ese valor en `.env`.

**2. Comillas en el `.env`.** Con `GEMINI_API_KEY="AIzaSy..."` (comillas
incluidas), Docker Compose pasa el valor literal con comillas al contenedor,
rompiendo la autenticación igual. En un `.env` consumido por `env_file:`, los
valores van sin comillas: `GEMINI_API_KEY=AIzaSy...`.

**3. Certificado TLS nunca se emitía — `Caddyfile input` con
`your-domain.com`.** Caddy reintentaba obtener el certificado para el
dominio placeholder del repositorio, que evidentemente no resuelve a
ninguna IP real, y el reto ACME (`http-01`/`tls-alpn-01`) fallaba con 404 /
`unauthorized` en bucle. Solución: reemplazar `your-domain.com` en el
`Caddyfile` por el dominio real (`lab-agent.duckdns.org`) y recrear el
contenedor `caddy` (`docker compose down caddy && docker compose up -d
caddy`) para que descartara el estado ACME viejo cacheado en el volumen
`caddy_data`.

**4. Verificación de puertos/red antes de long dominio final.** Para separar
"problema de DNS" de "problema de firewall" se usó, desde una máquina
externa: `nc -zv <ip> 80` y `nc -zv <ip> 443` (conectividad TCP cruda), y en
el propio servidor: `sudo ss -tlnp | grep -E ':80|:443'` para confirmar que
Caddy escuchaba en `0.0.0.0` y no solo en `127.0.0.1`.

**5. `scripts/set_webhook.py` no existe dentro del contenedor.** El
`Dockerfile` solo copia `src/` y `data/pdf/`; `scripts/` nunca llega a la
imagen. En vez de modificar el `Dockerfile` para un script de un solo uso, se
invocó la misma función (`set_webhook()`) inline con `python3 -c` dentro del
contenedor ya corriendo, aprovechando que ahí sí están el módulo `lab_agent`
y las variables de entorno.

## Notas y decisiones de diseño

- El índice FAISS se persiste en un volumen de Docker (`faiss_index`) para
  que sobreviva a los reinicios del contenedor y no se reconstruya (ni se
  vuelvan a generar los embeddings, con su costo de cuota) en cada
  despliegue. Elimina el volumen si se actualizan los PDFs de origen.
- `GEMINI_API_KEY` y `TELEGRAM_BOT_TOKEN` son secretos — nunca se hace commit
  de `.env`; solo `.env.example` está versionado.
- Caddy se eligió sobre nginx/certbot por manejar TLS automático
  (emisión + renovación) con una configuración de una sola línea, sin
  necesidad de cronear la renovación del certificado por separado.
- El agente usa la Gemini API pública (API key), no Vertex AI (OAuth/ADC) —
  ver el punto 1 de troubleshooting para la distinción entre ambos.
