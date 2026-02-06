#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SourceToScrape:
    url: str
    title: str | None
    publisher: str | None
    source_type: str | None


@dataclass(frozen=True)
class QuestionToScrape:
    dataset_id: str
    question_key: str
    question_text: str
    sources: list[SourceToScrape]


def _read_text(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "source"


def _short_hash(value: str, *, length: int = 10) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length]


def _extract_questions_from_yaml(lines: list[str]) -> list[QuestionToScrape]:
    dataset_id_re = re.compile(r'^\s*-\s*dataset_id:\s*"([^"]+)"\s*$')
    question_key_re = re.compile(r'^\s*-\s*question_key:\s*"([^"]+)"\s*$')
    question_text_re = re.compile(r'^\s*question_text:\s*"([^"]+)"\s*$')

    url_re = re.compile(r'^\s*-\s*url:\s*"([^"]+)"\s*$')
    title_re = re.compile(r'^\s*title:\s*"([^"]+)"\s*$')
    publisher_re = re.compile(r'^\s*publisher:\s*"([^"]+)"\s*$')
    source_type_re = re.compile(r'^\s*source_type:\s*"([^"]+)"\s*$')

    current_dataset_id: str | None = None
    current_question_key: str | None = None
    current_question_text: str | None = None
    current_sources: list[SourceToScrape] = []

    current_source_url: str | None = None
    current_source_title: str | None = None
    current_source_publisher: str | None = None
    current_source_type: str | None = None

    questions: list[QuestionToScrape] = []

    def flush_source() -> None:
        nonlocal current_source_url, current_source_title, current_source_publisher, current_source_type
        if current_source_url is None:
            return
        current_sources.append(
            SourceToScrape(
                url=current_source_url,
                title=current_source_title,
                publisher=current_source_publisher,
                source_type=current_source_type,
            )
        )
        current_source_url = None
        current_source_title = None
        current_source_publisher = None
        current_source_type = None

    def flush_question() -> None:
        nonlocal current_question_key, current_question_text, current_sources
        flush_source()
        if current_dataset_id and current_question_key and current_question_text:
            questions.append(
                QuestionToScrape(
                    dataset_id=current_dataset_id,
                    question_key=current_question_key,
                    question_text=current_question_text,
                    sources=current_sources,
                )
            )
        current_question_key = None
        current_question_text = None
        current_sources = []

    for line in lines:
        dataset_match = dataset_id_re.match(line)
        if dataset_match:
            flush_question()
            current_dataset_id = dataset_match.group(1)
            continue

        question_match = question_key_re.match(line)
        if question_match:
            flush_question()
            current_question_key = question_match.group(1)
            continue

        question_text_match = question_text_re.match(line)
        if question_text_match:
            current_question_text = question_text_match.group(1)
            continue

        url_match = url_re.match(line)
        if url_match:
            flush_source()
            current_source_url = url_match.group(1)
            continue

        title_match = title_re.match(line)
        if title_match and current_source_url is not None:
            current_source_title = title_match.group(1)
            continue

        publisher_match = publisher_re.match(line)
        if publisher_match and current_source_url is not None:
            current_source_publisher = publisher_match.group(1)
            continue

        source_type_match = source_type_re.match(line)
        if source_type_match and current_source_url is not None:
            current_source_type = source_type_match.group(1)
            continue

    flush_question()
    return questions


def _post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected dict JSON response from scraper API, got {type(parsed).__name__}")
    return parsed


def scrape_url_markdown(
    api_url: str,
    *,
    url: str,
    headless: bool,
    use_proxies: bool,
    timeout_ms: int,
) -> str:
    # Allow a little extra on the HTTP request beyond the scraper's internal timeout.
    http_timeout_seconds = max(5.0, (timeout_ms / 1000.0) + 15.0)
    payload = {
        "url": url,
        "headless": headless,
        "use_proxies": use_proxies,
        "timeout_ms": timeout_ms,
    }
    try:
        result = _post_json(api_url, payload, timeout_seconds=http_timeout_seconds)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Scraper API request failed for {url}: {exc}") from exc

    status = result.get("status")
    if status != "success":
        raise RuntimeError(f"Scraper returned non-success for {url}: {result!r}")

    data = result.get("data")
    if not isinstance(data, str):
        raise RuntimeError(f"Scraper returned non-string data for {url}: {type(data).__name__}")

    return data


