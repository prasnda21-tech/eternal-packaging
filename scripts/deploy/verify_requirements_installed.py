#!/usr/bin/env python3
"""Verify a requirements file against an installed environment (no network).

Uses packaging.requirements.Requirement and importlib.metadata.
Direct URL installs (PEP 508 name @ url) must match PEP 610 direct_url.json
(url + optional archive hash). Does not skip URL lines or trust name-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from importlib.metadata import Distribution, distributions
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


def _die(msg: str) -> None:
    print(f"[eternal-deploy] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _split_url(url: str) -> tuple[str, str | None]:
    base, frag = urldefrag(url.strip())
    frag = frag.strip() or None
    return base, frag


def _installed_archive_hash(direct: dict[str, Any]) -> str | None:
    archive = direct.get("archive_info")
    if not isinstance(archive, dict):
        return None
    h = archive.get("hash")
    if not h or not isinstance(h, str):
        return None
    return h.strip()


def _find_dist(name: str, search_paths: list[str] | None) -> Distribution | None:
    want = canonicalize_name(name)
    dists = (
        distributions(path=search_paths)
        if search_paths is not None
        else distributions()
    )
    for dist in dists:
        meta_name = dist.metadata.get("Name")
        if not meta_name:
            continue
        if canonicalize_name(meta_name) == want:
            return dist
    return None


def _parse_requirement(line: str) -> Requirement | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith("-"):
        _die(f"dependency evidence: unsupported option line: {raw}")
    try:
        return Requirement(raw)
    except InvalidRequirement:
        # Trailing "# comment" on pin lines only (never strip URL fragments).
        if "://" not in raw and " @ " not in raw and " #" in raw:
            try:
                return Requirement(raw.split(" #", 1)[0].strip())
            except InvalidRequirement as exc:
                _die(f"dependency evidence: cannot parse requirements line: {raw} ({exc})")
        _die(f"dependency evidence: cannot parse requirements line: {raw}")


def _verify_direct_url(req: Requirement, dist: Distribution) -> None:
    assert req.url
    try:
        text = dist.read_text("direct_url.json")
    except Exception as exc:  # noqa: BLE001 — surface as evidence failure
        _die(
            f"dependency evidence: cannot read direct_url.json for {req.name} "
            f"(PEP 610): {exc}"
        )
    if text is None:
        _die(
            f"dependency evidence: direct_url.json missing for {req.name} "
            f"(PEP 610); cannot verify URL install from {req.url}"
        )
    try:
        direct = json.loads(text)
    except json.JSONDecodeError as exc:
        _die(f"dependency evidence: direct_url.json invalid JSON for {req.name}: {exc}")
    if not isinstance(direct, dict):
        _die(f"dependency evidence: direct_url.json not an object for {req.name}")

    installed_url = direct.get("url")
    if not installed_url or not isinstance(installed_url, str):
        _die(
            f"dependency evidence: direct_url.json missing url for {req.name}; "
            f"required {req.url}"
        )

    req_base, req_frag = _split_url(req.url)
    inst_base, _ = _split_url(installed_url)
    if req_base != inst_base:
        _die(
            f"dependency evidence: direct URL mismatch for {req.name}: "
            f"required={req_base} installed={inst_base}"
        )

    if req_frag:
        # Prefer archive_info.hash (PEP 610); also accept fragment on stored url.
        installed_hash = _installed_archive_hash(direct)
        if installed_hash is None:
            _, inst_frag = _split_url(installed_url)
            installed_hash = inst_frag
        if not installed_hash:
            _die(
                f"dependency evidence: direct_url.json missing hash for {req.name}; "
                f"required fragment {req_frag}"
            )
        # Normalize sha256=hex vs bare hex
        def norm_hash(h: str) -> str:
            h = h.strip().lower()
            if h.startswith("sha256="):
                return h
            if len(h) == 64 and all(c in "0123456789abcdef" for c in h):
                return f"sha256={h}"
            return h

        if norm_hash(installed_hash) != norm_hash(req_frag):
            _die(
                f"dependency evidence: direct URL hash mismatch for {req.name}: "
                f"required={req_frag} installed={installed_hash}"
            )


def verify_file(req_path: Path, search_paths: list[str] | None) -> None:
    if not req_path.is_file():
        _die(f"dependency evidence: requirements missing: {req_path}")

    checked = 0
    for lineno, line in enumerate(req_path.read_text(encoding="utf-8").splitlines(), 1):
        req = _parse_requirement(line)
        if req is None:
            continue
        if req.marker is not None and not req.marker.evaluate():
            continue

        dist = _find_dist(req.name, search_paths)
        if dist is None:
            _die(
                f"dependency evidence: package not installed: {req.name} "
                f"(requirements line {lineno})"
            )

        version = dist.version
        if req.specifier and version not in req.specifier:
            _die(
                f"dependency evidence: version mismatch for {req.name}: "
                f"required={req.specifier} installed={version}"
            )

        if req.url:
            _verify_direct_url(req, dist)
        elif not req.specifier:
            # Bare name: only prove presence (already found).
            pass

        checked += 1

    if checked == 0:
        _die(f"dependency evidence: no verifiable requirements lines in {req_path}")
    print(f"dependency evidence OK lines={checked} file={req_path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", type=Path)
    parser.add_argument(
        "--purelib",
        action="append",
        default=None,
        help="Restrict distribution search to this path (repeatable). "
        "Default: active interpreter environment.",
    )
    args = parser.parse_args(argv)
    verify_file(args.requirements, args.purelib)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
