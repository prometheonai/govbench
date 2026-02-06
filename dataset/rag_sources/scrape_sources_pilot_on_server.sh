#!/usr/bin/env bash
set -euo pipefail

# Scrape all URLs from sources_pilot.yaml via the local scraper API (localhost:9501)
# and store markdown files under the per-dataset base folders:
# - BZK          -> <THEON_DATA_DIR>/BZK/<VAC_UUID>/sources/*.md
# - Omgevingswet -> <THEON_DATA_DIR>/Omgevingswet/<VAC_UUID>/sources/*.md
#
# Hard rule: skips any URL containing "/vraag-en-antwoord/".
# Idempotent: skips URLs whose .md file already exists.
# Auto-splits: runs split_markdown_by_heading.py on each new .md file (disable with --no-split).
#
# Dependencies: bash, python3, jq, curl, (shasum OR sha256sum)

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_INPUT_SCRIPT_DIR="$SCRIPT_DIR/sources_pilot.yaml"
DEFAULT_INPUT_REPO_REL="govtech_hackathon/golden/rag_sources/sources_pilot.yaml"
SPLITTER_SCRIPT="$SCRIPT_DIR/split_markdown_by_heading.py"

INPUT="$DEFAULT_INPUT_SCRIPT_DIR"
INPUT_WAS_SET="false"
SCRAPER_API_URL="http://localhost:9501/scrape_url_markdown"

BZK_DIR="${BZK_DIR:-/data/BZK}"
OMGEVINGSWET_DIR="${OMGEVINGSWET_DIR:-/data/Omgevingswet}"

HEADLESS="true"
USE_PROXIES="false"
TIMEOUT_MS="45000"

DATASET_ID_FILTER=""
QUESTION_KEY_FILTER=""
ONLY_URL_FILTER=""

DRY_RUN="false"
YES="false"
DO_SPLIT="true"
FORCE="false"

CACHE_DIR="/tmp/theon_scrape_cache_md"
REMOTE_SSH_TARGET="${REMOTE_SSH_TARGET:-<user>@<server-ip>}"

usage() {
  cat <<'EOF'
Usage:
  ./scrape_sources_pilot_on_server.sh [options]

Options:
  --input PATH                 Path to sources_pilot.yaml
  --api-url URL                Scraper API URL (default http://localhost:9501/scrape_url_markdown)

  --bzk-dir PATH               Base output folder for BZK dataset
  --omgevingswet-dir PATH       Base output folder for Omgevingswet dataset

  --dataset-id ID              Only scrape one dataset_id (e.g. bzk_pilot | omgevingswet)
  --question-key UUID          Only scrape one question_key (VAC UUID)
  --only-url URL               Only scrape one specific URL (must exist in YAML after filters)

  --headless true|false
  --use-proxies true|false
  --timeout-ms N

  --dry-run                    Print what would be done; do not create folders or call API
  --yes                        Skip interactive confirmation

  --no-split                   Do not run the markdown splitter after scraping
  --force                      Re-scrape even if .md file already exists

Environment:
  REMOTE_SSH_TARGET            Used only for the missing-input scp hint (default: <user>@<server-ip>)

Examples:
  # Scrape everything (skips already-downloaded, auto-splits)
  ./scrape_sources_pilot_on_server.sh

  # Scrape one VAC as a test (dry-run)
  ./scrape_sources_pilot_on_server.sh --question-key e7909bd4-93b1-400a-8a36-e685b26f52a7 --dry-run

  # Re-scrape everything, skip splitting
  ./scrape_sources_pilot_on_server.sh --force --no-split
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing dependency: $cmd" >&2
    exit 1
  fi
}

sha256_10() {
  local input="$1"
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$input" | shasum -a 256 | cut -c1-10
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$input" | sha256sum | awk '{print $1}' | cut -c1-10
    return
  fi
  echo "Need shasum or sha256sum" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="$2"; INPUT_WAS_SET="true"; shift 2 ;;
    --api-url) SCRAPER_API_URL="$2"; shift 2 ;;
    --bzk-dir) BZK_DIR="$2"; shift 2 ;;
    --omgevingswet-dir) OMGEVINGSWET_DIR="$2"; shift 2 ;;
    --dataset-id) DATASET_ID_FILTER="$2"; shift 2 ;;
    --question-key) QUESTION_KEY_FILTER="$2"; shift 2 ;;
    --only-url) ONLY_URL_FILTER="$2"; shift 2 ;;
    --headless) HEADLESS="$2"; shift 2 ;;
    --use-proxies) USE_PROXIES="$2"; shift 2 ;;
    --timeout-ms) TIMEOUT_MS="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift 1 ;;
    --yes) YES="true"; shift 1 ;;
    --no-split) DO_SPLIT="false"; shift 1 ;;
    --force) FORCE="true"; shift 1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

