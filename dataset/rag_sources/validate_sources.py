#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


URL_EXCLUSIONS_DEFAULT = ["/vraag-en-antwoord/"]


@dataclass(frozen=True)
class UrlIssue:
    url: str
    issue: str
    detail: str = ""


def _extract_question_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    question_key_re = re.compile(r'^\s*-\s*question_key:\s*"([^"]+)"\s*$')
    blocks: list[tuple[str, list[str]]] = []

    current_key: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = question_key_re.match(line)
        if match:
            if current_key is not None:
                blocks.append((current_key, current_lines))
            current_key = match.group(1)
            current_lines = []
            continue

        if current_key is not None:
            current_lines.append(line)

    if current_key is not None:
        blocks.append((current_key, current_lines))

    return blocks


def _extract_urls_from_question_block(lines: list[str]) -> list[str]:
    url_re = re.compile(r'^\s*-\s*url:\s*"([^"]+)"\s*$')
    return [m.group(1) for l in lines if (m := url_re.match(l))]


def _probe_url(url: str, *, timeout_seconds: float) -> UrlIssue | None:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if 200 <= int(status) < 400:
                return None
            return UrlIssue(url=url, issue="http_status", detail=str(status))
    except HTTPError as exc:
        # urllib may raise for some redirects; accept 3xx
        if 300 <= int(exc.code) < 400:
            return None
        return UrlIssue(url=url, issue="http_error", detail=str(exc.code))
    except URLError as exc:
        return UrlIssue(url=url, issue="url_error", detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI tool
        return UrlIssue(url=url, issue="exception", detail=f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RAG source YAML files (regex-based).")
    parser.add_argument("paths", nargs="+", help="YAML files to validate")
    parser.add_argument(
        "--exclude-substring",
        action="append",
        default=[],
        help="URL substrings to exclude (repeatable). Default: /vraag-en-antwoord/",
    )
    parser.add_argument(
        "--check-http",
        action="store_true",
        help="Probe each unique URL over HTTP (2xx/3xx ok).",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    url_exclusions = args.exclude_substring or URL_EXCLUSIONS_DEFAULT

    issues: list[UrlIssue] = []

    all_question_keys: list[str] = []
    all_urls: list[str] = []

    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.exists():
            issues.append(UrlIssue(url=str(path), issue="missing_file"))
            continue

        lines = path.read_text(encoding="utf-8").splitlines()
        blocks = _extract_question_blocks(lines)
        for question_key, block_lines in blocks:
            all_question_keys.append(question_key)
            urls = _extract_urls_from_question_block(block_lines)
            all_urls.extend(urls)

            # duplicates within question
            seen: set[str] = set()
            for u in urls:
                if u in seen:
                    issues.append(UrlIssue(url=u, issue="duplicate_within_question", detail=question_key))
                seen.add(u)

            # excluded patterns
            for u in urls:
                for bad in url_exclusions:
                    if bad in u:
                        issues.append(UrlIssue(url=u, issue="excluded_url", detail=bad))

    # duplicate question keys
    seen_q: set[str] = set()
    for q in all_question_keys:
        if q in seen_q:
            issues.append(UrlIssue(url=q, issue="duplicate_question_key"))
        seen_q.add(q)

    unique_urls = sorted(set(all_urls))

    if args.check_http and unique_urls:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(_probe_url, u, timeout_seconds=args.timeout_seconds): u for u in unique_urls}
            for fut in as_completed(futures):
                issue = fut.result()
                if issue is not None:
                    issues.append(issue)

    # report
    print(f"Questions: {len(all_question_keys)} (unique: {len(set(all_question_keys))})")
    print(f"URLs: {len(all_urls)} (unique: {len(unique_urls)})")
    print(f"Exclusions: {url_exclusions}")
    print(f"Issues: {len(issues)}")

    for issue in sorted(issues, key=lambda x: (x.issue, x.url)):
        if issue.detail:
            print(f"- {issue.issue}\t{issue.url}\t{issue.detail}")
        else:
            print(f"- {issue.issue}\t{issue.url}")

    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

