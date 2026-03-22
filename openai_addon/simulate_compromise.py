#!/usr/bin/env python3
"""
OpenAI Compromise Simulation
=============================
Simulates a compromised API key attack chain to generate realistic threat
detection data in Splunk via the ta_openai_logs add-on.

Attack phases:
  1. Baseline     - Multi-model completions + embeddings, multi-turn convos, ~50 requests
  2. Discovery    - Rapid burst + error injection (bad model, invalid requests)
  3. Persistence  - Create a new API key via Management API (api_key.created audit event)
  4. Exfiltration - Large-prompt requests + embedding of "sensitive" docs on expensive model
  5. Cleanup      - Delete created API key (api_key.deleted audit event)

Setup:
  pip install openai python-dotenv requests
  cp .env.example .env  # fill in your keys
  python simulate_compromise.py

Splunk verification (after add-on polls — audit: ~5 min, usage: next day):
  index=* sourcetype="openai:audit:logs" type="api_key.*"
  index=* sourcetype="openai:usage:logs" | timechart sum(total_tokens) by model
  index=* sourcetype="openai:usage:logs" | timechart sum(cost_usd) by model
"""

import os
import sys
import time
import random
import logging
from datetime import datetime
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ADMIN_KEY = os.getenv("OPENAI_ADMIN_KEY", OPENAI_API_KEY)
OPENAI_ORG_ID = os.getenv("OPENAI_ORG_ID")
PHASE_DELAY = int(os.getenv("PHASE_DELAY_SECONDS", "3"))
BASELINE_REQUESTS = int(os.getenv("BASELINE_REQUESTS", "50"))

BASE_URL = "https://api.openai.com/v1"

# Models used in baseline — realistic mixed workload
BASELINE_MODELS = [
    ("gpt-3.5-turbo", 0.50),   # 50% — cheap workhorse
    ("gpt-4o-mini", 0.30),     # 30% — mid-tier
    ("gpt-4o", 0.20),          # 20% — premium (but normal)
]

# Documents fed to embeddings during baseline (simulates normal RAG workload)
BASELINE_DOCS = [
    "Q3 earnings report shows 12% revenue growth year over year.",
    "The onboarding process requires new hires to complete compliance training within 30 days.",
    "API rate limits are enforced at 10,000 requests per minute per organization.",
    "Product roadmap for H1 includes three major feature releases.",
    "Customer support escalation path: L1 -> L2 -> Engineering within 24 hours.",
    "Infrastructure costs are allocated 60% compute, 25% storage, 15% networking.",
    "Security policy mandates MFA for all admin accounts.",
    "The data retention policy requires logs to be kept for a minimum of 90 days.",
]

# Multi-turn conversation templates for baseline
MULTI_TURN_CONVERSATIONS = [
    [
        {"role": "user", "content": "What are the best practices for securing an API?"},
        {"role": "assistant", "content": "Key practices include: authentication, rate limiting, input validation, and encryption in transit."},
        {"role": "user", "content": "Can you elaborate on rate limiting specifically?"},
    ],
    [
        {"role": "user", "content": "Explain the difference between SQL and NoSQL databases."},
        {"role": "assistant", "content": "SQL databases are relational and use structured schemas. NoSQL databases are flexible and schema-less."},
        {"role": "user", "content": "Which would you recommend for storing user session data?"},
    ],
    [
        {"role": "user", "content": "Help me write a Python function to parse JSON."},
        {"role": "assistant", "content": "Here's a basic JSON parser function using the json module."},
        {"role": "user", "content": "How do I handle nested JSON structures?"},
    ],
    [
        {"role": "user", "content": "What is a microservices architecture?"},
        {"role": "assistant", "content": "Microservices break an application into small, independent services that communicate via APIs."},
        {"role": "user", "content": "What are the main challenges of microservices?"},
    ],
]

