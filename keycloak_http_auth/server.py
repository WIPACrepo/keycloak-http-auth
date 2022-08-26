"""
Server for keycloak token http auth
"""

import logging

from tornado.web import HTTPError
from rest_tools.server import RestServer, RestHandler, RestHandlerSetup, authenticated, catch_error
from rest_tools.utils import from_environment


class Main(RestHandler):
    @authenticated
    @catch_error
    async def get(self, *args):
        method = self.request.headers.get('X-Original-Method', '')
        path = self.request.headers.get('X-Original-URI', '')
        token = self.auth_data

        # test basic token properties
        if 'sub' not in token:
            raise HTTPError(403, reason='sub not in token')

        username = token.get('posix', {}).get('username', None)
        if not username:
            username = token.get('upn', None)
        if not username:
            username = token.get('preferred_username', None)
        if not username:
            raise HTTPError(400, 'username missing from token')

        logging.info(f'request for user {username}: {method}:{path}')

        # check for posix info
        uid = token.get('posix', {}).get('uid', None)
        if not uid:
            raise HTTPError(403, reason='posix.uid missing from token')
        gid = token.get('posix', {}).get('gid', None)
        if not gid:
            raise HTTPError(403, reason='posix.gid missing from token')
        gids = set(token.get('posix', {}).get('group_gids', []))
        if gid:
            gids.add(gid)

        # if you want to do other checks, add them here

        # set nginx uid and gid
        self.set_header('REMOTE_USER', username)
        self.set_header('X_UID', uid)
        self.set_header('X_GID', gid)
        self.set_header('X_GROUPS', ','.join(f'{g}' for g in gids))
        self.write('')


class Health(RestHandler):
    def get(self):
        self.write('')


def create_server():
    default_config = {
        'HOST': 'localhost',
        'PORT': 8080,
        'DEBUG': False,
        'ISSUERS': None,
        'AUDIENCE': None,
        'KEYCLOAK_URL': None,
        'KEYCLOAK_REALM': 'IceCube',
    }
    config = from_environment(default_config)

    rest_config = {
        'debug': config['DEBUG'],
        'auth': {
            'openid_url': f'{config["KEYCLOAK_URL"]}/auth/realms/{config["KEYCLOAK_REALM"]}',
            'audience': config['AUDIENCE'].split(','),
            'issuers': config['ISSUERS'].split(','),
        }
    }
    kwargs = RestHandlerSetup(rest_config)

    server = RestServer(debug=config['DEBUG'])
    server.add_route('/healthz', Health, kwargs)
    server.add_route(r'/(.*)', Main, kwargs)

    server.startup(address=config['HOST'], port=config['PORT'])

    return server