require_cmd python3
require_cmd jq
require_cmd curl

if [[ "$DO_SPLIT" == "true" && ! -f "$SPLITTER_SCRIPT" ]]; then
  echo "Splitter script not found: $SPLITTER_SCRIPT" >&2
  echo "Either place split_markdown_by_heading.py next to this script, or use --no-split." >&2
  exit 1
fi

if [[ "$INPUT_WAS_SET" != "true" && ! -f "$INPUT" && -f "$DEFAULT_INPUT_REPO_REL" ]]; then
  INPUT="$DEFAULT_INPUT_REPO_REL"
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Input not found: $INPUT" >&2
  echo >&2
  echo "This usually means sources_pilot.yaml is not on the server yet." >&2
  echo "Fix options:" >&2
  echo "- Copy it next to this script (recommended), e.g. from your local machine:" >&2
  echo "    scp govtech_hackathon/golden/rag_sources/sources_pilot.yaml ${REMOTE_SSH_TARGET}:\"$DEFAULT_INPUT_SCRIPT_DIR\"" >&2
  echo "- Or pass --input /absolute/path/to/sources_pilot.yaml" >&2
  exit 1
fi

# Export filters for the Python extractor.
export INPUT_PATH="$INPUT"
export DATASET_ID_FILTER="$DATASET_ID_FILTER"
export QUESTION_KEY_FILTER="$QUESTION_KEY_FILTER"
export ONLY_URL_FILTER="$ONLY_URL_FILTER"

# Extract scrape jobs as JSON Lines: one record per (dataset_id, question_key, url)
JOBS_JSONL="$(python3 - <<'PY'
import json
import os
import re
import sys

input_path = os.environ.get("INPUT_PATH")
if not input_path:
    raise SystemExit("Missing INPUT_PATH env var")
dataset_filter = os.environ.get("DATASET_ID_FILTER") or ""
question_filter = os.environ.get("QUESTION_KEY_FILTER") or ""
only_url_filter = os.environ.get("ONLY_URL_FILTER") or ""

dataset_id_re = re.compile(r'^\s*-\s*dataset_id:\s*"([^"]+)"\s*$')
question_key_re = re.compile(r'^\s*-\s*question_key:\s*"([^"]+)"\s*$')
question_text_re = re.compile(r'^\s*question_text:\s*"([^"]+)"\s*$')

url_re = re.compile(r'^\s*-\s*url:\s*"([^"]+)"\s*$')
title_re = re.compile(r'^\s*title:\s*"([^"]+)"\s*$')
publisher_re = re.compile(r'^\s*publisher:\s*"([^"]+)"\s*$')
source_type_re = re.compile(r'^\s*source_type:\s*"([^"]+)"\s*$')

current_dataset_id = None
current_question_key = None
current_question_text = None

current_url = None
current_title = None
current_publisher = None
current_source_type = None

