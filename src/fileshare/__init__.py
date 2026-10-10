import argparse
import json
import socket
import threading
import time
import uuid
import selectors
from datetime import datetime
from sys import stderr


def logger(who: str):
    """
    Curried function for logging that prints date and stuff.
    """

    def log(*value: object):
        print(
            f"[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S}] <{who}>",
            *value,
            file=stderr,
            flush=True,
        )

    return log


DISCOVERY_PORT = 54321
CLIENT_ID = uuid.uuid4()


def main() -> None:
    # Create main thread logger.
    log = logger("MAIN")

    # Command line arguments.
    parser = argparse.ArgumentParser(description="P2P File-sharing")
    parser.add_argument(
        "--fileserver-port",
        "-p",
        type=int,
        default=54320,
        help="TCP port number for the file server (default: 54320)",
    )
    parser.add_argument(
        "--ack-port",
        "-a",
        type=int,
        default=54321,
        help="UDP port number for listening (default: 54321); " \
             "use to prevent clashing ports for processes on same machine",
    )
    # TODO: --log log.txt
    args = parser.parse_args()

    # Signal for shutdown.
    # Threads will check this atomically for graceful shutdown.
    shutdown_event = threading.Event()

    threads = [
        threading.Thread(
            target=discovery_server,
            args=(DISCOVERY_PORT, args.fileserver_port, args.ack_port, shutdown_event),
        ),
        threading.Thread(
            target=file_server,
            args=(args.fileserver_port, shutdown_event),
        ),
    ]

    for thread in threads:
        thread.start()

    # While-try for graceful keyboard interrupt handling.
    while not shutdown_event.is_set():
        try:
            time.sleep(0.4)

        except KeyboardInterrupt:
            log("MAIN", "Shutting down servers due to keyboard interrupt")

            shutdown_event.set()

            for thread in threads:
                thread.join()

sel = selectors.DefaultSelector()

def discovery_server(discovery_port: int, server_port: int, ack_port: int, shutdown_event: threading.Event):
    # Create logger for discovery server, shorthand "DSCV".
    log = logger("DSCV")

    with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery_server,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ack_server
        ):
        discovery_server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        discovery_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        discovery_server.bind(("", discovery_port))
        discovery_server.settimeout(0.4)
        discovery_server.setblocking(False)

        # A separate ACK socket is needed when running several processes on 
        # one machine: when the ACK is sent to the host, it is always received 
        # by only one process, preventing other processes from being ACKed.
        # Using a separate socket to listen for the ACK allows the intended
        # recipieint to get it.
        ack_server.bind(("", ack_port))
        ack_server.settimeout(0.4)
        ack_server.setblocking(False)

        sel.register(discovery_server, selectors.EVENT_READ, data=discovery_port)
        sel.register(ack_server, selectors.EVENT_READ, data=ack_port)

        log(f"Started listening on port {discovery_port}")

        next_broadcast_time = time.monotonic()

        while not shutdown_event.is_set():
            # Periodical UDP broadcast.
            now = time.monotonic()
            if now >= next_broadcast_time:
                discovery_server.sendto(
                    json.dumps({"type": "peer_request", "port": ack_port, "id": str(CLIENT_ID)}).encode(),
                    ("255.255.255.255", discovery_port),
                )
                next_broadcast_time = now + 10.0

            # Receive either peer request or ACK.
            try:
                events = sel.select(timeout=0.4)

                if not events:
                    continue

                for key, _ in events:
                    sock = key.fileobj
                    (payload, (host, _port)) = sock.recvfrom(128)
            except TimeoutError:
                continue
            except OSError as error:
                log(error)
                continue

            # Unmarshall payload.
            try:
                message = json.loads(payload)

            except json.JSONDecodeError as error:
                log(f"Payload is invalid JSON: {error}; {payload!r}")
                continue

            # Switch on message type.
            if not "type" in message:
                log('Received message is missing the "type" field')
                continue

            if message["type"] == "peer_request":
                log(f"Received request from peer {host}")

                if message["id"] == str(CLIENT_ID):
                    log("Ignored request from self")
                    continue

                discovery_server.sendto(
                    json.dumps({"type": "peer_ack", "port": server_port}).encode(),
                    (host, message["port"]),
                )

            elif message["type"] == "peer_ack":
                if not "port" in message:
                    log(
                        f"Received ACK from peer {host} but is missing file server port",
                    )
                    continue

                log(
                    f"Received ACK from peer {host}; their file server is listening on {message['port']}",
                )

                # TODO: threading.Thread(target=connect_to_peer).run() and request files.

            else:
                log(
                    "DSCV",
                    f"Discarding message from peer {host} with unknown type: {message['type']!r}",
                )


def file_server(server_port: int, shutdown_event: threading.Event):
    # Create logger for file server, shorthand "FSRV".
    log = logger("FSRV")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("", server_port))
        server.listen()
        server.settimeout(0.4)

        log(f"Started listening on port {server_port}")

        while not shutdown_event.is_set():
            try:
                (_peer, (peer_host, peer_port)) = server.accept()
            except TimeoutError:
                continue

            log(f"Peer {peer_host}:{peer_port} connected")

            # TODO: Spawn a recv-loop thread for the peer and serve them.
