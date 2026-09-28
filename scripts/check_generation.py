"""기본 실행은 설정만 확인한다. --live를 지정해야 합성 자료를 외부로 전송한다."""

import argparse
import os
from pathlib import Path
import sys
from typing import Mapping

# 저장소에서 별도 패키지 설치 없이 실행할 수 있도록 소스 경로를 지정한다.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values

from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.domain.evidence_qa import EvidenceInput
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIClaimGenerator, OpenAIGenerationSettings,
)


def load_settings(path: Path, env: Mapping[str, str] | None = None) -> OpenAIGenerationSettings:
    """명시한 파일만 읽고, 기존 환경 변수를 우선하며 프로세스 환경은 변경하지 않는다."""
    if not path.is_file():
        raise ValueError('설정 파일이 없습니다.')
    values = dotenv_values(path, encoding='utf-8-sig', interpolate=False)
    merged = {key: value for key, value in values.items() if value is not None}
    merged.update(os.environ if env is None else env)
    return OpenAIGenerationSettings.from_env(merged)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='답변 생성 설정 확인 및 선택적 합성 샘플 호출')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env', help='기본값: 저장소 루트의 .env')
    parser.add_argument('--live', action='store_true', help='실제 API 1회 호출: 외부 전송과 비용 발생 가능')
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
    except (OSError, ValueError):
        # 잘못 입력된 설정값에도 키가 들어 있을 수 있어 원래 예외는 출력하지 않는다.
        print('설정 확인 실패: .env 경로, 필수 키, 숫자·불리언 설정을 확인하세요.', file=sys.stderr)
        return 2

    if not args.live:
        print('설정 형식 확인 완료. API 키 값은 출력하지 않았습니다.')
        print('실제 API 호출 없음. 인증·모델 접근 권한·잔액은 확인하지 않았습니다.')
        if not settings.allow_external_api or settings.local_only:
            print('외부 호출은 현재 설정에서 차단됩니다.')
        else:
            print('외부 호출 허용 설정입니다. 실제 요청은 --live 실행 시에만 발생합니다.')
        return 0

    if not settings.allow_external_api or settings.local_only:
        print('호출 차단: ALLOW_EXTERNAL_API=true, LOCAL_ONLY=false가 필요합니다.', file=sys.stderr)
        return 2

    # 사용자 논문 대신 직접 작성한 한 쌍의 합성 원문·한국어 근거를 사용한다.
    evidence = (EvidenceInput('sample-ev-1', 'sample-chunk-1',
        '이 설명에서 어텐션은 병렬 계산을 가능하게 한다.',
        'In this explanation, attention enables parallel computation.', None, 1),)
    try:
        result = OpenAIClaimGenerator(settings).generate_claims('어텐션의 계산상 장점은?', evidence)
        for claim in result.claims:
            if (not claim.evidence_ids or len(set(claim.evidence_ids)) != len(claim.evidence_ids)
                    or not set(claim.evidence_ids) <= {'sample-ev-1'}):
                raise GenerationFormatError('잘못된 근거 참조')
    except GenerationFormatError:
        print('응답 형식 또는 근거 참조 검증 실패.', file=sys.stderr)
        return 1
    except GenerationUnavailable:
        print('생성 실패: 호출 허용 설정, 인증·한도·네트워크·제공자 상태를 확인하세요.', file=sys.stderr)
        return 1
    print('합성 샘플 호출 완료. DB 저장·검색·사실성 검증은 수행하지 않았습니다.')
    if not result.claims:
        print('생성기가 답변을 보류했습니다. 샘플 품질은 별도로 확인하세요.')
    for claim in result.claims:
        print(f'{claim.text} [{", ".join(claim.evidence_ids)}]')
    return 0


if __name__ == '__main__':
    # Windows의 비한국어 기본 인코딩에서도 안내문을 출력할 수 있게 한다.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