def flush_source():
    global current_url, current_title, current_publisher, current_source_type
    if current_dataset_id and current_question_key and current_question_text and current_url:
        if dataset_filter and current_dataset_id != dataset_filter:
            pass
        elif question_filter and current_question_key != question_filter:
            pass
        elif only_url_filter and current_url != only_url_filter:
            pass
        else:
            print(json.dumps({
                "dataset_id": current_dataset_id,
                "question_key": current_question_key,
                "question_text": current_question_text,
                "url": current_url,
                "title": current_title,
                "publisher": current_publisher,
                "source_type": current_source_type,
            }, ensure_ascii=False))
    current_url = None
    current_title = None
    current_publisher = None
    current_source_type = None

with open(input_path, "r", encoding="utf-8") as f:
    for line in f:
        m = dataset_id_re.match(line)
        if m:
            flush_source()
            current_dataset_id = m.group(1)
            continue

        m = question_key_re.match(line)
        if m:
            flush_source()
            current_question_key = m.group(1)
            current_question_text = None
            continue

        m = question_text_re.match(line)
        if m:
            current_question_text = m.group(1)
            continue

        m = url_re.match(line)
        if m:
            flush_source()
            current_url = m.group(1)
            continue

        m = title_re.match(line)
        if m and current_url is not None:
            current_title = m.group(1)
            continue

        m = publisher_re.match(line)
        if m and current_url is not None:
            current_publisher = m.group(1)
            continue

        m = source_type_re.match(line)
        if m and current_url is not None:
            current_source_type = m.group(1)
            continue

flush_source()
PY
)"

if [[ -z "$JOBS_JSONL" ]]; then
  echo "No URLs matched your filters." >&2
  exit 3
fi

VAC_COUNT="$(printf '%s\n' "$JOBS_JSONL" | jq -r '.question_key' | sort -u | wc -l | tr -d ' ')"
URL_COUNT="$(printf '%s\n' "$JOBS_JSONL" | jq -r '.url' | wc -l | tr -d ' ')"
UNIQUE_URL_COUNT="$(printf '%s\n' "$JOBS_JSONL" | jq -r '.url' | sort -u | wc -l | tr -d ' ')"

echo "Will scrape:"
echo "- Input: $INPUT"
echo "- API: $SCRAPER_API_URL"
echo "- BZK dir: $BZK_DIR"
echo "- Omgevingswet dir: $OMGEVINGSWET_DIR"
echo "- VACs: $VAC_COUNT"
echo "- URLs: $URL_COUNT (unique: $UNIQUE_URL_COUNT)"
echo "- headless=$HEADLESS use_proxies=$USE_PROXIES timeout_ms=$TIMEOUT_MS"
echo "- cache_dir=$CACHE_DIR"
echo "- dry_run=$DRY_RUN force=$FORCE split=$DO_SPLIT"

if [[ "$DRY_RUN" == "true" ]]; then
  exit 0
fi

if [[ "$YES" != "true" ]]; then
  echo
  echo "About to create per-VAC folders under:"
  echo "  - $BZK_DIR"
  echo "  - $OMGEVINGSWET_DIR"
  echo
  read -r -p 'Type "yes" to proceed: ' CONFIRM
  if [[ "${CONFIRM,,}" != "yes" ]]; then
    echo "Aborted."
    exit 4
  fi
fi

mkdir -p "$CACHE_DIR"

scrape_one() {
  local url="$1"
  local out="$2"

  # Derive stable name from URL (host + slug + hash)
  local host path_ slug hash
  host="${url#*://}"; host="${host%%/*}"
  path_="${url#*://$host}"; path_="${path_#/}"; path_="${path_%/}"
  slug="$(printf '%s' "${path_:-root}" | tr '/?&=:%' '_' | tr -cd '[:alnum:]_.-')"
  hash="$(sha256_10 "$url")"

  local tmp_out="${out}.tmp"

  jq -n --arg url "$url" \
    --argjson headless "$HEADLESS" \
    --argjson use_proxies "$USE_PROXIES" \
    --argjson timeout_ms "$TIMEOUT_MS" \
    '{url:$url, headless:$headless, use_proxies:$use_proxies, timeout_ms:$timeout_ms}' \
  | curl -sS -X POST "$SCRAPER_API_URL" -H 'Content-Type: application/json' -d @- \
  | jq -r '.data' > "$tmp_out"

  mv "$tmp_out" "$out"
  echo "wrote $out"
}

SCRAPED_COUNT=0
SKIPPED_COUNT=0

while read -r job; do
  dataset_id="$(printf '%s' "$job" | jq -r '.dataset_id')"
  question_key="$(printf '%s' "$job" | jq -r '.question_key')"
  question_text="$(printf '%s' "$job" | jq -r '.question_text')"
  url="$(printf '%s' "$job" | jq -r '.url')"
  title="$(printf '%s' "$job" | jq -r '.title // empty')"
  publisher="$(printf '%s' "$job" | jq -r '.publisher // empty')"
  source_type="$(printf '%s' "$job" | jq -r '.source_type // empty')"

  if [[ "$url" == *"/vraag-en-antwoord/"* ]]; then
    echo "SKIP (excluded): $url" >&2
    continue
  fi

  out_base=""
  case "$dataset_id" in
    bzk_pilot) out_base="$BZK_DIR" ;;
    omgevingswet) out_base="$OMGEVINGSWET_DIR" ;;
    *)
      echo "Unknown dataset_id '$dataset_id' for url: $url" >&2
      exit 5
      ;;
  esac

  vac_dir="$out_base/$question_key"
  sources_dir="$vac_dir/sources"
  mkdir -p "$sources_dir"

  # Write question metadata once (idempotent)
  if [[ ! -f "$vac_dir/question.json" ]]; then
    jq -n \
      --arg dataset_id "$dataset_id" \
      --arg question_key "$question_key" \
      --arg question_text "$question_text" \
      '{dataset_id:$dataset_id, question_key:$question_key, question_text:$question_text}' \
      > "$vac_dir/question.json"
  fi

  host="${url#*://}"; host="${host%%/*}"
  path_="${url#*://$host}"; path_="${path_#/}"; path_="${path_%/}"
  slug="$(printf '%s' "${path_:-root}" | tr '/?&=:%' '_' | tr -cd '[:alnum:]_.-')"
  hash="$(sha256_10 "$url")"
  out_file="${host}__${slug}__${hash}.md"
  out_path="$sources_dir/$out_file"

  # Idempotent: skip if file already exists (unless --force)
  if [[ -f "$out_path" && "$FORCE" != "true" ]]; then
    echo "SKIP (exists): $out_path"
    ((SKIPPED_COUNT++)) || true
    continue
  fi

  cache_path="$CACHE_DIR/$out_file"
  if [[ -f "$cache_path" && "$FORCE" != "true" ]]; then
    cp -f "$cache_path" "$out_path"
    echo "copied (cache) $out_path"
  else
    scrape_one "$url" "$out_path"
    cp -f "$out_path" "$cache_path" || true
  fi
  ((SCRAPED_COUNT++)) || true

  # Idempotent manifest: only append if URL not already present
  manifest_path="$vac_dir/manifest.ndjson"
  if [[ ! -f "$manifest_path" ]] || ! grep -q "\"url\":\"$url\"" "$manifest_path" 2>/dev/null; then
    jq -nc \
      --arg url "$url" \
      --arg file "sources/$out_file" \
      --arg title "$title" \
      --arg publisher "$publisher" \
      --arg source_type "$source_type" \
      --arg scraped_at "$(date -Is)" \
      '{url:$url, file:$file, title:($title|select(length>0)?), publisher:($publisher|select(length>0)?), source_type:($source_type|select(length>0)?), scraped_at:$scraped_at}' \
      >> "$manifest_path"
  fi

  # Auto-split if enabled
  if [[ "$DO_SPLIT" == "true" ]]; then
    python3 "$SPLITTER_SCRIPT" --input "$out_path" --overwrite
  fi
done < <(printf '%s\n' "$JOBS_JSONL")

echo ""
echo "Done. Scraped: $SCRAPED_COUNT, Skipped (already exists): $SKIPPED_COUNT"

