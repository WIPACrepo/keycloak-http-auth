import subprocess
from pathlib import Path
import http.server
from threading import Thread
import socket
import time
import shutil

import pytest
import requests

from .util import *


def port():
    """Get an ephemeral port number."""
    # https://unix.stackexchange.com/a/132524
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    addr = s.getsockname()
    ephemeral_port = addr[1]
    s.close()
    return ephemeral_port

config = """
listen 0.0.0.0:{nginx_port};
location /auth {{
  internal;
  proxy_pass              http://127.0.0.1:{app_port}/;
  proxy_connect_timeout   {timeout}s;
  proxy_read_timeout      {timeout}s;
  proxy_send_timeout      {timeout}s;
  proxy_set_header        Content-Length "";
  proxy_set_header        X-Original-URI $request_uri;
  proxy_set_header        X-Original-Method $request_method;
}}
"""

@pytest.fixture(scope="session")
def fake_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.wfile.write(b'HTTP/1.0 200 OK\r\n')
            self.send_header('REMOTE_USER', 'user')
            self.send_header('X_UID', '123')
            self.send_header('X_GID', '456')
            self.end_headers()
            self.wfile.write(b'\r\n')
    app_port = port()
    s = http.server.HTTPServer(('', app_port), Handler)
    t = Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield app_port

@pytest.fixture(scope="session")
def nginx(tmp_path_factory, fake_server):
    workdir = Path(__file__).parent.parent
    subprocess.run(['docker', 'build', '-t', 'wipac/keycloak-http-auth:testing',
                    '-f', 'Dockerfile_nginx', f'{workdir}'], check=True, cwd=workdir)

    test_volume = tmp_path_factory.mktemp('cache')
    nginx_config = tmp_path_factory.mktemp('conf') / 'config.conf'
    nginx_port = port()
    app_port = fake_server

    nginx_config.write_text(config.format(nginx_port=nginx_port, app_port=app_port, timeout=1))

    with subprocess.Popen(['docker', 'run', '--rm', '--network=host', '--name', 'test_nginx_integration',
                           '-v', f'{nginx_config}:/etc/nginx/custom.conf:ro',
                           '-v', f'{test_volume}:/mnt/data:rw', 'wipac/keycloak-http-auth:testing']) as p:
        # wait for server to come up
        for i in range(10):
            try:
                socket.create_connection(('localhost', nginx_port), 0.1)
            except ConnectionRefusedError:
                if i >= 9:
                    raise
                else:
                    time.sleep(.1)

        def fn(*args):
            return subprocess.run(['docker', 'exec', 'test_nginx_integration']+list(args), check=True, cwd=workdir, capture_output=True).stdout

        try:
            yield {
                'data': test_volume,
                'nginx_port': nginx_port,
                'app_port': app_port,
                'exec_in_container': fn,
            }
        finally:
            p.terminate()

@pytest.fixture(autouse=True)
def clear_test_volume(nginx):
    for child in nginx['data'].iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

def test_missing(nginx):
    r = requests.get(f'http://localhost:{nginx["nginx_port"]}/missing')
    assert r.status_code == 404

def test_read(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)
    (nginx["data"] / "test").write_bytes(data)

    r = requests.get(f'http://localhost:{nginx["nginx_port"]}/data/test')
    r.raise_for_status()
    assert r.content == data

def test_read_bad_perms(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)
    (nginx["data"] / "test").write_bytes(data)
    (nginx["data"] / "test").chmod(0x700)

    r = requests.get(f'http://localhost:{nginx["nginx_port"]}/data/test')
    with pytest.raises(Exception):
        r.raise_for_status()

def test_write(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)

    r = requests.put(f'http://localhost:{nginx["nginx_port"]}/data/test', data=data)
    r.raise_for_status()

    f = (nginx["data"] / "test")
    assert f.read_bytes() == data
    out = nginx['exec_in_container']('stat','-c','%u %g','/mnt/data/test')
    assert out.strip() == b'123 456'

def test_write_bad_perms(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o555)

    r = requests.put(f'http://localhost:{nginx["nginx_port"]}/data/test', data=data)
    with pytest.raises(Exception):
        r.raise_for_status()

    f = (nginx["data"] / "test")
    assert not f.exists()

def test_delete(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)

    r = requests.put(f'http://localhost:{nginx["nginx_port"]}/data/test', data=data)
    r.raise_for_status()

    r = requests.delete(f'http://localhost:{nginx["nginx_port"]}/data/test')
    r.raise_for_status()

    f = (nginx["data"] / "test")
    assert not f.exists()

def test_mkdir(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)

    r = requests.request('MKCOL', f'http://localhost:{nginx["nginx_port"]}/data/test/')
    r.raise_for_status()

    f = (nginx["data"] / "test")
    assert f.is_dir()
    out = nginx['exec_in_container']('stat','-c','%u %g','/mnt/data/test')
    assert out.strip() == b'123 456'
