"""전용 프로세스에서 임베딩 모델의 다운로드·로딩·계산을 검증한다."""

import argparse
import math
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from solo_leveling.infrastructure.embeddings.embedder import DEFAULT_MODEL_NAME


def prepare(model_name, offline):
    """메인 스레드에서 실행하며 사용자 문서 대신 고정 샘플을 계산한다."""
    started = perf_counter()
    try:
        print('1/3 임베딩 라이브러리 불러오는 중…', flush=True)
        from sentence_transformers import SentenceTransformer
        print('2/3 모델 준비 중… 캐시가 없으면 다운로드합니다.' if not offline else
              '2/3 로컬 캐시만으로 모델 로딩 중…', flush=True)
        model = SentenceTransformer(model_name, local_files_only=offline)
        print('3/3 한국어 샘플 임베딩 계산 중…', flush=True)
        vectors = model.encode(['어텐션은 문맥의 관련 정보를 활용한다.'], convert_to_numpy=True)
        dimension = model.get_sentence_embedding_dimension()
        if (len(vectors) != 1 or not isinstance(dimension, int) or dimension <= 0
                or len(vectors[0]) != dimension
                or not all(math.isfinite(float(value)) for value in vectors[0])):
            raise ValueError('벡터 검증 실패')
        print(f'준비 완료: 차원={dimension}, 소요={perf_counter() - started:.1f}초', flush=True)
        return 0
    except Exception:
        print('준비 실패: 패키지 설치·네트워크·캐시·메모리를 확인하세요. 원본 예외는 출력하지 않습니다.',
              file=sys.stderr, flush=True)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='임베딩 모델 다운로드·로딩·계산 준비 점검')
    parser.add_argument('--model', default=DEFAULT_MODEL_NAME)
    parser.add_argument('--offline', action='store_true', help='다운로드 없이 로컬 캐시만 확인')
    parser.add_argument('--timeout', type=int, default=600, help='최대 대기 초, 기본 600')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.model.strip() or args.timeout <= 0:
        parser.error('모델 이름과 양의 timeout이 필요합니다.')
    if args.worker:
        return prepare(args.model, args.offline)
    print('논문 DB·색인은 변경하지 않습니다. OpenAI API 키는 필요하지 않습니다.', flush=True)
    print('모델 캐시를 저장하므로 MCP와 같은 OS 사용자·가상환경에서 실행하세요.', flush=True)
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--model', args.model]
    env = dict(os.environ)
    if args.offline:
        command.append('--offline')
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    try:
        return subprocess.run(command, env=env, timeout=args.timeout).returncode
    except subprocess.TimeoutExpired:
        print('준비 시간 초과: 이번 준비용 프로세스만 종료했습니다. 기존 MCP에는 영향이 없습니다.', file=sys.stderr)
        return 124


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
