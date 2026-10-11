import argparse
import base64
import json
import socket
import threading
import time
import uuid
import selectors
import logging
from datetime import datetime
from sys import stdout
from pathlib import Path
from enum import StrEnum


DISCOVERY_PORT = 54321
CLIENT_ID = uuid.uuid4()
LENGTH_PREFIX_SIZE = 4
BROADCAST_INTERVAL = 10.0

peer_table = {}

class ExtraFilter(logging.Filter):
    def filter(self, item):
        if hasattr(item, "ip") and hasattr(item, "port"):
            item.ip = f"({item.ip}"
            item.port = f":{item.port}) "
        elif hasattr(item, "ip"):
            item.ip = f"({item.ip}) "
        elif hasattr(item, "port"):
            item.port = f"({item.port}) "

        if not hasattr(item, "ip"):
            item.ip = ""

        if not hasattr(item, "port"):
            item.port = ""

        if not hasattr(item, "type"):
            item.type = ""
        else:
            item.type = f" (type: {item.type})"

        return True

log_format = "{%(levelname)s} [%(asctime)s] <%(who)s> %(ip)s%(port)s%(direction)s: %(message)s%(type)s"
log_date_format = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=log_date_format,
    handlers = [
        logging.StreamHandler(stdout)
    ]
)

root_logger = logging.getLogger()
root_logger.addFilter(ExtraFilter())


class MessageType(StrEnum):
    PEER_REQUEST = "peer_request"
    ACK = "peer_ack"
    ID = "peer_id"
    FILE_LIST = "file_list"
    DATA_REQUEST = "data_request"
    DATA = "file_data"
    ERROR = "error"

class Direction(StrEnum):
    SELF = "SELF"
    SEND = "SEND"
    RECEIVE = "RECV"


def main() -> None:
    # Create main thread logger.
    who = "MAIN"

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
    parser.add_argument(
        "--log",
        "-l",
        type=str,
        default="log.txt",
        help="Name of log file to write to (default: log.txt)"
    )
    args = parser.parse_args()

    file_handler = logging.FileHandler(args.log, mode="w")
    file_handler.setFormatter(logging.Formatter(fmt=log_format, datefmt=log_date_format))
    root_logger.addHandler(file_handler)

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
            logging.info("Shutting down servers due to keyboard interrupt", extra={"who": who, "direction": Direction.SELF})

            shutdown_event.set()

            for thread in threads:
                thread.join()

sel = selectors.DefaultSelector()

def discovery_server(discovery_port: int, server_port: int, ack_port: int, shutdown_event: threading.Event):
    # Create logger for discovery server, shorthand "DSCV".
    who = "DSCV"

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
        # recipient to get it.
        ack_server.bind(("", ack_port))
        ack_server.settimeout(0.4)
        ack_server.setblocking(False)

        sel.register(discovery_server, selectors.EVENT_READ, data=discovery_port)
        sel.register(ack_server, selectors.EVENT_READ, data=ack_port)

        logging.info(f"Started listening on port {discovery_port}", extra={"who": who, "direction": "SELF"})

        next_broadcast_time = time.monotonic()

        while not shutdown_event.is_set():
            # Periodical UDP broadcast.
            now = time.monotonic()
            if now >= next_broadcast_time:
                discovery_server.sendto(
                    json.dumps({"type": MessageType.PEER_REQUEST, "port": ack_port, "id": str(CLIENT_ID)}).encode(),
                    ("255.255.255.255", discovery_port),
                )
                logging.info("Broadcast message", extra={"who": who, "direction": Direction.SEND, "type": MessageType.PEER_REQUEST})
                next_broadcast_time = now + BROADCAST_INTERVAL

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
                logging.error(error, extra={"who": who, "direction": "SELF"})
                continue

            # Unmarshall payload.
            try:
                message = json.loads(payload)

            except json.JSONDecodeError as error:
                logging.error(f"Payload is invalid JSON: {error}; {payload!r}", 
                              extra={"who": who, "direction": Direction.RECEIVE, "ip": host})
                continue

            # Switch on message type.
            if not "type" in message:
                logging.error(f"Message has no type", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})
                continue

            logging.info("Got message", extra={"who": who, "direction": Direction.RECEIVE, "type": message["type"], "ip": host})

            if message["type"] == MessageType.PEER_REQUEST:
                if not "port" in message:
                    logging.error("Request is missing port", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})
                    continue

                logging.info("Peer request", extra={"who": who, "direction": Direction.RECEIVE, "ip": host, "port": message["port"]})

                if not "id" in message:
                    logging.error("Request is missing ID", 
                                  extra={"who": who, "direction": Direction.RECEIVE, "ip": host, "port": message["port"]})
                    continue

                if message["id"] == str(CLIENT_ID):
                    logging.info("Request ignored (from self)", 
                                 extra={"who": who, "direction": Direction.RECEIVE, "ip": host, "port": message["port"]})
                    continue

                if message["id"] in peer_table:
                    logging.info("Request ignored (from connected peer)", 
                                 extra={"who": who, "direction": Direction.RECEIVE, "ip": host, "port": message["port"]})
                    continue

                discovery_server.sendto(
                    json.dumps({"type": MessageType.ACK, "id": str(CLIENT_ID), "port": server_port}).encode(),
                    (host, message["port"]),
                )
                logging.info("ACK sent", extra={"who": who, "direction": Direction.SEND, "ip": host, "port": message["port"], "type": MessageType.ACK})

            elif message["type"] == MessageType.ACK:
                if not "port" in message:
                    logging.error("Request is missing port", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})
                    continue

                logging.info(f"ACK; file server at {message['port']}", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})

                thread = threading.Thread(
                    target=connect_to_peer,
                    args=(host, message["port"], message["id"], shutdown_event),
                    daemon=True
                )
                thread.start()

            else:
                logging.error(f"Type unknown: {message['type']!r}", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})


