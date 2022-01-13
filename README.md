# keycloak-http-auth
Validate auth and enforce ACLs for HTTP-based Keycloak token requests. Ideal for answering nginx auth requests.

Because this is meant for non-interactive use, failures only return 403.

On success, these http headers are returned:
* REMOTE_USER: the `posix.username` or `upn` or `preferred_username` in the token (required)
* X_UID: the `posix.uid` in the token (optional)
* X_GID: the `posix.gid` in the token (optional)

UID and GID are useful for POSIX filesystem access, for example [https://unix.stackexchange.com/a/489744](https://unix.stackexchange.com/a/489744).

## Configuration

Primary configuration is via environment variables:

* ISSUERS: the issuers, comma-separated
* AUDIENCE: the audience
* BASE_PATH: the base path of the server, before any authorized paths in the token
