# Lab Agent — Telegram Bot for Lab Result Interpretation

A LangGraph agent that interprets lab test results against a reference-values
PDF (RAG over FAISS) and answers via a Telegram bot, served through a FastAPI
webhook.

The agent triages each message into one of three paths:

- **ANALIZAR_EXAMEN** — a lab value was provided → retrieve reference ranges (RAG) → evaluate and flag critical values.
- **PEDIR_INFO** — a lab test was mentioned but key info is missing (units, fasting state, etc.) → ask for it.
- **FUERA_DE_ALCANCE** — anything outside lab-result interpretation (symptoms, general medical questions) → politely decline and redirect to a doctor.

## Project structure

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
│   └── set_webhook.py          # one-off script to register the webhook with Telegram
├── data/
│   ├── pdf/                     # source reference PDFs (versioned)
│   └── faiss_index/             # persisted vector index (git-ignored, built on first run)
├── notebooks/                  # original exploratory notebooks (kept for reference)
├── Dockerfile
├── docker-compose.yml           # api + Caddy (automatic HTTPS) for the OCI deploy
├── Caddyfile
├── requirements.txt
├── .env.example
└── .gitignore
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL
```

Run the API locally:

```bash
PYTHONPATH=src uvicorn lab_agent.api.main:app --reload
```

The first request builds the FAISS index from `data/pdf/*.pdf` and persists it
to `data/faiss_index/`; subsequent runs load it from disk instead of
re-embedding.

## Telegram integration — what you actually need

You chose **webhook mode**, which means:

1. **A Telegram bot token.** Create one via [@BotFather](https://t.me/BotFather) → `/newbot`. Put it in `TELEGRAM_BOT_TOKEN`.
2. **A public HTTPS endpoint.** Telegram will only deliver updates to an `https://` URL with a valid certificate — no self-signed certs, no plain HTTP. This is why the deploy uses Caddy: it gets a free Let's Encrypt certificate automatically for your domain.
3. **A domain name pointing at your OCI instance's public IP** (an A record). A bare IP won't get a valid cert from Let's Encrypt.
4. **Ports 80 and 443 open** on the OCI instance's security list / firewall (Oracle Cloud blocks these by default — you must open them in the VCN's security list *and* in the instance's `iptables`/`firewalld` if enabled).
5. Register the webhook once the app is reachable (see below).

You do **not** need a separate "API service" beyond this FastAPI app — it *is*
the API. Telegram calls it directly; there's no additional layer required.

## Deploying to Oracle Cloud (OCI)

On the OCI compute instance:

```bash
# 1. Install Docker + Compose plugin (Ubuntu example)
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker

# 2. Clone your repo
git clone <your-repo-url> lab-agent && cd lab-agent

# 3. Configure environment
cp .env.example .env
# edit .env with real values; TELEGRAM_WEBHOOK_URL = https://your-domain.com/telegram/webhook

# 4. Point the Caddyfile at your real domain
#    edit Caddyfile: replace "your-domain.com" with your actual domain

# 5. Bring it up
sudo docker compose up -d --build
```

Open the required ports:

- **OCI Console** → your instance's VCN → Security Lists (or Network Security Group) → add ingress rules for TCP 80 and 443 from `0.0.0.0/0`.
- On the instance itself, if `iptables`/`firewalld` is active, allow 80/443 there too.

Once the container is up and your domain resolves to the instance, register
the webhook with Telegram:

```bash
PYTHONPATH=src python3 scripts/set_webhook.py
```

Verify it's live:

```bash
curl https://your-domain.com/health
```

Then message your bot on Telegram — the webhook delivers the update to
`/telegram/webhook`, the graph runs, and the bot replies.

## Notes

- The FAISS index is persisted in a Docker volume (`faiss_index`) so it survives container restarts and isn't rebuilt (and re-embedded) on every deploy. Delete the volume if you update the source PDFs.
- `GEMINI_API_KEY` and `TELEGRAM_BOT_TOKEN` are secrets — never commit `.env`; only `.env.example` is versioned.