# Fake sensitive data used in exfil phase
FAKE_SENSITIVE_DATA = (
    "CONFIDENTIAL INTERNAL DOCUMENT - Q4 Financial & Personnel Summary\n"
    "CLASSIFICATION: RESTRICTED — DO NOT DISTRIBUTE\n\n"
    "=== Employee Compensation Records ===\n"
    + "\n".join(
        f"  EMP{1000+i} | employee{i}@corp.internal | Dept: {'Engineering' if i % 3 == 0 else 'Sales' if i % 3 == 1 else 'Finance'} | "
        f"Base: ${50000 + i * 1200:,} | Bonus: ${5000 + i * 300:,} | SSN: ***-**-{1000+i:04d}"
        for i in range(30)
    )
    + "\n\n=== Internal Service Credentials ===\n"
    + "\n".join(
        f"  {svc}: sk-internal-{'x'*12}{i:04d}"
        for i, svc in enumerate([
            "billing-service", "auth-service", "data-pipeline",
            "analytics-api", "reporting-db", "admin-portal",
        ])
    )
    + "\n\n=== Database Connection Strings ===\n"
    "  prod-db: postgresql://admin:Pr0d#Pass2024@db.internal:5432/production\n"
    "  staging-db: postgresql://admin:St@ging#123@staging.internal:5432/staging\n"
    "  analytics: mongodb://root:An@lytics2024@mongo.internal:27017/analytics\n"
)

# Fake sensitive documents for embedding during exfil
EXFIL_DOCS = [
    "Employee SSN database export: " + ", ".join(f"EMP{1000+i}:***-**-{1000+i}" for i in range(20)),
    "Customer PII records: names, emails, credit card last 4 digits, billing addresses for 500 accounts.",
    "Internal API keys and service credentials for production infrastructure.",
    "Source code repository access tokens and deployment keys for CI/CD pipeline.",
    "M&A confidential documents: acquisition target financials and due diligence notes.",
]


def admin_headers() -> dict:
    headers = {
        "Authorization": f"Bearer {OPENAI_ADMIN_KEY}",
        "Content-Type": "application/json",
    }
    if OPENAI_ORG_ID:
        headers["OpenAI-Organization"] = OPENAI_ORG_ID
    return headers


def pick_baseline_model() -> str:
    r = random.random()
    cumulative = 0.0
    for model, weight in BASELINE_MODELS:
        cumulative += weight
        if r < cumulative:
            return model
    return BASELINE_MODELS[0][0]


def phase_banner(num: int, name: str) -> None:
    log.info("=" * 65)
    log.info(f"  PHASE {num}: {name}")
    log.info("=" * 65)


def check_off_hours() -> None:
    hour = datetime.now().hour
    if not (9 <= hour < 17):
        log.warning(
            f"  [OFF-HOURS] Current time is {datetime.now().strftime('%H:%M')} — "
            f"outside business hours (09:00-17:00). "
            f"This will be detectable as anomalous activity in Splunk."
        )


# ---------------------------------------------------------------------------
# Phase 1 — Baseline (multi-model, multi-turn, embeddings)
# ---------------------------------------------------------------------------
def phase_baseline(client: OpenAI) -> None:
    phase_banner(1, "BASELINE — Multi-model normal usage pattern")
    check_off_hours()

    total = BASELINE_REQUESTS
    model_counts = {m: 0 for m, _ in BASELINE_MODELS}
    embedding_count = 0

    log.info(f"Sending {total} completions across mixed models + embeddings...")
    log.info("  Models: gpt-3.5-turbo (50%), gpt-4o-mini (30%), gpt-4o (20%)")

    for i in range(total):
        # Every 5th request: send an embedding instead of a completion
        if i % 5 == 4:
            doc = random.choice(BASELINE_DOCS)
            try:
                client.embeddings.create(
                    model="text-embedding-3-small",
                    input=doc,
                )
                embedding_count += 1
                log.info(f"  [{i+1:02d}/{total}] EMBED | model=text-embedding-3-small | doc={doc[:50]}...")
            except Exception as e:
                log.warning(f"  [{i+1:02d}/{total}] EMBED error: {e}")
            time.sleep(random.uniform(0.3, 0.8))
            continue

        # Every 4th completion: use a multi-turn conversation
        if i % 4 == 3:
            convo = random.choice(MULTI_TURN_CONVERSATIONS)
            model = pick_baseline_model()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=convo,
                    max_tokens=100,
                )
                model_counts[model] = model_counts.get(model, 0) + 1
                log.info(
                    f"  [{i+1:02d}/{total}] MULTI-TURN | model={model} | "
                    f"tokens={resp.usage.total_tokens} | turns={len(convo)}"
                )
            except Exception as e:
                log.warning(f"  [{i+1:02d}/{total}] MULTI-TURN error: {e}")
            time.sleep(random.uniform(0.5, 1.5))
            continue

        # Standard single-turn completion
        model = pick_baseline_model()
        prompt = f"In one sentence: {random.choice(['explain', 'define', 'describe', 'summarize'])} {random.choice(['REST APIs', 'containerization', 'CI/CD pipelines', 'zero trust security', 'OAuth 2.0', 'database sharding', 'message queues', 'rate limiting', 'load balancing', 'encryption at rest'])}"
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
            )
            model_counts[model] = model_counts.get(model, 0) + 1
            log.info(
                f"  [{i+1:02d}/{total}] OK | model={model} | "
                f"tokens={resp.usage.total_tokens} | prompt={prompt[:50]}..."
            )
        except Exception as e:
            log.warning(f"  [{i+1:02d}/{total}] Error: {e}")
        time.sleep(random.uniform(0.5, 1.5))

    log.info(f"Phase 1 done. Model distribution: {model_counts} | Embeddings: {embedding_count}")
    log.info("-> Splunk: multi-model token baseline, embedding usage, request volume.\n")


