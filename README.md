# OCIS Storage Dump Utility

[![CI](https://github.com/frostyslav/ocis-storage-dumper/actions/workflows/ci.yml/badge.svg)](https://github.com/frostyslav/ocis-storage-dumper/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-96%25-brightgreen)](https://github.com/frostyslav/ocis-storage-dumper/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue)](https://mypy-lang.org/)
[![License](https://img.shields.io/github/license/frostyslav/ocis-storage-dumper)](https://github.com/frostyslav/ocis-storage-dumper/blob/main/LICENSE)

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
uv sync

# With dev dependencies (pytest, mypy, coverage)
uv sync --extra dev
```

## Usage

```bash
ocis-dump [topdir] [outdir] [options]
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
| `--storage-prefix PATH` | Custom storage path (default: `storage/users/spaces`) |

### Examples

Dump all files to the current directory:

```bash
ocis-dump /srv/ocis ./backup
```

List files for a specific user without copying:

```bash
ocis-dump -l -u "John Doe"
```

Dry-run to see what would change:

```bash
ocis-dump /srv/ocis ./backup --dry-run
```

Force a full re-dump:

```bash
ocis-dump /srv/ocis ./backup --force
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

### ocis-mpkview

Inspect the decoded contents of `.mpk` (MessagePack) metadata files:

```bash
# View a single file
ocis-mpkview path/to/node.mpk

# Search a directory for all mpk files
ocis-mpkview /srv/ocis/storage -s

# Write output to file
ocis-mpkview node.mpk -o output.txt
```

### ocis-symlink-verify

Verify (and optionally repair) the internal symlink tree OCIS uses:

```bash
# Check user data symlinks
ocis-symlink-verify /srv/ocis --data

# Check and fix metadata symlinks
ocis-symlink-verify /srv/ocis --metadata --fix
```

## Development

```bash
# Install with dev deps
uv sync --extra dev

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=ocis_dumper --cov-report=term-missing

# Run type checking
uv run mypy src/

# Run linting and formatting (via prek)
prek run --all-files
```

### Pre-commit Hooks (prek)

This project uses [prek](https://github.com/frostyslav/prek) for pre-commit checks. The configuration is in `prek.toml` and includes:

- File hygiene (trailing whitespace, EOF, merge conflicts, large files)
- Ruff linting and formatting
- Mypy type checking

```bash
# Run all checks
prek run --all-files

# Install as git hook
prek install
```

### Project Structure

```
.
├── src/ocis_dumper/      # Package source
│   ├── common.py         # Shared utilities
│   ├── dump.py           # Main dump tool
│   ├── mpkview.py        # MPK file viewer
│   └── symlink_verify.py # Symlink repair tool
├── tests/                # Unit tests (143 tests, 96% coverage)
├── .github/workflows/    # CI pipeline (lint, test, typecheck)
├── pyproject.toml        # Package config, deps, ruff, pytest
├── prek.toml             # Pre-commit hook configuration
├── Dockerfile
└── README.md
```

### CI Pipeline

The GitHub Actions CI runs on every push and PR to `main`:

- **lint** — Ruff linting and formatting checks
- **test** — pytest with coverage across Python 3.10, 3.11, 3.12
- **typecheck** — mypy strict type checking

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

Contributions are welcome. Please run `prek run --all-files` and `uv run pytest` before submitting a PR.
