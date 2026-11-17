"""인증된 원격 요청의 목록·고정 맥락·기존 실행 조회. 생성·provider 포트는 없다."""
import time
from uuid import UUID
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_identities import PostgresRemoteOwnerResolver
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.application.remote_replay import read_existing_execution
from solo_leveling.interfaces.mcp.remote_recovery import restored_guidance


class RemoteProductQueries:
    def __init__(self, pool):
        self.pool = pool
        self.owners = PostgresRemoteOwnerResolver(pool)
        self.contexts = PostgresRemoteContextResolver(pool)
        self.executions = PostgresRemoteExecutionStore(pool)

    def owner(self):
        token = get_access_token()
        if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'papers:read' not in token.scopes):
            return None
        return self.owners.resolve_owner(token.verified_identity())

    def list_papers(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000:
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            if owner is None:
                return {'status':'not_found'}
            with self.pool.connection() as conn:
                rows = conn.execute('''SELECT p.paper_id,p.title,v.server_version_id,pub.publication_id
                    FROM server_processing_versions v
                    JOIN server_papers p ON p.owner_user_id=v.owner_user_id AND p.paper_id=v.paper_id
                    JOIN server_processing_publications pub ON pub.owner_user_id=v.owner_user_id
                        AND pub.paper_id=v.paper_id AND pub.server_version_id=v.server_version_id
                        AND pub.publication_id=v.active_publication_id
                    WHERE p.owner_user_id=%s AND p.deleted_at IS NULL
                    ORDER BY p.paper_id,v.server_version_id LIMIT 21 OFFSET %s''',(owner,offset)).fetchall()
            return {'status':'ok','papers':[{k:str(v) if v is not None else None for k,v in row.items()}
                    for row in rows[:20]],'next_offset':offset+20 if len(rows)>20 else None}
        except Exception:
            return {'status':'unavailable'}

    def open_paper(self, paper_id, version_id, context_key):
        try:
            if not isinstance(version_id,str) or not version_id.strip() or len(version_id)>512:
                return {'status':'invalid_argument'}
            ids = [str(UUID(paper_id)),version_id,str(UUID(context_key))]
        except (ValueError,TypeError,AttributeError):
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            binding = self.contexts.pin(owner,ids[2],ids[0],ids[1]) if owner else None
            if binding is None:
                return {'status':'not_found'}
            return {'status':'ok', 'context_id':binding.context_id, 'paper_id':binding.paper_id,
                    'version_id':binding.server_version_id, 'publication_id':binding.publication_id,
                    'message':'추천 페이지와 근거는 이 PDF 판본 기준입니다. 다른 판본은 페이지가 다를 수 있습니다.'}
        except ValueError as error:
            return {'status':'conflict' if str(error)=='remote_context_key_conflict' else 'unavailable'}
        except Exception:
            return {'status':'unavailable'}

    def execution(self, request_key):
        if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>512:
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            row = self.executions.lookup(owner,'ask_paper',request_key) if owner else None
            if row is None or not row.access_allowed:
                return {'status':'not_found'}
            # 읽기 포트만 사용한다. 조회 없음·오류·unknown에서도 접수하지 않는다.
            view = read_existing_execution(self.executions,row.lookup)
            if view is None:
                return {'status':'not_found'}
            return {'status':'ok', **restored_guidance(view), 'result':view.payload}
        except Exception:
            return {'status':'unavailable'}


def register_product_queries(server, queries):
    @server.tool()
    def list_remote_papers(offset: int = 0) -> dict:
        """현재 계정의 서버 처리 논문을 20개씩 조회한다. PDF·로컬 파일을 열지 않는다."""
        return queries.list_papers(offset)

    @server.tool()
    def open_remote_paper(paper_id: str, version_id: str, context_key: str) -> dict:
        """UUID context_key로 판본·게시를 고정한다. 응답 유실 시 같은 키를 사용한다. AI 호출은 없다."""
        return queries.open_paper(paper_id,version_id,context_key)

    @server.tool()
    def get_remote_question(request_key: str) -> dict:
        """기존 질문만 조회한다. 조회 실패·미존재는 재제출 이유가 아니며 새 유료 작업을 만들지 않는다."""
        return queries.execution(request_key)