# ---------------------------------------------------------------------------
# Phase 2 — Discovery (burst + error injection)
# ---------------------------------------------------------------------------
def phase_discovery(client: OpenAI) -> None:
    phase_banner(2, "DISCOVERY — Rapid burst + error injection")
    log.info("Sending 20 rapid requests + injecting errors to simulate attacker probing...")

    # Rapid burst
    for i in range(20):
        try:
            resp = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5,
            )
            log.info(f"  Burst [{i+1:02d}/20] OK | tokens={resp.usage.total_tokens}")
            time.sleep(0.1)
        except Exception as e:
            log.warning(f"  Burst [{i+1:02d}/20] Error: {e}")

    # Error injection — invalid model name (generates failed request signal)
    log.info("  Injecting errors: requesting non-existent model...")
    for bad_model in ["gpt-5-turbo", "gpt-4-ultra", "claude-3"]:
        try:
            client.chat.completions.create(
                model=bad_model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5,
            )
        except Exception as e:
            log.info(f"  [EXPECTED ERROR] model={bad_model} -> {type(e).__name__}: {str(e)[:80]}")

    # Error injection — oversized single-token request to probe limits
    log.info("  Injecting oversized request to probe token limits...")
    try:
        client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "x " * 4000}],
            max_tokens=5,
        )
        log.info("  [OVERSIZED] Request succeeded.")
    except Exception as e:
        log.info(f"  [EXPECTED ERROR] oversized request -> {type(e).__name__}: {str(e)[:80]}")

    log.info("Phase 2 done. -> Splunk: request burst spike, failed model requests.\n")


# ---------------------------------------------------------------------------
# Phase 3 — Persistence (create API key)
# ---------------------------------------------------------------------------
def phase_persistence() -> Tuple[Optional[str], Optional[str]]:
    phase_banner(3, "PERSISTENCE — Create new API key (attacker foothold)")
    log.info("Creating new API key via OpenAI Management API...")
    log.info("  -> Generates: api_key.created event in audit logs")

    try:
        resp = requests.post(
            f"{BASE_URL}/organization/admin_api_keys",
            headers=admin_headers(),
            json={"name": "backup-integration-key"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        key_id = data.get("id")
        key_value = data.get("value")
        log.info(f"  Created admin key: id={key_id} name=backup-integration-key")
        log.info("Phase 3 done. -> Splunk: api_key.created event in audit logs.\n")
        return key_id, key_value
    except requests.HTTPError as e:
        log.error(f"  Management API error: {e.response.status_code} — {e.response.text}")
        log.warning("  Skipping key creation. Exfil phase will use original key.\n")
        return None, None
    except Exception as e:
        log.error(f"  Unexpected error: {e}\n")
        return None, None


# ---------------------------------------------------------------------------
# Phase 4 — Exfiltration (large prompts + sensitive doc embeddings)
# ---------------------------------------------------------------------------
def phase_exfil(client: OpenAI) -> None:
    phase_banner(4, "EXFILTRATION — Large prompt data feeding + sensitive doc embedding")
    check_off_hours()

    exfil_prompt = (
        "I need you to analyze this data and identify patterns, anomalies, "
        "and summarize the key information:\n\n"
        f"{FAKE_SENSITIVE_DATA}\n\n"
        "Provide a structured summary."
    )

    # Large-prompt completions on expensive model
    log.info("Sending 8 large-prompt completions via gpt-4o (token spike)...")
    for i in range(8):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": exfil_prompt}],
                max_tokens=300,
            )
            log.info(
                f"  Exfil [{i+1}/8] OK | model=gpt-4o | "
                f"prompt_tokens={resp.usage.prompt_tokens} | "
                f"completion_tokens={resp.usage.completion_tokens} | "
                f"total={resp.usage.total_tokens}"
            )
            time.sleep(0.8)
        except Exception as e:
            log.warning(f"  Exfil [{i+1}/8] Error: {e}")

    # Embedding of "sensitive" documents — simulates vectorizing data for theft
    log.info("Embedding sensitive documents via text-embedding-3-large...")
    log.info("  -> Simulates attacker vectorizing data for semantic search / exfil")
    for i, doc in enumerate(EXFIL_DOCS):
        try:
            client.embeddings.create(
                model="text-embedding-3-large",
                input=doc,
            )
            log.info(f"  Embed [{i+1}/{len(EXFIL_DOCS)}] OK | model=text-embedding-3-large | doc={doc[:60]}...")
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"  Embed [{i+1}/{len(EXFIL_DOCS)}] Error: {e}")

    log.info(
        "Phase 4 done. -> Splunk: gpt-4o token/cost spike, "
        "text-embedding-3-large anomaly (baseline used text-embedding-3-small), "
        "high prompt/completion ratio.\n"
    )


