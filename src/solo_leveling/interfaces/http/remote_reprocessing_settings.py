"""명시적인 전용 복구 설정만 조립하며 DB·외부 API에는 접근하지 않는다."""
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit, parse_qsl
from solo_leveling.application.quiz.remote_contract import load as _load
from .remote_reprocessing import PostgresReprocessingReviews

@dataclass(frozen=True)
class ReprocessingSettings:
    dsn: str = field(repr=False)
    arguments: dict = field(repr=False)


def load_reprocessing_settings(env=None):
    env = os.environ if env is None else env
    enabled = env.get('REMOTE_REPROCESSING_ENABLED', 'false')
    if enabled == 'false':
        return None
    try:
        if enabled != 'true':
            raise ValueError()
        raw = env['REMOTE_REPROCESSING_SETTINGS_JSON']
        if not isinstance(raw,str) or len(raw.encode('utf-8')) > 16384:
            raise ValueError()
        data = _load(raw)
        if type(data) is not dict or set(data) != {'config'}:
            raise ValueError()
        PostgresReprocessingReviews(None, **data)
        dsn = env['REMOTE_REPROCESSING_DATABASE_URL'].strip()
        url = urlsplit(dsn)
        if (url.scheme not in ('postgres','postgresql') or not url.hostname or not url.username
                or not url.password or url.path in ('','/') or url.fragment or not 1 <= (url.port or 5432) <= 65535):
            raise ValueError()
        query = parse_qsl(url.query,keep_blank_values=True)
        if len(query)>1 or any(k!='sslmode' for k,_ in query):
            raise ValueError()
        if not (dict(query).get('sslmode')=='verify-full' or url.hostname in ('localhost','127.0.0.1','::1')
                and dict(query).get('sslmode') in (None,'disable')):
            raise ValueError()
        return ReprocessingSettings(dsn,data)
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('reprocessing_settings_invalid') from None


def reprocessing_connection_dsn(settings):
    """복구 TLS 풀에만 배포 패키지의 CA를 지정하고 전역 DB 설정은 유지한다."""
    if dict(parse_qsl(urlsplit(settings.dsn).query)).get('sslmode') != 'verify-full':
        return settings.dsn
    import certifi
    from psycopg.conninfo import make_conninfo
    return make_conninfo(settings.dsn, sslrootcert=certifi.where())
