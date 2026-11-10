# Build: AI-Powered Supplier Receipt Intelligence System

Build a portfolio-grade AI system for small retailers that converts arbitrary supplier/wholesaler receipts into structured purchasing intelligence.

The system must be **fast, reliable, modular, and easy to demo**.

Do not over-engineer it. Prefer simple components that are easy to run locally and deploy later.

RAG is not the primary data-access mechanism. Build a hybrid retrieval/query system. PostgreSQL is the source of truth and SQL handles exact structured/analytical queries. pgvector is used only for semantic retrieval where keyword/fuzzy matching is insufficient. The query layer must route requests between SQL, fuzzy/keyword search, vector search, or a combination. Do not retrieve database rows through embeddings when SQL can answer the question directly.

---

# 1. Core Product

A retailer receives supplier/wholesaler receipts.

Receipts can be:

- Printed
- Handwritten
- Clear or somewhat messy
- 1 item or many items
- Any combination of products
- Completely different product categories
- Different quantities and units
- Different supplier naming conventions
- Different spellings, abbreviations, and shorthand

For example, one receipt could contain:

```text
SHARMA TRADERS

Rice          25 KG       ₹1850
PRLE G        20 PKT       ₹200
Harpic         6 BTL       ₹540
Clinic Plus    8 PCS       ₹720
Sugar         15 KG        ₹720
Soap          24 PCS       ₹960
```

There must be **no hardcoded assumption that receipts contain particular products**.

The receipt parser must support an arbitrary number of line items.

---

# 2. Main User Capabilities

The system should eventually allow a retailer to:

### Receipt ingestion

Upload/send a receipt image.

The system should:

1. Determine whether the image is actually a receipt/invoice.
2. Reject unrelated images.
3. Extract receipt metadata.
4. Extract every line item.
5. Normalize the extracted data.
6. Resolve product identities.
7. Store the purchase history.
8. Make the data available for future queries and analytics.

### Natural-language queries

Examples:

> How much rice did I buy last month?

> What was my average buying price for Harpic in the last 9 months?

> Which supplier gave me the cheapest Parle-G?

> How much did I spend on shampoo this month?

> Find the receipt where I bought that biscuit for around ₹10.

> Show my rice purchases from Sharma Traders.

### Dashboard

Eventually provide:

- Product selection
- Monthly purchase quantity
- Monthly spending
- Average buying price
- Minimum/maximum price
- Supplier comparison
- Price trends
- 9-month historical trends
- Purchase frequency
- Other useful purchase analytics

---

# 3. Architecture

Use this architecture:

```text
                         USER
                          │
                          ▼
                     Discord
                  (added later)
                          │
                          ▼
                   Backend API
                          │
              ┌───────────┴───────────┐
              │                       │
        Receipt Image             User Query
              │                       │
              ▼                       ▼
     Receipt Classifier              LLM
              │                       │
          ┌───┴───┐             Query Router
          │       │                   │
         NO      YES          ┌───────┼────────┐
          │       │           ▼       ▼        ▼
       Reject   Gemini       SQL     RAG    SQL+RAG
                  Vision       │       │        │
                   │           └───────┼────────┘
                   ▼                   │
             Structured JSON           │
                   │                   │
                   ▼                   │
             Validation                │
                   │                   │
                   ▼                   │
          Product Resolution            │
                   │                   │
                   ▼                   │
          PostgreSQL + pgvector ◄──────┘
                   │
                   ▼
                  LLM
                   │
             ┌─────┴─────┐
             ▼           ▼
          Discord    Dashboard
         (later)
```

---

# 4. Development Order

Follow this order strictly.

## Phase 0 — Inspect and plan

Before writing code:

- Inspect the existing repository.
- Understand the current structure.
- Identify existing useful code.
- Do not unnecessarily rewrite working components.
- Decide the simplest reasonable backend structure.
- Document assumptions.

Then provide a short implementation plan.

### CHECKPOINT 0

Before proceeding, verify:

- Architecture is clear.
- Dependencies are justified.
- No unnecessary services are being introduced.
- Backend can run independently of Discord.

