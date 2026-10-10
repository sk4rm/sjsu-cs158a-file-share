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

We can detect duplicate ACKs by keeping track of peer IPs.
Ignore duplicate ACKs so we don't recreate new TCP connections.

Note: we could technically remove the need for ACK if Peer B is the one initiating the TCP connection.

### File list

```json
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
