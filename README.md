# OCIS Storage Dump Utility

Extract files from an OCIS instance (personal or project spaces) into a standard POSIX directory structure. The tool walks the OCIS storage, resolves the node/blob relationships, and copies files into a human-readable tree.

Key features:

- Incremental dumps — only new or changed files are copied (based on size/mtime)
- Parallel file copies for faster I/O
- User filtering by display name or username
- Dry-run and list-only modes
- Docker support

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended) or pip

## Installation

```bash
# Create venv and install
uv venv
source .venv/bin/activate
uv pip install .

# With dev dependencies (pytest)
uv pip install ".[dev]"
```

## Usage

```bash
python dump.py [topdir] [outdir] [options]
```

| Flag | Description |
|------|-------------|
| `topdir` | OCIS storage root. Default: `$HOME/.ocis` |
| `outdir` | Output directory. Default: `.` |
| `-l, --list` | List files without copying |
| `-n, --dry-run` | Show what would be copied |
| `-u, --user NAME` | Filter by user's display name |
| `-un, --username USER` | Filter by actual username |
| `-i, --info` | Only show space info, skip file tree |
| `-f, --force` | Force overwrite even if unchanged |
| `-j, --jobs N` | Parallel copy threads (default: 4) |
| `-v, --verbose` | Show all file operations |
| `-q, --quiet` | Only show errors and summary |

### Examples

Dump all files to the current directory:

```bash
python dump.py /srv/ocis ./backup
```

List files for a specific user without copying:

```bash
python dump.py -l -u "John Doe"
```

Dry-run to see what would change:

```bash
python dump.py /srv/ocis ./backup --dry-run
```

Force a full re-dump:

```bash
python dump.py /srv/ocis ./backup --force
```

## Docker

```bash
docker build -t ocis-dump .

docker run --rm \
  -v /path/to/ocis:/data:ro \
  -v /path/to/output:/output \
  ocis-dump /data /output
```

## Other Tools

### mpkview.py

Inspect the decoded contents of `.mpk` (MessagePack) metadata files:

```bash
# View a single file
python mpkview.py path/to/node.mpk

# Search a directory for all mpk files
python mpkview.py /srv/ocis/storage -s

# Write output to file
python mpkview.py node.mpk -o output.txt
```

### symlink_verify.py

Verify (and optionally repair) the internal symlink tree OCIS uses:

```bash
# Check user data symlinks
python symlink_verify.py /srv/ocis --data

# Check and fix metadata symlinks
python symlink_verify.py /srv/ocis --metadata --fix
```

## Development

```bash
# Install with dev deps (editable)
uv pip install -e ".[dev]"

# Run tests
pytest

# Run linting (via pre-commit)
pre-commit run --all-files
```

### Project Structure

```
.
├── src/ocis_dumper/      # Package source
│   ├── common.py         # Shared utilities
│   ├── dump.py           # Main dump tool
│   ├── mpkview.py        # MPK file viewer
│   └── symlink_verify.py # Symlink repair tool
├── tests/                # Unit tests
├── pyproject.toml        # Package config, deps, ruff, pytest
├── Dockerfile
└── README.md
```

## MPK File Structure

Each node in OCIS storage has a `.mpk` (MessagePack) metadata file. A root space node contains:

```python
{
    b"user.ocis.propagation": b"1",
    b"user.ocis.owner.id": b"<uuid>",
    b"user.ocis.space.type": b"personal",          # or "project"
    b"user.ocis.space.name": b"Firstname Lastname",
    b"user.ocis.space.alias": b"personal/username",
    b"user.ocis.treesize": b"11078159600",          # total size in bytes
    b"user.ocis.tmtime": b"2024-05-08T17:35:06.250801079Z",
    b"user.ocis.owner.idp": b"https://owncloud.example.com",
    b"user.ocis.owner.type": b"primary",
    b"user.ocis.tmp.etag": b"",
}
```

Individual file/directory nodes contain:

```python
{
    b"user.ocis.parentid": b"<parent-node-id>",
    b"user.ocis.blobid": b"<blob-id>",   # empty or "N/A" for directories
    b"user.ocis.name": b"document.pdf",
    b"user.ocis.type": b"1",             # 1 = file, 2 = directory
}
```

Use `ocis-mpkview` to inspect these files directly.

## Limitations

- Designed for the default OCIS decomposed filesystem storage backend. Other backends are not supported.
- Only files available as blobs are copied. Partially uploaded or externally stored files are skipped.

## Contributing

Contributions are welcome. Please run `pre-commit run --all-files` and `pytest` before submitting a PR.
