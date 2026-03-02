---
spec_id: aroya-ops-phase-1-postgres-on-kush-schema-sfdc-ext
task_id: d2369fbe-05de-40f4-a8e8-2176091d8880
date: 2026-03-02
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# Aroya-Ops Phase 1: Postgres on Kush + Schema + SFDC Extract Rewire

## Objective

Stand up a Postgres database on Kush, create the schema for aroya-ops pipeline data, and rewire the SFDC extract to write to Postgres instead of flat JSON files. This is the foundation for replacing the entire flat-file data layer with a proper database. All Horticulture opportunity and account data from Salesforce, full history, no date bounds.

## Why

The current aroya-ops stack reads and writes flat JSON files. Every query loads the entire dataset into memory. There are no indexes, no joins, no aggregations at the data layer. McMahon builds 12-15K tokens of context per chat message by serializing JSON. Moving to Postgres gives us: fast filtered queries, materialized views for common analysis patterns, proper upsert for incremental extraction, audit trail, and a foundation for the hourly reconciliation that certifies pipeline numbers against live Salesforce.

## Architecture

```
Kush (192.168.1.100)
  ├── Qdrant (Docker, port 6333)        -- existing, do not touch
  ├── forge-brain (port 8300/8301)      -- existing, do not touch
  ├── Postgres (Docker, port 5432)      -- NEW
  └── Sentinel (port 8200)              -- existing, do not touch

Haze (dev machine)
  └── ~/Projects/aroya-ops-new/         -- working copy
      ├── sfdc_extract.py               -- MODIFY: write to Postgres
      ├── .env                          -- ADD: Postgres connection string
      └── schema/                       -- NEW: SQL schema files
```

## Part 1: Postgres Container on Kush

SSH to Kush (`ssh kush`, user bradwood) and run:

```bash
/usr/local/bin/docker run -d \
  --name aroya-postgres \
  --restart unless-stopped \
  -e POSTGRES_DB=aroya_ops \
  -e POSTGRES_USER=aroya \
  -e POSTGRES_PASSWORD=<generate a secure password> \
  -p 5432:5432 \
  -v aroya-pg-data:/var/lib/postgresql/data \
  postgres:16-alpine
```

**CRITICAL:** Docker on Kush is at `/usr/local/bin/docker`, NOT in the default non-interactive SSH PATH. Always use the full path when running Docker commands via SSH.

Save the generated password to `~/Projects/aroya-ops-new/.env` on Haze as `POSTGRES_URL=postgresql://aroya:<password>@192.168.1.100:5432/aroya_ops`.

Verify connectivity from Haze: `psql $POSTGRES_URL -c "SELECT 1"` (install psql via brew if needed, or use Python psycopg2 to test).

## Part 2: Schema

Create `~/Projects/aroya-ops-new/schema/001_initial.sql`:

