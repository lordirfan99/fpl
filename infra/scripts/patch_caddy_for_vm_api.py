#!/usr/bin/env python3
"""Add one site-block import for the VM API without rewriting other routes."""
from __future__ import annotations

import argparse
from pathlib import Path


def render(source: str, import_path: str) -> str:
    directive = f"import {import_path}"
    lines = source.splitlines(keepends=True)
    existing = [line for line in lines if line.strip() == directive]
    if len(existing) > 1:
        raise ValueError("Caddy import appears more than once")
    if existing:
        return source

    catch_all = [index for index, line in enumerate(lines) if line.strip() == "handle {"]
    if len(catch_all) != 1:
        raise ValueError("Expected exactly one catch-all handle block")
    index = catch_all[0]
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines.insert(index, f"{indent}{directive}\n\n")
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("import_path")
    args = parser.parse_args()
    args.output.write_text(render(args.source.read_text(), args.import_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
