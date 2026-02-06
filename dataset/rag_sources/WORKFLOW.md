# Workflow: scale source discovery to all questions (answer-free)

This workflow expands from the pilot (`sources_pilot.yaml`) to the full question sets without ever adding VAC answers as context.

## 0) Non-negotiables

- Do **not** store/copy any VAC answer text.
- Keep all sources in `govtech_hackathon/golden/rag_sources/` (never inside `rijksoverheid_vacs_*.md`).
- Exclude any source URL containing `/vraag-en-antwoord/`.

## 1) Create the full question list (keys + text)

Preferred inputs (already in-repo):

- `govtech_hackathon/golden/rijksoverheid_vacs_bzk.csv` (175 questions)
- `govtech_hackathon/golden/rijksoverheid_vacs_omgevingswet.csv` (5 questions)

Only extract and keep:

- `id` (VAC UUID) → `question_key`
- `question` → `question_text`
- (optional) `subjects`, `themes`, `authorities` for clustering

Do **not** copy `reference_answer` / `reference_answer_html`.

## 2) Cluster questions by topic

Use a simple strategy:

- primary: `subjects` (best signal for BZK/Woo/DigiD/BRP/etc.)
- secondary: keywords in `question_text` (e.g., “bezwaar”, “dwangsom”, “omgevingsvergunning”)

Goal: identify “topic families” that can share the same backbone sources.

## 3) Apply topic templates (backbone sources)

For each cluster, copy the template backbone sources from `topic_templates.md` into the per-question `sources` list.

Keep the list short:

- target: **3–7 sources** per question
- each added source must contribute unique coverage (law vs procedure vs portal vs PDF)

## 4) Add question-specific sources

Then, per question, add 0–3 sources that are **only** relevant to that exact question.

Examples:

- elections: add a Kiesraad page about “stempas kwijt” only if the question asks that
- BRP: add briefadres/RNI sources only if the question mentions them
- omgevingsvergunning: add specific IPLO procedure pages (regulier/uitgebreid) only if needed

## 5) Quality gate (repeatable)

Run these checks before scraping:

- **No answer pages**: no `/vraag-en-antwoord/` in any source URL
- **No duplicates within a question**
- **URL reachability**: \(2xx/3xx\) for all sources (allow redirects)
- **Source authority**: prefer official domains (`wetten.overheid.nl`, `iplo.nl`, `omgevingswet.overheid.nl`, `kiesraad.nl`, `rechtspraak.nl`, `digid.nl`, `logius.nl`, `rvig.nl`, etc.)

## 6) Scrape notes (optional but helpful)

For laws on `wetten.overheid.nl`, add `scrape_notes` for:

- relevant chapters/articles to extract (keeps your scraper focused)
- optionally pin a version date (e.g., `/2026-01-01/0/...`) when reproducibility matters

## 7) Output file strategy

Keep files small and reviewable:

- `sources_pilot.yaml` (current pilot: 10 BZK + 5 Omgevingswet)
- later: `sources_bzk_full.yaml` and `sources_omgevingswet.yaml` (or one combined file), generated from the CSV lists

Always update `meta.checked_at` when sources are revalidated.
