"""Normalize a TiForge run without modifying its raw events."""

from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from .normalize_runtime_events import build_nodes, build_run, load_events, normalize_events, save_jsonl


BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_FILE = BASE_DIR / "scratch" / "runtime_smoke_events.jsonl"
ARCHIVE_DIR = BASE_DIR / "archive"


def create_run_dir(archive_dir):
    run_id = f"tf-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:12]}"
    run_dir = archive_dir / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def archive_run(run_dir, metadata, capture_error=None, source_file="raw_events.jsonl"):
    events = load_events(run_dir / "raw_events.jsonl")
    run_id = run_dir.name
    nodes = build_nodes(events, run_id)
    run = build_run(events, nodes, run_id)
    if capture_error:
        run["capture_error"] = capture_error
        if run["status"] == "unknown":
            run["status"] = "failed"

    save_jsonl(run_dir / "runs.jsonl", [run])
    save_jsonl(run_dir / "nodes.jsonl", nodes)
    save_jsonl(run_dir / "events.jsonl", normalize_events(events, run_id))
    manifest = {
        **metadata,
        "run_id": run_id,
        "archived_at": datetime.now().astimezone().isoformat(),
        "source_file": source_file,
        "event_count": len(events),
        "node_count": len(nodes),
        "workflow_status": run["status"],
        "capture_error": capture_error,
        "raw_events_file": "raw_events.jsonl",
        "normalized_run_file": "runs.jsonl",
        "normalized_node_file": "nodes.jsonl",
        "normalized_event_file": "events.jsonl",
    }
    save_json(run_dir / "manifest.json", manifest)
    return manifest


def main():
    if not INPUT_FILE.is_file() or not load_events(INPUT_FILE):
        print(f"找不到有效的原始事件：{INPUT_FILE.resolve()}")
        return 1
    run_dir = create_run_dir(ARCHIVE_DIR)
    shutil.copy2(INPUT_FILE, run_dir / "raw_events.jsonl")
    archive_run(run_dir, {}, source_file=str(INPUT_FILE.resolve()))
    print(f"归档目录：{run_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
