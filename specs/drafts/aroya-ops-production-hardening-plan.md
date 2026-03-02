# AROYA-OPS Production Hardening Plan

## Current State

**What it is:** A sales execution dashboard for AROYA's Horticulture business unit. Live Salesforce data, AI-augmented pipeline analysis (McMahon chat interface), CRO prep sheets, rep drill-downs.

**Stack:** Node.js server (2,463-line monolith), Python SFDC extraction, React 18 via CDN (no build step), Bash cron orchestration. Zero npm dependencies. Zero database.

**Data flow today:**
```
Cron (6 AM) -> sfdc_extract.py -> 5 JSON files -> daily-pipeline.sh packages -> forecast-data-latest.json -> server.js reads from disk (60s cache) -> dashboard + McMahon chat
```

**What works well:**
- SFDC extraction is robust (OAuth2 + SOAP fallback, pagination)
- McMahon chat has a real query tool (query_sfdc_data) with Claude tool_use
- RAG layer extracts insights from conversations, builds deal intel
- CRO prep sheet auto-generates deal-by-deal analysis
- MCP endpoint works for Claude Desktop users

**What doesn't scale:**
- All data is flat JSON files, re-parsed every 60 seconds
- No schema, no validation, no relational queries
- SOQL queries are hardcoded for Horticulture class, FY26 date ranges
- Server.js is a 2,463-line monolith (auth, routes, data, chat, MCP, RAG all mixed)
- Quotas are hardcoded in server.js AND in quotas.csv (dual source)
- Only runs on Haze via ngrok tunnel. No resilience.
- Zero tests. Zero alerting on extraction failure.
- RAG data (insights, sessions, deal-intel) grows unbounded with naive pruning

---

## The Seven Workstreams

### 1. Flat Files to Postgres

**What changes:** Replace all JSON file I/O with Postgres. The database becomes the single source of truth. server.js reads from Postgres, not from disk.

**Schema design (initial tables):**

```sql
-- Core Salesforce objects
CREATE TABLE opportunities (
    sfdc_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    account_id      TEXT REFERENCES accounts(sfdc_id),
    owner_id        TEXT REFERENCES users(sfdc_id),
    stage_name      TEXT,
    amount          NUMERIC(12,2),
    close_date      DATE,
    forecast_category TEXT,
    forecast_category_name TEXT,
    probability     NUMERIC(5,2),
    type            TEXT,
    lead_source     TEXT,
    created_date    TIMESTAMPTZ,
    last_modified   TIMESTAMPTZ,
    last_activity   DATE,
    last_stage_change DATE,
    has_open_activity BOOLEAN,
    has_overdue_task BOOLEAN,
    push_count      INTEGER DEFAULT 0,
    is_closed       BOOLEAN DEFAULT FALSE,
    is_won          BOOLEAN DEFAULT FALSE,
    primary_quote   TEXT,
    synced_quote    TEXT,
    discovery_completed BOOLEAN,
    roi_completed   BOOLEAN,
    budget_confirmed BOOLEAN,
    contact_id      TEXT,
    primary_contact TEXT,
    class           TEXT,
    category        TEXT,
    description     TEXT,
    loss_reason     TEXT,
    -- Extraction metadata
    extracted_at    TIMESTAMPTZ NOT NULL,
    extraction_batch TEXT NOT NULL
);

CREATE TABLE accounts (
    sfdc_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    billing_state   TEXT,
    -- Future: industry, employee_count, annual_revenue, etc.
    extracted_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE users (
    sfdc_id         TEXT PRIMARY KEY,
    first_name      TEXT,
    last_name       TEXT,
    name            TEXT NOT NULL,
    email           TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    extracted_at    TIMESTAMPTZ NOT NULL
);

-- Quotas (replace CSV + hardcoded values)
CREATE TABLE quotas (
    rep_name        TEXT NOT NULL,
    month           TEXT NOT NULL,  -- YYYY-MM
    amount          NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (rep_name, month)
);

-- Board plan targets
CREATE TABLE board_plan (
    month           TEXT PRIMARY KEY,  -- YYYY-MM
    target          NUMERIC(12,2) NOT NULL
);

-- Pipeline snapshots for build rate analysis
CREATE TABLE pipeline_snapshots (
    snapshot_date   DATE NOT NULL,
    sfdc_id         TEXT NOT NULL,
    owner_id        TEXT,
    amount          NUMERIC(12,2),
    close_date      DATE,
    stage_name      TEXT,
    forecast_category TEXT,
    created_date    TIMESTAMPTZ,
    PRIMARY KEY (snapshot_date, sfdc_id)
);

-- Extraction audit trail
CREATE TABLE extractions (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL,  -- running, success, failed
    records_extracted JSONB,  -- {pipeline: 45, won: 120, ...}
    error_message   TEXT,
    batch_id        TEXT UNIQUE NOT NULL
);

-- RAG: insights (replace insights.jsonl)
CREATE TABLE rag_insights (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    category        TEXT,
    content         TEXT NOT NULL,
    source          TEXT  -- manual, chat, review
);

-- RAG: deal intel (replace deal-intel.json)
CREATE TABLE rag_deal_intel (
    id              SERIAL PRIMARY KEY,
    account_name    TEXT NOT NULL,
    recorded_at     DATE NOT NULL,
    verdict         TEXT,
    amount          NUMERIC(12,2),
    push_count      INTEGER,
    note            TEXT,
    rep_name        TEXT
);

-- RAG: sessions (replace sessions.jsonl)
CREATE TABLE rag_sessions (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    summary         TEXT
);

-- Reconciliation results
CREATE TABLE reconciliation_runs (
    id              SERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    status          TEXT NOT NULL,  -- pass, drift, error
    pipeline_count_sfdc INTEGER,
    pipeline_count_db INTEGER,
    pipeline_amount_sfdc NUMERIC(12,2),
    pipeline_amount_db NUMERIC(12,2),
    drift_details   JSONB,
    duration_ms     INTEGER
);
```