---

# 5. Phase 1 — Backend foundation

Build the backend first.

Create:

- API server
- Configuration/environment handling
- Database connection
- Logging
- Error handling
- Health endpoint
- Basic project structure
- Database migrations/schema

Keep the backend independent from Discord.

It must be possible to test everything using HTTP requests, scripts, or local test fixtures.

### CHECKPOINT 1

Run:

- Server startup test
- Database connection test
- Health endpoint test
- Basic API tests
- Lint/type checks where applicable

Fix failures before moving forward.

---

# 6. Phase 2 — Database

Use:

**PostgreSQL + pgvector**

Do NOT introduce a separate vector database unless there is a demonstrated technical reason.

PostgreSQL is the source of truth.

Design a schema around:

```text
products
product_aliases
suppliers
receipts
purchase_items
product_variants
alias_observations
```

The exact schema can be refined after inspecting the requirements.

A purchase item should be able to represent:

```text
product
quantity
unit
unit_price
line_total
```

Support pack/variant information where necessary.

For example:

```text
Product: Shampoo
Pack size: 180
Pack unit: ml
Quantity: 12
Purchase unit: bottle
Unit price: ₹85
```

Do not assume every product uses kg/litre/pieces.

Units could include:

- kg
- g
- litre
- ml
- piece
- packet
- box
- bottle
- dozen
- etc.

### CHECKPOINT 2

Test:

- Insert supplier
- Insert receipt
- Insert arbitrary number of purchase items
- Insert different units
- Retrieve receipt
- Retrieve product history
- Verify relationships and constraints
- Verify pgvector functionality

---

# 7. Phase 3 — Receipt classification

Before expensive extraction, determine whether the uploaded image is actually a supplier receipt/invoice.

Do NOT classify something as a receipt merely because it contains numbers.

Examples:

### Receipt

```text
Sharma Traders

Rice       25kg    1850
Soap       12pcs    480

Total             2330
```

→ receipt

### Random image

```text
OTP: 928371
Order ID: 823817
```

→ not a receipt

### Calculator photograph

```text
250
+450
+800
=1500
```

→ not a receipt

### Handwritten supplier bill

```text
Rice 10kg 700
Soap 12 480
Total 1180
```

→ receipt

The classifier should return something conceptually similar to:

```json
{
  "is_receipt": true,
  "confidence": 0.95,
  "document_type": "supplier_purchase_receipt"
}
```

Use confidence thresholds, but **do not blindly assume particular threshold values are optimal**. Make them configurable and test them.

### Performance requirement

Do not automatically perform multiple expensive vision/LLM calls for every image.

If a lightweight classifier can reliably perform the gate, prefer it.

If the initial implementation uses the same vision model for classification and extraction, benchmark the latency and optimize later.

### CHECKPOINT 3

Create a small test set containing:

- Genuine printed receipts
- Handwritten receipts
- Random photographs
- Screenshots
- Number-heavy non-receipt images
- Poor-quality receipts

Measure:

- False acceptance
- False rejection
- Latency

Fix obvious failures before continuing.

---

# 8. Phase 4 — Receipt extraction

Use multimodal vision/document understanding.

The system must extract:

- Supplier
- Date
- Invoice number if available
- Receipt total if available
- Every line item

The output must support arbitrary `N` items.

Example:

```json
{
  "supplier": "Sharma Traders",
  "date": "2026-09-29",
  "invoice_number": "ST-4821",
  "items": [
    {
      "raw_name": "RICE",
      "quantity": 25,
      "unit": "kg",
      "unit_price": 74,
      "line_total": 1850
    },
    {
      "raw_name": "PRLE G",
      "quantity": 20,
      "unit": "packet",
      "unit_price": 10,
      "line_total": 200
    }
  ],
  "receipt_total": 2050
}
```

Do not hardcode product categories.

Do not assume every receipt has the same structure.

Do not assume every field exists.

Handle missing/uncertain fields gracefully.

---

# 9. Phase 5 — Validation and normalization

Validate extracted data before persistence.

Examples:

