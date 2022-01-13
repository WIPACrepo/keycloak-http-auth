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

        if True:  # here is where you would test policy
            if 'sub' not in token:
                raise HTTPError(403, reason='sub not in token')
            logging.info(f'valid request for user {token["sub"]}: {method}:{path}')
            username = token.get('posix', {}).get('username', None)
            if not username:
                username = token.get('upn', None)
            if not username:
                username = token.get('preferred_username', None)
            if not username:
                raise HTTPError(400, 'username missing from token')
            self.set_header('REMOTE_USER', username)
            if uid := token.get('posix', {}).get('uid', None):
                self.set_header('X_UID', uid)
            if gid := token.get('posix', {}).get('gid', None):
                self.set_header('X_GID', gid)
            self.write('')
        else:
            self.send_error(403, 'not authorized')

def create_server():
    default_config = {
        'HOST': 'localhost',
        'PORT': 8080,
        'DEBUG': False,
        'ISSUERS': None,
        'AUDIENCE': None,
        'BASE_PATH': '/',
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
    server.add_route(r'/(.*)', Main, kwargs)

    server.startup(address=config['HOST'], port=config['PORT'])

    return server
