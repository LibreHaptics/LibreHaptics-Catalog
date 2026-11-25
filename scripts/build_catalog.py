#!/usr/bin/env python3
"""
LibreHaptics-Catalog generator (Schema v3, Wariant A — Zero-Token).

Reads sources.json, fetches each repository's librehaptics-mod.json template
and latest GitHub Release assets, resolves assetPattern -> downloadUrl + sha256,
and generates:
  - standalone-games.json (+ quest-games.json alias)
  - pcvr-games.json
  - pc-games.json
  - catalog.json
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT_DIR / "sources.json"
USER_AGENT = "LibreHaptics-Catalog-Builder/3.0"
SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")


def http_get_text(url: str, token: str | None = None, accept: str | None = None) -> str:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def http_get_json(url: str, token: str | None = None) -> Any:
    return json.loads(http_get_text(url, token=token, accept="application/vnd.github+json"))


def normalize_version(tag: str) -> str:
    cleaned = tag.strip()
    if cleaned.startswith(("v", "V")) and len(cleaned) > 1 and cleaned[1].isdigit():
        return cleaned[1:]
    return cleaned


def extract_sha256(asset: dict[str, Any], assets_by_name: dict[str, dict[str, Any]]) -> str:
    # 1. Native GitHub Releases API digest ("sha256:<64 hex>")
    digest = str(asset.get("digest") or "").strip()
    if digest.lower().startswith("sha256:"):
        cand = digest.split(":", 1)[1].strip().lower()
        if SHA256_RE.fullmatch(cand):
            return cand

    # 2. Companion sidecar file "<asset>.sha256"
    sidecar = assets_by_name.get(f"{asset['name']}.sha256")
    if sidecar and sidecar.get("browser_download_url"):
        try:
            body = http_get_text(sidecar["browser_download_url"])
            match = SHA256_RE.search(body)
            if match:
                return match.group(1).lower()
        except Exception as exc:
            print(f"  [warn] Failed reading sidecar {sidecar['name']}: {exc}", file=sys.stderr)

    return ""


def match_release_asset(
    step: dict[str, Any], assets: list[dict[str, Any]]
) -> dict[str, Any] | None:
    pattern = (step.get("assetPattern") or "").strip()
    file_name = (step.get("fileName") or "").strip()

    non_checksum = [a for a in assets if not str(a.get("name", "")).endswith(".sha256")]

    if pattern:
        for asset in non_checksum:
            if fnmatch.fnmatch(asset["name"], pattern):
                return asset

    if file_name:
        for asset in non_checksum:
            if asset["name"].lower() == file_name.lower():
                return asset

    return None


def resolve_steps(
    steps: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    assets_by_name: dict[str, dict[str, Any]],
    release_version: str,
    repo: str,
    strict: bool,
) -> None:
    for step in steps:
        matched = match_release_asset(step, assets)
        if matched is None:
            if step.get("downloadUrl"):
                continue
            msg = f"{repo}: no release asset matched pattern='{step.get('assetPattern')}' fileName='{step.get('fileName')}'"
            if strict:
                raise RuntimeError(msg)
            print(f"  [warn] {msg}", file=sys.stderr)
            continue

        step["fileName"] = matched["name"]
        step["downloadUrl"] = matched["browser_download_url"]
        sha = extract_sha256(matched, assets_by_name)
        if sha:
            step["sha256"] = sha
        elif strict:
            raise RuntimeError(f"{repo}: missing SHA-256 for asset '{matched['name']}'")

        if release_version and not step.get("version"):
            step["version"] = release_version


def resolve_mod_entry(
    source: dict[str, Any], token: str | None, strict: bool
) -> dict[str, Any]:
    repo = source["repo"].strip()
    branch = (source.get("branch") or "main").strip()
    manifest_path = (source.get("manifestPath") or "librehaptics-mod.json").strip().lstrip("/")

    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{manifest_path}"
    entry = http_get_json(raw_url)

    if not isinstance(entry, dict) or not entry.get("id") or not entry.get("name"):
        raise RuntimeError(f"{repo}: invalid manifest (missing 'id' or 'name')")

    release = http_get_json(f"https://api.github.com/repos/{repo}/releases/latest", token=token)
    tag_name = str(release.get("tag_name") or "").strip()
    release_version = normalize_version(tag_name) if tag_name else str(entry.get("version") or "1.0.0")
    assets = release.get("assets") or []
    assets_by_name = {str(a.get("name")): a for a in assets if a.get("name")}

    entry["schemaVersion"] = 3
    if release_version:
        entry["version"] = release_version

    targets = entry.get("targets")
    if isinstance(targets, dict):
        for target_spec in targets.values():
            if not isinstance(target_spec, dict):
                continue
            install = target_spec.get("install")
            if isinstance(install, dict) and isinstance(install.get("steps"), list):
                resolve_steps(install["steps"], assets, assets_by_name, release_version, repo, strict)
            if isinstance(target_spec.get("artifacts"), list):
                resolve_steps(target_spec["artifacts"], assets, assets_by_name, release_version, repo, strict)

    root_install = entry.get("install")
    if isinstance(root_install, dict) and isinstance(root_install.get("steps"), list):
        resolve_steps(root_install["steps"], assets, assets_by_name, release_version, repo, strict)
    if isinstance(entry.get("artifacts"), list):
        resolve_steps(entry["artifacts"], assets, assets_by_name, release_version, repo, strict)

    return entry


def resolve_pack_repo(
    source: dict[str, Any], token: str | None, strict: bool
) -> list[dict[str, Any]]:
    repo = source["repo"].strip()
    branch = (source.get("branch") or "main").strip()
    index_path = (source.get("indexPath") or "librehaptics-packs.json").strip().lstrip("/")

    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{index_path}"
    index_data = http_get_json(raw_url)
    pack_entries = index_data.get("games") or index_data.get("packs") or []
    if not isinstance(pack_entries, list):
        return []

    release = http_get_json(f"https://api.github.com/repos/{repo}/releases/latest", token=token)
    tag_name = str(release.get("tag_name") or "").strip()
    release_version = normalize_version(tag_name) if tag_name else "1.0.0"
    assets = release.get("assets") or []
    assets_by_name = {str(a.get("name")): a for a in assets if a.get("name")}

    resolved: list[dict[str, Any]] = []
    for raw_entry in pack_entries:
        if not isinstance(raw_entry, dict) or not raw_entry.get("id"):
            continue
        entry = copy.deepcopy(raw_entry)
        entry["schemaVersion"] = 3
        entry.setdefault("version", release_version)

        targets = entry.get("targets")
        if isinstance(targets, dict):
            for target_spec in targets.values():
                if isinstance(target_spec, dict):
                    install = target_spec.get("install")
                    if isinstance(install, dict) and isinstance(install.get("steps"), list):
                        resolve_steps(install["steps"], assets, assets_by_name, release_version, repo, strict)
        root_install = entry.get("install")
        if isinstance(root_install, dict) and isinstance(root_install.get("steps"), list):
            resolve_steps(root_install["steps"], assets, assets_by_name, release_version, repo, strict)

        resolved.append(entry)

    return resolved


def load_previous_by_id() -> dict[str, dict[str, Any]]:
    catalog_path = ROOT_DIR / "catalog.json"
    if not catalog_path.exists():
        return {}
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        return {
            g["id"]: g
            for g in (data.get("games") or [])
            if isinstance(g, dict) and g.get("id")
        }
    except Exception:
        return {}


def has_category(entry: dict[str, Any], *names: str) -> bool:
    cats = {str(c).strip().lower() for c in (entry.get("categories") or [])}
    return any(n in cats for n in names)


def is_standalone(entry: dict[str, Any]) -> bool:
    targets = entry.get("targets")
    if isinstance(targets, dict) and targets.get("standalone") is not None:
        return True
    if has_category(entry, "standalone", "quest"):
        return True
    if not isinstance(targets, dict) and not has_category(entry, "pcvr", "pc", "pc-flat"):
        return True
    return False


def is_pcvr(entry: dict[str, Any]) -> bool:
    targets = entry.get("targets")
    if isinstance(targets, dict) and targets.get("pcvr") is not None:
        return True
    return has_category(entry, "pcvr")


def is_pc_any(entry: dict[str, Any]) -> bool:
    targets = entry.get("targets")
    if isinstance(targets, dict) and (targets.get("pcvr") is not None or targets.get("pc") is not None):
        return True
    return has_category(entry, "pcvr", "pc", "pc-flat")


def write_catalog_file(path: Path, games: list[dict[str, Any]]) -> None:
    payload = {
        "schemaVersion": 3,
        "games": sorted(games, key=lambda g: str(g.get("id", "")).lower()),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LibreHaptics Schema v3 catalogs from sources.json.")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing output JSON files.")
    parser.add_argument("--strict", action="store_true", help="Fail on any unreachable repo, missing release, or missing SHA-256.")
    args = parser.parse_args()

    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    token = os.environ.get("GITHUB_TOKEN") or None
    previous = load_previous_by_id()

    entries_by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for mod_src in sources.get("modRepositories") or []:
        repo = mod_src.get("repo", "<unknown>")
        print(f"[mod] {repo} ({mod_src.get('branch', 'main')})")
        try:
            entry = resolve_mod_entry(mod_src, token=token, strict=args.strict)
            entries_by_id[entry["id"]] = entry
        except Exception as exc:
            errors.append(f"{repo}: {exc}")
            print(f"  [error] {exc}", file=sys.stderr)
            fallback_id = repo.split("/")[-1]
            if not args.strict and fallback_id in previous:
                print(f"  [fallback] keeping previous entry for '{fallback_id}'", file=sys.stderr)
                entries_by_id[fallback_id] = previous[fallback_id]

    for pack_src in sources.get("packRepositories") or []:
        repo = pack_src.get("repo", "<unknown>")
        print(f"[packs] {repo} ({pack_src.get('branch', 'main')})")
        try:
            for entry in resolve_pack_repo(pack_src, token=token, strict=args.strict):
                entries_by_id[entry["id"]] = entry
        except Exception as exc:
            errors.append(f"{repo}: {exc}")
            print(f"  [error] {exc}", file=sys.stderr)

    for fed in sources.get("federatedManifests") or []:
        if isinstance(fed, dict) and fed.get("id") and fed.get("manifestUrl"):
            entries_by_id[fed["id"]] = fed

    if args.strict and errors:
        print(f"\nStrict validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1

    all_games = list(entries_by_id.values())
    standalone_games = [g for g in all_games if is_standalone(g)]
    pcvr_games = [g for g in all_games if is_pcvr(g)]
    pc_games = [g for g in all_games if is_pc_any(g)]

    print(
        f"\nResolved: total={len(all_games)}, standalone={len(standalone_games)}, "
        f"pcvr={len(pcvr_games)}, pc={len(pc_games)}"
    )

    if args.dry_run:
        print("Dry-run complete — no files modified.")
        return 0

    write_catalog_file(ROOT_DIR / "catalog.json", all_games)
    write_catalog_file(ROOT_DIR / "standalone-games.json", standalone_games)
    write_catalog_file(ROOT_DIR / "quest-games.json", standalone_games)
    write_catalog_file(ROOT_DIR / "pcvr-games.json", pcvr_games)
    write_catalog_file(ROOT_DIR / "pc-games.json", pc_games)
    print("Catalog files updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