```text
quantity × unit_price ≈ line_total
```

and:

```text
sum(line totals) ≈ receipt total
```

Account for:

- discounts
- taxes
- rounding
- missing totals
- handwritten ambiguity

Do not reject an otherwise valid receipt solely because totals cannot be perfectly reconciled.

Give extracted data confidence information where useful.

### CHECKPOINT 4

Test with:

- Single-item receipt
- 5-item receipt
- 20+ item receipt
- Handwritten receipt
- Mixed product categories
- Missing fields
- Incorrect/ambiguous OCR
- Discount/tax receipts

Verify that arbitrary line items are preserved.

---

# 10. Phase 6 — Product Entity Resolution

This is a major feature.

Different suppliers may write the same product differently:

```text
PARLE G
PARLE-G
PARLEG
PRLE G
P-G BIS
```

These may all represent:

```text
Parle-G
```

The resolver should use a cascade:

```text
1. Exact alias
        ↓
2. Normalized string match
        ↓
3. Fuzzy matching
        ↓
4. Semantic similarity / pgvector
        ↓
5. Contextual reasoning
        ↓
6. Human confirmation when genuinely ambiguous
```

Optimize this aggressively.

Known aliases should be resolved with cheap database lookups.

Do not invoke embeddings or LLMs unnecessarily.

---

# 11. Supplier context

Supplier is useful context but must NOT be treated as the global identity of a product.

Example:

```text
Supplier A:
PG → Parle-G

Supplier B:
PG → Parle-G

Supplier C:
PG → Priya Gold
```

Therefore do not permanently create:

```text
PG = Parle-G
```

without considering context.

Store observations and confidence.

---

# 12. Self-learning aliases

The system should gradually learn store-specific vocabulary.

Example:

```text
PRLE G
```

appears repeatedly in receipts and consistently corresponds to Parle-G.

The system can create:

```text
PRLE G → Parle-G
```

with a confidence score.

Use multiple signals:

- Frequency
- String similarity
- Semantic similarity
- Supplier context
- Pack size
- Unit
- Price
- Historical mappings
- Previous confirmations

Repetition alone must NOT be sufficient.

High-confidence mappings may be automatically accepted.

Ambiguous mappings should remain candidates or request confirmation.

Preserve the original raw product name for traceability.

### CHECKPOINT 5

Test:

- Same product with different spellings
- Same abbreviation across suppliers
- Same abbreviation representing different products
- New unseen product
- Repeated unknown aliases
- Ambiguous mappings

Verify that the resolver does not incorrectly collapse unrelated products.

---

# 13. Phase 7 — Persistence

Once validated and resolved:

Store:

```text
Receipt
 ├── Supplier
 ├── Date
 ├── Invoice
 ├── Total
 └── Purchase Items
       ├── Product
       ├── Variant
       ├── Quantity
       ├── Unit
       ├── Unit Price
       └── Line Total
```

Also preserve:

- Raw extracted name
- Original receipt image/reference
- Confidence information
- Alias-resolution information where useful

This provides traceability.

---

# 14. Phase 8 — Embeddings and RAG

Use pgvector inside PostgreSQL.

RAG is NOT the default mechanism for every query.

PostgreSQL remains the source of truth.

Use RAG for:

- Semantic receipt retrieval
- Contextual historical searches
- Similar receipts
- Ambiguous natural-language references
- Queries where exact keyword matching is insufficient

Examples:

> Find the receipt where I bought that biscuit for around ₹10.

> Find receipts similar to this purchase.

> Show receipts related to snacks from last month.

Do NOT use RAG to perform exact financial calculations.

---
 # 15 RAG / RETRIEVAL EVALUATION
--------------------------
Before considering semantic retrieval complete:

1. Create a small gold test set of representative queries.
2. Include:
   - exact product queries
   - misspelled product names
   - supplier-specific queries
   - ambiguous aliases such as "PG"
   - date-constrained queries
   - receipt-finding queries
   - mixed SQL + semantic queries
3. Compare:
   - exact/normalized matching
   - fuzzy matching
   - pgvector retrieval
   - hybrid retrieval
