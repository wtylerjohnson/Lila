"""Local regression guard: refuse external Python DNS resolution before lookup."""
import ipaddress
import json
import os
import socket

_original = socket.getaddrinfo


def _getaddrinfo(host, *args, **kwargs):
    value = host.decode() if isinstance(host, bytes) else host
    allowed = value in (None, '', 'localhost')
    if not allowed:
        try:
            address = ipaddress.ip_address(value)
            allowed = address.is_loopback or address.is_unspecified
        except ValueError:
            pass
    if not allowed:
        path = os.environ.get('LILA_TEST_NETWORK_GUARD_LOG')
        if path:
            with open(path, 'a') as handle:
                handle.write(json.dumps({'event': 'external_resolution_blocked',
                                         'host': str(value), 'pid': os.getpid()}) + '\n')
        raise socket.gaierror(socket.EAI_NONAME, 'external DNS disabled by regression guard')
    return _original(host, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo
