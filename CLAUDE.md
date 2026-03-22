# CLAUDE.md — Splunk / OpenAI Security Project

## Project Overview

This repo is a security-focused Splunk project with two components:
1. **`openai_addon/`** — A Splunk Technical Add-on (TA) that ingests OpenAI usage and audit logs into Splunk
2. **`openai_addon/simulate_compromise.py`** + **`simulate_attacker.py`** — Attack simulation scripts that generate realistic threat data in Splunk for building detections

The owner (Ken) is building threat detections on top of OpenAI API activity data. The simulation scripts generate real API traffic against OpenAI that gets ingested by the TA and shows up in Splunk as detectable attack patterns.

---

## Repository Structure

```
/Users/ken-ai/Desktop/Splunk/
├── CLAUDE.md                          # This file
├── .gitignore                         # Excludes .env, .sim_state.json, *.tar.gz, output/, jira_reference/
├── openai_addon/
│   ├── simulate_compromise.py         # 5-phase compromised key attack chain
│   ├── simulate_attacker.py           # Multi-scenario attack suite (--scenario flag)
│   ├── .env                           # NEVER COMMIT — real API keys
│   ├── .env.example                   # Template for .env
│   ├── .sim_state.json                # Slow-burn state tracker (auto-generated, gitignored)
│   ├── README.md                      # TA installation and usage docs
│   ├── Welcome.md                     # Additional notes
│   ├── build.sh                       # Convenience build script (wraps UCC framework)
│   ├── globalConfig.json              # UCC framework build config (UI, inputs, account schema)
│   ├── package/                       # TA source — input to UCC build
│   │   ├── app.manifest               # Splunk app metadata
│   │   ├── bin/                       # Modular input Python scripts
│   │   │   ├── openai_api_client.py   # Shared HTTP client (auth, retries, rate limiting)
│   │   │   ├── openai_audit_logs.py   # Audit log modular input
│   │   │   ├── openai_usage_logs.py   # Usage log modular input
│   │   │   ├── openai_checkpoint.py   # Checkpoint persistence (KV Store + file fallback)
│   │   │   ├── openai_consts.py       # Shared constants
│   │   │   ├── openai_utils.py        # Shared utilities
│   │   │   └── import_declare_test.py # UCC-generated import helper
│   │   ├── default/                   # Splunk conf files
│   │   │   ├── app.conf               # App identity (id: ta_openai_logs, version: 1.0.0)
│   │   │   ├── inputs.conf            # Input definitions (usage: 3600s, audit: 300s)
│   │   │   ├── props.conf             # Field extractions, CIM mappings, cost calc
│   │   │   ├── transforms.conf        # KV store config
│   │   │   ├── eventtypes.conf        # CIM event type definitions
│   │   │   ├── tags.conf              # CIM tags
│   │   │   ├── macros.conf            # SPL macros
│   │   │   └── collections.conf       # KV Store collection definition
│   │   ├── lib/
│   │   │   └── requirements.txt       # Python deps (requests, solnlib, splunktaucclib)
│   │   ├── lookups/
│   │   │   ├── openai_models.csv      # 33 models with input/output pricing per 1k tokens
│   │   │   └── openai_error_codes.csv # 16 error codes with descriptions
│   │   └── metadata/
│   │       └── default.meta           # Splunk ACL/permissions
│   ├── output/                        # UCC build output — gitignored
│   ├── tests/
│   │   ├── test_checkpoint.py         # Unit tests for checkpoint logic
│   │   └── test_openai_api_client.py  # Unit tests for API client
│   └── jira_reference/                # Reference TA (Jira Cloud) — gitignored, used for UCC patterns
└── anthropic_addon/                   # Future: Anthropic/Claude usage TA (currently empty)
```

---

## The Splunk Add-on (`ta_openai_logs`)

### What it collects

| Input | Sourcetype | Poll Interval | API Endpoint |
|---|---|---|---|
| Usage Logs | `openai:usage:logs` | Every hour (3600s) | `GET /organization/usage/completions` |
| Audit Logs | `openai:audit:logs` | Every 5 min (300s) | `GET /organization/audit_logs` |

### Key data fields

