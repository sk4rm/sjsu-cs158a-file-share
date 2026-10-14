> Original README preserved below. Apply [Current protocol](#current-protocol) for the updated wire format.

# File share

## Message format

Everything below assumes that all peers have already started their TCP file servers (any port) and UDP discovery server (must listen at `54321`)

### Peer request

The broadcaster communicates two pieces of information: its unique client ID, generated at launch, and the port number it should receive ACKs at. The client ID allows the broadcaster to identify its own peer requests and ignore them. The port number to receive ACKs at allows several processes on the same machine to communicate with each other: if all processes try to share the same port for receiving ACKs, then only one process will receive all ACKs, preventing the intended recipient from ever receiving the ACK. If all processes are being executed on several machines, the ACK port can be left at `54321`.

Peer A broadcasts to port `54321` via UDP:

```json
{
  "type": "peer_request",
  "id": "2eededf8-01ea-4871-91eb-795dcbba37bc",
  "port": 54321
}
```

### Peer ACK

A peer should ignore its own broadcast.

Peer B receives the broadcast and sends an ACK back to Peer A:

```json
{
  "type": "peer_ack",
  "id": "2eededf8-01ea-4871-91eb-795dcbba37bc",
  "port": 43210 // Example Peer B's file server port.
}
```

Peer B gets Peer A's IP address for free in Python:

```py
data, addr = s.recvfrom(1024)
#     ^^^^ Peer A's IP address.
```

So, Peer B knows enough to send a UDP ACK to `<Peer A's IP address>:54321`.

Peer A receives the ACK and can now initiate a TCP connection to Peer B's file server.

We can detect duplicate ACKs by keeping track of peer IDs.
Ignore duplicate ACKs so we don't recreate new TCP connections.

Note: we could technically remove the need for ACK if Peer B is the one initiating the TCP connection.

### Peer ID

After Peer A receives the ACK, it sends its ID to Peer B.

```json
{
  "type": "peer_id",
  "id": "2eededf8-01ea-4871-91eb-795dcbba37bc"
}
```

This allows Peer B to add Peer A's ID to its file table, guaranteeing that Peer B will not accept further requests for connection from Peer A while they are already connected.

### File list

Peer A and Peer B both send each other their file lists periodically. The payload is prefixed with its length, so each peer knows how much they need to read.

```json
0ABC
{
  "type": "file_list",
  "files": ["foo.txt", "bar.jpg", "baz.md", "buzz.exe"]
}
```

### Data request

```json
{
  "type": "data_request",
  "filename": "foo.txt"
}
```

### File data

```json
{
  "type": "file_data",
  "filename": "foo.txt",
  "data": "/9j/4AAQSkZJRg..." // Base-64 encoded data.
}
```


## Current protocol

All messages are UTF-8 JSON objects with a string `type`. UUIDs are strings
generated at startup; ports are JSON integers from 1 through 65535.

Use one UDP socket on **54321** for discovery and ACKs; `--ack-port` is removed.
One datagram contains one JSON object, no prefix, at most 128 bytes. Broadcast to
`255.255.255.255:54321` at startup and every 10 seconds. Keep listening, ignore
self requests, and unicast ACKs to the request's source IP and advertised UDP port.

Every TCP message has a **4-byte unsigned 32-bit big-endian integer** header followed
by exactly that many UTF-8 JSON bytes. Count bytes, not characters. Length must
be positive. The hexadecimal text header above is obsolete. A 38-byte payload
has header bytes `00 00 00 26`; no newline or separator follows the payload.
Loop over short reads. EOF inside a frame or a reset means disconnection.

| Transport | `type` | Required fields besides `type` |
| --- | --- | --- |
| UDP | `peer_request` | `id`: sender UUID; `port`: UDP port, 54321. |
| UDP | `peer_ack` | `id`: responder UUID; `port`: responder's TCP port. |
| TCP | `peer_id` | `id`: client's UUID; its first TCP message, sent once. |
| TCP | `file_list` | `files`: array of filename strings; replaces the old list; may be empty. |
| TCP | `data_request` | `filename`: advertised filename string. |
| TCP | `file_data` | `filename`: string; `data`: padded standard base64 string, or `""` for an empty file. |
| TCP | `error` | `message`: string, currently `"file not found"`. |

In the ACK example above, B must use its own UUID instead of A's. The requester
connects to the ACK's source IP and TCP port. Deduplicate by UUID, not IP. If
startup creates crossed connections, retain the one initiated by the smaller UUID.

Base64 keeps binary content inside JSON with roughly 33% encoding overhead;
it is simpler than parsing a separate raw stream. Files are buffered whole.
A missing file returns `{"type":"error","message":"file not found"}` without
closing TCP. Other caught parsing/socket failures close and clean up the peer;
malformed JSON in a complete frame is logged and the next frame may be read.

Send file lists immediately and every 10 seconds. Request from the first matching
connected peer; try the next if its socket closed. Skip names already shared.
Write downloads before copying to shared. On EOF/reset, remove that socket's
peer entry and files. There is no goodbye; discover restarted nodes normally.
