"""승인 후 준비한 로컬 모델만으로 Mac 구성요소를 검증한다. 다운로드/API 호출 없음."""

import argparse
import os

os.environ.update(
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
    HF_HUB_DISABLE_TELEMETRY="1",
    TOKENIZERS_PARALLELISM="false",
    HF_HOME="/private/tmp/slp517-macos-hf",
)
import socket

network_attempts = []


def forbidden(*args, **kwargs):
    network_attempts.append(True)
    raise RuntimeError("OFFLINE_NETWORK_ATTEMPT")


socket.socket.connect = forbidden
socket.create_connection = forbidden
import time, json, math, resource, platform
from pathlib import Path
from dataclasses import replace
from importlib.metadata import version
from uuid import uuid4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if platform.system() != "Darwin":
    parser.error("이 검증은 macOS 전용입니다. Linux 검증을 대신하지 않습니다.")
started = time.perf_counter()
import torch

torch.set_num_threads(2)
torch.set_num_interop_threads(2)
from solo_leveling.workers.image_preflight import artifact_check
from solo_leveling.infrastructure.embeddings.processing_embedder import PyTorchProcessingEmbedder
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from solo_leveling.domain.remote_processing import EmbeddingSpec, ProcessedTranslation, ProcessingError
from tests.fixtures.pdf_builder import build_minimal_pdf

repo = Path(__file__).resolve().parents[2]
lock = json.loads((repo / "deploy/worker/model-lock.json").read_text())
folder = args.model_dir
artifact_check(folder, lock)
pdf = build_minimal_pdf(
    [
        "A completed paper can still be queried. Reopening keeps saved notes.",
        "Evidence should match the cited page. Notes remain available.",
    ]
)
parse_started = time.perf_counter()
document = ProcessingPdfParser().parse(pdf, str(uuid4()))
parse_seconds = time.perf_counter() - parse_started
assert document.page_count == 2
assert set(c.pdf_page for c in document.chunks) == {1, 2}
items = tuple(
    ProcessedTranslation(
        c.chunk_id,
        "완료한 논문에도 질문할 수 있습니다. 저장한 메모는 유지됩니다."
        if c.pdf_page == 1
        else "근거와 인용 페이지가 일치해야 합니다. 메모를 다시 볼 수 있습니다.",
        None,
    )
    for c in document.chunks
)
runtime = ";".join(f"{n}={version(n)}" for n in ("torch", "sentence-transformers", "transformers"))
spec = EmbeddingSpec(
    lock["model_id"],
    lock["revision"],
    lock["model_id"],
    lock["revision"],
    "mean",
    "l2",
    128,
    384,
    "cosine",
    "float32",
    "pytorch",
    runtime,
    lock["artifact_sha256"],
)
adapter = PyTorchProcessingEmbedder(model_dir=folder, check_allowed=lambda: True, batch_size=2)
encode_started = time.perf_counter()
first = adapter.embed(items, spec)
first_seconds = time.perf_counter() - encode_started
second_started = time.perf_counter()
second = adapter.embed(items, spec)
second_seconds = time.perf_counter() - second_started
assert first.chunk_ids == tuple(c.chunk_id for c in document.chunks)
assert len(first.vectors) == len(items)
norms = [math.hypot(*v) for v in first.vectors]
assert all(len(v) == 384 and all(math.isfinite(x) for x in v) for v in first.vectors)
assert all(abs(x - 1) < 1e-5 for x in norms)
delta = max(abs(a - b) for row1, row2 in zip(first.vectors, second.vectors) for a, b in zip(row1, row2))
assert delta <= 1e-6
try:
    adapter.embed(items, replace(spec, artifact_sha256="f" * 64))
except ProcessingError as e:
    mismatch_code = str(e)
else:
    raise AssertionError("artifact mismatch accepted")
assert mismatch_code == "PROCESSING_EMBEDDING_ARTIFACT_MISMATCH"
assert not network_attempts
report = dict(
    status="MACOS_COMPONENTS_VERIFIED",
    platform=platform.platform(),
    architecture=platform.machine(),
    python=platform.python_version(),
    packages={
        n: version(n) for n in ("torch", "sentence-transformers", "transformers", "tokenizers", "numpy", "pdfplumber")
    },
    model_id=lock["model_id"],
    revision=lock["revision"],
    artifact_sha256=lock["artifact_sha256"],
    verified_files=len(lock["files"]),
    pdf_pages=document.page_count,
    chunks=len(document.chunks),
    vectors=len(first.vectors),
    dimension=384,
    normalization="l2",
    precision="float32",
    device="cpu",
    torch_threads=2,
    parse_seconds=round(parse_seconds, 4),
    first_encode_with_load_seconds=round(first_seconds, 4),
    second_encode_with_reload_seconds=round(second_seconds, 4),
    total_seconds=round(time.perf_counter() - started, 4),
    peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 2),
    repeat_max_absolute_difference=delta,
    norm_min=min(norms),
    norm_max=max(norms),
    artifact_mismatch_code=mismatch_code,
    blocked_network_attempts=len(network_attempts),
    api_calls=0,
    translation="수동 합성 번역: provider 미실행",
    linux_validated=False,
)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
