"""취소 쓰기는 기본 비활성이며 별도 DB 역할만 사용할 수 있다."""
import os
from urllib.parse import urlsplit, unquote


def load_processing_cancel_dsn(env=None):
    env=os.environ if env is None else env
    if env.get('REMOTE_JOB_CANCELLATIONS_ENABLED','false') == 'false':
        return None
    try:
        if env.get('REMOTE_JOB_CANCELLATIONS_ENABLED') != 'true':
            raise ValueError()
        value=env['REMOTE_JOB_CANCELLATION_DATABASE_URL'].strip()
        target=urlsplit(value)
        read=urlsplit(env['REMOTE_MCP_DATABASE_URL'].strip())
        if (target.scheme not in ('postgres','postgresql') or not target.hostname
                or not target.username or not target.password or target.path in ('','/') or target.fragment
                or (target.hostname,target.port or 5432,target.path,target.query) !=
                   (read.hostname,read.port or 5432,read.path,read.query)):
            raise ValueError()
        for name in ('DATABASE_URL','REMOTE_MCP_DATABASE_URL','REMOTE_ANNOTATION_MCP_DATABASE_URL','REMOTE_ANNOTATION_WEB_DATABASE_URL'):
            if env.get(name) and unquote(urlsplit(env[name]).username or '') == unquote(target.username):
                raise ValueError()
        return value
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('processing_cancel_settings_invalid') from None
