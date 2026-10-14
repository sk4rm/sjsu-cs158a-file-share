# File share

## Run locally

- Use Python 3.13 or newer.
- Run commands from the repository root.
- Connect peers to the same local router or hotspot; avoid campus Wi-Fi.
- Allow UDP 54321 and the selected TCP port through the firewall.

```sh
mkdir -p src/fileshare/shared src/fileshare/downloads
uv run fileshare
```

Without uv:

```sh
PYTHONPATH=src python3.13 -c 'import fileshare; fileshare.main()'
```

| Option | Default | Usage |
| --- | --- | --- |
| `--fileserver-port`, `-p` | `54320` | Set the TCP listening port. |
| `--shared`, `-s` | `shared` | Select a folder under `src/fileshare/`. Create it before starting. |
| `--downloads`, `-d` | `downloads` | Select the downloads folder under `src/fileshare/`. |
| `--log`, `-l` | `log.txt` | Set the log path relative to the working directory. Each launch overwrites it. |

```sh
mkdir -p src/fileshare/shared2 src/fileshare/downloads2
uv run fileshare -p 55001 -s shared2 -d downloads2 -l node2-log.txt
```

## Select a file

1. Put offered files in `src/fileshare/shared/`, or the folder selected with `--shared`.
2. Press Enter at the prompt to display local and peer filenames.
3. Type the exact filename, such as `filename.extension`, and press Enter.
4. Check `log.txt` for `File saved: filename.extension` or an error. `Request attempted` alone does not confirm completion.
5. Check both `src/fileshare/downloads/` and `src/fileshare/shared/` for the downloaded file.
6. Press Ctrl-C to exit.

- The first connected peer advertising that filename is selected automatically.
- Files already present in the shared folder are not requested.
- Allow up to 10 seconds for periodic file-list updates.

## Run five nodes with Docker

Run these commands in a POSIX shell from the repository root:

```sh
docker pull python:3.13-slim
docker network create fileshare-demo
for i in 1 2 3 4 5; do
    mkdir -p ".demo/node$i/src/fileshare/shared" ".demo/node$i/src/fileshare/downloads"
    printf 'Shared by node %s\n' "$i" > ".demo/node$i/src/fileshare/shared/node$i.txt"
done
```

Open five terminals in the repository root. Set `NODE` to a different number
from 1 through 5 in each terminal, then run:

```sh
NODE=1
docker run --rm -it --name "fileshare-node$NODE" --network fileshare-demo \
    --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$PWD,dst=/code,readonly" \
    --mount "type=bind,src=$PWD/.demo/node$NODE,dst=/data" \
    -w /data -e PYTHONPATH=/code/src -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.13-slim python -u -c 'import fileshare; fileshare.main()'
```

- Keep all five containers on the same normal bridge network.
- Use UDP 54321 and TCP 54320 in every container; each container has its own IP.
- Leave host ports unpublished; do not add `-p` port mappings or use `--internal`.
- Keep the data mount writable. Find each node's files and `log.txt` under `.demo/nodeN/`.
- For peers on separate physical machines, run the agents natively on the same router or hotspot.

### Check the network

1. Press Enter in node 2; check that the peer list includes `node1.txt`, `node3.txt`, `node4.txt`, and `node5.txt`.
2. Enter `node1.txt` in node 2 and check the saved copies:

   ```sh
   cmp .demo/node1/src/fileshare/shared/node1.txt .demo/node2/src/fileshare/downloads/node1.txt
   cmp .demo/node1/src/fileshare/shared/node1.txt .demo/node2/src/fileshare/shared/node1.txt
   ```

3. Leave `node5.txt` undownloaded. Press Ctrl-C in node 5, then press Enter in node 2; check that `node5.txt` disappears.
4. Restart node 5 with the same command; check that its filename returns.
5. Stop all nodes with Ctrl-C, then remove the network:

   ```sh
   docker network rm fileshare-demo
   ```

## Protocol

Use the [current protocol specification](PROTOCOL.md#current-protocol) for message fields and framing.
Update all group members together; binary TCP headers are incompatible with the earlier hexadecimal text headers.
