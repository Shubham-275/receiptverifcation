<p align="center">
  <img src="docs/assets/mark.svg" width="52" height="52" alt="Receipt Intelligence mark">
</p>

<h1 align="center">Receipt Intelligence</h1>

<p align="center">
  <em>Bringing ease to SMSE</em><br>
  <sub>From messy supplier receipts to interactive intelligence </sub>
</p>

<p align="center">
  <img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-f4f1e9?style=flat-square&amp;labelColor=171613&amp;logo=python&amp;logoColor=a64a2c">
  <img alt="FastAPI" src="https://img.shields.io/badge/fastapi-0.162-f4f1e9?style=flat-square&amp;labelColor=171613&amp;logo=fastapi&amp;logoColor=a64a2c">
  <img alt="PostgreSQL and pgvector" src="https://img.shields.io/badge/postgresql-pgvector-f4f1e9?style=flat-square&amp;labelColor=171613&amp;logo=postgresql&amp;logoColor=a64a2c">
  <img alt="Gemini Vision" src="https://img.shields.io/badge/vision-gemini-f4f1e9?style=flat-square&amp;labelColor=171613">
  <img alt="173 tests passing" src="https://img.shields.io/badge/tests-173_passing-a64a2c?style=flat-square&amp;labelColor=171613">
</p>

<p align="center">
  <img src="docs/screenshots/login.png" alt="Receipt Intelligence access screen with a shopkeeper doodle" width="100%">
</p>

<p align="center">
  <sub><strong>Owner access.</strong> Hashed passwords, signed HTTP-only sessions, one ledger.</sub>
</p>

<p align="center">
  <a href="#why">Why</a> ·
  <a href="#product-tour">Product tour</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#api">API</a> ·
  <a href="#project-layout">Project layout</a> ·
  <a href="#testing">Testing</a> ·
  <a href="#design-notes">Design</a>
</p>

---

## Why

Independent retailers run on receipts. They arrive printed, handwritten, photographed at an angle, and abbreviated beyond recognition — `PRLE G`, `HARPIC 1L`, `AMUL BTR 500`. Spreadsheets don't survive that.

Receipt Intelligence turns each document into structured purchasing data: supplier, date, invoice, every line item, quantities, units, and prices. Then it makes the ledger answer back — exact totals, supplier comparisons, product histories, and plain-language questions.

> **The model reads. PostgreSQL counts.**
> Vision extraction happens once. Every sum, average, and comparison is computed in SQL. pgvector is reserved for the one thing SQL cannot do well: finding a receipt by meaning.

## At a glance

| Capability | What it does |
| :-- | :-- |
| **Vision intake** | Classifies the image, rejects non-receipts, and extracts supplier metadata plus every line item. |
| **Product resolution** | A five-step cascade — exact alias → normalized name → fuzzy → pgvector → LLM — maps shorthand to one canonical product. |
| **Analytics** | Category composition, supplier concentration, monthly spend and item volume, top products, and value vs volume. |
| **Inventory** | Searchable product ledger with volume, average unit cost, total spend, order count, and last purchase. |
| **Receipt archive** | Every invoice with line-item detail, arithmetic checks, month filters, and CSV export. |
| **Ask the ledger** | Routes questions to exact SQL, semantic retrieval, or both — and never sends thousands of raw rows to a model. |
| **Owner access** | First-run setup, PBKDF2-SHA256 passwords, HMAC-signed HTTP-only sessions, protected ledger APIs. |
| **Zero-build UI** | Hand-written HTML, CSS, and JavaScript served directly by FastAPI. No frontend toolchain. |

---

## Product tour

### 01 / The ledger

<p align="center">
  <img src="docs/screenshots/overview.png" alt="Overview dashboard with total verified spend, monthly cash flow and latest invoices" width="100%">
</p>

<p align="center">
  <sub><strong>Overview</strong> — verified spend, monthly cash flow, latest invoices. One store, one number.</sub>
</p>

### 02 / The shape of spend

<p align="center">
  <img src="docs/screenshots/analytics.png" alt="Analytics workspace with category donut, supplier concentration and KPI strip" width="100%">
</p>

<p align="center">
  <sub><strong>Analytics</strong> — where the money goes, which suppliers carry the ledger, and how item volume moves with purchasing value.</sub>
</p>

