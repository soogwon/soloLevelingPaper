"""로컬 stdio와 원격 MCP가 독립적으로 필요한 모듈만 불러온다."""

__all__ = ['create_server']


def __getattr__(name):
    if name == 'create_server':
        from .server import create_server
        return create_server
    raise AttributeError(name)
