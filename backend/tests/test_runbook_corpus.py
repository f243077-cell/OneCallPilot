"""The real runbooks/ corpus (S1.2, S2.2; AI-026): every file chunks within the
ingest limits with a heading, and none names a benchmark scenario, a testbed
release or a testbed container (architecture §11, risk H7: runbooks are
generic operations docs, never the benchmark's answers)."""

import re
from pathlib import Path

import yaml

from oncallpilot.runbooks.ingest import MAX_CHUNK_CHARS, split_markdown

ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = sorted((ROOT / "runbooks").glob("*.md"))


def test_the_corpus_exists_and_chunks_within_limits() -> None:
    assert len(RUNBOOKS) >= 5
    for path in RUNBOOKS:
        chunks = split_markdown(f"runbooks/{path.name}", path.read_text(encoding="utf-8"))
        assert chunks, path.name
        for chunk in chunks:
            assert chunk.heading, f"{path.name}: a chunk without a heading"
            assert len(chunk.content) <= MAX_CHUNK_CHARS


def test_no_runbook_names_the_benchmark_or_the_testbed() -> None:
    scenarios = yaml.safe_load((ROOT / "benchmark" / "scenarios.yaml").read_text(encoding="utf-8"))
    targets = yaml.safe_load((ROOT / "runner" / "targets.yaml").read_text(encoding="utf-8"))
    names = [s["name"] for s in scenarios["scenarios"]]
    releases = [r for t in targets["services"].values() for r in t["releases"]]
    forbidden = [*names, *releases, "chaos", "scenario", "benchmark"]
    for path in RUNBOOKS:
        text = path.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word.lower() not in text, f"{path.name} mentions {word!r}"
        assert not re.search(r"\bcs-[a-z]", text), f"{path.name} names a testbed container"
