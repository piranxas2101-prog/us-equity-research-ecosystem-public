from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.run_manifest.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_state(project_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=project_root, check=True, capture_output=True, text=True
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def capture_manifest(
    *,
    project_root: Path,
    run_id: str,
    artifacts: list[Path],
    configs: list[Path],
    runs_dir: Path,
) -> Path:
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in run_id):
        raise ValueError("run_id must contain only letters, digits, dot, underscore, or hyphen.")
    run_dir = runs_dir / run_id
    artifact_dir = run_dir / "artifacts"
    run_dir.mkdir(parents=True, exist_ok=False)
    artifact_dir.mkdir()

    records: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for requested in artifacts:
        source = requested if requested.is_absolute() else project_root / requested
        if not source.exists() or not source.is_file():
            records.append({"source": str(requested), "status": "missing"})
            continue
        name = source.name
        if name in seen_names:
            name = f"{source.parent.name}_{name}"
        seen_names.add(name)
        destination = artifact_dir / name
        shutil.copy2(source, destination)
        records.append(
            {
                "source": str(source.resolve()),
                "snapshot": str(destination.resolve()),
                "status": "captured",
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )

    config_records = []
    for requested in configs:
        source = requested if requested.is_absolute() else project_root / requested
        config_records.append(
            {
                "path": str(source.resolve()),
                "exists": source.exists(),
                "sha256": sha256_file(source) if source.exists() and source.is_file() else None,
            }
        )

    manifest = {
        "schema": SCHEMA,
        "run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root.resolve()),
        "git": git_state(project_root),
        "configs": config_records,
        "artifacts": records,
        "captured_count": sum(item["status"] == "captured" for item in records),
        "missing_count": sum(item["status"] == "missing" for item in records),
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture immutable, checksummed artifacts for one ecosystem run.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", default="output/runs")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--config", action="append", default=[])
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    path = capture_manifest(
        project_root=project_root,
        run_id=args.run_id,
        artifacts=[Path(item) for item in args.artifact],
        configs=[Path(item) for item in args.config],
        runs_dir=project_root / args.runs_dir,
    )
    print(f"[MANIFEST] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
