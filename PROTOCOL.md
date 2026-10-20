# File share

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
