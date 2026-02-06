# Golden Dataset

Golden evaluation dataset for measuring RAG-based digital assistant quality on Dutch government (Rijksoverheid) Q&A content.

## Structure

```
golden/
├── evaluation_dataset.jsonl      # 15 questions with ground truth (primary format)
├── evaluation_dataset.json       # Same dataset with metadata envelope
├── evaluation_dataset.csv        # Questions only (no ground truth)
├── load_evaluation_dataset.py    # Loader with RAGAS/TruLens/LangChain converters
│
├── rijksoverheid_vacs_bzk.md     # 175 BZK VAC questions + reference answers (markdown)
├── rijksoverheid_vacs_bzk.csv    # Same, full CSV export from Rijksoverheid API
├── rijksoverheid_vacs_omgevingswet.md   # 5 Omgevingswet VAC questions (markdown)
├── rijksoverheid_vacs_omgevingswet.csv  # Same, CSV export
├── list_of_subjects.md           # All Rijksoverheid subject categories
│
├── cache_vacs/                   # Raw API cache: 5 Omgevingswet VACs
├── cache_vacs_bzk/               # Raw API cache: 175 BZK VACs
│
└── rag_sources/                  # External context sources for RAG (answer-free)
    ├── sources_pilot.yaml        # URL→question mapping for 15 pilot questions
    ├── topic_templates.md        # Reusable source templates per topic
    ├── scrape_sources_to_server.py       # Scraper (uses local API)
    ├── scrape_sources_pilot_on_server.sh # Server-side scrape wrapper
    ├── split_markdown_by_heading.py      # Chunk splitter for scraped markdown
    ├── validate_sources.py               # YAML source validation
    ├── README.md                         # Scraping instructions & hard rules
    └── WORKFLOW.md                       # Scaling workflow
```

## Datasets

The evaluation dataset contains **15 questions** across two subsets:

| Dataset        | Questions | Source |
|----------------|-----------|--------|
| `bzk_pilot`    | 10        | Ministerie van BZK VACs |
| `omgevingswet` | 5         | Omgevingswet VACs |

Each question has a `question_id` (VAC UUID), `question`, `ground_truth` answer, and `source_url`.

## Usage

```python
from load_evaluation_dataset import load_dataset, to_ragas_format

# Load all questions
dataset = load_dataset()

# Filter by subset
bzk_only = load_dataset(dataset_filter="bzk_pilot")

# Convert for RAGAS evaluation
ragas_dataset = to_ragas_format(dataset)
```

## Key Rules

- **Never** include VAC answer text as RAG context (that would be circular).
- **Exclude** any URL containing `/vraag-en-antwoord/` from RAG sources.
- The `rijksoverheid_vacs_*.md` files are reference only -- used to extract question text and stable IDs, not as RAG input.
