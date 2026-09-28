"""번역 설정을 확인한다. --live에서만 합성 원문 하나를 실제 번역한다."""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='번역 설정 확인 및 선택적 합성 원문 호출')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--live', action='store_true', help='실제 외부 전송·비용 발생 가능')
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            raise ValueError('설정 파일 없음')
        values = dotenv_values(args.env_file, encoding='utf-8-sig', interpolate=False)
        merged = {key: value for key, value in values.items() if value is not None}
        config = OpenAITranslationConfig.from_env({**merged, **os.environ})
    except (ValueError, OSError):
        print('번역 설정 확인 실패: 파일·키·번역 모델·프롬프트 버전·숫자·불리언을 확인하세요.', file=sys.stderr)
        return 2
    if not args.live:
        print('번역 설정 형식 확인 완료. 실제 API 호출·키 출력 없음.')
        print('외부 호출 차단 상태.' if not config.allow_external_api or config.local_only else
              '외부 호출 허용 설정. 실제 요청은 --live 실행 시에만 발생합니다.')
        return 0
    if not config.allow_external_api or config.local_only:
        print('외부 번역 API 호출이 차단되어 있습니다.', file=sys.stderr)
        return 2
    chunk = Chunk('sample', 'sample-parse', 0,
                  'Accuracy was 82% on 100 synthetic examples. Other settings were not evaluated.')
    batch = TranslationService(OpenAITranslationProvider(config)).translate([chunk], config.translation_settings)
    item = batch.items[0]
    if not item.succeeded:
        print(f'번역 실패: {item.failure_code.value}', file=sys.stderr)
        return 1
    print('합성 원문 번역 완료. DB 저장·색인·번역 품질 자동 검증은 수행하지 않았습니다.')
    print(item.text)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