**Migration path:**
1. Stand up Postgres on Kush (Docker, same pattern as Qdrant)
2. Create schema via migration scripts (use raw SQL, no ORM overhead)
3. Backfill from existing JSON files (one-time load script)
4. Verify row counts and totals match JSON data exactly
5. Cut over extraction to write to Postgres
6. Cut over server.js to read from Postgres
7. Keep JSON files as backup for 30 days, then remove

**Postgres on Kush rationale:** Kush already runs Qdrant, forge-brain, Sentinel, classifier. Adding Postgres is one more Docker container. Kush has the capacity. When Runtz arrives, Postgres can move there.

---

### 2. Rewire SFDC Extract for Full Opportunity + Account Data

**What changes:** sfdc_extract.py gets rewritten to:
- Write directly to Postgres (psycopg2) instead of JSON files
- Pull Account objects as a separate entity (not just nested on Opportunity)
- Expand field coverage to support detailed pipeline analysis
- Use batch upsert (INSERT ON CONFLICT UPDATE) so extractions are idempotent

**New SOQL queries:**

Current queries only pull Horticulture class, FY26 date range. For production:

1. **Opportunities (all)** -- Remove class filter. Add fields: NextStep, Campaign, OpportunitySource. Expand date range to include FY24-FY26 for historical analysis. Use LAST_N_DAYS or parameterized dates.

2. **Accounts** -- New standalone query:
```sql
SELECT Id, Name, BillingState, BillingCity, Industry,
       Type, OwnerId, CreatedDate, LastModifiedDate,
       NumberOfEmployees, AnnualRevenue
FROM Account
WHERE Id IN (SELECT AccountId FROM Opportunity
             WHERE Class_Opportunity_GT__c = 'Horticulture')
```

3. **Contacts** -- New query for primary contacts on opportunities:
```sql
SELECT Id, FirstName, LastName, Email, Phone, Title, AccountId
FROM Contact
WHERE AccountId IN (SELECT AccountId FROM Opportunity
                    WHERE Class_Opportunity_GT__c = 'Horticulture')
```

4. **Pipeline snapshots** -- Keep existing logic but write to pipeline_snapshots table instead of JSON

5. **Users** -- Same query, write to users table

**Extraction lifecycle:**
```
1. Insert extractions row (status=running, batch_id=uuid)
2. Authenticate to SFDC
3. Query each object, upsert to Postgres
4. Update extractions row (status=success, record counts)
5. On failure: update extractions row (status=failed, error_message)
```

---

### 3. Build Views for Fast Pipeline Analysis

**What changes:** Create Postgres materialized views and indexes that pre-compute the aggregations server.js currently does in JavaScript on every request.

**Core views:**

