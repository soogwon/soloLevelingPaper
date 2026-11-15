import json
from pathlib import Path
import socket

import pytest

from evaluation.embedding import onnx_capture as runner
from evaluation.embedding.comparison import EvaluationError, load_json, synthetic_capture

DATA = Path(__file__).resolve().parents[2] / "evaluation/embedding/cases.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("실제 네트워크 금지"))


def snapshot(tmp_path):
    folder = tmp_path / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots" / ("a" * 40)
    (folder / "onnx").mkdir(parents=True)
    (folder / "onnx/model.onnx").write_bytes(b"fixed-onnx")
    return folder


def test_digest_is_bound_to_selected_local_onnx_file(tmp_path):
    folder = snapshot(tmp_path)
    before = runner.onnx_artifact_digest(folder, "onnx/model.onnx")
    (folder / "onnx/model.onnx").write_bytes(b"changed")
    assert runner.onnx_artifact_digest(folder, "onnx/model.onnx") != before


@pytest.mark.parametrize("name", ["../model.onnx", "model.onnx", "onnx/missing.onnx"])
def test_capture_rejects_escape_non_onnx_folder_and_missing_file(tmp_path, name):
    with pytest.raises(EvaluationError):
        runner.capture(DATA, snapshot(tmp_path), name, tmp_path / "out")


def test_capture_uses_offline_fresh_workers_and_aggregates(tmp_path, monkeypatch):
    folder = snapshot(tmp_path)
    calls = []

    def fake(command, env, **kwargs):
        assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
        row = synthetic_capture(load_json(DATA))
        row["settings"]["runtime"] = "onnx"
        row.update(cold_ms=50 + len(calls), warm_ms=[5, 6], token_observations={})
        Path(command[command.index("--output") + 1]).write_text(json.dumps(row), encoding="utf-8")
        calls.append(command)
        return 512 + len(calls)

    monkeypatch.setattr(runner, "sample_worker", fake)
    monkeypatch.setattr(runner, "version", lambda _: "test-version")
    output = tmp_path / "capture"
    report = runner.capture(DATA, folder, "onnx/model.onnx", output)
    result = load_json(output / "onnx.json")
    assert result["measurements"]["cold_ms"] == [50, 51, 52]
    assert result["measurements"]["warm_ms"] == [5, 6] * 3
    assert result["measurements"]["peak_rss_bytes"] == 515
    assert report["decision"] == "pending_thresholds"


def test_existing_output_and_invalid_limits_fail_before_worker(tmp_path, monkeypatch):
    folder = snapshot(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    calls = []
    monkeypatch.setattr(runner, "sample_worker", lambda *a, **k: calls.append(1))
    with pytest.raises(FileExistsError):
        runner.capture(DATA, folder, "onnx/model.onnx", output)
    with pytest.raises(EvaluationError):
        runner.capture(DATA, folder, "onnx/model.onnx", tmp_path / "new", repeats=0)
    assert calls == []
