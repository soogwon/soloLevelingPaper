"""인증 기기의 저장 항목을 고정된 원격 게시본에 연결한다."""
from datetime import datetime
from hashlib import sha256
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^\S+$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Offset = Annotated[StrictInt, Field(ge=0, le=65536)]


class InsightItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    insight_id: Identifier
    kind: Literal['keyword', 'important_sentence']
    text: Text
    content_kind: Literal['extracted', 'generated']
    producer: Literal['extractor', 'model', 'user']
    producer_version: Identifier | None = None
    source_chunk_id: Identifier
    source_translation_revision_id: Identifier | None = None
    remote_chunk_id: UUID
    original_sha256: Digest
    translated_sha256: Digest | None = None
    pdf_page: Annotated[StrictInt, Field(ge=1)]
    start_offset: Offset
    end_offset: Offset
    quote_original: Text


class InsightImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: StrictBool
    request_key: UUID
    source_collection_id: Identifier
    local_context_id: Identifier
    local_version_id: Identifier
    local_parse_revision_id: Identifier
    pdf_sha256: Digest
    publication_id: UUID
    parse_revision_id: UUID
    translation_revision_id: UUID
    embedding_set_id: UUID
    section_id: Identifier | None = None
    stored_at: datetime
    expected_revision: Annotated[StrictInt, Field(ge=1)] | None = None
    items: Annotated[list[InsightItem], Field(min_length=1, max_length=20)]


class InsightConflict(ValueError):
    pass


def digest(value):
    return sha256(value.encode('utf-8')).hexdigest()


def verify_item(conn, owner, bound, section_id, item):
    row = conn.execute("""SELECT c.original_text,c.pdf_page,c.section_id,t.translated_text
        FROM server_processing_chunks c LEFT JOIN server_translation_chunks t
          ON t.owner_user_id=c.owner_user_id AND t.chunk_id=c.chunk_id
          AND t.translation_revision_id=%s AND t.translation_status='translated'
        WHERE c.owner_user_id=%s AND c.paper_id=%s AND c.server_version_id=%s
          AND c.parse_revision_id=%s AND c.chunk_id=%s""",
        (bound['translation_revision_id'], owner, bound['paper_id'], bound['server_version_id'],
         bound['parse_revision_id'], item.remote_chunk_id)).fetchone()
    if (row is None or item.pdf_page != row['pdf_page'] or (section_id and section_id != row['section_id'])
            or not item.text.strip() or not item.quote_original.strip()
            or not 0 <= item.start_offset < item.end_offset <= len(row['original_text'])
            or digest(row['original_text']) != item.original_sha256
            or row['original_text'][item.start_offset:item.end_offset] != item.quote_original
            or (item.content_kind == 'extracted') != (item.producer == 'extractor')
            or (item.content_kind == 'extracted' and
                (item.source_translation_revision_id or item.text != item.quote_original))
            or (item.source_translation_revision_id is None) != (item.translated_sha256 is None)
            or (item.translated_sha256 is not None and (not row['translated_text']
                or digest(row['translated_text']) != item.translated_sha256))):
        raise InsightConflict('insight_source_mismatch')
    verified = item.model_dump(mode='json', exclude={'original_sha256', 'translated_sha256'})
    verified['parse_revision_id'] = str(bound['parse_revision_id'])
    verified['translation_revision_id'] = (str(bound['translation_revision_id'])
                                           if item.translated_sha256 is not None else None)
    return verified


class PostgresRemoteInsightImports:
    def __init__(self, pool):
        self.pool = pool

    def save(self, owner, device, context, request: InsightImport, *, preview=False):
        if ((not preview and not request.confirmed) or request.stored_at.utcoffset() is None
                or len({item.insight_id for item in request.items}) != len(request.items)):
            raise InsightConflict('insight_import_invalid')
        source = request.model_dump(mode='json', exclude={'items', 'confirmed', 'request_key', 'expected_revision'})
        fingerprint = digest(json.dumps(request.model_dump(mode='json', exclude={'request_key', 'confirmed'}),
                                        ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        with self.pool.connection() as conn, conn.transaction():
            # 서로 다른 collection도 같은 맥락의 요청 키를 교차 소비할 수 없다.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (json.dumps(['remote-insights', owner, str(context)]),))
            matches = conn.execute("SELECT * FROM server_lock_insight_import(%s,%s,%s,%s,%s)",
                                   (owner, device, context, request.local_version_id, request.pdf_sha256)).fetchall()
            if len(matches) != 1:
                return None
            bound = matches[0]
            if any(str(bound[key]) != source[key] for key in (
                    'publication_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id')):
                raise InsightConflict('insight_binding_changed')
            key = (owner, context, device, request.request_key)
            old = conn.execute("""SELECT payload_sha256,source_collection_id,applied_revision
                FROM server_remote_insight_requests
                WHERE (owner_user_id,context_id,device_id,request_key)=(%s,%s,%s,%s)""", key).fetchone()
            if old:
                if old['payload_sha256'] != fingerprint:
                    raise InsightConflict('insight_request_conflict')
                return {'source_collection_id': old['source_collection_id'], 'revision': old['applied_revision']}
            collection = (owner, context, device, request.source_collection_id)
            current = conn.execute("""SELECT revision,source FROM server_remote_insight_collections
                WHERE (owner_user_id,context_id,device_id,source_collection_id)=(%s,%s,%s,%s)""",
                collection).fetchone()
            if (current['revision'] if current else None) != request.expected_revision:
                raise InsightConflict('insight_revision_conflict')
            if current and any(current['source'][k] != value for k, value in source.items() if k != 'stored_at'):
                raise InsightConflict('insight_source_changed')
            items = [verify_item(conn, owner, bound, request.section_id, item) for item in request.items]
            revision = (request.expected_revision or 0) + 1
            if preview:
                return {'source_collection_id': request.source_collection_id, 'context_id': str(context),
                        'publication_id': source['publication_id'], 'section_id': request.section_id,
                        'next_revision': revision, 'items': items}
            conn.execute("""INSERT INTO server_remote_insight_collections
                (owner_user_id,context_id,device_id,source_collection_id,revision,source,items)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT (owner_user_id,context_id,device_id,source_collection_id)
                DO UPDATE SET revision=EXCLUDED.revision,source=EXCLUDED.source,items=EXCLUDED.items""",
                (*collection, revision, json.dumps(source), json.dumps(items)))
            conn.execute("""INSERT INTO server_remote_insight_requests
                (owner_user_id,context_id,device_id,request_key,source_collection_id,payload_sha256,applied_revision)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (*key, request.source_collection_id, fingerprint, revision))
            return {'source_collection_id': request.source_collection_id, 'revision': revision}