def _run_ssh(ssh_target: str, remote_command: str) -> None:
    subprocess.run(["ssh", ssh_target, remote_command], check=True)


def _mkdir_remote(ssh_target: str, remote_dir: str) -> None:
    command = f"mkdir -p {shlex.quote(remote_dir)}"
    _run_ssh(ssh_target, command)


def _scp_dir_to_remote_parent(ssh_target: str, local_dir: Path, remote_parent_dir: str) -> None:
    # Copies local_dir into remote_parent_dir, creating remote_parent_dir/local_dir.name
    subprocess.run(
        ["scp", "-r", str(local_dir), f"{ssh_target}:{remote_parent_dir}"],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape URLs from sources_pilot.yaml via scraper API and store markdowns per VAC on a remote server.",
    )
    parser.add_argument(
        "--input",
        default="govtech_hackathon/golden/rag_sources/sources_pilot.yaml",
        help="Input YAML file (default: sources_pilot.yaml).",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:9501/scrape_url_markdown",
        help="Scraper API URL (default: http://localhost:9501/scrape_url_markdown).",
    )
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--use-proxies", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--timeout-ms", type=int, default=45_000)

    parser.add_argument(
        "--ssh-target",
        required=True,
        help='SSH target for storing files, e.g. "user@myserver".',
    )
    parser.add_argument(
        "--remote-base-dir",
        required=True,
        help="Remote base directory to store datasets under.",
    )

    parser.add_argument("--dataset-id", default="", help="Only scrape one dataset_id (e.g. bzk_pilot).")
    parser.add_argument("--question-key", default="", help="Only scrape one question_key (VAC UUID).")
    parser.add_argument("--only-url", default="", help="Only scrape this URL (must exist in the YAML).")
    parser.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="Limit number of VAC folders to process (0 = no limit).",
    )
    parser.add_argument(
        "--max-urls",
        type=int,
        default=0,
        help="Limit total number of URL scrapes (0 = no limit).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done, but do not call the scraper API or write remotely.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually scrape and upload to the remote server.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help='Skip interactive confirmation (only used with --execute).',
    )

    args = parser.parse_args()

    if args.execute and args.dry_run:
        raise SystemExit("Use either --dry-run or --execute, not both.")

    if not args.execute and not args.dry_run:
        # Safety default.
        print("No mode selected. Defaulting to --dry-run.\n", file=sys.stderr)
        args.dry_run = True

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    questions = _extract_questions_from_yaml(_read_text(input_path))

    # Filters
    if args.dataset_id:
        questions = [q for q in questions if q.dataset_id == args.dataset_id]

    if args.question_key:
        questions = [q for q in questions if q.question_key == args.question_key]

    if args.only_url:
        questions = [
            QuestionToScrape(
                dataset_id=q.dataset_id,
                question_key=q.question_key,
                question_text=q.question_text,
                sources=[s for s in q.sources if s.url == args.only_url],
            )
            for q in questions
        ]
        questions = [q for q in questions if q.sources]

    if args.max_questions and args.max_questions > 0:
        questions = questions[: args.max_questions]

    if not questions:
        print("No matching questions found after filters.", file=sys.stderr)
        return 2

    remote_base_dir = args.remote_base_dir.rstrip("/")
    dataset_ids = sorted({q.dataset_id for q in questions})

    total_sources = sum(len(q.sources) for q in questions)
    unique_urls = sorted({s.url for q in questions for s in q.sources})

    print("Plan")
    print(f"- Input: {input_path}")
    print(f"- API: {args.api_url}")
    print(f"- SSH target: {args.ssh_target}")
    print(f"- Remote base dir: {remote_base_dir}")
    print(f"- Datasets: {', '.join(dataset_ids)}")
    print(f"- VAC folders: {len(questions)}")
    print(f"- Sources (total): {total_sources}")
    print(f"- Sources (unique URLs): {len(unique_urls)}")
    if args.max_urls:
        print(f"- Max URL scrapes: {args.max_urls}")
    if args.dry_run:
        print("- Mode: DRY RUN (no scraping, no remote writes)")
    else:
        print("- Mode: EXECUTE (scrape + upload)")

    print("\nRemote layout (per VAC)")
    example = questions[0]
    print(f"- {remote_base_dir}/{example.dataset_id}/{example.question_key}/")

    if args.dry_run:
        return 0

    if not args.yes:
        print("\nAbout to create/update directories on the remote server.")
        print(f"Remote base dir: {args.ssh_target}:{remote_base_dir}")
        confirmation = input('Type \"yes\" to proceed: ').strip().lower()
        if confirmation != "yes":
            print("Aborted.")
            return 3

    # Create remote base + dataset dirs
    _mkdir_remote(args.ssh_target, remote_base_dir)
    for ds in dataset_ids:
        _mkdir_remote(args.ssh_target, f"{remote_base_dir}/{ds}")

    scraped_cache: dict[str, str] = {}
    scraped_count = 0

    with tempfile.TemporaryDirectory(prefix="theon_rag_scrapes_") as tmpdir:
        staging_root = Path(tmpdir) / "rag_scrapes"
        staging_root.mkdir(parents=True, exist_ok=True)

        for q in questions:
            if args.max_urls and scraped_count >= args.max_urls:
                break

            local_question_dir = staging_root / q.dataset_id / q.question_key
            local_question_dir.mkdir(parents=True, exist_ok=True)
            local_sources_dir = local_question_dir / "sources"
            local_sources_dir.mkdir(parents=True, exist_ok=True)

            manifest: dict[str, Any] = {
                "dataset_id": q.dataset_id,
                "question_key": q.question_key,
                "question_text": q.question_text,
                "scraped_at_unix": int(time.time()),
                "scraper_api_url": args.api_url,
                "sources": [],
            }

            for idx, source in enumerate(q.sources, start=1):
                if args.max_urls and scraped_count >= args.max_urls:
                    break

                parsed = urlparse(source.url)
                host = parsed.netloc or "unknown_host"
                url_slug = _slugify(f"{host}{parsed.path}")
                url_hash = _short_hash(source.url)
                filename = f"{idx:03d}__{url_slug}__{url_hash}.md"
                out_path = local_sources_dir / filename

                entry: dict[str, Any] = {
                    "url": source.url,
                    "title": source.title,
                    "publisher": source.publisher,
                    "source_type": source.source_type,
                    "file": f"sources/{filename}",
                    "status": "pending",
                }

                try:
                    if source.url in scraped_cache:
                        markdown = scraped_cache[source.url]
                    else:
                        markdown = scrape_url_markdown(
                            args.api_url,
                            url=source.url,
                            headless=bool(args.headless),
                            use_proxies=bool(args.use_proxies),
                            timeout_ms=int(args.timeout_ms),
                        )
                        scraped_cache[source.url] = markdown

                    out_path.write_text(markdown, encoding="utf-8")
                    entry["status"] = "success"
                    entry["chars"] = len(markdown)
                    scraped_count += 1

                except Exception as exc:  # noqa: BLE001 - CLI tool: record and continue
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    # Still write a file for traceability.
                    out_path.write_text(f"SCRAPE_ERROR\n\nURL: {source.url}\n\n{exc}\n", encoding="utf-8")
                    scraped_count += 1

                manifest["sources"].append(entry)

            (local_question_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            remote_dataset_dir = f"{remote_base_dir}/{q.dataset_id}"
            _scp_dir_to_remote_parent(args.ssh_target, local_question_dir, remote_dataset_dir)

    print(f"\nDone. Uploaded {min(scraped_count, total_sources)} source files to {args.ssh_target}:{remote_base_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

