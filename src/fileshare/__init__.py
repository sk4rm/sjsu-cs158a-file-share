import argparse
import base64
import json
import socket
import threading
import time
import uuid
import weakref
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

SHARED_FOLDER_NAME = "shared"
DOWNLOADS_FOLDER_NAME = "downloads"

peer_table = {}
peer_sockets = {}
connection_lock = threading.RLock()
connecting_peers = set()
peer_initiators = {}
send_locks = weakref.WeakKeyDictionary()

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
    datefmt=log_date_format
)

root_logger = logging.getLogger()
root_logger.addFilter(ExtraFilter())
root_logger.handlers = []


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
    parser.add_argument(
        "--shared",
        "-s",
        type=str,
        default="shared",
        help="Name of shared folder (default: shared)"
    )
    parser.add_argument(
        "--downloads",
        "-d",
        type=str,
        default="downloads",
        help="Name of downloads folder (default: downloads)"
    )
    args = parser.parse_args()

    global SHARED_FOLDER_NAME
    SHARED_FOLDER_NAME = args.shared
    global DOWNLOADS_FOLDER_NAME
    DOWNLOADS_FOLDER_NAME = args.downloads

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
        threading.Thread(
            target=input_handler,
            args=(shutdown_event,),
            daemon=True,  # A blocked input() must not prevent shutdown.
        )
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
                if not thread.daemon:
                    thread.join()


def input_handler(shutdown_event: threading.Event):
    while not shutdown_event.is_set():
        request = input("Press enter to show list of available files, or enter a file name: ")

        if request == "":
            print(f"My files: {get_file_list()}")
            print(f"Peer files: {get_all_peer_files()}\n")
        else:
            request_file(request)
            print("Request attempted\n")


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
        ack_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

                with connection_lock:
                    connected = message["id"] in peer_table
                if connected:
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

                if not isinstance(message.get("id"), str) or not message["id"]:
                    logging.error("ACK is missing a valid ID", extra={"who": who, "direction": Direction.RECEIVE, "ip": host})
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
    payload = payload.encode("utf-8")
    # Four-byte unsigned length in network byte order; count bytes, not characters.
    length = len(payload).to_bytes(LENGTH_PREFIX_SIZE, byteorder="big")
    return length + payload


def send_payload(peer: socket.socket, payload: str):
    frame = package_bytes(payload)
    with connection_lock:
        # Weak keys release the lock entry when its socket is no longer used.
        send_lock = send_locks.setdefault(peer, threading.Lock())
    # Serialize the entire frame without holding the peer-table lock during I/O.
    with send_lock:
        peer.sendall(frame)


def register_peer(peer: socket.socket, peer_id: str, initiator_id: str):
    with connection_lock:
        if peer_id == str(CLIENT_ID):
            return False
        previous = peer_sockets.get(peer_id)
        # Crossed startup connections must choose the same socket at both ends.
        # Keep the connection initiated by the smaller UUID; reject repeat dials.
        if previous is not None and peer_initiators.get(peer_id, "") <= initiator_id:
            return False
        peer_table[peer_id] = []
        peer_sockets[peer_id] = peer
        peer_initiators[peer_id] = initiator_id

    if previous is not None:
        try:
            previous.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        previous.close()
    return True


def connect_to_peer(server_address: int, server_port: int, server_id: int, shutdown_event: threading.Event):
    # Create logger for send loop, shorthand "SEND".
    who = "CNCT"

    with connection_lock:
        if server_id == str(CLIENT_ID) or server_id in peer_sockets or server_id in connecting_peers:
            return
        connecting_peers.add(server_id)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            client.connect((server_address, server_port))

            # Send our ID to the server, so they can add us to their peer table.
            payload = json.dumps({"type": MessageType.ID, "id": str(CLIENT_ID)})
            send_payload(client, payload)

            logging.info("ID sent", extra={"who": who, "direction": Direction.SEND, "ip": server_address, "port": server_port, "type": MessageType.ID})

            if not register_peer(client, server_id, str(CLIENT_ID)):
                return
            logging.info("Connected to peer", extra={"who": who, "direction": Direction.SELF, "ip": server_address, "port": server_port})

            # Start the receive loop.
            receive_from_peer(client, server_address, server_port, server_id, shutdown_event)
    except OSError as error:
        logging.error(f"Connection failed: {error}", extra={"who": who, "direction": Direction.SELF, "ip": server_address, "port": server_port})
    finally:
        with connection_lock:
            connecting_peers.discard(server_id)

