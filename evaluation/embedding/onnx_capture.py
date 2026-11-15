"""고정 로컬 ONNX 산출물만 사용하는 #391 후보 캡처."""

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
import time

from evaluation.embedding.comparison import compare, digest, load_json, require, validate_capture, validate_dataset
from evaluation.embedding.pytorch_capture import encode_vectors, sample_worker


def onnx_artifact_digest(model_dir: Path, file_name: str) -> str:
    target = (model_dir / file_name).resolve()
    require(target.is_file() and target.is_relative_to(model_dir.resolve()), "ONNX_ARTIFACT_INVALID")
    h = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def worker(dataset, model_dir, file_name, output, started_ns, *, max_length, batch_size, threads, warm_repeats):
    import onnxruntime as ort
    from sentence_transformers import SentenceTransformer

    ort.set_default_logger_severity(3)
    data = validate_dataset(load_json(dataset))
    model = SentenceTransformer(
        str(model_dir), backend="onnx",
        model_kwargs={"provider": "CPUExecutionProvider", "file_name": file_name, "export": False},
        local_files_only=True,
    )
    model.max_seq_length = max_length
    vectors = encode_vectors(model, data, batch_size)
    cold_ms = (time.perf_counter_ns() - started_ns) / 1e6
    warm_ms = []
    for _ in range(warm_repeats):
        started = time.perf_counter()
        encode_vectors(model, data, batch_size)
        warm_ms.append((time.perf_counter() - started) * 1000)
    settings = dict(
        model_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        model_revision=model_dir.name,
        tokenizer_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        tokenizer_revision=model_dir.name,
        pooling=json.dumps(model[1].get_config_dict(), sort_keys=True), normalization="l2",
        max_length=max_length, dimension=len(next(iter(vectors["chunks"].values()))), distance="cosine",
        runtime="onnx",
        runtime_version=f"onnxruntime={version('onnxruntime')};sentence-transformers={version('sentence-transformers')}",
        precision="float32", artifact_sha256="0" * 64,
    )
    with output.open("x", encoding="utf-8") as handle:
        json.dump(dict(settings=settings, cold_ms=cold_ms, warm_ms=warm_ms, **vectors), handle, allow_nan=False)


def capture(dataset, model_dir, file_name, output_dir, *, repeats=3, warm_repeats=3, max_length=128,
            batch_size=8, threads=1, timeout=240):
    require(all(type(v) is int and v > 0 for v in
                (repeats, warm_repeats, max_length, batch_size, threads, timeout)), "CAPTURE_LIMIT_INVALID")
    data = validate_dataset(load_json(dataset))
    model_dir = Path(model_dir).absolute()
    require(model_dir.is_dir() and model_dir.parent.name == "snapshots"
            and len(model_dir.name) == 40 and all(c in "0123456789abcdef" for c in model_dir.name)
            and model_dir.parent.parent.name ==
            "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2", "MODEL_SNAPSHOT_INVALID")
    require(isinstance(file_name, str) and file_name.startswith("onnx/")
            and not Path(file_name).is_absolute() and ".." not in Path(file_name).parts, "ONNX_ARTIFACT_INVALID")
    fingerprint = onnx_artifact_digest(model_dir, file_name)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads),
               PYTHONIOENCODING="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts/capture_onnx_embeddings.py"
    captures = []
    for index in range(repeats):
        result_path = output_dir / f"worker-{index}.json"
        command = [sys.executable, str(script), "--worker", "--dataset", str(Path(dataset).absolute()),
                   "--model-dir", str(model_dir), "--file-name", file_name, "--output", str(result_path.absolute()),
                   "--started-ns", str(time.perf_counter_ns()), "--warm-repeats", str(warm_repeats),
                   "--max-length", str(max_length), "--batch-size", str(batch_size), "--threads", str(threads)]
        peak = sample_worker(command, env, timeout=timeout)
        row = load_json(result_path)
        row["settings"]["artifact_sha256"] = fingerprint
        result = dict(schema_version=1, dataset_sha256=digest(data), synthetic=False,
                      settings=row["settings"], embedding_fingerprint=digest(row["settings"]),
                      chunks=row["chunks"], queries=row["queries"], token_observations=row["token_observations"],
                      measurements=dict(cold_ms=[row["cold_ms"]], warm_ms=row["warm_ms"], peak_rss_bytes=peak,
                                        environment=f"{platform.system()} {platform.release()};Python {platform.python_version()};"
                                                    f"{platform.machine()};{platform.processor()}",
                                        procedure="onnx-local-artifact-v1;imports+load+encode+token-count",
                                        rss_method=f"psutil {version('psutil')};10ms sampled sum RSS process tree",
                                        batch_policy=f"chunks-then-queries;batch={batch_size}",
                                        threads=str(threads), device="cpu"))
        validate_capture(result, data)
        captures.append(result)
    require(onnx_artifact_digest(model_dir, file_name) == fingerprint, "MODEL_CHANGED_DURING_CAPTURE")
    require(all(c["settings"] == captures[0]["settings"] for c in captures), "SETTINGS_CHANGED_DURING_CAPTURE")
    candidate = captures[0]
    candidate["measurements"]["cold_ms"] = [c["measurements"]["cold_ms"][0] for c in captures]
    candidate["measurements"]["warm_ms"] = [t for c in captures for t in c["measurements"]["warm_ms"]]
    candidate["measurements"]["peak_rss_bytes"] = max(c["measurements"]["peak_rss_bytes"] for c in captures)
    report = compare(data, candidate, candidate)
    report["repeat_vector_metrics"] = [compare(data, candidate, c)["vector_metrics"] for c in captures]
    for name, value in (("onnx.json", candidate), ("candidate-self-report.json", report)):
        with (output_dir / name).open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
    return report
