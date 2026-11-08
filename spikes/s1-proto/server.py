"""Dependency-free loopback HTTP/WS fixture, deliberately not production telephony."""
import base64
import contextvars
import hashlib
import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RelayServer:
    def __init__(self, port=0, turn=None):
        self.port, self.turn = port, turn
        self.http = self.thread = None
        self.clients = set()
        self.lock = threading.Lock()

    def start(self):
        if self.thread is not None:
            return
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def respond(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
            def do_POST(self):
                self.respond(403, {'accepted': False, 'reason': 'offline probe'})
            def read_exact(self, size):
                data = self.rfile.read(size)
                if len(data) != size:
                    raise EOFError()
                return data
            def send_frame(self, payload, opcode=1):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                size = len(data)
                header = bytes([0x80 | opcode, size]) if size < 126 else bytes([0x80 | opcode,126])+struct.pack('!H',size)
                self.wfile.write(header+data)
                self.wfile.flush()
            def do_GET(self):
                if self.path == '/health':
                    return self.respond(200, {'ok':True,'offline':True})
                if self.path != '/relay' or self.headers.get('Upgrade','').lower() != 'websocket':
                    return self.respond(404, {'error':'not found'})
                key = self.headers.get('Sec-WebSocket-Key','')
                if len(key) != 24:
                    return self.respond(400, {'error':'invalid upgrade'})
                accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                self.send_header('Upgrade','websocket')
                self.send_header('Connection','Upgrade')
                self.send_header('Sec-WebSocket-Accept',accept)
                self.end_headers()
                with owner.lock:
                    owner.clients.add(self.connection)
                try:
                    while True:
                        a,b = self.read_exact(2)
                        size = b & 127
                        if size == 126:
                            size = struct.unpack('!H',self.read_exact(2))[0]
                        if not a & 128 or not b & 128 or size > 16384:
                            self.send_frame(struct.pack('!H',1009),8)
                            break
                        mask = self.read_exact(4)
                        raw = self.read_exact(size)
                        raw = bytes(value ^ mask[i%4] for i,value in enumerate(raw))
                        opcode = a & 15
                        if opcode == 8:
                            self.send_frame(raw,8)
                            break
                        if opcode == 9:
                            self.send_frame(raw,10)
                            continue
                        if opcode != 1:
                            break
                        frame = json.loads(raw)
                        if frame.get('type') == 'prompt' and frame.get('last') is True:
                            def emit(text):
                                self.send_frame({'type':'text','token':text,'last':False})
                            if owner.turn:
                                owner.turn(frame.get('voicePrompt',''), emit)
                            else:
                                emit('Offline probe.')
                            self.send_frame({'type':'text','token':'','last':True})
                except (EOFError, OSError, ValueError):
                    pass
                finally:
                    with owner.lock:
                        owner.clients.discard(self.connection)
                    self.close_connection = True
        self.http = ThreadingHTTPServer(('127.0.0.1',self.port),Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_port
        scope = contextvars.copy_context()
        self.thread = threading.Thread(target=lambda: scope.run(self.http.serve_forever, .05), daemon=True,
                                       name='hermes-line-s1')
        self.thread.start()

    def stop(self):
        if self.thread is None or not self.thread.is_alive():
            return
        with self.lock:
            for client in self.clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise TimeoutError('offline listener did not stop')