### 03 / Inventory &amp; archive

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/screenshots/inventory.png" alt="Inventory product ledger" width="100%">
      <p><sub><strong>Inventory</strong> — every product, its average cost, volume and last recorded invoice.</sub></p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/screenshots/receipts.png" alt="Receipt archive with supplier, invoice, items and total" width="100%">
      <p><sub><strong>Receipts</strong> — the complete archive, line-item extractions and arithmetic checks.</sub></p>
    </td>
  </tr>
</table>

### 04 / Ask the ledger

<p align="center">
  <img src="docs/screenshots/intelligence.png" alt="Natural language query answered with a PostgreSQL aggregate" width="100%">
</p>

<p align="center">
  <sub><strong>Intelligence</strong> — plain-language questions, routed to exact SQL or semantic retrieval. The answer above came from a PostgreSQL aggregate in 4.1 ms.</sub>
</p>

### 05 / Intake

<p align="center">
  <img src="docs/screenshots/scan.png" alt="Scan receipt dialog with file drop, preview mode and batch support" width="100%">
</p>

<p align="center">
  <sub><strong>Scan a receipt</strong> — single documents or a batch, saved to the ledger or previewed without writing.</sub>
</p>

---

## Architecture

```mermaid
flowchart LR
  subgraph Browser["Ledger dashboard"]
    UI["Overview · Analytics · Inventory · Receipts · Intelligence"]
  end

  subgraph API["FastAPI"]
    AUTH["Auth / sessions"]
    INTAKE["Receipt intake"]
    ANALYTICS["Analytics"]
    QUERY["Query engine"]
  end

  subgraph VISION["Vision layer"]
    CLASSIFY["Classifier"]
    EXTRACT["Extractor"]
    RESOLVE["5-step resolver"]
  end

  DB[("PostgreSQL + pgvector")]

  UI -->|"upload"| INTAKE
  INTAKE --> CLASSIFY --> EXTRACT --> RESOLVE
  RESOLVE --> DB
  DB --> ANALYTICS --> UI
  UI -->|"question"| QUERY
  DB --> QUERY --> UI
  AUTH -.->|"protects"| INTAKE
  AUTH -.->|"protects"| ANALYTICS
  AUTH -.->|"protects"| QUERY
```

### Ingestion pipeline

1. **Classify** — decide whether the image is actually a receipt before spending extraction tokens.
2. **Extract** — supplier, date, invoice number, totals, and an arbitrary number of line items.
3. **Normalize** — quantities, units, prices, and line arithmetic.
4. **Resolve** — exact alias → normalized name → RapidFuzz → pgvector → LLM, stopping at the first confident match. Confirmed aliases are learned for next time.
5. **Embed** — receipt content is embedded into a 768-dimension vector for semantic recall.
6. **Persist** — receipts, items, products, aliases, suppliers, and embeddings land in PostgreSQL.

### Query routing

| Route | Used for | Engine |
| :-- | :-- | :-- |
| **SQL** | totals, quantities, averages, cheapest supplier, price trends | PostgreSQL aggregates |
| **pgvector** | “find the receipt where…”, vague item references | semantic retrieval over receipt content |
| **Hybrid** | questions that need both exact numbers and supporting receipts | SQL aggregate + vector matches |

The query engine classifies the question, executes the appropriate path, and composes the answer from structured results. Aggregation stays inside PostgreSQL.

### Data model

`receipts` · `purchase_items` · `products` · `product_aliases` · `suppliers` · `users`

---

## Quickstart

