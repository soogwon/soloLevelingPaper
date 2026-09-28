"""사용자가 지정한 PDF 한 편으로 전체 흐름을 확인하는 로컬 실행기."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values

from solo_leveling.infrastructure.bootstrap import build_services
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from solo_leveling.infrastructure.storage.vector_store import get_client
from solo_leveling.workers.ingestion import register_and_ingest
from solo_leveling.application.evidence_qa.serialization import (
    serialize_answer_response, serialize_stored_evidence_result,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='PDF 등록·번역·검색·답변·근거 조회 확인')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--question', required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--live', action='store_true', help='논문 외부 전송 및 유료 API 호출 허용')
    args = parser.parse_args(argv)
    try:
        pdf = args.pdf.resolve(strict=True)
        if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
            raise ValueError('PDF 파일 필요')
        with pdf.open('rb') as source:
            if source.read(5) != b'%PDF-':
                raise ValueError('PDF 형식 필요')
        if not args.question.strip() or not 1 <= args.top_k <= 20:
            raise ValueError('질문 또는 검색 개수 오류')
        if not args.env_file.is_file():
            raise ValueError('설정 파일 없음')
        values = dotenv_values(args.env_file, encoding='utf-8-sig', interpolate=False)
        env = {**{key: value for key, value in values.items() if value is not None}, **os.environ}
        generation = OpenAIGenerationSettings.from_env(env)
        translation = OpenAITranslationConfig.from_env(env)
    except (ValueError, OSError):
        print('입력 확인 실패: PDF·질문·설정 파일을 확인하세요. top-k는 1~20입니다.', file=sys.stderr)
        return 2
    if not args.live:
        print('입력·설정 형식 확인 완료. DB 생성·모델 다운로드·API 호출 없음.')
        print('--live 실행 시 논문 원문과 질문·검색 근거가 외부 API로 전송됩니다.')
        return 0
    if any(not config.allow_external_api or config.local_only for config in (generation, translation)):
        print('외부 호출 차단: ALLOW_EXTERNAL_API=true, LOCAL_ONLY=false가 필요합니다.', file=sys.stderr)
        return 2

    stage = '저장소 준비'
    try:
        # 매번 독립된 폴더를 사용해 기존 서비스 DB·벡터를 변경하지 않는다.
        work = Path(tempfile.mkdtemp(prefix='paper-local-'))
        print(f'테스트 저장 위치: {work}', flush=True)
        print('실행마다 새로 번역합니다. 청크 수에 따라 비용·시간이 늘어납니다.', flush=True)
        db = str(work / 'data.sqlite')
        chroma = str(work / 'chroma')
        services = build_services(db, get_client(chroma), generation=generation, translation=translation)
        stage = '등록·번역·색인'
        print(f'{stage} 진행 중…', flush=True)
        registered = register_and_ingest(db, chroma, str(pdf),
            translation_service=services.translation, translation_settings=services.translation_settings)
        print(json.dumps({'registration': registered}, ensure_ascii=False, indent=2))
        if registered.get('status') != 'ready':
            print('검색 가능한 등록 결과가 없습니다. 등록 상태와 limitations를 확인하세요.', file=sys.stderr)
            return 1
        stage = '검색·답변 생성'
        print(f'{stage} 진행 중…', flush=True)
        response = services.answer.answer(args.question, version_id=registered['version_id'], top_k=args.top_k)
        print(json.dumps(serialize_answer_response(response), ensure_ascii=False, indent=2))
        ids = [item.evidence_id for item in response.result.citations]
        if ids:
            stage = '저장 근거 조회'
            result = services.evidence.get(response.context_id, ids)
            print(json.dumps(serialize_stored_evidence_result(result), ensure_ascii=False, indent=2))
        else:
            print('반환된 근거가 없어 근거 조회를 생략했습니다.')
        print('완료. 테스트 DB·벡터는 위 폴더에 남아 있습니다. 원본 PDF는 변경하지 않았습니다.')
        return 0
    except KeyboardInterrupt:
        print('사용자가 중단했습니다. 생성된 테스트 데이터는 보존됩니다.', file=sys.stderr)
        return 130
    except Exception:
        # 제공자 응답·키·스택을 노출하지 않는다. 실패 데이터는 진단을 위해 남긴다.
        print(f'{stage} 실패. 네트워크·의존성·API 권한·PDF 상태를 확인하세요. 테스트 데이터는 보존됩니다.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