def get_file_list():
    shared_path = Path(f"src/fileshare/{SHARED_FOLDER_NAME}")
    return [file.name for file in shared_path.iterdir() if file.is_file()]

def file_server_send(peer: socket.socket, peer_host: str, peer_port: int, filename: str):
    # Create logger for serving files, shorthand "SERV".
    who = "SERV"

    # Only files in the shared folder can be requested.
    if filename not in get_file_list():
        payload = json.dumps({"type": MessageType.ERROR, "message": "file not found"})
        send_payload(peer, payload)
        logging.info(f"File not found: {filename}",
                     extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.ERROR})
        return

    # Encode the file as base-64 so it can go inside the JSON message.
    file_bytes = Path(f"src/fileshare/{SHARED_FOLDER_NAME}", filename).read_bytes()
    data = base64.b64encode(file_bytes).decode()
    payload = json.dumps({"type": MessageType.DATA, "filename": filename, "data": data})
    send_payload(peer, payload)
    logging.info(f"File sent: {filename}",
                 extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.DATA})

def request_file(filename: str):
    # Create logger for requesting files, shorthand "REQ".
    who = "REQ"

    # Files are identified by name only, so keep the copy we already have.
    if filename in get_file_list():
        logging.info(f"Already have {filename}", extra={"who": who, "direction": Direction.SELF})
        return

    # Ask the first peer that advertises the file.
    with connection_lock:
        peers = [peer_sockets[id] for id, files in peer_table.items()
                 if filename in files and id in peer_sockets]
    for peer in peers:
        try:
            (peer_host, peer_port) = peer.getpeername()
            payload = json.dumps({"type": MessageType.DATA_REQUEST, "filename": filename})
            send_payload(peer, payload)
            logging.info(f"File requested: {filename}",
                         extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.DATA_REQUEST})
            return
        except OSError as error:
            logging.error(f"Request failed: {error}", extra={"who": who, "direction": Direction.SELF})

    logging.info(f"No peer has {filename}", extra={"who": who, "direction": Direction.SELF})

def broadcast_file_list(sock: socket.socket, peer_host: str, peer_port: int, shutdown_event: threading.Event, stop_event: threading.Event):
    # Use shorthand "BRDC" for file broadcast loop.
    who = "BRDC"
    next_broadcast_time = time.monotonic()

    while not shutdown_event.is_set() and not stop_event.is_set():
        now = time.monotonic()
        if now >= next_broadcast_time:
            file_list = get_file_list()
            file_payload = json.dumps({"type": MessageType.FILE_LIST, "files": file_list})
            try:
                send_payload(sock, file_payload)
            except OSError as error:
                if not stop_event.is_set():
                    logging.error(f"File list send failed: {error}",
                                  extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port})
                return
            logging.info(f"File list sent: {file_list}", 
                         extra={"who": who, "direction": Direction.SEND, "ip": peer_host, "port": peer_port, "type": MessageType.FILE_LIST})

            next_broadcast_time = now + BROADCAST_INTERVAL


def receive(peer: socket.socket, requested_bytes: int, shutdown_event: threading.Event):
    result = b''
    while len(result) < requested_bytes and not shutdown_event.is_set():
        try:
            chunk = peer.recv(requested_bytes - len(result))

            if not chunk:
                return b""

            result += chunk
        except ConnectionResetError:
            return b""

    return result if len(result) == requested_bytes else b""

