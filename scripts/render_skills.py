"""Render committed Kimiflow host skill files from repository sources."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
from typing import Iterable


CANONICAL_SOURCE = "docs/render/kimiflow/canonical/SKILL.md"
HOST_OVERLAYS = (("codex", "docs/render/kimiflow/overlays/codex.md", "skills/kimiflow/SKILL.md"), ("pi", "docs/render/kimiflow/overlays/pi.md", "hosts/pi/skills/kimiflow/SKILL.md"))
RENDER_TARGETS = ((CANONICAL_SOURCE, "SKILL.md"),) + tuple(
    (source, output) for _, source, output in HOST_OVERLAYS
)


def _write_if_changed(path: Path, data: str) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def render(root: Path, *, check: bool = False) -> list[str]:
    """Render all host skill outputs.

    Returns repository-relative output paths that differ from their sources.
    In check mode the files are not written.
    """

    root = root.resolve()
    # Validate every source and output before reading or writing any of them.
    # Generated destinations can otherwise escape through a symlinked parent.
    for pair in RENDER_TARGETS:
        for relative in pair:
            path = root
            for part in Path(relative).parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError(f"symlink in render path: {relative}")
            if path.exists() and not path.is_file():
                raise ValueError(f"render path is not a file: {relative}")
    canonical = root / CANONICAL_SOURCE
    if not canonical.is_file():
        raise FileNotFoundError(f"missing canonical render source: {CANONICAL_SOURCE}")

    changed: list[str] = []
    for source_rel, output_rel in RENDER_TARGETS:
        source = root / source_rel
        output = root / output_rel
        if not source.is_file():
            raise FileNotFoundError(f"missing render source: {source_rel}")
        data = source.read_text(encoding="utf-8")
        old = output.read_text(encoding="utf-8") if output.exists() else None
        if old == data:
            continue
        changed.append(output_rel)
        if not check:
            _write_if_changed(output, data)
    return changed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Kimiflow host skill outputs from the canonical source plus host overlays."
    )
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--check", action="store_true", help="report drift without writing outputs")
    parser.add_argument("--quiet", action="store_true", help="suppress success output")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    try:
        changed = render(root, check=args.check)
    except (OSError, ValueError) as exc:
        print(f"kimiflow render: {exc}", flush=True)
        return 2

    if changed:
        if args.check:
            print("kimiflow render: drift in " + ", ".join(changed), flush=True)
            return 1
        if not args.quiet:
            print("kimiflow render: wrote " + ", ".join(changed), flush=True)
        return 0

    if not args.quiet:
        print("kimiflow render: outputs current", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