```sql
-- Current month forecast by rep
CREATE MATERIALIZED VIEW mv_forecast_current AS
SELECT
    u.name AS rep,
    SUM(CASE WHEN o.is_closed AND o.is_won THEN o.amount ELSE 0 END) AS closed_won,
    SUM(CASE WHEN NOT o.is_closed AND o.forecast_category_name = 'Commit' THEN o.amount ELSE 0 END) AS commit_amount,
    SUM(CASE WHEN NOT o.is_closed AND o.forecast_category_name = 'Best Case' THEN o.amount ELSE 0 END) AS best_case_amount,
    SUM(CASE WHEN NOT o.is_closed AND o.forecast_category_name = 'Pipeline' THEN o.amount ELSE 0 END) AS pipeline_amount,
    COUNT(*) FILTER (WHERE NOT o.is_closed) AS open_deal_count
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
WHERE DATE_TRUNC('month', o.close_date) = DATE_TRUNC('month', CURRENT_DATE)
  AND o.class = 'Horticulture'
  AND o.type != 'Renewal'
GROUP BY u.name;

-- YTD closed won by rep (FY26 = Sep 2025 - Aug 2026)
CREATE MATERIALIZED VIEW mv_ytd_won AS
SELECT
    u.name AS rep,
    COUNT(*) AS deal_count,
    SUM(o.amount) AS total_amount,
    AVG(o.amount) AS avg_deal_size,
    AVG(EXTRACT(EPOCH FROM (o.close_date - o.created_date::date)) / 86400) AS avg_days_to_close
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
WHERE o.is_closed AND o.is_won
  AND o.close_date >= '2025-09-01' AND o.close_date <= '2026-08-31'
  AND o.class = 'Horticulture' AND o.type != 'Renewal'
GROUP BY u.name;

-- Pipeline health (risks, zombies, stalled)
CREATE MATERIALIZED VIEW mv_pipeline_risks AS
SELECT
    o.sfdc_id,
    o.name,
    a.name AS account_name,
    u.name AS rep,
    o.amount,
    o.stage_name,
    o.forecast_category_name,
    o.push_count,
    o.last_activity,
    o.close_date,
    o.primary_quote IS NOT NULL AS has_quote,
    CASE
        WHEN o.push_count >= 7 THEN 'dead'
        WHEN o.push_count >= 5 THEN 'zombie'
        WHEN o.push_count >= 3 THEN 'at_risk'
        WHEN o.last_activity IS NULL AND o.stage_name = 'Negotiation' THEN 'stalled'
        ELSE 'healthy'
    END AS risk_level
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
LEFT JOIN accounts a ON o.account_id = a.sfdc_id
WHERE NOT o.is_closed
  AND o.class = 'Horticulture' AND o.type != 'Renewal';

-- Win rate by rep (trailing 12 months)
CREATE MATERIALIZED VIEW mv_win_rates AS
SELECT
    u.name AS rep,
    COUNT(*) FILTER (WHERE o.is_won) AS wins,
    COUNT(*) FILTER (WHERE o.is_closed AND NOT o.is_won) AS losses,
    ROUND(COUNT(*) FILTER (WHERE o.is_won)::NUMERIC /
          NULLIF(COUNT(*) FILTER (WHERE o.is_closed), 0), 3) AS win_rate
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
WHERE o.is_closed
  AND o.close_date >= CURRENT_DATE - INTERVAL '12 months'
  AND o.class = 'Horticulture' AND o.type != 'Renewal'
GROUP BY u.name;

-- Monthly pipeline build rate
CREATE MATERIALIZED VIEW mv_build_rate AS
SELECT
    TO_CHAR(o.created_date, 'YYYY-MM') AS created_month,
    u.name AS rep,
    COUNT(*) AS new_deals,
    SUM(o.amount) AS new_amount
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
WHERE o.created_date >= '2025-09-01'
  AND o.class = 'Horticulture' AND o.type != 'Renewal'
GROUP BY TO_CHAR(o.created_date, 'YYYY-MM'), u.name;

-- Renewal health
CREATE MATERIALIZED VIEW mv_renewals AS
SELECT
    u.name AS rep,
    COUNT(*) FILTER (WHERE NOT o.is_closed) AS open_count,
    SUM(o.amount) FILTER (WHERE NOT o.is_closed) AS open_amount,
    COUNT(*) FILTER (WHERE o.is_closed AND o.is_won) AS won_count,
    SUM(o.amount) FILTER (WHERE o.is_closed AND o.is_won) AS won_amount,
    COUNT(*) FILTER (WHERE NOT o.is_closed AND o.close_date < CURRENT_DATE) AS overdue_count,
    SUM(o.amount) FILTER (WHERE NOT o.is_closed AND o.close_date < CURRENT_DATE) AS overdue_amount
FROM opportunities o
JOIN users u ON o.owner_id = u.sfdc_id
WHERE o.type = 'Renewal'
  AND o.class = 'Horticulture'
  AND o.close_date >= '2025-09-01'
GROUP BY u.name;
```