```sql
-- Extraction audit trail
CREATE TABLE extraction_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/failed
    opps_extracted INTEGER DEFAULT 0,
    accounts_extracted INTEGER DEFAULT 0,
    users_extracted INTEGER DEFAULT 0,
    error TEXT,
    duration_seconds REAL
);

-- Salesforce Users (for owner name resolution)
CREATE TABLE users (
    sfdc_id TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    name TEXT NOT NULL,
    email TEXT,
    is_active BOOLEAN DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Accounts
CREATE TABLE accounts (
    sfdc_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    billing_state TEXT,
    owner_id TEXT REFERENCES users(sfdc_id),
    type TEXT,                               -- Customer, Prospect, Partner, etc.
    category TEXT,                            -- Category_Opportunity_GT__c (weed, berries, etc.)
    created_date TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extraction_id INTEGER REFERENCES extraction_runs(id)
);

-- Opportunities (the main table)
CREATE TABLE opportunities (
    sfdc_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_id TEXT REFERENCES accounts(sfdc_id),
    stage_name TEXT,
    amount NUMERIC(12,2),
    close_date DATE,
    forecast_category TEXT,
    forecast_category_name TEXT,
    probability INTEGER,
    owner_id TEXT REFERENCES users(sfdc_id),
    type TEXT,                               -- New Business, Renewal, etc.
    lead_source TEXT,
    created_date TIMESTAMPTZ,
    last_modified_date TIMESTAMPTZ,
    last_activity_date DATE,
    last_stage_change_date TIMESTAMPTZ,
    has_open_activity BOOLEAN,
    has_overdue_task BOOLEAN,
    push_count INTEGER DEFAULT 0,
    is_closed BOOLEAN DEFAULT false,
    is_won BOOLEAN DEFAULT false,
    primary_quote_id TEXT,                   -- SBQQ__PrimaryQuote__c
    synced_quote_id TEXT,
    discovery_completed BOOLEAN DEFAULT false,
    roi_analysis_completed BOOLEAN DEFAULT false,
    budget_confirmed BOOLEAN DEFAULT false,
    contact_id TEXT,
    primary_contact TEXT,                    -- Primary_Contact_GT__c
    class TEXT,                              -- Class_Opportunity_GT__c (always 'Horticulture')
    category TEXT,                           -- Category_Opportunity_GT__c
    description TEXT,
    loss_reason TEXT,                        -- Loss_Reason__c
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extraction_id INTEGER REFERENCES extraction_runs(id)
);

-- Quotas (seeded from quotas.csv, manually maintained)
CREATE TABLE quotas (
    id SERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(sfdc_id),
    user_name TEXT NOT NULL,
    month TEXT NOT NULL,                     -- YYYY-MM
    amount NUMERIC(12,2) NOT NULL,
    active_from DATE,                        -- when this rep started
    active_to DATE,                          -- when this rep departed (NULL = active)
    UNIQUE(user_id, month)
);

-- Indexes for common query patterns
CREATE INDEX idx_opps_close_date ON opportunities(close_date);
CREATE INDEX idx_opps_owner ON opportunities(owner_id);
CREATE INDEX idx_opps_stage ON opportunities(stage_name);
CREATE INDEX idx_opps_is_closed ON opportunities(is_closed);
CREATE INDEX idx_opps_is_won ON opportunities(is_won);
CREATE INDEX idx_opps_type ON opportunities(type);
CREATE INDEX idx_opps_category ON opportunities(category);
CREATE INDEX idx_opps_created ON opportunities(created_date);
CREATE INDEX idx_opps_account ON opportunities(account_id);
CREATE INDEX idx_opps_forecast ON opportunities(forecast_category_name);
CREATE INDEX idx_accounts_state ON accounts(billing_state);
CREATE INDEX idx_accounts_type ON accounts(type);
```

Run the schema against the Postgres instance:
```bash
psql $POSTGRES_URL -f ~/Projects/aroya-ops-new/schema/001_initial.sql
```

## Part 3: Seed Quotas

Create `~/Projects/aroya-ops-new/schema/002_seed_quotas.sql` that reads from `~/Projects/aroya-ops-new/quotas.csv` and inserts into the quotas table. The CSV has columns: Rep, Month (YYYY-MM format or similar), Amount. Map rep names to user sfdc_ids after the users table is populated.

Alternatively, write a small Python script `seed_quotas.py` that reads quotas.csv and inserts via psycopg2. This is preferred since the CSV format may need parsing.

For Jon Prime: set `active_to` to his departure date. His historical quota data stays for comparison.

## Part 4: Rewire sfdc_extract.py

Modify `~/Projects/aroya-ops-new/sfdc_extract.py` to:

1. **Add Postgres connection.** Read `POSTGRES_URL` from .env. Use psycopg2. Connect at start, close at end.

2. **Create extraction_runs record** at the start of each run. Update it with counts and status at the end.

3. **Remove date bounds from all queries.** The ONLY filter is `Class_Opportunity_GT__c = 'Horticulture'`. Pull ALL history.
   - Open pipeline: remove `AND CloseDate >= 2024-03-01 AND CloseDate <= 2026-02-28`
   - Closed won: remove date bounds
   - Closed lost: remove date bounds
   - Renewals: remove date bounds
   - Users: no change needed

4. **Add accounts query.** New SOQL query:
   ```
   SELECT Id, Name, BillingState, OwnerId, Type, CreatedDate
   FROM Account
   WHERE Id IN (SELECT AccountId FROM Opportunity WHERE Class_Opportunity_GT__c = 'Horticulture')
   ```
   Note: If this subquery doesn't work in SOQL, collect unique AccountIds from opportunity results and query accounts in batches.

5. **Add Category to accounts.** The Category field is on the Opportunity, not the Account object. For accounts, derive the category from the most common Category_Opportunity_GT__c value across their opportunities. Store this as a post-processing step after opps are loaded.