4. Measure retrieval hit rate / Recall@K and latency.
5. Keep the cheapest method that reliably answers each query.
6. Only add reranking if evaluation shows it materially improves retrieval.
7. Do not use vector retrieval for exact numerical aggregation.
# 16. Phase 9 — LLM Query Layer

The LLM acts as the query interpreter/orchestrator.

It determines whether the request needs:

```text
SQL
RAG
SQL + RAG
```

Examples:

### SQL

> How much rice did I buy last month?

### SQL

> What was my average rice buying price over 9 months?

### SQL

> Which supplier charged me the most for sugar?

### RAG

> Find the receipt where I bought that biscuit around ₹10.

### SQL + RAG

> Find the rice receipts from Sharma Traders and tell me whether my buying price has increased recently.

---

# 17. Critical performance rule

Never retrieve thousands of rows and ask the LLM to perform calculations itself.

Instead:

```text
User
 ↓
LLM
 ↓
SQL
 ↓
PostgreSQL performs aggregation
 ↓
Small result
 ↓
LLM explains result
```

For example:

```sql
AVG(unit_price)
SUM(quantity)
SUM(line_total)
MIN(unit_price)
MAX(unit_price)
```

should be calculated by PostgreSQL.

The LLM should receive the result, not the entire dataset.

---

# 18. Query performance

Optimize for fast responses.

Use:

- Database indexes
- Proper query plans
- Connection pooling
- Cached product mappings
- Cached common queries where appropriate
- Precomputed monthly statistics/materialized views where useful
- pgvector indexes where appropriate
- Minimal LLM calls
- Minimal retrieved context
- Async receipt processing

The goal is:

> **Expensive AI processing happens when the receipt is ingested. Historical queries should be cheap and fast.**

Do not optimize prematurely with unnecessary infrastructure. Benchmark first.

### CHECKPOINT 6

Create representative data and benchmark:

- Exact product lookup
- Monthly aggregation
- 9-month price trend
- Supplier comparison
- Semantic retrieval
- SQL + RAG query
- End-to-end query latency

Identify slow operations and optimize them.

---

# 19. Phase 10 — Analytics backend

Create backend endpoints/services for dashboard analytics.

Examples:

```text
GET product/:id/history
GET product/:id/monthly-stats
GET product/:id/suppliers
GET spending/monthly
GET purchases/recent
```

Support:

- Average buying price
- Quantity purchased
- Total spending
- Min/max price
- Supplier comparison
- Monthly trends
- Purchase count
- Price change

Where appropriate, use precomputed monthly statistics.

---

# 20. Phase 11 — Backend end-to-end test

Before Discord integration, prove the backend works independently.

Use a test receipt containing random products:

```text
Rice
Harpic
Shampoo
Soap
Biscuits
Sugar
```

and arbitrary quantities/units.

Verify:

```text
Image
 ↓
Receipt classification
 ↓
Extraction
 ↓
Validation
 ↓
Product resolution
 ↓
PostgreSQL
 ↓
Embeddings
 ↓
Natural-language query
 ↓
SQL/RAG
 ↓
Correct answer
```

### CHECKPOINT 7 — HARD GATE

Do NOT begin Discord integration until:

- Receipt classification works
- Arbitrary line-item extraction works
- Database persistence works
- Product resolution works
- Alias learning works sufficiently
- SQL queries work
- RAG retrieval works
- Analytics endpoints work
- Backend tests pass
- Basic latency benchmarks are acceptable

If something fails, fix it before proceeding.

---

# 21. Phase 12 — Discord integration

Only now add Discord.

Discord should be a **thin adapter**, not part of the core business logic.

The backend should remain usable without Discord.

Discord functionality:

### Receipt channel

User uploads an image.

Bot:

```text
Receipt received ✓
Processing...
```

If invalid:

```text
❌ This doesn't appear to be a supplier receipt.
Please upload a clear receipt image.
```

If successful:

```text
✅ Receipt processed.

7 items added
Total purchase: ₹4,280
```

### Query channel

User:

```text
How much Harpic did I buy in the last 3 months?
```