**Indexes:**
```sql
CREATE INDEX idx_opp_close_date ON opportunities(close_date);
CREATE INDEX idx_opp_owner ON opportunities(owner_id);
CREATE INDEX idx_opp_class_type ON opportunities(class, type);
CREATE INDEX idx_opp_forecast ON opportunities(forecast_category_name) WHERE NOT is_closed;
CREATE INDEX idx_opp_created ON opportunities(created_date);
CREATE INDEX idx_opp_closed ON opportunities(is_closed, is_won);
```

**Refresh strategy:** Materialized views refresh after every extraction (daily at 6 AM, hourly during reconciliation). One SQL command: `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_*`.

---

### 4. Learning Logic for New Views from McMahon Queries

**What this means:** When Brad asks McMahon ad hoc questions that require custom aggregations, the system should learn from those queries and create optimized views so future similar questions are instant.

**Architecture:**

```
Brad asks McMahon: "Show me all deals that pushed 3+ times in Q1 and are still open"
                              |
McMahon uses query_sfdc_data tool -> SQL query executes against Postgres
                              |
Post-query: extract the WHERE/GROUP BY pattern -> store as "query pattern"
                              |
If a pattern recurs 3+ times (or Brad says "remember this view"):
  -> Auto-generate a materialized view
  -> Register it in a view_registry table
  -> Future McMahon queries check registry first
```

**Implementation:**

```sql
CREATE TABLE query_patterns (
    id              SERIAL PRIMARY KEY,
    pattern_hash    TEXT UNIQUE NOT NULL,  -- hash of normalized query shape
    sql_template    TEXT NOT NULL,
    description     TEXT,
    occurrence_count INTEGER DEFAULT 1,
    last_used       TIMESTAMPTZ DEFAULT NOW(),
    materialized    BOOLEAN DEFAULT FALSE,
    view_name       TEXT  -- populated when materialized
);
```

**The query_sfdc_data tool gets rewritten:**
- Current: filters JSON arrays in JavaScript
- New: generates and executes SQL against Postgres
- After execution: logs the query pattern
- If pattern count >= 3: auto-creates a materialized view

**McMahon "remember this" command:**
- Existing: `remember: <text>` saves to RAG insights
- New: `remember this view: <description>` creates a named materialized view from the last query
- The view is registered, refreshed on extraction, and available as a fast path for future queries

---

### 5. Move Host from Haze to Kush