6. **Upsert all records** using `INSERT ... ON CONFLICT (sfdc_id) DO UPDATE SET ...`. This makes the extract idempotent. Run it hourly or daily, same result.

7. **Keep flat file output.** After writing to Postgres, ALSO write the existing JSON files (dual-write). The current server.js still reads from flat files. We'll cut over in a later phase.

8. **Add psycopg2 to requirements.** Create or update `~/Projects/aroya-ops-new/requirements.txt` with psycopg2-binary.

## Part 5: Verify

After the extract runs:
1. `SELECT COUNT(*) FROM opportunities;` should return all Horticulture opps (expect hundreds to low thousands)
2. `SELECT COUNT(*) FROM accounts;` should return all referenced accounts
3. `SELECT COUNT(*) FROM users;` should return active standard users
4. `SELECT DISTINCT category FROM opportunities;` should show the product categories
5. `SELECT owner_id, COUNT(*), SUM(amount) FROM opportunities WHERE is_closed = false GROUP BY owner_id;` should show open pipeline by rep
6. Flat JSON files should still be written (dual-write verification)
7. extraction_runs table should have one record with status 'success'

## Success Criteria

1. Postgres container running on Kush, port 5432, accessible from Haze.
2. All 5 tables created (extraction_runs, users, accounts, opportunities, quotas).
3. All indexes created.
4. SFDC extract runs successfully against live Salesforce and writes to Postgres.
5. Opportunities table contains ALL Horticulture opps with no date bounds. Class field is always 'Horticulture'.
6. Accounts table contains all accounts referenced by Horticulture opps with Name, BillingState, OwnerId, Type, CreatedDate.
7. Users table contains active standard users.
8. Every record has an extraction_id linking to extraction_runs.
9. Upsert works: running the extract twice does not create duplicates.
10. Flat JSON files are still written (dual-write). Existing server.js can still read them.
11. extraction_runs table logs the run with start time, end time, record counts, and status.
12. POSTGRES_URL is in .env and not hardcoded in the script.
13. psycopg2-binary is in requirements.txt.
14. Schema SQL file exists at schema/001_initial.sql and is runnable standalone.

## Constraints

- Python 3.x for the extract (match existing sfdc_extract.py)
- psycopg2-binary (not psycopg2, avoids libpq build dependency)
- Postgres 16 Alpine Docker image (small footprint)
- Docker on Kush is at `/usr/local/bin/docker` -- always use full path in SSH commands
- SSH to Kush: `ssh kush` (user bradwood, key auth)
- Do not modify anything on the existing aroya-ops installation. All work in ~/Projects/aroya-ops-new/
- Do not touch Qdrant, forge-brain, Sentinel, or any other service on Kush
- All timestamps stored as TIMESTAMPTZ (UTC)
- Volume mount for Postgres data persistence (survives container restart)

## Do Not Do

- Do not modify server.js or the dashboard (that's Phase 4)
- Do not build materialized views yet (that's Phase 3)
- Do not set up cron or scheduling (manual run for now, scheduling comes later)
- Do not pull product/line item data (future phase)
- Do not pull contact records beyond what's on the opportunity (future phase)
- Do not delete or modify the existing flat file pipeline (daily-pipeline.sh, generate_summary.py)
- Do not expose Postgres to the internet (localhost and LAN only)
- Do not install Postgres natively on Kush (Docker only)

## Machine Details

- **Kush** (192.168.1.100): Docker host for Postgres. SSH: `ssh kush`, user bradwood. Docker: `/usr/local/bin/docker`
- **Haze**: Dev machine. Working copy at `~/Projects/aroya-ops-new/`. SFDC credentials in `.env`.
- **Salesforce**: OAuth2 password grant, credentials in .env (SFDC_LOGIN_URL, SFDC_CLIENT_ID, SFDC_CLIENT_SECRET, SFDC_USERNAME, SFDC_PASSWORD, SFDC_SECURITY_TOKEN)

## Budget

Medium. Docker setup is quick. Schema is defined. SFDC query changes are straightforward (remove date bounds, add accounts query). Upsert logic and dual-write are the main engineering work.

## Execution

Use agent teams. Suggested decomposition:
1. Infrastructure agent: SSH to Kush, stand up Postgres container, verify connectivity
2. Schema agent: Create SQL files, run schema, seed quotas
3. Extract agent: Modify sfdc_extract.py, add Postgres writes, test against live SFDC