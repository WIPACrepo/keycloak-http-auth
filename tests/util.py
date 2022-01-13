import json
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from rest_tools.utils import Auth


@pytest.fixture(scope="session")
def gen_keys():
    priv = generate_private_key(65537, 2048)
    pub = priv.public_key()
    return (priv, pub)

@pytest.fixture(scope="session")
def gen_keys_bytes(gen_keys):
    priv, pub = gen_keys

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    print(priv_pem, pub_pem)
    return (priv_pem, pub_pem)

@pytest.fixture(scope="session")
def gen_jwk(gen_keys):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(gen_keys[1]))
    jwk['kid'] = 'testing'
    return jwk

@pytest.fixture
def make_token(gen_keys_bytes):
    def func(posix, issuer, audience):
        auth = Auth(gen_keys_bytes[0], pub_secret=gen_keys_bytes[1], algorithm='RS256', issuer=issuer)
        return auth.create_token('testing', payload={
            'scope': 'posix',
            'aud': audience,
            'posix': posix,
        }, headers={'kid': 'testing'})
    yield func
