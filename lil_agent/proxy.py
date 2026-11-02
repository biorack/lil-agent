"""A local HTTPS proxy that only lets traffic through to allowed hosts.

lil-agent points the agent harness at it (HTTPS_PROXY), so every host the
harness tries to reach is logged, and anything not on the allowlist is
refused. TLS is not intercepted: the proxy sees host names, not content.
"""

import select
import socket
import socketserver
import threading
import time
from collections import Counter


def _relay(a, b, idle=900):
    a.settimeout(None)
    b.settimeout(None)
    try:
        while True:
            ready, _, _ = select.select([a, b], [], [], idle)
            if not ready:
                return
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass
    finally:
        b.close()


class _Handler(socketserver.StreamRequestHandler):
    rbufsize = 0  # unbuffered, so nothing after the request headers is consumed

    def handle(self):
        request = self.rfile.readline(8192).decode("latin-1").split()
        while self.rfile.readline(8192) not in (b"\r\n", b"\n", b""):
            pass
        if len(request) != 3:
            return
        method, target = request[0], request[1]
        host, _, port = target.rpartition(":")
        if method != "CONNECT" or host not in self.server.allowed:
            self.server.record(target if method == "CONNECT" else f"{method} {target}",
                               "blocked")
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            upstream = socket.create_connection((host, int(port)), timeout=30)
        except OSError as e:
            self.server.record(target, f"failed: {e}")
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        self.server.record(target, "allowed")
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _relay(self.connection, upstream)


class AllowlistProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, allowed, log_path=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.allowed = set(allowed)
        self.log_path = log_path
        self.counts = Counter()
        self._lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def record(self, target, verdict):
        with self._lock:
            self.counts[(verdict, target)] += 1
            if self.log_path:
                with open(self.log_path, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{verdict}\t{target}\n")

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()

    def blocked(self):
        return {t: n for (v, t), n in self.counts.items() if v != "allowed"}

    def summary(self):
        return [f"{verdict:8s} {target}  x{n}"
                for (verdict, target), n in sorted(self.counts.items())]
