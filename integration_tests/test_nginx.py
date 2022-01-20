import subprocess
from pathlib import Path
import http.server
from threading import Thread
import socket
import time

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
user                    root;
worker_processes        1;
load_module             modules/ngx_http_fancyindex_module.so;
load_module             modules/ngx_http_dav_ext_module.so;
load_module             modules/ndk_http_module.so;
load_module             modules/ngx_http_lua_module.so;
error_log               /var/log/nginx/error.log warn;
pid                     /var/run/nginx.pid;
events {{
    worker_connections  1024;
}}
http {{
    sendfile            on;
    keepalive_timeout   65;
    gzip                on;
    server {{
      listen 0.0.0.0:{nginx_port};
      server_name "{dns}";
      port_in_redirect off;
      absolute_redirect off;
      root /mnt;
      index index.html;
      # Set the maximum size of uploads
      client_max_body_size 20000m;
      # Default is 60, May need to be increased for very large uploads
      client_body_timeout 3600s;
      location /auth {{
        internal;
        proxy_pass              http://127.0.0.1:{app_port}/;
        proxy_pass_request_body off;
        proxy_connect_timeout   {timeout}s;
        proxy_read_timeout      {timeout}s;
        proxy_send_timeout      {timeout}s;
        proxy_set_header        Content-Length "";
        proxy_set_header        X-Original-URI $request_uri;
        proxy_set_header        X-Original-Method $request_method;
      }}
      location /tmp {{
        internal;
      }}
      location / {{
        fancyindex              on;
        fancyindex_exact_size   off;
        alias                   /mnt/;
        client_body_temp_path   /mnt/tmp;
        # webdav setup
        dav_methods             PUT DELETE MKCOL COPY MOVE;
        dav_ext_methods         PROPFIND OPTIONS;
        create_full_put_path    on;
        dav_access              group:rw all:r;
        # auth subrequest
        auth_request            /auth;
        auth_request_set        $auth_status $upstream_status;
        auth_request_set        $saved_remote_user $upstream_http_REMOTE_USER;
        auth_request_set        $saved_remote_uid $upstream_http_X_UID;
        auth_request_set        $saved_remote_gid $upstream_http_X_GID;
        # impersonation
        access_by_lua_block {{
          local syscall_api = require 'syscall'
          local ffi = require "ffi"
          local nr = require("syscall.linux.nr")
          local sys = nr.SYS
          local uint = ffi.typeof("unsigned int")
          local syscall_long = ffi.C.syscall -- returns long
          local function syscall(...) return tonumber(syscall_long(...)) end
          local function setfsuid(id) return syscall(sys.setfsuid, uint(id)) end
          local function setfsgid(id) return syscall(sys.setfsgid, uint(id)) end
          local new_uid = tonumber(ngx.var.saved_remote_uid)
          local new_gid = tonumber(ngx.var.saved_remote_gid)
          ngx.log(ngx.NOTICE, "[Impersonating User " .. new_uid .. ":" .. new_gid .. "]")
          local previous_uid = setfsuid(new_uid)
          local actual_uid = setfsuid(new_uid)
          local previous_gid = setfsgid(new_gid)
          local actual_gid = setfsgid(new_gid)
          if actual_uid ~= new_uid or actual_gid ~= new_gid then
            ngx.log(ngx.CRIT, "Unable to impersonate users")
            ngx.exit(ngx.HTTP_INTERNAL_SERVER_ERROR)
          end
        }}
      }}
    }}
}}
"""

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
        for i in range(50):
            try:
                socket.create_connection(('127.0.0.1', nginx_port), 0.1)
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


def test_missing(nginx):
    r = requests.get(f'http://localhost:{nginx["nginx_port"]}/missing')
    assert r.status_code == 404

def test_read(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)
    (nginx["data"] / "test").write_bytes(data)

    r = requests.get(f'http://localhost:{nginx["nginx_port"]}/data/test')
    assert r.status_code == 200
    assert r.content == data

def test_write(nginx):
    data = b'foo bar baz'
    nginx["data"].chmod(0o777)

    r = requests.put(f'http://localhost:{nginx["nginx_port"]}/data/test', data=data)
    assert r.status_code == 204

    f = (nginx["data"] / "test")
    assert f.read_bytes() == data
    out = nginx['exec_in_container']('stat','-c','%u %g','/mnt/data/test')
    assert out.strip() == b'123 456'
