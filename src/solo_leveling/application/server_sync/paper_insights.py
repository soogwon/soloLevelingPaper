"""선택한 로컬 저장 항목만 원격 고정 판본으로 이관한다."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, UUID, uuid5

from solo_leveling.infrastructure.database.repository import read_insight_sources


def _digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


class PaperInsightImportService:
    def __init__(self, db_path, client):
        self.db_path = db_path
        self.client = client

    def _stored(self, context_id, scope):
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            source = conn.execute("""SELECT c.version_id,c.parse_revision_id,c.translation_revision_id,v.file_hash
                FROM learning_contexts c JOIN paper_versions v ON v.version_id=c.version_id
                WHERE c.context_id=?""", (context_id,)).fetchone()
            collection = conn.execute("""SELECT * FROM paper_insight_collections
                WHERE context_id=? AND scope=?""", (context_id, scope)).fetchone()
            if source is None or collection is None:
                raise ValueError('insight_collection_missing')
            items = []
            for row, item in read_insight_sources(conn, source, collection, scope):
                translated_sha = None
                if item.translation_revision_id is not None:
                    translated = conn.execute("""SELECT translated_text FROM translation_results
                        WHERE translation_revision_id=? AND chunk_id=? AND failure_code IS NULL""",
                        (item.translation_revision_id, item.chunk_id)).fetchone()
                    translated_sha = _digest(translated['translated_text'])
                items.append({
                    'insight_id': row['insight_id'], 'kind': item.kind, 'text': item.text,
                    'content_kind': item.content_kind, 'producer': item.producer,
                    'producer_version': item.producer_version, 'source_chunk_id': item.chunk_id,
                    'source_translation_revision_id': item.translation_revision_id,
                    'original_sha256': _digest(row['original_text']), 'translated_sha256': translated_sha,
                    'pdf_page': row['pdf_page'], 'start_offset': item.start_offset,
                    'end_offset': item.end_offset, 'quote_original': item.quote_original,
                })
            return {
                'source_collection_id': collection['collection_id'], 'local_context_id': context_id,
                'local_version_id': source['version_id'], 'local_parse_revision_id': source['parse_revision_id'],
                'pdf_sha256': source['file_hash'], 'stored_at': collection['created_at'], 'items': items,
            }

    def list_items(self, context_id, scope, remote_context_id):
        body = self._stored(context_id, scope)
        return {'local_context_id': context_id, 'scope': scope, 'remote_context_id': str(UUID(remote_context_id)),
                'collection_id': body['source_collection_id'], 'server': self.client.server_url,
                'connection': self.client.connection_id(),
                'items': [self._display(item) for item in body['items']]}

    @staticmethod
    def _display(item):
        return {key: item[key] for key in
                ('insight_id', 'kind', 'text', 'content_kind', 'pdf_page', 'quote_original')}

    def _post(self, context, suffix, body, connection):
        path = f'/v1/remote-insights/contexts/{UUID(context)}/imports{suffix}'
        with self.client.request('POST', path, json=body, expected_connection_id=connection) as response:
            if response.status_code in (400, 403, 404, 409, 422):
                raise ValueError('insight_import_rejected')
            response.raise_for_status()
            return response.json()

    def prepare(self, selection, selected_ids):
        body = self._stored(selection['local_context_id'], selection['scope'])
        selected = set(selected_ids)
        items = [item for item in body['items'] if item['insight_id'] in selected]
        if (body['source_collection_id'] != selection['collection_id']
                or not 1 <= len(items) == len(selected_ids) <= 20
                or any(not isinstance(item['pdf_page'], int) or item['pdf_page'] < 1 for item in items)):
            raise ValueError('insight_selection_changed')
        context = selection['remote_context_id']
        connection = selection['connection']
        mapped = self._post(context, '/map-sources', {
            'local_version_id': body['local_version_id'], 'pdf_sha256': body['pdf_sha256'],
            'section_scoped': selection['scope'] != 'paper',
            'sources': [{key: item[key] for key in ('original_sha256', 'translated_sha256', 'pdf_page')}
                        for item in items],
        }, connection)
        binding = {key: str(UUID(mapped[key])) for key in
                   ('publication_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id')}
        body.update(binding, section_id=mapped['section_id'], confirmed=True,
                    items=[dict(item, remote_chunk_id=str(UUID(chunk)))
                           for item, chunk in zip(items, mapped['remote_chunk_ids'], strict=True)])
        identity = json.dumps([connection, context, body], sort_keys=True, ensure_ascii=False)
        body['request_key'] = str(uuid5(NAMESPACE_URL, identity))
        review = {'server': self.client.server_url, 'remote_context_id': context,
                  'publication_id': binding['publication_id'], 'server_version_id': mapped['server_version_id'],
                  'items': [self._display(item) for item in items]}
        return review, {'context': context, 'connection': connection, 'body': body}

    def submit(self, snapshot):
        body = snapshot['body']
        result = self._post(snapshot['context'], '', body, snapshot['connection'])
        if (result.get('source_collection_id') != body['source_collection_id']
                or type(result.get('revision')) is not int or result['revision'] < 1):
            raise ValueError('insight_receipt_invalid')
        return {'status': 'saved', 'revision': result['revision']}
