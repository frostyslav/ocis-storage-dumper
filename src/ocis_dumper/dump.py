"""Dump files from OCIS storage into a standard POSIX directory structure."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

from tqdm import tqdm

from ocis_dumper.common import (
    decode_if_bytes,
    find_all_mpks,
    find_mpk,
    format_size,
    fourslashes,
    load_mpk,
)

# Storage path within OCIS data directory
DEFAULT_STORAGE_PREFIX = "storage/users/spaces"

# Timeout (seconds) for individual file copy operations
_COPY_TIMEOUT = 300

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Dump OCIS storage into a POSIX file tree (v4.0)",
    )
    parser.add_argument(
        "topdir",
        nargs="?",
        default=f"{os.getenv('HOME')}/.ocis",
        help="OCIS storage root directory. Default: $HOME/.ocis",
    )
    parser.add_argument(
        "outdir",
        nargs="?",
        default=".",
        help="Output directory for extracted files. Default: .",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List files without copying",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing anything",
    )
    parser.add_argument("-u", "--user", help="Filter by user's display name")
    parser.add_argument("-un", "--username", help="Filter by actual username")
    parser.add_argument(
        "-i",
        "--info",
        action="store_true",
        help="Only show space info, skip file tree",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force overwrite of all files, even if unchanged",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help="Number of parallel copy threads. Default: 4",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show all file operations",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only show errors and summary",
    )
    parser.add_argument(
        "--storage-prefix",
        default=DEFAULT_STORAGE_PREFIX,
        help=f"Storage path within OCIS data directory. Default: {DEFAULT_STORAGE_PREFIX}",
    )
    return parser


# ---------------------------------------------------------------------------
# MPK info extraction
# ---------------------------------------------------------------------------


def _space_info(mpk_data: dict) -> tuple[str, str, str, str, str]:
    """Extract space metadata from a root node's mpk contents.

    Returns
    -------
    tuple[str, str, str, str, str]
        (space_name, space_type, human_size, size_unit, username)

    """
    s_name: str = mpk_data.get(b"user.ocis.space.name", b"N/A").decode("utf-8")
    s_alias: str = mpk_data.get(b"user.ocis.space.alias", b"N/A").decode("utf-8")
    s_type: str = mpk_data.get(b"user.ocis.space.type", b"N/A").decode("utf-8")
    s_size_bytes = int(mpk_data.get(b"user.ocis.treesize", b"0"))
    s_user = s_alias.split("/")[1] if "/" in s_alias else s_alias
    human_size, size_unit = format_size(s_size_bytes)
    return s_name, s_type, human_size, size_unit, s_user


def _node_mpk_info(mpk_data: dict) -> tuple[str | None, str, str]:
    """Extract parent_id, blob_id, and name from a node's mpk data.

    Returns
    -------
    tuple[str | None, str, str]
        (parent_id, blob_id, name)

    """
    parent_id = mpk_data.get(b"user.ocis.parentid")
    blob_id = mpk_data.get(b"user.ocis.blobid", b"N/A")
    name = mpk_data.get(b"user.ocis.name", b"N/A")
    if parent_id is not None:
        parent_id = decode_if_bytes(parent_id)
    return parent_id, decode_if_bytes(blob_id), decode_if_bytes(name)


# ---------------------------------------------------------------------------
# Node discovery
# ---------------------------------------------------------------------------


def _find_space_nodes(path: Path) -> Generator[Path, None, None]:
    """Yield all 'nodes' directories two levels under the spaces path."""
    yield from path.glob("*/*/nodes")


def _gen_node_info(nodes_path: Path) -> tuple[Path, str, Path]:
    """Derive node_dir, space_id, and root_id from a nodes directory path.

    Returns
    -------
    tuple[Path, str, Path]
        (node_dir, space_id, root_id)

    """
    node_dir = nodes_path.parent
    space_id = node_dir.parts[-2] + node_dir.parts[-1]
    root_id = Path(nodes_path, fourslashes(space_id))
    return node_dir, space_id, root_id


# ---------------------------------------------------------------------------
# Parent resolution (iterative, with caching)
# ---------------------------------------------------------------------------


@dataclass
class _PathResolver:
    """Bundles caches used during parent path resolution."""

    space_id: str
    nodes_dir: Path
    mpk_cache: dict[Path, tuple[str | None, str, str]] = field(default_factory=dict)
    parent_name_cache: dict[str, str] = field(default_factory=dict)
    resolved_path_cache: dict[str, str] = field(default_factory=dict)

    def resolve(self, parent_id: str) -> str:
        """Resolve the full relative path for a given parent node ID.

        Returns
        -------
        str
            The relative path from the space root to the parent directory.

        """
        return _resolve_parent_path(self, parent_id)


def _resolve_parent_path(resolver: _PathResolver, parent_id: str) -> str:
    """Walk up the parent chain iteratively to build the relative path.

    Uses the resolver's caches to short-circuit when an ancestor's full path
    has already been computed. Intermediate nodes are also cached so that
    siblings and cousins resolve in O(1) on subsequent calls.

    Returns
    -------
    str
        The relative path from the space root to the parent directory.

    """
    cache = resolver.resolved_path_cache

    # Fast path: already resolved this exact parent
    if parent_id in cache:
        return cache[parent_id]

    # Collect names bottom-up until we hit the space root or a cached ancestor
    names: list[str] = []
    chain_ids: list[str] = []
    current_id: str | None = parent_id
    seen: set[str] = set()
    prefix = "."

    while current_id and current_id != resolver.space_id:
        if current_id in cache:
            prefix = cache[current_id]
            break

        if current_id in seen:
            logger.warning("Circular parent reference at node %s", current_id)
            break
        seen.add(current_id)
        chain_ids.append(current_id)

        parent_node_path = Path(resolver.nodes_dir, fourslashes(current_id))
        try:
            parent_mpk_path = find_mpk(parent_node_path)
            pid, _, pname = _cached_mpk_info(parent_mpk_path, resolver.mpk_cache)
        except (FileNotFoundError, ValueError):
            logger.warning("Unresolvable parent %s in node chain", current_id)
            break

        if current_id not in resolver.parent_name_cache:
            resolver.parent_name_cache[current_id] = pname
        names.append(pname)
        current_id = pid

    # Build the full path: prefix + reversed names (root-first order)
    result = _assemble_path(prefix, names)
    cache[parent_id] = result

    # Cache intermediate nodes so siblings/cousins resolve instantly
    _cache_intermediates(cache, chain_ids, names, prefix)

    return result


def _assemble_path(prefix: str, names: list[str]) -> str:
    """Combine a prefix path with a list of names (mutates names by reversing)."""
    if not names:
        return prefix
    names.reverse()
    if prefix == ".":
        return "./" + "/".join(names)
    return prefix + "/" + "/".join(names)


def _cache_intermediates(
    cache: dict[str, str],
    chain_ids: list[str],
    names: list[str],
    prefix: str,
) -> None:
    """Cache resolved paths for intermediate nodes in the chain."""
    for i in range(1, len(chain_ids)):
        node_id = chain_ids[i]
        if node_id not in cache:
            sub_names = names[: len(names) - i]
            if not sub_names:
                cache[node_id] = prefix
            elif prefix == ".":
                cache[node_id] = "./" + "/".join(sub_names)
            else:
                cache[node_id] = prefix + "/" + "/".join(sub_names)


def _cached_mpk_info(
    mpk_path: Path,
    cache: dict[Path, tuple[str | None, str, str]],
) -> tuple[str | None, str, str]:
    """Load mpk info with caching to avoid redundant reads.

    Returns
    -------
    tuple[str | None, str, str]
        (parent_id, blob_id, name)

    """
    if mpk_path not in cache:
        mpk_data = load_mpk(mpk_path)
        cache[mpk_path] = _node_mpk_info(mpk_data)
    return cache[mpk_path]


def _build_file_tree(
    node_mpks: list[Path],
    space_id: str,
    nodes_dir: Path,
) -> dict[str, tuple[str, str]]:
    """Build a dict of {filename: (relative_parent_path, blob_id)}.

    Uses an iterative parent-walk with a cache to avoid redundant mpk reads.

    Returns
    -------
    dict[str, tuple[str, str]]
        Mapping of filename to (parent_path, blob_id).

    """
    parent_name_cache: dict[str, str] = {}
    mpk_cache: dict[Path, tuple[str | None, str, str]] = {}
    resolver = _PathResolver(
        space_id=space_id,
        nodes_dir=nodes_dir,
        mpk_cache=mpk_cache,
        parent_name_cache=parent_name_cache,
    )
    files: dict[str, tuple[str, str]] = {}

    for mpk_path in tqdm(node_mpks, leave=False, desc="Building file tree"):
        try:
            parent_id, blob_id, name = _cached_mpk_info(mpk_path, mpk_cache)
        except (ValueError, FileNotFoundError) as e:
            logger.warning("Skipping %s: %s", mpk_path, e)
            continue

        # Only process nodes that have actual blob data (files, not dirs)
        if blob_id == "N/A":
            continue

        if parent_id == space_id:
            files[name] = (".", blob_id)
        elif parent_id is not None:
            rel_path = resolver.resolve(parent_id)
            files[name] = (rel_path, blob_id)

    return files


# ---------------------------------------------------------------------------
# File copy logic
# ---------------------------------------------------------------------------


def _needs_copy(src: Path, dst: Path) -> bool:
    """Return True if dst is missing or differs from src (by size or mtime)."""
    if not dst.exists():
        return True
    src_stat = src.stat()
    dst_stat = dst.stat()
    if src_stat.st_size != dst_stat.st_size:
        return True
    return src_stat.st_mtime != dst_stat.st_mtime


def _copy_one(
    blob_path: Path,
    write_path: Path,
    *,
    force: bool,
) -> tuple[str, str, int]:
    """Copy a single blob to its destination.

    Returns
    -------
    tuple[str, str, int]
        (filename, status, bytes_copied) where status is "copied", "skipped",
        or "error: ...".

    """
    try:
        write_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if force or _needs_copy(blob_path, write_path):
            shutil.copy2(blob_path, write_path)
            return (write_path.name, "copied", blob_path.stat().st_size)
    except OSError as e:
        return (write_path.name, f"error: {e}", 0)
    else:
        return (write_path.name, "skipped", 0)


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------


@dataclass
class _CopyStats:
    """Accumulator for copy operation statistics."""

    copied: int = 0
    skipped: int = 0
    errors: int = 0
    folders: int = 0
    bytes_copied: int = 0
    elapsed: float = field(default=0.0, init=False)


# ---------------------------------------------------------------------------
# Processing helpers (to keep main() manageable)
# ---------------------------------------------------------------------------


def _process_space(
    node: Path,
    args: argparse.Namespace,
    stats: _CopyStats,
    root_mpk_data: dict,
) -> None:
    """Process a single space node: discover files and copy them.

    Parameters
    ----------
    node : Path
        The 'nodes' directory for this space.
    args : argparse.Namespace
        Parsed CLI arguments.
    stats : _CopyStats
        Accumulator for copy statistics.
    root_mpk_data : dict
        Already-loaded root MPK data (avoids redundant reads).

    """
    node_dir, space_id, _root_id = _gen_node_info(node)

    node_mpks = find_all_mpks(node_dir)
    files_and_parents = _build_file_tree(
        node_mpks=node_mpks,
        space_id=space_id,
        nodes_dir=node,
    )

    _space_name, space_type, _tree_size, _size_unit, space_user = _space_info(
        root_mpk_data,
    )

    copy_tasks = _collect_copy_tasks(
        files_and_parents, node_dir, space_type, space_user, args
    )

    _execute_copies(copy_tasks, space_user, args, stats)
    stats.folders += _count_folders(files_and_parents, node_dir)


def _collect_copy_tasks(
    files_and_parents: dict[str, tuple[str, str]],
    node_dir: Path,
    space_type: str,
    space_user: str,
    args: argparse.Namespace,
) -> list[tuple[Path, Path]]:
    """Build the list of (source, destination) pairs for copying."""
    tasks: list[tuple[Path, Path]] = []
    for name, (parent_path, blob_id) in files_and_parents.items():
        blob_path = Path(node_dir, "blobs", fourslashes(blob_id))
        if blob_path.exists() and blob_path.is_file():
            if args.verbose:
                logger.info("  %s/%s", parent_path, name)
            if not args.list:
                full_path = Path(space_type, space_user, parent_path, name)
                write_path = Path(args.outdir, full_path)
                tasks.append((blob_path, write_path))
    return tasks


def _count_folders(
    files_and_parents: dict[str, tuple[str, str]],
    node_dir: Path,
) -> int:
    """Count entries that are directories (blob doesn't exist as a file)."""
    count = 0
    for _parent_path, blob_id in files_and_parents.values():
        blob_path = Path(node_dir, "blobs", fourslashes(blob_id))
        if not (blob_path.exists() and blob_path.is_file()):
            count += 1
    return count


def _log_dry_run(
    copy_tasks: list[tuple[Path, Path]],
    *,
    force: bool,
) -> None:
    """Log which files would be copied in a dry run."""
    would_copy_files: list[str] = []
    for src, dst in copy_tasks:
        if force or _needs_copy(src, dst):
            would_copy_files.append(str(dst))
    logger.info(
        "  Dry run: %d would copy, %d would skip",
        len(would_copy_files),
        len(copy_tasks) - len(would_copy_files),
    )
    for filepath in would_copy_files:
        logger.info("    %s", filepath)


def _execute_copies(
    copy_tasks: list[tuple[Path, Path]],
    space_user: str,
    args: argparse.Namespace,
    stats: _CopyStats,
) -> None:
    """Run the copy tasks, either as dry-run or actual parallel copies."""
    if args.list or args.dry_run:
        if args.dry_run:
            _log_dry_run(copy_tasks, force=args.force)
        return

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(_copy_one, src, dst, force=args.force): (src, dst)
            for src, dst in copy_tasks
        }
        for future in tqdm(
            as_completed(futures),
            total=len(futures),
            leave=False,
            desc=f"Copying ({space_user})",
            disable=args.quiet,
        ):
            try:
                filename, status, nbytes = future.result(timeout=_COPY_TIMEOUT)
            except FutureTimeoutError:
                _src, dst = futures[future]
                stats.errors += 1
                logger.warning("    TIMEOUT: %s", dst.name)
                continue
            if status == "copied":
                stats.copied += 1
                stats.bytes_copied += nbytes
                if args.verbose:
                    logger.info("    Saved %s", filename)
            elif status == "skipped":
                stats.skipped += 1
                if args.verbose:
                    logger.info("    Skipped %s (unchanged)", filename)
            else:
                stats.errors += 1
                logger.error("    FAILED %s: %s", filename, status)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the OCIS dump tool."""
    parser = _build_parser()
    args = parser.parse_args()

    # Configure logging based on verbosity
    if args.quiet:
        log_level = logging.ERROR
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    top = Path(args.topdir)
    if not Path(top, "storage").is_dir():
        msg = f"'storage' folder not found in {top}"
        raise NotADirectoryError(msg)
    logger.info("OCIS root: %s", top)

    start_time = time.time()
    stats = _CopyStats()

    for node in _find_space_nodes(path=Path(top, args.storage_prefix)):
        _node_dir, _space_id, root_id = _gen_node_info(node)
        try:
            root_mpk_data = load_mpk(find_mpk(root_id))
        except FileNotFoundError:
            logger.debug("No mpk for %s", root_id)
            continue
        except ValueError:
            logger.warning("Unpack failed for %s", root_id)
            continue

        space_info_tuple = _space_info(root_mpk_data)
        space_name, _space_type, _tree_size, _size_unit, space_user = space_info_tuple

        if not _matches_user_filter(args, space_name, space_user):
            continue

        _log_space_header(space_info_tuple, root_id)

        if args.info:
            continue

        _process_space(node, args, stats, root_mpk_data)

    stats.elapsed = time.time() - start_time
    _log_summary(stats)


def _matches_user_filter(
    args: argparse.Namespace,
    space_name: str,
    space_user: str,
) -> bool:
    """Return True if this space passes the user/username filters."""
    if args.user and args.user.lower() not in space_name.lower():
        logger.debug("Skipping space %s (user filter)", space_name)
        return False
    if args.username and args.username.lower() not in space_user.lower():
        logger.debug("Skipping space %s (username filter)", space_user)
        return False
    return True


def _log_space_header(
    space_info_tuple: tuple[str, str, str, str, str],
    root_id: Path,
) -> None:
    """Log the space info header."""
    space_name, space_type, tree_size, size_unit, space_user = space_info_tuple
    logger.info("\n[%s/%s]", space_type, space_name)
    logger.info("  username:  %s", space_user)
    logger.info("  root:      %s", root_id)
    logger.info("  tree size: %s %s", tree_size, size_unit)


def _log_summary(stats: _CopyStats) -> None:
    """Log the final run summary."""
    logger.info("\n--- Summary ---")
    logger.info("Copied:  %d", stats.copied)
    logger.info("Skipped: %d (unchanged)", stats.skipped)
    logger.info("Errors:  %d", stats.errors)
    logger.info("Folders: %d", stats.folders)
    if stats.bytes_copied > 0:
        human_size, unit = format_size(stats.bytes_copied)
        logger.info("Data:    %s %s", human_size, unit)
    logger.info("Time:    %.1fs", stats.elapsed)


if __name__ == "__main__":
    main()
