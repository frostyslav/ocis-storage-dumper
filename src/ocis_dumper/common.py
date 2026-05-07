"""Shared utilities for OCIS storage tools."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import msgpack  # type: ignore[import-untyped]
from tqdm import tqdm

logger = logging.getLogger(__name__)

# Size thresholds for human-readable formatting
_KIB = 1024
_MIB = 1024**2
_GIB = 1024**3


def fourslashes(s: str | bytes) -> str:
    """Split an OCIS node ID into the 2/2/2/2/rest directory structure.

    Parameters
    ----------
    s : str | bytes
        The node ID to split.

    Returns
    -------
    str
        The split path string (e.g. "ab/cd/ef/12/rest").

    """
    if s is None:
        return ""
    s = decode_if_bytes(s)
    split_id = [s[i : i + 2] for i in range(0, 8, 2)]
    split_id.append(s[8:])
    return "/".join(split_id)


def decode_if_bytes(s: str | bytes) -> str:
    """Decode bytes to UTF-8 string, pass through if already a string.

    Parameters
    ----------
    s : str | bytes
        The value to decode.

    Returns
    -------
    str
        The decoded string.

    """
    if isinstance(s, bytes):
        return s.decode("utf-8")
    return s


def load_mpk(file: Path) -> dict:
    """Load and decode a MessagePack file.

    Raises
    ------
    ValueError
        If the file cannot be unpacked.

    """
    try:
        with file.open("rb") as f:
            return msgpack.unpack(f, raw=True)
    except ValueError as exc:
        msg = f"Unpack failed for file: {file}"
        raise ValueError(msg) from exc


def find_all_mpks(path: Path, desc: str = "Finding all mpk files") -> list[Path]:
    """Walk a directory tree and return all .mpk file paths.

    Parameters
    ----------
    path : Path
        Root directory to search.
    desc : str
        Description for the progress bar.

    Returns
    -------
    list[Path]
        All discovered .mpk file paths.

    """
    mpks: list[Path] = []
    for root, _, files in tqdm(os.walk(path), leave=False, desc=desc):
        mpks.extend(Path(root, f) for f in files if f.endswith(".mpk"))
    return mpks


def find_mpk(path: Path) -> Path:
    """Find the .mpk file for a given node path.

    Tries <path>.mpk first, then looks in the parent directory.

    Parameters
    ----------
    path : Path
        The node path to find an mpk for.

    Returns
    -------
    Path
        The discovered .mpk file path.

    Raises
    ------
    FileNotFoundError
        If no matching .mpk file is found.

    """
    trivial_mpk = Path(f"{path}.mpk")
    if trivial_mpk.exists():
        return trivial_mpk
    # Look one level up for any .mpk file
    try:
        possible_mpks = [f for f in path.parent.glob("*.mpk") if f.exists()]
    except (IndexError, OSError):
        possible_mpks = []
    if possible_mpks:
        return possible_mpks[0]
    msg = f"No mpk file found for node: {path}"
    raise FileNotFoundError(msg)


def format_size(size_bytes: int) -> tuple[str, str]:
    """Convert byte count to a human-readable (value, unit) tuple.

    Parameters
    ----------
    size_bytes : int
        The size in bytes.

    Returns
    -------
    tuple[str, str]
        (formatted_value, unit) e.g. ("5.0", "MiB").

    """
    if size_bytes >= _GIB:
        return str(round(size_bytes / _GIB, 2)), "GiB"
    if size_bytes >= _MIB:
        return str(round(size_bytes / _MIB, 2)), "MiB"
    if size_bytes >= _KIB:
        return str(round(size_bytes / _KIB, 2)), "KiB"
    return str(size_bytes), "bytes"
