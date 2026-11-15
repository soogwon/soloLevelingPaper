"""고정 자료의 벡터 캡처를 비교한다. 모델·네트워크·DB에 접근하지 않는다."""

import hashlib
import json
import math
from pathlib import Path


class EvaluationError(ValueError):
    """입력 본문을 포함하지 않는 고정 오류 코드."""


def require(condition, code):
    if not condition:
        raise EvaluationError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_object,
                          parse_constant=lambda _: require(False, 'NON_FINITE_JSON'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EvaluationError('INPUT_UNREADABLE') from None


SCOPE = ('owner_id', 'version_id', 'parse_revision_id', 'translation_revision_id', 'embedding_scope')
SETTINGS = ('model_id', 'model_revision', 'tokenizer_id', 'tokenizer_revision', 'pooling', 'normalization',
            'max_length', 'dimension', 'distance', 'runtime', 'runtime_version', 'precision', 'artifact_sha256')


def _positive_int(value):
    return type(value) is int and value > 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_dataset(data):
    require(isinstance(data, dict) and type(data.get('schema_version')) is int
            and data['schema_version'] == 1, 'DATASET_INVALID')
    require(isinstance(data.get('version'), str) and bool(data['version']), 'DATASET_INVALID')
    require(data.get('review_status') in ('draft', 'reviewed'), 'DATASET_REVIEW_INVALID')
    chunks, queries = data.get('chunks'), data.get('queries')
    require(isinstance(chunks, list) and bool(chunks) and isinstance(queries, list) and bool(queries),
            'DATASET_INVALID')
    ids = set()
    for row in chunks + queries:
        require(isinstance(row, dict), 'DATASET_INVALID')
        key = row.get('id')
        require(isinstance(key, str) and key and key not in ids, 'DATASET_ID_INVALID')
        ids.add(key)
        require(isinstance(row.get('text'), str) and bool(row['text'].strip()), 'DATASET_INVALID')
        require(all(isinstance(row.get(k), str) and row[k] for k in SCOPE), 'SCOPE_INVALID')
    for row in chunks:
        require(_positive_int(row.get('pdf_page')) and isinstance(row.get('section_id'), str), 'SCOPE_INVALID')
    for row in queries:
        pages, sections = row.get('pdf_pages'), row.get('section_ids')
        require(isinstance(pages, list) and all(_positive_int(p) for p in pages), 'SCOPE_INVALID')
        require(isinstance(sections, list) and all(isinstance(s, str) and s for s in sections), 'SCOPE_INVALID')
        require(_positive_int(row.get('top_k')), 'TOP_K_INVALID')
        required = row.get('required_chunk_ids')
        eligible = {c['id'] for c in allowed_chunks(data, row)}
        require(isinstance(required, list) and bool(required) and all(isinstance(k, str) for k in required)
                and len(required) == len(set(required))
                and set(required) <= eligible and len(required) <= row['top_k'], 'REQUIRED_EVIDENCE_INVALID')
    return data


def allowed_chunks(data, query):
    # 실제 어댑터의 보안 검증이 아니라, 비교에 사용하는 exact-search 기준 집합이다.
    return [row for row in data['chunks'] if all(row[k] == query[k] for k in SCOPE)
            and (not query['pdf_pages'] or row['pdf_page'] in query['pdf_pages'])
            and (not query['section_ids'] or row['section_id'] in query['section_ids'])]


def validate_capture(capture, data):
    require(isinstance(capture, dict) and type(capture.get('schema_version')) is int
            and capture['schema_version'] == 1, 'CAPTURE_INVALID')
    require(capture.get('dataset_sha256') == digest(data), 'DATASET_MISMATCH')
    require(type(capture.get('synthetic')) is bool, 'CAPTURE_INVALID')
    settings = capture.get('settings')
    require(isinstance(settings, dict) and set(settings) == set(SETTINGS), 'SETTINGS_INVALID')
    require(all(isinstance(settings[k], str) and settings[k] for k in SETTINGS
                if k not in ('max_length', 'dimension')), 'SETTINGS_INVALID')
    require(_positive_int(settings['dimension']) and _positive_int(settings['max_length']), 'SETTINGS_INVALID')
    require(settings['distance'] == 'cosine', 'DISTANCE_UNSUPPORTED')
    require(settings['normalization'] in ('l2', 'none'), 'SETTINGS_INVALID')
    require(len(settings['artifact_sha256']) == 64 and
            all(c in '0123456789abcdef' for c in settings['artifact_sha256']), 'SETTINGS_INVALID')
    require(settings['runtime'] in (('synthetic',) if capture['synthetic'] else ('pytorch', 'onnx')),
            'RUNTIME_INVALID')
    require(capture.get('embedding_fingerprint') == digest(settings), 'FINGERPRINT_MISMATCH')
    for group in ('chunks', 'queries'):
        vectors = capture.get(group)
        require(isinstance(vectors, dict) and set(vectors) == {r['id'] for r in data[group]}, 'VECTOR_IDS_INVALID')
        for vector in vectors.values():
            require(isinstance(vector, list) and len(vector) == settings['dimension'], 'DIMENSION_INVALID')
            require(all(_number(v) for v in vector), 'VECTOR_NON_FINITE')
            norm = math.hypot(*vector)
            require(math.isfinite(norm) and norm > 0, 'VECTOR_NORM_INVALID')
            if settings['normalization'] == 'l2':
                require(abs(norm - 1) <= 1e-5, 'NORMALIZATION_INVALID')
    measurements = capture.get('measurements')
    if capture['synthetic']:
        require(measurements is None, 'SYNTHETIC_MEASUREMENT_INVALID')
    else:
        require(isinstance(measurements, dict), 'MEASUREMENTS_REQUIRED')
        for key in ('cold_ms', 'warm_ms'):
            require(isinstance(measurements.get(key), list) and bool(measurements[key])
                    and all(_number(v) and v >= 0 for v in measurements[key]), 'MEASUREMENTS_INVALID')
        require(_number(measurements.get('peak_rss_bytes')) and measurements['peak_rss_bytes'] > 0,
                'MEASUREMENTS_INVALID')
        for key in ('environment', 'procedure', 'rss_method', 'batch_policy', 'threads', 'device'):
            require(isinstance(measurements.get(key), str) and bool(measurements[key]), 'MEASUREMENTS_INVALID')
    return capture


def cosine(left, right):
    a, b = math.hypot(*left), math.hypot(*right)
    return max(-1.0, min(1.0, math.fsum((x / a) * (y / b) for x, y in zip(left, right))))


def ranked(data, capture, query):
    vector = capture['queries'][query['id']]
    rows = [(c['id'], cosine(vector, capture['chunks'][c['id']])) for c in allowed_chunks(data, query)]
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def compare(data, baseline, candidate):
    validate_dataset(data)
    for capture in (baseline, candidate):
        validate_capture(capture, data)
    require(baseline['synthetic'] == candidate['synthetic'], 'MEASUREMENT_MODE_MISMATCH')
    # 정밀도·runtime·산출물 해시 이외의 설정 차이는 같은 모델 비교로 인정하지 않는다.
    require(all(baseline['settings'][k] == candidate['settings'][k] for k in SETTINGS
                if k not in ('runtime', 'runtime_version', 'precision', 'artifact_sha256')), 'MODEL_CONTRACT_MISMATCH')
    vector_metrics = {}
    for group in ('chunks', 'queries'):
        errors, distances = [], []
        for key, vector in baseline[group].items():
            other = candidate[group][key]
            errors.extend(abs(x - y) for x, y in zip(vector, other))
            distances.append(1 - cosine(vector, other))
        require(all(math.isfinite(v) for v in errors + distances), 'METRIC_NON_FINITE')
        vector_metrics[group] = {'max_absolute_error': max(errors),
                                 'mean_absolute_error': math.fsum(errors) / len(errors),
                                 'max_cosine_distance': max(distances)}
    rows = []
    for query in data['queries']:
        orders = [ranked(data, c, query) for c in (baseline, candidate)]
        tops = [[key for key, _ in order[:query['top_k']]] for order in orders]
        needed = set(query['required_chunk_ids'])
        ranks = [{key: i + 1 for i, (key, _) in enumerate(order)} for order in orders]
        rows.append({'query_id': query['id'], 'eligible_chunks': len(orders[0]),
                     'baseline_top_k': tops[0], 'candidate_top_k': tops[1],
                     'top_k_overlap': len(set(tops[0]) & set(tops[1])) / len(tops[0]),
                     'baseline_required_recall': len(needed & set(tops[0])) / len(needed),
                     'candidate_required_recall': len(needed & set(tops[1])) / len(needed),
                     'required_rank_changes': {key: ranks[1][key] - ranks[0][key] for key in sorted(needed)},
                     'baseline_cutoff_margin': _margin(orders[0], query['top_k']),
                     'candidate_cutoff_margin': _margin(orders[1], query['top_k'])})
    return {'schema_version': 1, 'dataset_sha256': digest(data),
            'dataset_version': data['version'], 'relevance_review_status': data['review_status'],
            'quality_measurement': not baseline['synthetic'], 'decision': 'pending_thresholds',
            'search_method': 'exact_cosine_prefiltered', 'tie_break': 'chunk_id_ascending',
            'baseline_fingerprint': baseline['embedding_fingerprint'],
            'candidate_fingerprint': candidate['embedding_fingerprint'],
            'baseline_settings': baseline['settings'], 'candidate_settings': candidate['settings'],
            'baseline_capture_sha256': digest(baseline), 'candidate_capture_sha256': digest(candidate),
            'performance_comparable': _performance_comparable(baseline, candidate),
            'vector_metrics': vector_metrics, 'queries': rows,
            'measurements': {'baseline': baseline['measurements'], 'candidate': candidate['measurements']}}


def _margin(order, k):
    return order[k - 1][1] - order[k][1] if len(order) > k else None


def _performance_comparable(baseline, candidate):
    if baseline['synthetic']:
        return False
    a, b = baseline['measurements'], candidate['measurements']
    if a.get('protocol_version') == 2 or b.get('protocol_version') == 2:
        if (a.get('protocol_version') != 2 or b.get('protocol_version') != 2
                or not a.get('input_artifacts_sha256')
                or a['input_artifacts_sha256'] != b.get('input_artifacts_sha256')):
            return False
    return all(a[k] == b[k] for k in ('environment', 'procedure', 'rss_method', 'batch_policy', 'threads', 'device')) \
        and all(len(a[k]) == len(b[k]) for k in ('cold_ms', 'warm_ms'))


def synthetic_capture(data):
    """비교 경로의 smoke 전용. 의미 품질·모델 성능 측정에는 쓰지 않는다."""
    validate_dataset(data)
    settings = dict(model_id='synthetic-hash', model_revision='fixture-v1', tokenizer_id='none', tokenizer_revision='none',
                    pooling='none', normalization='l2', max_length=512, dimension=8, distance='cosine',
                    runtime='synthetic', runtime_version='1', precision='float64',
                    artifact_sha256=digest('synthetic-hash-v1'))
    capture = {'schema_version': 1, 'dataset_sha256': digest(data), 'synthetic': True, 'settings': settings,
               'embedding_fingerprint': digest(settings), 'measurements': None}
    for group in ('chunks', 'queries'):
        capture[group] = {}
        for row in data[group]:
            raw = hashlib.sha256(row['text'].encode('utf-8')).digest()[:8]
            vector = [v - 127.5 for v in raw]
            norm = math.hypot(*vector)
            capture[group][row['id']] = [v / norm for v in vector]
    return capture
