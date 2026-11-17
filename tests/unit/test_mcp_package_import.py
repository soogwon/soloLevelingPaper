"""원격 패키지 조회가 로컬 stdio 서버를 불러오지 않는지 확인한다."""
import subprocess
import sys


def test_remote_package_does_not_import_local_server():
    subprocess.run([sys.executable, '-c', '''
import sys
import types
import solo_leveling.interfaces.mcp as package
assert 'solo_leveling.interfaces.mcp.server' not in sys.modules
server = types.ModuleType('solo_leveling.interfaces.mcp.server')
server.create_server = lambda: 'local'
sys.modules[server.__name__] = server
from solo_leveling.interfaces.mcp import create_server
assert create_server is server.create_server
assert create_server() == 'local'
try:
    package.missing_attribute
except AttributeError:
    pass
else:
    raise AssertionError('없는 속성은 거부해야 합니다.')
'''], check=True)