def package_bytes(payload: str):
    length = f"{len(payload):0{LENGTH_PREFIX_SIZE}x}"
    return (length + payload).encode()


def connect_to_peer(server_address: int, server_port: int, server_id: int, shutdown_event: threading.Event):
    # Create logger for send loop, shorthand "SEND".
    who = "CNCT"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        client.connect((server_address, server_port))

        peer_table[server_id] = []

        logging.info("Connected to peer", extra={"who": who, "direction": Direction.SELF, "ip": server_address, "port": server_port})

        # Send our ID to the server, so they can add us to their peer table.
        payload = json.dumps({"type": MessageType.ID, "id": str(CLIENT_ID)})
        client.sendall(package_bytes(payload))

        logging.info("ID sent", extra={"who": who, "direction": Direction.SEND, "ip": server_address, "port": server_port, "type": MessageType.ID})

        # Start the receive loop.
        receive_from_peer(client, server_address, server_port, server_id, shutdown_event)

def get_file_list():
    shared_path = Path("src/fileshare/shared")
    return [file.name for file in shared_path.iterdir() if file.is_file()]

def file_server_send(peer: socket.socket, peer_host: str, peer_port: int, filename: str):
    # Create logger for serving files, shorthand "SERV".
    who = "SERV"

    # Only files in the shared folder can be requested.
    if filename not in get_file_list():
        payload = json.dumps({"type": MessageType.ERROR, "message": "file not found"})
        peer.sendall(package_bytes(payload))
        logging.info(f"File not found: {filename}",
                     extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.ERROR})
        return

    # Encode the file as base-64 so it can go inside the JSON message.
    file_bytes = Path("src/fileshare/shared", filename).read_bytes()
    data = base64.b64encode(file_bytes).decode()
    payload = json.dumps({"type": MessageType.DATA, "filename": filename, "data": data})
    peer.sendall(package_bytes(payload))
    logging.info(f"File sent: {filename}",
                 extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.DATA})

def broadcast_file_list(sock: socket.socket, peer_host: str, peer_port: int, shutdown_event: threading.Event, stop_event: threading.Event):
    # Use shorthand "BRDC" for file broadcast loop.
    who = "BRDC"
    next_broadcast_time = time.monotonic()

    while not shutdown_event.is_set() and not stop_event.is_set():
        now = time.monotonic()
        if now >= next_broadcast_time:
            file_list = get_file_list()
            file_payload = json.dumps({"type": MessageType.FILE_LIST, "files": file_list})
            sock.sendall(package_bytes(file_payload))
            logging.info(f"File list sent: {file_list}", 
                         extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.FILE_LIST})

            next_broadcast_time = now + BROADCAST_INTERVAL


