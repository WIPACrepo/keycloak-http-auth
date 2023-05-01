from contextlib import contextmanager
from pathlib import Path
import asyncio
import random
import requests
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
from functools import partial
import argparse
from prometheus_client import CollectorRegistry, push_to_gateway, Summary, Gauge, Info
from threading import Thread
import logging


DATA_URL = 'http://localhost:8000'
HEALTH_URL = 'http://localhost:8080/basic_status'

registry = CollectorRegistry()


@contextmanager
def run_nginx():
    base_dir = Path(__file__).parent.parent
    subprocess.run('podman build -t keycloak-http-auth:nginx-test -f Dockerfile_nginx .', shell=True, check=True, cwd=base_dir)
    subprocess.run('podman build -t keycloak-http-auth:test -f Dockerfile .', shell=True, check=True, cwd=base_dir)
    with TemporaryDirectory() as tmpdirname:
        subprocess.run('podman pod create -p 8000:80 -p 8080:8080 -p 8081:8081 --userns=keep-id --cpus=.2 nginx', shell=True, check=True)
        try:
            p = Path(tmpdirname) / 'tmp'
            p.mkdir()
            p.chmod(0o777)

            subprocess.run(f'podman run --rm -d --name nginx-test --pod nginx --user=root -v {tmpdirname}:/mnt keycloak-http-auth:nginx-test', shell=True, check=True, cwd=base_dir)

            p = Path(tmpdirname) / 'auth.py'
            with open(p, 'w') as f:
                f.write("""import asyncio
from rest_tools.server import RestServer, RestHandler, RestHandlerSetup, catch_error
class Main(RestHandler):
    @catch_error
    async def get(self, *args):
        self.set_header('X_UID', 1000)
        self.set_header('X_GID', 1000)
        self.set_header('X_GROUPS', '1000')
        self.write('')
kwargs = RestHandlerSetup({})
server = RestServer()
server.add_route(r'/(.*)', Main, kwargs)
server.startup('', port=8081)
asyncio.get_event_loop().run_forever()
""")
            subprocess.run(f'podman run --rm -d --name nginx-test-auth --pod nginx -v {tmpdirname}:/mnt keycloak-http-auth:test python /mnt/auth.py', shell=True, check=True, cwd=base_dir)

            p = Path(tmpdirname) / 'data'
            p.mkdir()
            yield p
        finally:
            subprocess.run('podman pod kill nginx', shell=True, check=True)
            subprocess.run('podman pod rm -f nginx', shell=True, check=True)
            

REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request', ['method'], registry=registry)

async def get(size=100, speed=0.1):
    """GET, size in MB"""
    logging.info(f'get: size={size}MB, speed={speed}MB/s')
    with REQUEST_TIME.labels('get').time():
        proc = await asyncio.create_subprocess_shell(
            f"curl -XGET -o /dev/null --limit-rate {int(speed*1000)}k http://localhost:8000/data/{size}MB",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        ret = await proc.wait()
    logging.debug('get: ret code=%s', ret)

async def put(size=100, speed=0.1):
    """PUT, size in MB"""
    logging.info(f'put: size={size}MB, speed={speed}MB/s')
    with REQUEST_TIME.labels('put').time(), NamedTemporaryFile() as f:
        for _ in range(0, size):
            f.write(random.randbytes(1000000))
        f.flush()
        proc = await asyncio.create_subprocess_shell(
            f"curl -XPUT -T{f.name} -o /dev/null --limit-rate {int(speed*1000)}k http://localhost:8000/data/{size}MB",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        ret = await proc.wait()
    logging.debug('put: ret code=%s', ret)

DURATION = Summary('health_processing_seconds', 'Time spent processing health request', ['status'], registry=registry)
CONNECTIONS = Gauge('connections', 'number of connections', registry=registry)

def get_status():
    logging.info('get_status')
    start = time.time()
    r = requests.get(HEALTH_URL)
    end = time.time()
    ret = {'time': start, 'duration': end-start, 'status': r.status_code}
    DURATION.labels(ret['status']).observe(ret['duration'])
    if r.status_code != 200:
        return ret
    lines = r.text.split('\n')
    ret['connections'] = int(lines[0].split(':')[-1].strip())
    CONNECTIONS.set(ret['connections'])
    return ret


def health_loop(**kwargs):
    with open('stats', 'w') as f:
        i = Info('batch', 'batch variables')
        i.info(kwargs)
        while True:
            start = time.time()
            print(get_status(), file=f, flush=True)
            push_to_gateway('localhost:9091', job='batchA', registry=registry)
            time.sleep(max(0, 1-(time.time()-start))) # sleep up to 1 second


async def get_loop(requests=0, **kwargs):
    await asyncio.sleep(15)
    while True:
        tasks = set()
        for _ in range(requests):
            tasks.add(asyncio.create_task(get(**kwargs)))
        while True:
            done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                await t
                tasks.add(asyncio.create_task(get(**kwargs)))


async def put_loop(requests=0, **kwargs):
    while True:
        start = time.time()
        tasks = []
        for _ in range(requests):
            tasks.append(asyncio.create_task(put(**kwargs)))
        await asyncio.gather(*tasks)
        await asyncio.sleep(max(0, 1-(time.time()-start))) # sleep up to 1 second


async def start_async(**kwargs):
    g = get_loop(**{k.replace('get_',''):v for k,v in kwargs.items() if k.startswith('get_')})
    p = put_loop(**{k.replace('put_',''):v for k,v in kwargs.items() if k.startswith('put_')})
    await asyncio.gather(g, p)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--get-requests', type=int, default=0, help='GET requests per second')
    parser.add_argument('--get-size', type=int, default=100, help='GET size of transfer in MB')
    parser.add_argument('--get-speed', type=float, default=1.0, help='GET speed of transfer in MB/s')
    parser.add_argument('--put-requests', type=int, default=0, help='PUT requests per second')
    parser.add_argument('--put-size', type=int, default=100, help='PUT size of transfer in MB')
    parser.add_argument('--put-speed', type=float, default=1.0, help='PUT speed of transfer in MB/s')
    parser.add_argument('--log-level', default='info')
    args = parser.parse_args()
    kwargs = vars(args)

    logging.basicConfig(level=getattr(logging, kwargs.pop('log_level').upper()), format='%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)s - %(message)s')

    with run_nginx() as data_path:
        health = Thread(daemon=True, target=health_loop, kwargs=kwargs).start()
        asyncio.run(start_async(**kwargs))

if __name__ == '__main__':
    main()