Bot calls the backend and returns the result.

Do NOT put database logic directly inside the Discord bot.

Discord → Backend API → Intelligence system.

### CHECKPOINT 8

Test:

- Receipt upload
- Invalid image
- Handwritten receipt
- Multi-item receipt
- Natural-language query
- Error handling
- Multiple users/retailers if multi-user support is implemented

---

# 22. Final dashboard

Build the dashboard after the backend is proven.

The dashboard should consume analytics APIs.

It should not independently duplicate business logic.

Suggested layout:

```text
┌──────────────────────────────────────────────┐
│              Receipt Intelligence             │
├──────────────────────────────────────────────┤
│ Product: [ Rice ▼ ]                          │
├──────────────┬──────────────┬────────────────┤
│ Avg Price    │ Qty Bought   │ Total Spent    │
│ ₹XX          │ XXX kg       │ ₹XX,XXX        │
├──────────────┴──────────────┴────────────────┤
│                                              │
│       9-Month Buying Price Trend             │
│                                              │
├──────────────────────────────────────────────┤
│                                              │
│       Monthly Quantity Purchased             │
│                                              │
├──────────────────────┬───────────────────────┤
│ Supplier Comparison  │ Purchase Frequency    │
└──────────────────────┴───────────────────────┘
```

Keep it clean and useful rather than adding unnecessary widgets.

---

# 23. Security and reliability

At minimum:

- Environment variables for API keys
- Never hardcode secrets
- Validate uploaded files
- Restrict accepted image formats
- Reasonable file-size limits
- API error handling
- Database constraints
- Logging without exposing secrets
- User/store isolation if multiple retailers are supported

Do not build unnecessary authentication complexity before the core system works.

---

# 24. What NOT to build

Do NOT add:

- WhatsApp integration
- Grok
- Multiple unnecessary LLM providers
- Separate vector database
- Complex microservices
- Kubernetes
- Event-driven infrastructure unless genuinely needed
- Unnecessary agents
- RAG for simple SQL calculations
- LLM-based arithmetic
- Hardcoded product categories
- Hardcoded single-item receipt assumptions

Keep the architecture understandable.

---

# 25. Engineering rule: regular checks

Do not build the entire application and test only at the end.

After every major phase:

1. Run tests.
2. Test a realistic example.
3. Check errors/logs.
4. Check database state.
5. Check latency where relevant.
6. Fix failures.
7. Only then move to the next phase.

Maintain a simple progress checklist:

```text
[x] Repository inspected
[x] Backend foundation
[x] PostgreSQL
[x] pgvector
[x] Receipt classifier
[x] Receipt extraction
[x] Validation
[x] Product resolver
[x] Alias learning
[x] Persistence
[x] SQL query layer
[x] RAG
[x] LLM orchestration
[x] Performance optimization
[x] Analytics API
[x] End-to-end backend test
[x] Discord adapter
[x] Dashboard
[x] Final testing
```

At each checkpoint, report:

```text
STATUS
------
Completed:
...

Tests:
...

Passed:
...

Failed:
...

Performance:
...

Issues:
...

Next:
...
```

Do not hide failures.

Do not claim a component works without actually testing it.

---

# 26. Final technical principle

The system should follow this philosophy:

```text
Gemini Vision
    ↓
Understand receipt once
    ↓
Structured purchase data
    ↓
PostgreSQL = source of truth
    ↓
pgvector = semantic memory
    ↓
LLM = query understanding/orchestration
    ↓
SQL = exact calculations
    ↓
RAG = contextual retrieval
    ↓
Discord + Dashboard = interfaces
```

The most important performance principle is:

> **Process expensive information once, store it properly, and make future queries cheap.**

The most important data principle is:

> **Never assume what products a receipt contains. Every receipt may contain an arbitrary number of arbitrary products.**

The most important AI principle is:

> **Use the LLM where language/vision reasoning is needed, not where PostgreSQL can do the job faster and more reliably.**

Start with **Phase 0: inspect the repository and report the existing structure and proposed implementation plan. Do not start Discord integration.**