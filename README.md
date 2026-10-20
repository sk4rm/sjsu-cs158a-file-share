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

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path src/fileshare/shared, src/fileshare/downloads | Out-Null
uv run fileshare
```

Without uv:

```sh
PYTHONPATH=src python3.13 -c 'import fileshare; fileshare.main()'
```

PowerShell, with Python 3.13 or newer on `PATH`:

```powershell
$env:PYTHONPATH = 'src'
python -c 'import fileshare; fileshare.main()'
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

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path src/fileshare/shared2, src/fileshare/downloads2 | Out-Null
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

Run the commands for your shell from the repository root. On Windows, use Docker
Desktop with Linux containers.

POSIX:

```sh
docker pull python:3.13-slim
docker network create fileshare-demo
for i in 1 2 3 4 5; do
    mkdir -p ".demo/node$i/src/fileshare/shared" ".demo/node$i/src/fileshare/downloads"
    printf 'Shared by node %s\n' "$i" > ".demo/node$i/src/fileshare/shared/node$i.txt"
done
```

PowerShell:

```powershell
docker pull python:3.13-slim
docker network create fileshare-demo
foreach ($i in 1..5) {
    $shared = ".demo/node$i/src/fileshare/shared"
    $downloads = ".demo/node$i/src/fileshare/downloads"
    New-Item -ItemType Directory -Force -Path $shared, $downloads | Out-Null
    "Shared by node $i" | Set-Content -LiteralPath "$shared/node$i.txt" -Encoding ascii
}
```

Open five terminals in the repository root. Choose a different number from 1
through 5 for `NODE` (POSIX) or `$node` (PowerShell) in each terminal.

POSIX:

```sh
NODE=1
docker run --rm -it --name "fileshare-node$NODE" --network fileshare-demo \
    --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$PWD,dst=/code,readonly" \
    --mount "type=bind,src=$PWD/.demo/node$NODE,dst=/data" \
    -w /data -e PYTHONPATH=/code/src -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.13-slim python -u -c 'import fileshare; fileshare.main()'
```

PowerShell:

```powershell
$node = 1
$repoRoot = (Get-Location).Path
$dataPath = (Resolve-Path -LiteralPath ".demo/node$node").Path
docker run --rm -it --name "fileshare-node$node" --network fileshare-demo `
    --mount "type=bind,src=$repoRoot,dst=/code,readonly" `
    --mount "type=bind,src=$dataPath,dst=/data" `
    -w /data -e PYTHONPATH=/code/src -e PYTHONDONTWRITEBYTECODE=1 `
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

   PowerShell; both comparisons should return `True`:

   ```powershell
   $sourceHash = (Get-FileHash -LiteralPath .demo/node1/src/fileshare/shared/node1.txt -Algorithm SHA256 -ErrorAction Stop).Hash
   (Get-FileHash -LiteralPath .demo/node2/src/fileshare/downloads/node1.txt -Algorithm SHA256 -ErrorAction Stop).Hash -eq $sourceHash
   (Get-FileHash -LiteralPath .demo/node2/src/fileshare/shared/node1.txt -Algorithm SHA256 -ErrorAction Stop).Hash -eq $sourceHash
   ```

3. Leave `node5.txt` undownloaded. Press Ctrl-C in node 5, then press Enter in node 2; check that `node5.txt` disappears.
4. Restart node 5 with the same command; check that its filename returns.
5. Stop all nodes with Ctrl-C, then remove the network:

   ```sh
   docker network rm fileshare-demo
   ```

   PowerShell:

   ```powershell
   docker network rm fileshare-demo
   ```

## Protocol

Use the [current protocol specification](PROTOCOL.md#current-protocol) for message fields and framing.
Update all group members together; binary TCP headers are incompatible with the earlier hexadecimal text headers.

## Demo execution

Node 1 (log and data in `demo/node1`):

```
Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node3.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node3.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node3.txt', 'node4.txt', 'node5.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node4.txt', 'node3.txt', 'node5.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name: node1.txt
Request attempted

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node4.txt', 'node3.txt', 'node5.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt']
Peer files: ['node2.txt', 'node4.txt', 'node3.txt', 'node5.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
```

Node 2 (log and data in `demo/node2`):

```
Press enter to show list of available files, or enter a file name:
My files: ['node2.txt']
Peer files: ['node3.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node2.txt']
Peer files: ['node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node2.txt']
Peer files: ['node5.txt', 'node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name: node1.txt
Request attempted

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt', 'node2.txt']
Peer files: ['node5.txt', 'node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt', 'node2.txt']
Peer files: ['node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node1.txt', 'node2.txt']
Peer files: ['node5.txt', 'node3.txt', 'node4.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
```

Node 3 (log and data in `demo/node3`):

```
Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node5.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name: node5.txt
Request attempted

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt']
Peer files: ['node2.txt', 'node5.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name: node5.txt
Request attempted

Press enter to show list of available files, or enter a file name:
My files: ['node3.txt', 'node5.txt']
Peer files: ['node2.txt', 'node5.txt', 'node1.txt', 'node4.txt']

Press enter to show list of available files, or enter a file name:
```

Node 4 (log and data in `demo/node4`):

```
Press enter to show list of available files, or enter a file name:
My files: ['node4.txt']
Peer files: ['node3.txt', 'node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node4.txt']
Peer files: ['node3.txt', 'node5.txt', 'node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node4.txt']
Peer files: ['node3.txt', 'node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
My files: ['node4.txt']
Peer files: ['node3.txt', 'node5.txt', 'node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name: node5.txt
Request attempted

Press enter to show list of available files, or enter a file name:
My files: ['node4.txt', 'node5.txt']
Peer files: ['node3.txt', 'node5.txt', 'node2.txt', 'node1.txt']

Press enter to show list of available files, or enter a file name:
```

Node 5 (log and data in `demo/node5`):

```
Press enter to show list of available files, or enter a file name:
My files: ['node5.txt']
Peer files: ['node1.txt', 'node2.txt', 'node4.txt', 'node3.txt']

Press enter to show list of available files, or enter a file name: ^C

Press enter to show list of available files, or enter a file name:
My files: ['node5.txt']
Peer files: ['node4.txt', 'node1.txt', 'node2.txt', 'node3.txt']

Press enter to show list of available files, or enter a file name:
```

### Demo explanation

Nodes 1, 2, and 3 are all run. All of them can see each other's files, indicating successful discovery and file list propagation.

Node 4 is then run. Nodes 1, 2, 3, and 4 can all see each other's files.

Node 5 is then run. Every node can now see every other node's files.

Node 2 requests `node1.txt`. Node 1 has this file and serves it to node 2. Node 2's file list now includes `node1.txt`. Node 1's peer file list now includes `node1.txt`; nothing happens when it tries to request this file, since it already has a file of the same name.

Node 5 is terminated. Since node 5 left, nodes 1, 2, 3, and 4 can no longer see node 5's file list. Node 3 attempts to request `node5.txt`, but since node 5 is no longer connected, it is unable to obtain the file.

Node 5 is run again. Every node can now see every other node's files. Nodes 3 and 4 are now able to successfully obtain `node5.txt` from node 5.

## Division of work

Zi Hong (`sk4rm`): peer request, peer ACK, documentation

Ellen (`ellm135`): TCP connection, file list exchange, logging

Paul (`marcospaull`): data request, file transfer, file missing message
