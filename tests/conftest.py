"""Foundation tests must not resolve or connect to external services."""

import socket

import pytest


@pytest.fixture(autouse=True)
def local_test_environment(monkeypatch: pytest.MonkeyPatch, request) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    def deny_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("External network access is forbidden in foundation tests.")

    if request.node.get_closest_marker('postgres'):
        original_resolve, original_connect = socket.getaddrinfo, socket.create_connection
        def local_resolve(host, *args, **kwargs):
            if host not in {'127.0.0.1','localhost','::1'}:
                return deny_network()
            return original_resolve(host, *args, **kwargs)
        def local_connect(address, *args, **kwargs):
            if address[0] not in {'127.0.0.1','localhost','::1'}:
                return deny_network()
            return original_connect(address, *args, **kwargs)
        monkeypatch.setattr(socket, 'getaddrinfo', local_resolve)
        monkeypatch.setattr(socket, 'create_connection', local_connect)
    else:
        monkeypatch.setattr(socket, "getaddrinfo", deny_network)
        monkeypatch.setattr(socket, "create_connection", deny_network)
