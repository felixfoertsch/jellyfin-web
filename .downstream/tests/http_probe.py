#!/usr/bin/env python3
"""Mock backend and frontend HTTP/WebSocket probes; not a playback test."""
from __future__ import annotations

import base64
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import time


class Backend(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self) -> None:
        if self.path.startswith('/socket'):
            if self.headers.get('Upgrade', '').lower() != 'websocket':
                self.send_error(400, 'WebSocket upgrade header missing')
                return
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(), usedforsecurity=False
            ).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            self.close_connection = True
            return
        body = json.dumps({'mock_backend': True, 'path': self.path,
                           'forwarded_proto': self.headers.get('X-Forwarded-Proto')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def probe(port: int) -> None:
    def get(path: str, headers: dict[str, str] | None = None):
        connection = HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            data = response.read() if response.status != 101 else b''
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), data
        finally:
            connection.close()

    for attempt in range(30):
        try:
            if get('/healthz')[0] == 200 and get('/System/Info/Public')[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Frontend did not become healthy')
    for path in ['/', '/web']:
        status, headers, _ = get(path)
        assert status == 302 and headers['location'] == '/web/', (path, status, headers)
    for path in ['/web/', '/web/index.html']:
        status, headers, body = get(path)
        assert status == 200 and b'<html' in body.lower(), (path, status)
        assert headers['cache-control'] == 'no-cache'
    status, _, body = get('/web/config.json')
    assert status == 200 and isinstance(json.loads(body), dict)
    assert get('/web/definitely-not-an-asset.js')[0] == 404
    for path in ['/System/Info/Public', '/Videos/test/stream?sample=a%2Fb&n=1']:
        status, _, body = get(path, {'X-Forwarded-Proto': 'https'})
        result = json.loads(body)
        assert status == 200 and result['mock_backend'] and result['path'] == path
        assert result['forwarded_proto'] == 'https'
    key = base64.b64encode(b'0123456789abcdef').decode()
    status, headers, _ = get('/socket?sample=1', {
        'Connection': 'Upgrade', 'Upgrade': 'websocket',
        'Sec-WebSocket-Key': key, 'Sec-WebSocket-Version': '13'
    })
    expected = base64.b64encode(hashlib.sha1(
        (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(), usedforsecurity=False
    ).digest()).decode()
    assert status == 101 and headers['sec-websocket-accept'] == expected
    print('PASS: redirects, static assets, missing assets, API, stream URL, HTTPS forwarding, WebSocket handshake')


if __name__ == '__main__':
    if sys.argv[1] == 'serve':
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8096
        ThreadingHTTPServer(('0.0.0.0', port), Backend).serve_forever()
    else:
        probe(int(sys.argv[2]))
