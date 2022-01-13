import socket
import asyncio

import pytest
from requests.exceptions import HTTPError
from rest_tools.client import AsyncSession
import requests_mock

from keycloak_http_auth.server import create_server

from .util import *

@pytest.fixture
def port():
    """Get an ephemeral port number."""
    # https://unix.stackexchange.com/a/132524
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    addr = s.getsockname()
    ephemeral_port = addr[1]
    s.close()
    return ephemeral_port

@pytest.fixture
async def server(monkeypatch, port, make_token, gen_jwk):
    monkeypatch.setenv('DEBUG', 'True')
    monkeypatch.setenv('PORT', str(port))

    monkeypatch.setenv('ISSUERS', 'issuer')
    monkeypatch.setenv('AUDIENCE', 'aud')
    monkeypatch.setenv('BASE_PATH', '/base')
    monkeypatch.setenv('KEYCLOAK_URL', 'http://foo')
    monkeypatch.setenv('KEYCLOAK_REALM', 'testing')

    with requests_mock.Mocker(real_http=True) as m:
        m.get('http://foo/auth/realms/testing/.well-known/openid-configuration', text=json.dumps({
            'token_endpoint': 'http://foo/auth/realms/testing/token',
            'jwks_uri': 'http://foo/auth/realms/testing/certs',
        }))
        m.get('http://foo/auth/realms/testing/certs', text=json.dumps({
            'keys': [gen_jwk],
        }))

        s = create_server()

    def fn(posix):
        token = make_token(posix, 'issuer', 'aud')
        session = AsyncSession(retries=0)
        async def fn2(method, path, headers):
            url = f'http://localhost:{port}{path}'
            headers['Authorization'] = 'Bearer '+token
            ret = await asyncio.wrap_future(session.request(method, url, timeout=0.1, headers=headers))
            ret.raise_for_status()
            return ret
        return fn2

    try:
        yield fn
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_server_root_path(server):
    client = server({'username': 'foo', 'uid': 1000, 'gid': 1001})
    ret = await client('GET', '/', headers={
        'X-Original-Method': 'GET',
        'X-Original-URI': '/base/',
    })
    assert ret.headers['REMOTE_USER'] == 'foo'
    assert ret.headers['X_UID'] == '1000'
    assert ret.headers['X_GID'] == '1001'

@pytest.mark.asyncio
async def test_server_post(server):
    client = server({'username': 'foo', 'uid': 1000, 'gid': 1000})
    with pytest.raises(HTTPError, match='405'):
        await client('POST', '/', headers={
            'X-Original-Method': 'GET',
            'X-Original-URI': '/base/',
        })

@pytest.mark.asyncio
async def test_server_missing_user(server):
    client = server({})
    with pytest.raises(HTTPError, match='400'):
        await client('GET', '/', headers={
            'X-Original-Method': 'GET',
            'X-Original-URI': '/base/',
        })
