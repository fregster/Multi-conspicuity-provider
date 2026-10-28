"""
TCP gate in front of the hub's public ports. It runs on the host network, so
UFW's ordinary INPUT rules govern these ports (Docker-published ports skip
them), and splices each connection through to a loopback-only port that
docker-compose.yml publishes for the real service:

  beast  peer-map's Beast input. readsb's beast_reduce_plus_out opens with
         0x1A 0xE4 + the 36-char receiver UUID (sendUUID in readsb's net_io.c).
  mlat   mlat-server. mlat-client opens with one JSON handshake line that
         carries "uuid" (mlat/client/jsonclient.py).
  open   peer-map's Beast output. Readers send nothing, so there's no UUID to
         check - limit it by source address with UFW instead (hub/ufw.sh).

With HUB_ALLOWED_UUIDS (comma-separated) set, beast/mlat connections whose
UUID isn't listed are dropped before reaching the service; empty = no check.
A UUID is only an identifier sent in clear, not a secret. Stdlib only.
"""

import json
import os
import socket
import threading
import time

ALLOWED = {u.strip().lower() for u in os.environ.get("HUB_ALLOWED_UUIDS", "").split(",") if u.strip()}
HELLO_TIMEOUT_S = 15


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def beast_uuid(head):
    return head[2:38].decode("ascii", "replace").lower() if head[:2] == b"\x1a\xe4" else None


def mlat_uuid(head):
    try:
        return str(json.loads(head.split(b"\n", 1)[0])["uuid"]).lower()
    except (ValueError, KeyError, TypeError):
        return None


# kind: (bytes the hello needs, uuid parser)
CHECKS = {
    "beast": (lambda buf: len(buf) >= 38, beast_uuid),
    "mlat": (lambda buf: b"\n" in buf, mlat_uuid),
}


def read_hello(sock, complete, limit=4096):
    buf = b""
    while not complete(buf) and len(buf) < limit:
        chunk = sock.recv(limit - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def pipe(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


def handle(client, addr, kind, upstream):
    head = b""
    try:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if ALLOWED and kind in CHECKS:
            complete, parse = CHECKS[kind]
            client.settimeout(HELLO_TIMEOUT_S)
            head = read_hello(client, complete)
            uuid = parse(head)
            if uuid not in ALLOWED:
                log(f"{kind}: rejected {addr[0]} uuid={uuid}")
                client.close()
                return
            client.settimeout(None)
            log(f"{kind}: accepted {addr[0]} uuid={uuid}")
        up = socket.create_connection(upstream, timeout=10)
        up.settimeout(None)
        up.sendall(head)
    except OSError as e:
        log(f"{kind}: {addr[0]} dropped: {e}")
        client.close()
        return
    threading.Thread(target=pipe, args=(up, client), daemon=True).start()
    pipe(client, up)


def serve(srv, kind, upstream):
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle, args=(client, addr, kind, upstream), daemon=True).start()


def main():
    # Upstreams are the loopback ports published in docker-compose.yml.
    routes = [
        (int(os.environ.get("HUB_BEAST_IN_PORT", "30004")), "beast", ("127.0.0.1", 39004)),
        (int(os.environ.get("HUB_BEAST_OUT_PORT", "30005")), "open", ("127.0.0.1", 39005)),
        (int(os.environ.get("HUB_MLAT_PORT", "31090")), "mlat", ("127.0.0.1", 39090)),
    ]
    log(f"UUID check {'on: ' + str(len(ALLOWED)) + ' allowed' if ALLOWED else 'off'}")
    threads = []
    for port, kind, upstream in routes:
        srv = socket.create_server(("0.0.0.0", port))
        log(f"{kind}: :{port} -> {upstream[0]}:{upstream[1]}")
        threads.append(threading.Thread(target=serve, args=(srv, kind, upstream), daemon=True))
        threads[-1].start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
