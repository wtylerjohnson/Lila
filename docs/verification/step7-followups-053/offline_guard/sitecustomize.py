"""Local regression guard: refuse external Python DNS resolution before lookup."""
import ipaddress
import errno
import json
import os
import socket

_original = socket.getaddrinfo
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _local(value):
    if value in (None, '', 'localhost'):
        return True
    try:
        address = ipaddress.ip_address(value)
        return address.is_loopback or address.is_unspecified
    except ValueError:
        return False


def _record(event, host):
    path = os.environ.get('LILA_TEST_NETWORK_GUARD_LOG')
    if path:
        with open(path, 'a') as handle:
            handle.write(json.dumps({'event': event, 'host': str(host),
                                     'pid': os.getpid()}) + '\n')


def _getaddrinfo(host, *args, **kwargs):
    value = host.decode() if isinstance(host, bytes) else host
    if not _local(value):
        _record('external_resolution_blocked', value)
        raise socket.gaierror(socket.EAI_NONAME, 'external DNS disabled by regression guard')
    return _original(host, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo


def _connect(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
        _record('external_connect_blocked', address[0])
        raise PermissionError(errno.EPERM, 'external IP disabled by regression guard')
    return _original_connect(sock, address)


def _connect_ex(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
        _record('external_connect_ex_blocked', address[0])
        return errno.EPERM
    return _original_connect_ex(sock, address)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
