# RAG source map (answer-free)

This folder contains **only external context sources** (URLs/PDFs/etc.) to scrape for building an RAG dataset.

### 1. Copy scripts to server (from local machine)

```bash
scp govtech_hackathon/golden/rag_sources/scrape_sources_pilot_on_server.sh \
    govtech_hackathon/golden/rag_sources/sources_pilot.yaml \
    govtech_hackathon/golden/rag_sources/split_markdown_by_heading.py \
    <user>@<server-ip>:/path/to/theon/data/
```

### 2. Run in tmux (keeps running after disconnect)

```bash
ssh <user>@<server-ip>

# Start a new tmux session named "scrape"
tmux new -s scrape

# Inside tmux: run the scraper
cd /path/to/theon/data
chmod +x scrape_sources_pilot_on_server.sh split_markdown_by_heading.py
./scrape_sources_pilot_on_server.sh --yes

# Detach from tmux (keeps running): press Ctrl+B, then D

# Later: re-attach to see progress
tmux attach -t scrape

# When done: kill the session
tmux kill-session -t scrape
```

### 3. Script options

```bash
# Default: scrape + auto-split (skips already-downloaded files)
./scrape_sources_pilot_on_server.sh --yes

# Scrape without splitting
./scrape_sources_pilot_on_server.sh --no-split --yes

# Force re-scrape everything (overwrites existing files)
./scrape_sources_pilot_on_server.sh --force --yes

# Dry-run: see what would be scraped
./scrape_sources_pilot_on_server.sh --dry-run

# Split existing files manually (recursive)
for dir in /path/to/theon/data/BZK /path/to/theon/data/Omgevingswet; do
  python3 split_markdown_by_heading.py --input "$dir" --recursive --overwrite
done
```

## Hard rules (non-negotiable)

- **Never** copy or include VAC answer text as context.
- **Never** write sources into the VAC markdown files (`rijksoverheid_vacs_*.md`). Those files contain answers and are only used to extract *question text* and stable IDs.
- **Exclude** all sources whose URL path contains `/vraag-en-antwoord/` (VAC Q&A answer pages).

## Data format

We store sources in YAML files with this shape:

```yaml
meta:
  checked_at: "YYYY-MM-DD"
  url_exclusions:
    - "/vraag-en-antwoord/"
datasets:
  - dataset_id: "bzk_pilot"
    dataset_name: "BZK pilot (10 questions)"
    items:
      - question_key: "<VAC UUID>"
        question_text: "<verbatim question>"
        sources:
          - url: "https://..."
            title: "<human-readable title>"
            publisher: "<organization>"
            source_type: "law|procedure|portal|guidance|pdf"
            why_relevant: "<1-2 sentences: what unique coverage this adds>"
            scrape_notes: "<optional: what to extract>"
```

## Notes for scrapers

- Prefer stable, canonical domains: `wetten.overheid.nl`, `iplo.nl`, `omgevingswet.overheid.nl`, `kiesraad.nl`, `rechtspraak.nl`, `digid.nl`, `logius.nl`, `rvig.nl`, `nederlandwereldwijd.nl`, etc.
- For `wetten.overheid.nl`, scrape **specific articles/chapters** where possible (store as `scrape_notes`).