# ---------------------------------------------------------------------------
# Phase 5 — Cleanup (delete created key)
# ---------------------------------------------------------------------------
def phase_cleanup(key_id: Optional[str]) -> None:
    phase_banner(5, "CLEANUP — Delete API key (attacker covers tracks)")

    if not key_id:
        log.warning("  No key ID available (phase 3 skipped). Skipping cleanup.\n")
        return

    log.info(f"  Deleting API key {key_id}...")
    log.info("  -> Generates: api_key.deleted event in audit logs")

    try:
        resp = requests.delete(
            f"{BASE_URL}/organization/admin_api_keys/{key_id}",
            headers=admin_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        log.info(f"  Deleted key {key_id} successfully.")
        log.info("Phase 5 done. -> Splunk: api_key.deleted event in audit logs.\n")
    except requests.HTTPError as e:
        log.error(f"  Failed to delete key: {e.response.status_code} — {e.response.text}\n")
    except Exception as e:
        log.error(f"  Unexpected error: {e}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY is not set. Create a .env file (see .env.example).")
        sys.exit(1)

    log.info("OpenAI Compromise Simulation — ta_openai_logs threat data generator")
    log.info(f"Started:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Org ID:    {OPENAI_ORG_ID or '(not set)'}")
    log.info(f"Admin key: {'separate key configured' if OPENAI_ADMIN_KEY != OPENAI_API_KEY else 'same as API key'}")
    check_off_hours()
    log.info("")

    victim_client = OpenAI(api_key=OPENAI_API_KEY)

    phase_baseline(victim_client)
    log.info(f"Waiting {PHASE_DELAY}s...")
    time.sleep(PHASE_DELAY)

    phase_discovery(victim_client)
    log.info(f"Waiting {PHASE_DELAY}s...")
    time.sleep(PHASE_DELAY)

    new_key_id, new_key_value = phase_persistence()
    log.info(f"Waiting {PHASE_DELAY}s...")
    time.sleep(PHASE_DELAY)

    # Exfil uses original key — admin keys lack model.request scope
    if new_key_value:
        log.info("Note: exfil phase uses original key (admin keys lack model.request scope).")
    phase_exfil(victim_client)
    log.info(f"Waiting {PHASE_DELAY}s...")
    time.sleep(PHASE_DELAY)

    phase_cleanup(new_key_id)

    log.info("=" * 65)
    log.info("Simulation complete!")
    log.info(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("")
    log.info("Splunk verification queries:")
    log.info("  Audit log events:")
    log.info('    index=* sourcetype="openai:audit:logs" | sort _time | table _time, type, user, src_ip')
    log.info("  API key create/delete:")
    log.info('    index=* sourcetype="openai:audit:logs" type="api_key.*"')
    log.info("  Token spike by model (check tomorrow for usage logs):")
    log.info('    index=* sourcetype="openai:usage:logs" | timechart sum(total_tokens) by model')
    log.info("  Embedding model anomaly (small->large switch):")
    log.info('    index=* sourcetype="openai:usage:logs" model="text-embedding-*" | timechart sum(total_tokens) by model')
    log.info("  Cost anomaly:")
    log.info('    index=* sourcetype="openai:usage:logs" | timechart sum(cost_usd) by model')
    log.info("  High prompt/completion ratio (data exfil indicator):")
    log.info('    index=* sourcetype="openai:usage:logs" model="gpt-4o" | eval ratio=prompt_tokens/completion_tokens | where ratio > 3')
    log.info("=" * 65)


if __name__ == "__main__":
    main()