def receive(peer: socket.socket, requested_bytes: int, shutdown_event: threading.Event):
    result = b''
    while len(result) < requested_bytes and not shutdown_event.is_set():
        try:
            chunk = peer.recv(requested_bytes - len(result))

            if not chunk:
                return ""

            result += chunk
        except ConnectionResetError:
            return ""

    return result.decode()

def get_all_peer_files():
    result = []

    for peer in peer_table:
        result += peer_table[peer]

    # Convert to a set first to remove duplicates.
    return list(set(result))

def receive_from_peer(peer: socket.socket, peer_host: str, peer_port: int, peer_id: str, shutdown_event: threading.Event):
    # Create logger for receive loop, shorthand "RECV".
    who = "RECV"
    id = peer_id

    stop_broadcast = threading.Event()

    # Spawn file broadcast thread.
    broadcast_thread = threading.Thread(
        target=broadcast_file_list,
        args=(peer, peer_host, peer_port, shutdown_event, stop_broadcast),
        daemon=True
    )
    broadcast_thread.start()

    while not shutdown_event.is_set():
        length_bytes = receive(peer, LENGTH_PREFIX_SIZE, shutdown_event)

        if not length_bytes:
            logging.info(f"Peer left, removing their files: {peer_table[id]}", extra={"who": who, "direction": "SELF", "ip": peer_host, "port": peer_port})
            peer_table.pop(id, None)
            logging.info(f"Current files known: {get_all_peer_files()}", extra={"who": who, "direction": "SELF"})
            stop_broadcast.set()
            peer.close()
            return

        length = int(length_bytes, 16)

        payload = receive(peer, length, shutdown_event)

        if not payload:
            logging.info(f"Peer left, removing their files: {peer_table[id]}", extra={"who": who, "direction": "SELF", "ip": peer_host, "port": peer_port})
            peer_table.pop(id, None)
            logging.info(f"Current files known: {get_all_peer_files()}", extra={"who": who, "direction": "SELF"})
            stop_broadcast.set()
            peer.close()
            return
        
        try:
            message = json.loads(payload)

        except json.JSONDecodeError as error:
            logging.error(f"Payload is invalid JSON: {error}; {payload!r}", 
                          extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
            continue

        if not "type" in message:
            logging.error(f"Message has no type", extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
            continue

        logging.info(f"Got message", 
                     extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port, "type": message["type"]})

        if message["type"] == MessageType.ID:
            logging.info(f"Registered ID {message['id']}", 
                         extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
            peer_table[message["id"]] = []
            id = message["id"]
        elif not id:
            logging.warning(f"Cannot accept message; ID is not registered", 
                            extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
            continue
        else:
            if message["type"] == MessageType.FILE_LIST:
                if not "files" in message:
                    logging.error(f"Message has no file list", 
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                    continue

                peer_table[id] = message["files"]
                logging.info(f"File list: {peer_table[id]}", 
                            extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                logging.info(f"Current files known: {get_all_peer_files()}", extra={"who": who, "direction": "SELF"})
            elif message["type"] == MessageType.DATA_REQUEST:
                if not "filename" in message:
                    logging.error(f"Message has no filename",
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                    continue

                file_server_send(peer, peer_host, peer_port, message["filename"])
            else:
                logging.error(f"Type unknown: {message['type']!r}",
                            extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})



def file_server(server_port: int, shutdown_event: threading.Event):
    # Create logger for file server, shorthand "FSRV".
    who = "FSRV"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("", server_port))
        server.listen()
        server.settimeout(0.4)

        logging.info(f"Started listening on port {server_port}", extra={"who": who, "direction": "SELF"})

        while not shutdown_event.is_set():
            try:
                (peer, (peer_host, peer_port)) = server.accept()
            except TimeoutError:
                continue

            logging.info(f"Peer connected", extra={"who": who, "direction": "SELF", "ip": peer_host, "port": peer_port})

            # Spawn a recv-loop thread.
            thread = threading.Thread(
                target=receive_from_peer,
                args=(peer, peer_host, peer_port, "", shutdown_event),
                daemon=True
            )
            thread.start()
