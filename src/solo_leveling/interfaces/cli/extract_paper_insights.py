"""명시적으로 요청한 원문 추출만 수행한다. 기본은 미리보기이며 API 호출은 없다."""
import argparse
import json
import sys

from solo_leveling.application.paper_insights.service import ExtractionPolicy, PaperInsightService
from solo_leveling.infrastructure.retrieval.paper_insights import SQLiteInsightStore
from solo_leveling.domain.paper_insight import PaperInsightConflictError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--context-id', required=True)
    parser.add_argument('--scope', default='paper')
    parser.add_argument('--max-keywords', type=int, default=8)
    parser.add_argument('--max-sentences', type=int, default=5)
    parser.add_argument('--save', action='store_true', help='선정 결과를 명시적으로 저장')
    parser.add_argument('--replace', action='store_true', help='--save와 함께 기존 묶음 전체 교체 허용')
    args = parser.parse_args(argv)
    if args.replace and not args.save:
        parser.error('--replace는 --save와 함께 사용해야 합니다.')
    try:
        service = PaperInsightService(SQLiteInsightStore(args.db),
            ExtractionPolicy(max_keywords=args.max_keywords, max_sentences=args.max_sentences))
        preview = service.preview(args.context_id, args.scope)
        if args.save:
            service.save(preview, replace_existing=args.replace)
        print(json.dumps({'status': preview.status, 'saved': args.save,
            'keywords': sum(i.kind == 'keyword' for i in preview.items),
            'important_sentences': sum(i.kind == 'important_sentence' for i in preview.items),
            'producer_version': preview.producer_version}))
        return 0
    except PaperInsightConflictError:
        print('INSIGHT_EXTRACTION_CONFLICT', file=sys.stderr)
        return 1
    except Exception:
        # DB 경로·본문·질문·원문을 오류나 기본 출력에 넣지 않는다.
        print('INSIGHT_EXTRACTION_FAILED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
