#!/usr/bin/env bash
# build.sh — Build and optionally inspect the OpenAI TA
#
# Usage:
#   ./build.sh            — run tests + ucc-gen build
#   ./build.sh --inspect  — also run appinspect (cloud tag)
#   ./build.sh --package  — also slim-package the .spl
#
# Prerequisites:
#   pip install splunk-add-on-ucc-framework splunk-appinspect
#   pip install pytest requests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Running unit tests..."
python -m pytest tests/ -v

echo ""
echo "==> Building TA with ucc-gen..."
ucc-gen build --source package --ta-version 1.0.0

echo ""
echo "==> Build output: output/ta_openai_logs/"
ls -la output/ta_openai_logs/ 2>/dev/null || echo "  (directory not present yet — check ucc-gen output)"

if [[ "${1:-}" == "--inspect" || "${2:-}" == "--inspect" ]]; then
    echo ""
    echo "==> Running Splunk AppInspect (cloud tag)..."
    splunk-appinspect inspect output/ta_openai_logs/ --included-tags cloud
fi

if [[ "${1:-}" == "--package" || "${2:-}" == "--package" ]]; then
    echo ""
    echo "==> Packaging .spl with slim..."
    slim package output/ta_openai_logs/
    echo "==> Package created:"
    ls -la *.spl 2>/dev/null || ls -la output/*.spl 2>/dev/null || true
fi

echo ""
echo "Done."