def get_all_peer_files():
    result = []

    with connection_lock:
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

    try:
        while not shutdown_event.is_set():
            length_bytes = receive(peer, LENGTH_PREFIX_SIZE, shutdown_event)

            if not length_bytes:
                break

            length = int.from_bytes(length_bytes, byteorder="big")
            if length == 0:
                raise ValueError("Message length must be positive")

            payload = receive(peer, length, shutdown_event)

            if not payload:
                break

            try:
                message = json.loads(payload)

            except json.JSONDecodeError as error:
                logging.error(f"Payload is invalid JSON: {error}; {payload!r}",
                              extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                continue

            if not isinstance(message, dict):
                raise ValueError("Message must be a JSON object")

            if not "type" in message:
                logging.error(f"Message has no type", extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                continue

            logging.info(f"Got message",
                         extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port, "type": message["type"]})

            if message["type"] == MessageType.ID:
                if id or not isinstance(message.get("id"), str) or not message["id"]:
                    raise ValueError("Missing, invalid, or repeated peer ID")
                id = message["id"]
                if not register_peer(peer, id, id):
                    logging.info("Duplicate connection ignored", extra={"who": who, "direction": Direction.SELF, "ip": peer_host, "port": peer_port})
                    break
                logging.info(f"Registered ID {message['id']}",
                             extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
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

                    if not isinstance(message["files"], list) or not all(isinstance(name, str) for name in message["files"]):
                        raise ValueError("File list must contain filenames")
                    with connection_lock:
                        if peer_sockets.get(id) is not peer:
                            break
                        peer_table[id] = message["files"]
                    logging.info(f"File list: {message['files']}",
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                    logging.info(f"Current files known: {get_all_peer_files()}", extra={"who": who, "direction": "SELF"})
                elif message["type"] == MessageType.DATA_REQUEST:
                    if not "filename" in message:
                        logging.error(f"Message has no filename",
                                    extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                        continue

                    file_server_send(peer, peer_host, peer_port, message["filename"])
                elif message["type"] == MessageType.DATA:
                    if not "filename" in message or not "data" in message:
                        logging.error(f"Message has no filename or data",
                                    extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                        continue

                    # Keep only the file name, so a peer cannot write outside the folders.
                    filename = Path(message["filename"]).name
                    file_bytes = base64.b64decode(message["data"], validate=True)

                    # Write to downloads first, and copy to shared only once the file is complete.
                    downloads_file = Path(f"src/fileshare/{DOWNLOADS_FOLDER_NAME}", filename)
                    downloads_file.parent.mkdir(exist_ok=True)
                    downloads_file.write_bytes(file_bytes)
                    Path(f"src/fileshare/{SHARED_FOLDER_NAME}", filename).write_bytes(downloads_file.read_bytes())

                    logging.info(f"File saved: {filename}",
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port, "type": MessageType.DATA})
                elif message["type"] == MessageType.ERROR:
                    logging.error(f"Peer error: {message.get('message')}",
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
                else:
                    logging.error(f"Type unknown: {message['type']!r}",
                                extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
    except (OSError, ValueError, TypeError, KeyError) as error:
        logging.error(f"Peer receive failed: {error}",
                      extra={"who": who, "direction": Direction.RECEIVE, "ip": peer_host, "port": peer_port})
    finally:
        stop_broadcast.set()
        try:
            # Only remove the entry if this socket still owns it.
            with connection_lock:
                if peer_sockets.get(id) is peer:
                    logging.info(f"Peer left, removing their files: {peer_table.get(id, [])}",
                                 extra={"who": who, "direction": Direction.SELF, "ip": peer_host, "port": peer_port})
                    peer_table.pop(id, None)
                    peer_sockets.pop(id, None)
                    peer_initiators.pop(id, None)
                    logging.info(f"Current files known: {get_all_peer_files()}", extra={"who": who, "direction": Direction.SELF})
        finally:
            peer.close()



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
