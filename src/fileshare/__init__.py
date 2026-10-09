import argparse
import json
import socket
import threading
import time
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
    # TODO: --log log.txt
    args = parser.parse_args()

    # Signal for shutdown.
    # Threads will check this atomically for graceful shutdown.
    shutdown_event = threading.Event()

    threads = [
        threading.Thread(
            target=discovery_server,
            args=(DISCOVERY_PORT, shutdown_event),
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


def discovery_server(discovery_port: int, shutdown_event: threading.Event):
    # Create logger for discovery server, shorthand "DSCV".
    log = logger("DSCV")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery_server:
        discovery_server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        discovery_server.bind(("", discovery_port))
        discovery_server.settimeout(0.4)

        log(f"Started listening on port {discovery_port}")

        next_broadcast_time = time.monotonic()

        while not shutdown_event.is_set():
            # Periodical UDP broadcast.
            now = time.monotonic()
            if now >= next_broadcast_time:
                discovery_server.sendto(
                    json.dumps({"type": "peer_request"}).encode(),
                    ("255.255.255.255", discovery_port),
                )
                next_broadcast_time = now + 10.0

            # Receive either peer request or ACK.
            try:
                (payload, (host, _port)) = discovery_server.recvfrom(48)
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

                discovery_server.sendto(
                    json.dumps({"type": "peer_ack", "port": 54320}).encode(),
                    (host, discovery_port),
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