**Usage logs** (`openai:usage:logs`) — daily aggregates per model:
- `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `requests`
- `cost_usd` — calculated via lookup: `(prompt_tokens/1000 * input_rate) + (completion_tokens/1000 * output_rate)`
- `start_time`, `end_time` (Unix timestamps, daily buckets)
- `vendor_product` = "OpenAI", `dest` = "api.openai.com"

**Audit logs** (`openai:audit:logs`) — real-time events:
- `type` (event type: `api_key.created`, `login.succeeded`, `project.created`, etc.)
- `user` — coalesced from `actor.session.user.email` → `actor.api_key.user.email` → `actor.session.user.id`
- `src_ip` — from `actor.session.ip_address`
- `action` — alias of `type`
- `app` — alias of `project.id`
- `status` — "success" or "failure" (derived from event type pattern)
- `effective_at` (timestamp)

### CIM mappings

| Sourcetype | CIM Data Model | Event Types |
|---|---|---|
| `openai:audit:logs` | Authentication | `openai_authentication`, `openai_authentication_login`, `openai_account_management` |
| `openai:usage:logs` | Application State | `openai_application_state`, `openai_completion_usage` |

### Checkpoint strategy
- **Usage input**: tracks `last_fetched_date` (YYYY-MM-DD), re-fetches last 2 days on each run to catch late data
- **Audit input**: tracks `last_event_id`, paginates backward through newest-first results until it hits that ID
- **Storage**: KV Store collection `ta_openai_logs_checkpoints` (replicated), with file-based fallback

### Build process
```bash
pip install splunk-add-on-ucc-framework
bash openai_addon/build.sh
# Output: openai_addon/output/ta_openai_logs/
# Package: openai_addon/ta_openai_logs-1.0.0.tar.gz
```

### API client design
- Base URL: `https://api.openai.com/v1`
- Auth: `Authorization: Bearer <key>` + optional `OpenAI-Organization` header
- Rate limiting: exponential backoff on 429, max 120s
- Retries: 3 retries on 500/502/503/504
- Timeouts: 10s connect, 60s read
- SSL: enforced

---

## Simulation Scripts

### `simulate_compromise.py` — 5-phase attack chain

Simulates a stolen API key being weaponized. Run with:
```bash
cd openai_addon && python3 simulate_compromise.py
```

| Phase | What happens | Splunk signal |
|---|---|---|
| 1. Baseline | 50 completions across gpt-3.5-turbo (50%), gpt-4o-mini (30%), gpt-4o (20%) + 10 text-embedding-3-small | Normal token/cost baseline, multi-model |
| 2. Discovery | 20 rapid requests + 3 invalid model names (404) + 1 oversized request | Request burst, error injection |
| 3. Persistence | Creates admin API key via `POST /organization/admin_api_keys` | `api_key.created` audit event |
| 4. Exfil | 8× gpt-4o with 1,395 prompt_tokens (fake employee/credential data) + 5× text-embedding-3-large | Token spike, cost spike, model drift, embedding model upgrade |
| 5. Cleanup | Deletes the created key | `api_key.deleted` audit event |

**Note on Phase 3**: Requires `OPENAI_ADMIN_KEY` — a separate admin API key (not a project key). Create at `platform.openai.com/organization/api-keys` with Owner role. Regular project keys cannot create/delete keys via API.

### `simulate_attacker.py` — 5-scenario attack suite

```bash
python3 simulate_attacker.py --scenario <name>
python3 simulate_attacker.py --scenario all
python3 simulate_attacker.py --scenario quota-exhaustion --quota-max 50
```

| Scenario | What it does | Key detection signal |
|---|---|---|
| `org-takeover` | Enumerates users/projects, creates 3 keys rapid-fire, creates project, invites+revokes user | Key churn in 5min window, admin action burst, invite events |
| `quota-exhaustion` | Max-token gpt-4o + o1-mini requests until rate limited | Cost spike, single-model concentration, 429 errors |
| `jailbreak-campaign` | 40 adversarial prompts at 0.15s intervals across 10 jailbreak templates | Request burst, low token variance, high refusal rate |
| `embedding-exfil` | 80 doc chunks via text-embedding-3-large, no completions | Embedding spike, zero completions anomaly |
| `slow-burn` | Stateful day-by-day escalation (day 1-2: normal, day 3-4: new models, day 5-6: expensive, day 7+: full exfil) | Week-over-week cost trend, model drift, prompt token growth |

