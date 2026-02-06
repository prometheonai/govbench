#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class MarkdownSection:
    index: int
    heading_level: int  # 0 for preamble / no-heading
    heading_text: Optional[str]
    breadcrumb_texts: List[str]
    breadcrumb_slugs: List[str]
    line_start: int
    line_end: int
    lines: List[str]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_START_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


def _slugify(text: str, *, max_len: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_text.lower()
    spaced = re.sub(r"\s+", "_", lowered.strip())
    safe = re.sub(r"[^a-z0-9._-]+", "_", spaced)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    if not safe:
        return "section"
    if len(safe) <= max_len:
        return safe
    return safe[:max_len].rstrip("._-") or "section"


@dataclass
class _OpenSection:
    heading_level: int
    heading_text: Optional[str]
    breadcrumb_texts: List[str]
    breadcrumb_slugs: List[str]
    line_start: int
    lines: List[str]


def _reindex_sections(sections: List[MarkdownSection]) -> List[MarkdownSection]:
    return [
        MarkdownSection(
            index=i,
            heading_level=s.heading_level,
            heading_text=s.heading_text,
            breadcrumb_texts=s.breadcrumb_texts,
            breadcrumb_slugs=s.breadcrumb_slugs,
            line_start=s.line_start,
            line_end=s.line_end,
            lines=s.lines,
        )
        for i, s in enumerate(sections)
    ]


class _MarkdownHeadingSplitter:
    def __init__(self, *, include_preamble: bool) -> None:
        self.include_preamble = include_preamble

        self.fence_char: Optional[str] = None
        self.fence_len = 0

        self.heading_stack: List[Tuple[int, str, str]] = []  # (level, text, slug)
        self.sections: List[MarkdownSection] = []

        self.preamble_lines: List[str] = []
        self.current: Optional[_OpenSection] = None

    def split(self, markdown_text: str) -> List[MarkdownSection]:
        lines = markdown_text.splitlines(keepends=True)

        for line_no, line in enumerate(lines, start=1):
            self._update_fence_state(line)

            heading = self._parse_heading(line)
            if heading is None:
                self._append_non_heading_line(line)
                continue

            level, heading_text = heading
            self._start_heading_section(line_no=line_no, line=line, level=level, heading_text=heading_text)

        self._finalize(total_lines=len(lines))
        return _reindex_sections(self.sections)

    def _update_fence_state(self, line: str) -> None:
        match = _FENCE_START_RE.match(line)
        if not match:
            return

        fence = match.group(1)
        ch = fence[0]
        ln = len(fence)

        if self.fence_char is None:
            self.fence_char = ch
            self.fence_len = ln
            return

        if ch == self.fence_char and ln >= self.fence_len:
            self.fence_char = None
            self.fence_len = 0

    def _parse_heading(self, line: str) -> Optional[Tuple[int, str]]:
        if self.fence_char is not None:
            return None

        match = _HEADING_RE.match(line)
        if not match:
            return None

        level = len(match.group(1))
        heading_text = match.group(2).strip()
        return level, heading_text

    def _append_non_heading_line(self, line: str) -> None:
        if self.current is None:
            self.preamble_lines.append(line)
            return
        self.current.lines.append(line)

    def _start_heading_section(self, *, line_no: int, line: str, level: int, heading_text: str) -> None:
        self._flush_current(line_end=line_no - 1)
        if self.include_preamble:
            self._flush_preamble(line_end=line_no - 1, label="preamble")
        else:
            self.preamble_lines = []
        self._update_heading_stack(level=level, heading_text=heading_text)

        breadcrumb_texts = [t for _, t, _ in self.heading_stack]
        breadcrumb_slugs = [s for _, _, s in self.heading_stack]
        self.current = _OpenSection(
            heading_level=level,
            heading_text=heading_text,
            breadcrumb_texts=breadcrumb_texts,
            breadcrumb_slugs=breadcrumb_slugs,
            line_start=line_no,
            lines=[line],
        )

    def _update_heading_stack(self, *, level: int, heading_text: str) -> None:
        while self.heading_stack and self.heading_stack[-1][0] >= level:
            self.heading_stack.pop()
        self.heading_stack.append((level, heading_text, _slugify(heading_text)))

    def _flush_current(self, *, line_end: int) -> None:
        if self.current is None:
            return

        open_section = self.current
        self.sections.append(
            MarkdownSection(
                index=len(self.sections),
                heading_level=open_section.heading_level,
                heading_text=open_section.heading_text,
                breadcrumb_texts=open_section.breadcrumb_texts,
                breadcrumb_slugs=open_section.breadcrumb_slugs,
                line_start=open_section.line_start,
                line_end=line_end,
                lines=open_section.lines,
            )
        )
        self.current = None

    def _flush_preamble(self, *, line_end: int, label: str) -> None:
        if not self.preamble_lines:
            return
        if not any(s.strip() for s in self.preamble_lines):
            self.preamble_lines = []
            return

        self.sections.append(
            MarkdownSection(
                index=len(self.sections),
                heading_level=0,
                heading_text=None,
                breadcrumb_texts=[label],
                breadcrumb_slugs=[label],
                line_start=1,
                line_end=line_end,
                lines=self.preamble_lines,
            )
        )
        self.preamble_lines = []

    def _finalize(self, *, total_lines: int) -> None:
        self._flush_current(line_end=total_lines)
        self._flush_preamble(line_end=total_lines, label="document")
        if self.sections:
            return

        self.sections.append(
            MarkdownSection(
                index=0,
                heading_level=0,
                heading_text=None,
                breadcrumb_texts=["empty"],
                breadcrumb_slugs=["empty"],
                line_start=1,
                line_end=0,
                lines=[],
            )
        )


def _split_sections(markdown_text: str, *, include_preamble: bool) -> List[MarkdownSection]:
    return _MarkdownHeadingSplitter(include_preamble=include_preamble).split(markdown_text)


def _write_sections(
    *,
    source_file: Path,
    output_dir: Path,
    sections: List[MarkdownSection],
    overwrite: bool,
) -> None:
    max_breadcrumb_slug_len = 200  # keep filenames well below filesystem limits
    if output_dir.exists():
        if not overwrite:
            raise SystemExit(f"Output dir already exists (use --overwrite): {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    width = max(4, len(str(len(sections))))
    manifest: dict[str, object] = {
        "source_file": str(source_file),
        "output_dir": str(output_dir),
        "sections": [],
    }

    for section in sections:
        breadcrumbs_slug = "__".join(section.breadcrumb_slugs) if section.breadcrumb_slugs else "section"
        breadcrumbs_slug = breadcrumbs_slug[:max_breadcrumb_slug_len].rstrip("._-") or "section"
        file_name = f"{section.index:0{width}d}__{breadcrumbs_slug}.md"
        out_path = output_dir / file_name
        out_path.write_text("".join(section.lines), encoding="utf-8")

        manifest["sections"].append(
            {
                "index": section.index,
                "heading_level": section.heading_level,
                "heading_text": section.heading_text,
                "breadcrumbs": section.breadcrumb_texts,
                "file": file_name,
                "line_start": section.line_start,
                "line_end": section.line_end,
            }
        )

    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iter_markdown_files(input_path: Path, *, recursive: bool) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise SystemExit(f"Input path not found: {input_path}")

    pattern = "**/*.md" if recursive else "*.md"
    return sorted(p for p in input_path.glob(pattern) if p.is_file())


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Split a markdown file into sub-markdown files per heading (#, ##, ###, ...).",
    )
    parser.add_argument("--input", required=True, help="Markdown file or directory.")
    parser.add_argument(
        "--output-root",
        help=(
            "Where to write splits. "
            "If omitted, writes next to each input file as <file_stem>__split/."
        ),
    )
    parser.add_argument("--recursive", action="store_true", help="If --input is a directory, recurse into subfolders.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output directories.")
    parser.add_argument(
        "--skip-preamble",
        action="store_true",
        help="Do not write a preamble section for text before the first heading.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_root = Path(args.output_root).resolve() if args.output_root else None
    recursive = bool(args.recursive)
    overwrite = bool(args.overwrite)
    include_preamble = not bool(args.skip_preamble)

    markdown_files = _iter_markdown_files(input_path, recursive=recursive)
    if not markdown_files:
        print(f"No .md files found under: {input_path}", file=sys.stderr)
        return 2

    for md_file in markdown_files:
        markdown_text = md_file.read_text(encoding="utf-8")
        sections = _split_sections(markdown_text, include_preamble=include_preamble)

        if output_root is None:
            out_dir = md_file.parent / f"{md_file.stem}__split"
        else:
            if input_path.is_dir():
                rel = md_file.resolve().relative_to(input_path.resolve())
                out_dir = output_root / rel.parent / md_file.stem
            else:
                out_dir = output_root / md_file.stem

        _write_sections(source_file=md_file, output_dir=out_dir, sections=sections, overwrite=overwrite)
        print(f"Wrote {len(sections)} sections -> {out_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