**You need:** Python 3.12+ (tested on 3.14), PostgreSQL 15+ with [`pgvector`](https://github.com/pgvector/pgvector), and a Gemini or OpenRouter API key.

```bash
git clone https://github.com/Shubham-275/receiptverifcation.git
cd receiptverifcation

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Open `.env` and set at minimum:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/receiptdb
DATABASE_URL_SYNC=postgresql+psycopg2://postgres:postgres@localhost:5432/receiptdb

# Gemini
GEMINI_API_KEY=your-key
LLM_PROVIDER=auto

# Generate one with:
# python -c "import secrets; print(secrets.token_urlsafe(48))"
AUTH_SECRET_KEY=replace-with-a-long-random-value
```

Using OpenRouter instead:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=google/gemma-3-12b-it
```

Create the database and apply migrations:

```bash
createdb receiptdb
alembic upgrade head
```

The first migration creates the `vector` extension. If your PostgreSQL role cannot create extensions, run `CREATE EXTENSION vector;` once as a superuser before migrating.

Start the ledger:

```bash
python run.py
```

Open **http://127.0.0.1:8000**. On an empty database the demo owner is seeded automatically:

```text
username  meera
password  ledger2026
```

Change `AUTH_SEED_USERNAME`, `AUTH_SEED_PASSWORD`, and `AUTH_SEED_DISPLAY_NAME` before the first run. Set `AUTH_SEED_ENABLED=false` to skip seeding and use the first-run **Claim the ledger** setup screen instead.

> `.env` is ignored by git. Never commit API keys or session secrets.

---

## API

Interactive docs: **http://127.0.0.1:8000/docs**

Ledger endpoints require the `ri_session` cookie. Health probes and the auth surface stay public.

| Method | Endpoint | Purpose |
| :-- | :-- | :-- |
| `GET` | `/api/v1/health` | Liveness probe |
| `GET` | `/api/v1/health/ready` | Readiness + database check |
| `GET` | `/api/v1/auth/status` | Setup / session state |
| `POST` | `/api/v1/auth/setup` | Create the first owner |
| `POST` | `/api/v1/auth/login` | Sign in |
| `POST` | `/api/v1/auth/logout` | Clear the session (`204` for APIs, `303` for browser forms) |
| `GET` | `/api/v1/auth/me` | Current owner |
| `POST` | `/api/v1/receipts/classify` | Receipt / not-receipt check |
| `POST` | `/api/v1/receipts/extract` | Extract without persisting |
| `POST` | `/api/v1/receipts/batch-extract` | Batch extract without persisting |
| `POST` | `/api/v1/receipts/upload` | Ingest and persist one receipt |
| `POST` | `/api/v1/receipts/batch-upload` | Batch ingest |
| `GET` | `/api/v1/receipts` | Receipt archive |
| `GET` | `/api/v1/receipts/{receipt_id}` | Receipt detail |
| `GET` | `/api/v1/analytics/overview` | Store KPIs |
| `GET` | `/api/v1/analytics/products` | Product ledger |
| `GET` | `/api/v1/analytics/spending/monthly` | Monthly spend |
| `GET` | `/api/v1/analytics/spending/composition` | Category composition |
| `GET` | `/api/v1/analytics/purchases/recent` | Latest invoices |
| `POST` | `/api/v1/queries` | Natural-language query |

```bash
# Sign in, then query the ledger
curl -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"username":"meera","password":"ledger2026"}' \
  http://127.0.0.1:8000/api/v1/auth/login

curl -b cookies.txt http://127.0.0.1:8000/api/v1/analytics/overview

curl -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"query":"Which supplier gave me the cheapest Harpic?"}' \
  http://127.0.0.1:8000/api/v1/queries
```

---

## Project layout

```text
app/
├── api/            FastAPI routers — auth, receipts, analytics, queries
├── models/         SQLAlchemy models — receipts, products, aliases, users
├── repositories/   SQL aggregation + pgvector search
├── services/       classifier, extractor, normalizer, resolver, query engine
├── schemas/        Pydantic request + response shapes
└── static/         zero-build dashboard — paper, ink, one terracotta accent
alembic/            schema and vector migrations
docs/               README screenshots and mark
tests/              173 tests
run.py              development server
```

## Tech stack

- **Python 3.14** · **FastAPI** · **SQLAlchemy 2 async** · **Alembic**
- **PostgreSQL** with **pgvector** for semantic retrieval
- **Gemini Vision** or **OpenRouter / Gemma 3 12B** for classification and extraction
- **RapidFuzz** for alias matching
- **Vanilla HTML, CSS, and JavaScript** for the dashboard

---

## Testing

```bash
pytest -q
```

```text
173 passed
```

External model calls are mocked, so the suite does not need a live API key. The test configuration points at `receiptdb_test` — see [`tests/conftest.py`](tests/conftest.py).

---

## Design notes

The dashboard is a Swiss editorial ledger: warm paper `#f4f1e9`, ink `#171613`, and one terracotta accent `#a64a2c`. Hairline rules instead of shadows. Square geometry. No gradients, no glass, no pill buttons.

| Paper | Ink | Terracotta |
| :--: | :--: | :--: |
| `#f4f1e9` | `#171613` | `#a64a2c` |

The hand-drawn shopkeeper is the only illustration — a small human counterweight to a wall of numbers. The interface is desktop-first but holds together on tablet and phone, and the dashboard is served as plain static files with no build step.

---

## Contributing

Issues and pull requests are welcome. If you change the UI, keep the palette and the rule-based layout intact. If you change the query engine, add a test that proves PostgreSQL still owns the arithmetic.

## Author

Built by [@Shubham-275](https://github.com/Shubham-275).

<p align="center">
  <sub>Made for ease</sub>
</p>
