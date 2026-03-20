# Splunk Add-on for OpenAI Logs (`ta_openai_logs`)

A Splunk Technical Add-on (TA) that collects **OpenAI Usage Logs** and **OpenAI Audit Logs** via the OpenAI API and maps them to the [Splunk Common Information Model (CIM)](https://docs.splunk.com/Documentation/CIM/latest/User/Overview).

---

## What It Does

| Input | Source Type | CIM Data Model |
|---|---|---|
| OpenAI Usage Logs | `openai:usage` | Application State |
| OpenAI Audit Logs | `openai:audit` | Authentication |

- Polls the OpenAI API on a configurable interval
- Persists checkpoints to avoid duplicate events
- Maps fields to CIM for use with Splunk Enterprise Security and other premium apps

---

## Requirements

- Splunk Enterprise or Splunk Cloud **9.0+**
- Python **3.9+** (bundled with Splunk 9+)
- An OpenAI API key with access to the Usage and/or Audit Logs endpoints
- [`splunk-add-on-ucc-framework`](https://pypi.org/project/splunk-add-on-ucc-framework/) (for building from source)

---

## Installation

### Option A — Sideload the built package

1. Download `ta_openai_logs-1.0.0.tar.gz` from the [Releases](../../releases) page.
2. In Splunk Web: **Apps → Manage Apps → Install app from file**.
3. Upload the `.tar.gz` and restart if prompted.

### Option B — Install from Splunkbase

*(Coming soon)*

---

## Configuration

1. After installation, navigate to **Apps → Splunk Add-on for OpenAI Logs → Configuration**.
2. Add an **Account** with your OpenAI API key.
3. Go to **Inputs** and create one or both inputs:
   - **OpenAI Usage Logs** — collects token/cost usage data
   - **OpenAI Audit Logs** — collects user/project audit events

---

## Building from Source

Requirements: Python 3.9+, `splunk-add-on-ucc-framework`

```bash
pip install splunk-add-on-ucc-framework

# Build the TA
python3 -m splunk_add_on_ucc_framework build --source package --ta-version 1.0.0

# Or use the convenience script
bash build.sh
```

The built add-on is placed in `output/ta_openai_logs/` and packaged as `ta_openai_logs-<version>.tar.gz`.

---

## Running Tests

```bash
pip install pytest
pytest tests/
```

---

## Project Structure

```
.
├── package/            # TA source code (UCC framework input)
│   ├── bin/            # Modular input scripts
│   ├── default/        # Splunk conf files
│   ├── lib/            # Vendored Python dependencies
│   ├── lookups/        # CSV lookup tables
│   └── metadata/       # Splunk permissions
├── tests/              # Unit tests
├── globalConfig.json   # UCC framework build configuration
├── build.sh            # Convenience build script
└── ta_openai_logs-1.0.0.tar.gz  # Pre-built package
```

---

## License

Apache 2.0