**Slow-burn state**: stored in `openai_addon/.sim_state.json` (gitignored). Increments once per calendar day. Delete the file to reset to day 1.

---

## Environment Config (`.env`)

Located at `openai_addon/.env` — never committed.

```
OPENAI_API_KEY=sk-proj-...        # Regular project key (required — all scenarios)
OPENAI_ADMIN_KEY=sk-admin-...     # Admin key (required — phase 3/5 of compromise sim, org-takeover)
OPENAI_ORG_ID=org-...             # Org ID (recommended)
PHASE_DELAY_SECONDS=3             # Delay between phases in simulate_compromise.py
BASELINE_REQUESTS=50              # Phase 1 request count
```

**Admin key distinction**: The admin key (`sk-admin-*`) is a separate key type created with Owner role. It can create/delete other API keys and manage org resources, but **cannot make model requests** (missing `model.request` scope). The regular project key is used for all completions/embeddings.

---

## Splunk Verification Queries

Audit events appear ~5 minutes after running (add-on poll interval).
Usage logs appear the **next day** — OpenAI's usage API only returns completed-day aggregates with up to 24hr delay.

```spl
# All recent audit events
index=* sourcetype="openai:audit:logs" | sort _time | table _time, type, user, src_ip

# API key create/delete events
index=* sourcetype="openai:audit:logs" type="api_key.*"

# Token spike by model
index=* sourcetype="openai:usage:logs" | timechart sum(total_tokens) by model

# Cost anomaly
index=* sourcetype="openai:usage:logs" | timechart sum(cost_usd) by model

# High prompt/completion ratio (exfil indicator)
index=* sourcetype="openai:usage:logs" model="gpt-4o"
| eval ratio=prompt_tokens/completion_tokens | where ratio > 3

# Embedding model anomaly (small → large switch)
index=* sourcetype="openai:usage:logs" model="text-embedding-*"
| timechart sum(total_tokens) by model

# Rapid key creation (3+ keys in 5 min)
index=* sourcetype="openai:audit:logs" type="api_key.created"
| bucket _time span=5m | stats count by _time, user | where count >= 3

# Week-over-week cost trend (slow-burn detection)
index=* sourcetype="openai:usage:logs" | timechart span=1d sum(cost_usd) by model
```

---

## Key Design Decisions

- **UCC framework** — the TA is built using Splunk's Add-on UCC Framework (`splunk-add-on-ucc-framework`). `globalConfig.json` defines the UI; `package/` is the source. Build output goes to `output/` (gitignored).
- **Two checkpoint strategies** — KV Store is primary (survives Splunk restarts, works in clusters), file-based is fallback. Both are managed by `openai_checkpoint.py`.
- **Cost calculation in Splunk** — done at search time via `props.conf` EVAL using the `openai_models.csv` lookup, not at ingest time. This means pricing can be updated by editing the lookup without re-ingesting data.
- **CIM compliance** — both sourcetypes are mapped to CIM data models so they work with Splunk Enterprise Security (ES) and ESCU detection rules out of the box.
- **Admin key separation** — management API operations (key create/delete, org management) require a separate admin key type. The simulation scripts handle this gracefully — if `OPENAI_ADMIN_KEY` is not set or is the same as the project key, admin phases fail gracefully and the script continues.
- **Usage log latency** — OpenAI's usage API returns daily aggregates with up to 24hr delay. The add-on re-fetches the last 2 days on every run to catch late-arriving records.

---

## Known Issues / Notes

- `gpt-3.5-turbo` is deprecated by OpenAI but still works for simulation purposes
- `o1-mini` uses `max_completion_tokens` instead of `max_tokens` — handled in `scenario_quota_exhaustion()`
- The `.obsidian/` directory in `openai_addon/` is gitignored — Ken uses Obsidian for notes in that directory
- `anthropic_addon/` directory exists but is currently empty — planned future TA for Anthropic/Claude API logs
- Jira Cloud reference TA is in `openai_addon/jira_reference/` (gitignored) — used as a pattern reference during initial TA development

---

## Git

- Repo: `ken-aisec/ta-openai-logs` on GitHub
- Default branch: `main`
- Initial commit: `2ea556b` — original TA release
- Second commit: `2f18ae4` — restructure into `openai_addon/`, add simulation scripts