**What changes:** The aroya-ops server moves from Haze (Brad's laptop) to Kush (always-on infrastructure machine). Dashboard stays available even when Brad closes his laptop.

**Migration steps:**

1. **Postgres is already on Kush** (from workstream 1)
2. **Deploy server.js to Kush:**
   - Copy ~/aroya-ops to Kush
   - Install Node.js on Kush (or use Docker)
   - Configure .env with SFDC credentials and ANTHROPIC_API_KEY
   - Run server on port 8401 (same as today)
3. **Ngrok tunnel moves to Kush:**
   - Install ngrok on Kush
   - Transfer aroyafc.ngrok.app domain config
   - Update serve.sh for Kush paths
4. **Cron job moves to Kush:**
   - daily-pipeline.sh runs on Kush
   - sfdc_extract.py runs on Kush (writes to local Postgres)
   - Python venv rebuilt on Kush (add psycopg2)
5. **Launchd -> systemd:**
   - Kush runs Linux, not macOS
   - Create systemd unit files for: server, ngrok, cron equivalent (systemd timer)
6. **DNS/firewall:**
   - Kush is on local network (192.168.1.100)
   - Ngrok handles external access (same domain)
   - No firewall changes needed

**Rollback:** Keep Haze copy running for 1 week after migration. Verify ngrok tunnel stability, cron execution, and dashboard responsiveness before decommissioning Haze copy.

---

### 6. Playwright QA Scripts

**What gets tested:**

**Dashboard tabs (from index.html):**
- Gap Bridge tab loads, shows forecast numbers
- Rep drill-down tabs load (Liam, Francis)
- Execution tab loads
- Quota tab loads with bubble-bar visualizations
- Renewals tab loads
- Ask AI (McMahon) tab opens chat interface

**McMahon chat:**
- Send a message, receive a streaming response
- Query tool fires (check for tool_use in response)
- "remember:" prefix saves to RAG
- Session continuity (follow-up questions reference context)

**CRO prep sheet:**
- /cro endpoint loads with valid token
- Prep data populates (rep scorecards, deal table, kill list, top 5 questions)
- Refresh button triggers new prep generation

**API endpoints:**
- GET /api/data returns forecast data with valid token
- GET /api/summary returns summary snapshot
- POST /api/chat streams response
- POST /api/query-sfdc returns structured query results
- SSE /sse delivers events
- MCP /mcp responds to JSON-RPC

**Auth:**
- Invalid token returns denied page
- Valid token returns dashboard
- CRO code generates session token
- Expired CRO token returns denied

**Data integrity:**
- Pipeline count matches between /api/data and /api/summary
- Rep names in data match known reps
- Dollar amounts are non-negative
- Close dates are valid ISO dates

**Reconciliation dashboard (new, from workstream 7):**
- Shows latest reconciliation status
- Pass/drift indicator is correct
- Drift details are viewable

**Test structure:**
```
tests/
  e2e/
    dashboard.spec.ts      # Tab navigation, data rendering
    mcmahon.spec.ts        # Chat, query tool, RAG
    cro-prep.spec.ts       # CRO prep sheet flow
    api.spec.ts            # REST endpoint contracts
    auth.spec.ts           # Token validation
    reconciliation.spec.ts # Recon dashboard
  fixtures/
    test-data.json         # Known-good dataset for deterministic tests
```

**Environment:** Tests run against a test instance with a known dataset (not live SFDC). Fixture data loaded into Postgres before test suite. Tests are idempotent and can run in CI.

---

### 7. Retool Flat File Views into SQL

**What changes:** Every place server.js reads from JSON files and computes aggregations in JavaScript gets replaced with a SQL query against Postgres.

**Current file reads in server.js -> SQL replacements:**

| Current (JS) | New (SQL) |
|--------------|-----------|
| `loadData()` reads forecast-data-latest.json, caches 60s | Query Postgres directly. Connection pool, no cache needed (Postgres IS the cache with materialized views) |
| `buildSystemPrompt()` constructs data context by iterating JSON arrays | Query mv_forecast_current, mv_pipeline_risks, mv_ytd_won. Structured SQL output replaces string concatenation |
| `executeQueryTool()` filters JSON arrays with nested loops | Direct SQL query with parameterized WHERE clauses. group_by maps to SQL GROUP BY |
| `handleTool('get_forecast_summary')` iterates won/pipeline arrays | `SELECT * FROM mv_forecast_current` + join with quotas table |
| `handleTool('get_rep_detail')` filters by owner, sorts by amount | `SELECT * FROM opportunities WHERE owner_id = $1 AND ... ORDER BY amount DESC` |
| `handleTool('query_deals')` complex nested filter logic | Parameterized SQL: `WHERE ($1 IS NULL OR rep = $1) AND ($2 IS NULL OR amount >= $2) AND ...` |
| `handleTool('get_pipeline_risks')` filters by push count, activity | `SELECT * FROM mv_pipeline_risks WHERE risk_level IN ('zombie', 'stalled', 'dead')` |
| `loadRagContext()` reads insights.jsonl, deal-intel.json, sessions.jsonl | `SELECT * FROM rag_insights ORDER BY created_at DESC LIMIT 20` etc. |
| `extractInsights()` appends to insights.jsonl | `INSERT INTO rag_insights (category, content, source) VALUES ($1, $2, $3)` |
| `extractDealIntel()` reads/writes deal-intel.json | `INSERT INTO rag_deal_intel ... ON CONFLICT UPDATE` |
| Quota lookup from hardcoded REP_QUOTAS object | `SELECT * FROM quotas WHERE rep_name = $1 AND month = $2` |

**server.js refactoring strategy:**
- Add pg (node-postgres) as the single new dependency
- Create a db.js module with connection pool and query helpers
- Replace each loadData/handleTool function one at a time
- Keep the HTTP routing, auth, streaming chat, and MCP protocol unchanged
- The system prompt builder queries views instead of constructing strings from arrays

---

### 8. Hourly Reconciliation

**What it does:** Every hour, run a lightweight SFDC extract and compare against what's in Postgres. Report drift. Build confidence that the numbers humans see match live Salesforce.

**Reconciliation logic:**

```python
# reconcile.py (runs hourly via cron/systemd timer)

1. Authenticate to SFDC
2. Run: SELECT COUNT(*), SUM(Amount) FROM Opportunity
   WHERE Class = 'Horticulture' AND IsClosed = false AND Type != 'Renewal'
   -> sfdc_pipeline_count, sfdc_pipeline_amount

3. Query Postgres:
   SELECT COUNT(*), SUM(amount) FROM opportunities
   WHERE class = 'Horticulture' AND is_closed = false AND type != 'Renewal'
   -> db_pipeline_count, db_pipeline_amount

4. Compare:
   - count_drift = abs(sfdc_count - db_count)
   - amount_drift = abs(sfdc_amount - db_amount)

5. If count_drift > 0 OR amount_drift > $100:
   - Run detailed comparison: pull SFDC IDs, diff against DB IDs
   - Identify: new in SFDC (not in DB), modified in SFDC (amount/stage changed), deleted in SFDC (in DB but not SFDC)
   - Trigger a full re-extraction if drift > threshold

6. Write to reconciliation_runs table:
   - status: 'pass' or 'drift'
   - counts, amounts, drift details as JSONB

7. If drift detected: post alert to Leroy bus (leroy_send_message to pm)
```

**Dashboard widget:** The System tab (or a new Reconciliation section) shows:
- Last reconciliation: timestamp + status (green check / yellow warning / red alert)
- Drift history (last 24 hours, sparkline)
- Click to expand: which deals drifted, what changed

**Confidence score:** After N consecutive clean reconciliations, display a confidence indicator: "Pipeline numbers verified against live Salesforce. Last 24 checks: 24/24 clean."

---

## Execution Sequence

These workstreams have dependencies. Here's the build order:

```
Phase 1: Foundation (do first, everything depends on it)
├── 1. Postgres setup on Kush (Docker container, schema, backfill)
├── 2. Rewire sfdc_extract.py to write to Postgres
└── 5. Move host from Haze to Kush (server + cron + ngrok)

Phase 2: Data Layer (Postgres is live, now optimize)
├── 3. Build materialized views + indexes
├── 7. Retool server.js to read from Postgres (replace all JSON reads)
└── 8. Build reconciliation script + hourly cron

Phase 3: Intelligence (data layer is solid, now add learning)
└── 4. McMahon query pattern learning + auto-materialized views

Phase 4: Verification (everything works, now prove it)
└── 6. Playwright QA scripts (test against production-like environment)
```

**Phase 1 is 3-4 specs.** Phase 2 is 2-3 specs. Phase 3 is 1 spec. Phase 4 is 1 spec. Total: ~8-10 specs over probably 2-3 days of Leroy execution time.

---

## Open Questions for Brad

1. **Account data depth:** How deep do we go on Account objects? Basic (name, state, industry) or full (employee count, annual revenue, custom fields)? More fields = richer analysis but heavier extraction.

2. **Historical data range:** Currently pulling FY24-FY26. Want to keep that window or expand to all-time? Postgres can handle it, but more data = longer extraction.

3. **Contact data:** The schema includes contacts. Do you want contact-level analysis (who's the decision maker, title patterns on won deals)? Or skip contacts for now?

4. **McMahon query learning (workstream 4):** This is the most ambitious piece. The auto-materialized-view pattern is powerful but complex. Want to do it in v1 or defer to v2 after the core migration is solid?

5. **Quota management:** Currently quotas are hardcoded. Want a simple admin endpoint to update quotas, or keep them in a table that gets manually updated via SQL?

6. **Kush capacity:** Kush is already running Qdrant (4GB/2CPU), forge-brain, classifier, Sentinel. Adding Postgres + aroya-ops server + hourly recon. Is Kush specced for this, or should some services move to Runtz when it arrives?

7. **Ngrok stability:** The ngrok tunnel is the single point of external access. If Kush reboots, does ngrok auto-reconnect? Need a systemd watchdog for this.
