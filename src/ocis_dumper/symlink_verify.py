"""Verify (and optionally repair) symlinks in OCIS storage."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from ocis_dumper.common import find_all_mpks, fourslashes, load_mpk

DEFAULT_METADATA_SUBDIR = "storage/metadata/spaces/"
DEFAULT_DATA_SUBDIR = "storage/users/spaces/"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Verify (and fix) incorrect symlinks in OCIS storage",
    )
    parser.add_argument("path", nargs="?", help="Path to OCIS data root")
    parser.add_argument(
        "-f",
        "--fix",
        action="store_true",
        help="Repair missing/incorrect symlinks",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed output",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-m",
        "--metadata",
        action="store_true",
        help="Process metadata tree",
    )
    group.add_argument(
        "-d",
        "--data",
        action="store_true",
        help="Process user data tree",
    )
    parser.add_argument(
        "--metadata-subdir",
        default=DEFAULT_METADATA_SUBDIR,
        help=f"Metadata storage subdirectory. Default: {DEFAULT_METADATA_SUBDIR}",
    )
    parser.add_argument(
        "--data-subdir",
        default=DEFAULT_DATA_SUBDIR,
        help=f"Data storage subdirectory. Default: {DEFAULT_DATA_SUBDIR}",
    )
    return parser


# ---------------------------------------------------------------------------
# MPK helpers
# ---------------------------------------------------------------------------


def _get_mpk_info(mpk_data: dict) -> dict[str, str]:
    """Extract name, parentid, and type from mpk data."""
    name = mpk_data.get(b"user.ocis.name", b"N/A").decode("utf-8")
    parentid = mpk_data.get(b"user.ocis.parentid", b"N/A").decode("utf-8")
    mpk_type = mpk_data.get(b"user.ocis.type", b"N/A").decode("utf-8")
    type_name_map = {"1": "file", "2": "dir"}
    return {
        "name": name,
        "parentid": parentid,
        "type": mpk_type,
        "type_name": type_name_map.get(mpk_type, "N/A"),
    }


def _mpkfile_to_dir(mpkfile: Path) -> Path:
    """Get the directory path that corresponds to an mpk file (stem without .mpk)."""
    return Path(mpkfile.parent, mpkfile.stem)


def _find_nodes_ancestor(path: Path) -> Path | None:
    """Walk up from path to find the nearest ancestor directory named 'nodes'.

    Returns
    -------
    Path | None
        The 'nodes' directory, or None if not found.

    """
    current = path
    while current != current.parent:
        if current.name == "nodes":
            return current
        current = current.parent
    return None


def _compute_symlink_path(mpk_content: dict[str, str], mpk_as_dir: Path) -> Path:
    """Compute where the symlink for this node should be located.

    Dynamically calculates the relative traversal depth from the mpk node
    back to the 'nodes' directory, rather than using a hardcoded number of
    '../' components.

    Returns
    -------
    Path
        The expected symlink location.

    """
    parent_path = fourslashes(mpk_content["parentid"])
    type_name = mpk_content["type_name"]

    # Determine the reference point for computing relative depth
    if type_name == "dir" and mpk_as_dir.is_dir():
        ref_point = mpk_as_dir
    elif type_name == "dir" and mpk_as_dir.is_file():
        ref_point = mpk_as_dir.parent
    elif type_name == "dir":
        ref_point = mpk_as_dir
    elif type_name == "file":
        ref_point = mpk_as_dir.parent
    else:
        msg = f"{mpk_as_dir} has unexpected type: {type_name}"
        raise NotADirectoryError(msg)

    # Find the 'nodes' ancestor and compute relative traversal
    nodes_dir = _find_nodes_ancestor(ref_point)
    if nodes_dir is None:
        # Fallback: use the legacy hardcoded depth
        if type_name == "dir":
            return Path(ref_point, "../../../../../", parent_path, mpk_content["name"])
        return Path(ref_point, "../../../../", parent_path, mpk_content["name"])

    # Compute how many levels up from ref_point to nodes_dir
    try:
        rel = ref_point.relative_to(nodes_dir)
        depth = len(rel.parts)
    except ValueError:
        depth = 5  # fallback

    traversal = "/".join([".."] * depth)
    return Path(ref_point, traversal, parent_path, mpk_content["name"])


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class _SymlinkStats:
    """Accumulator for symlink verification statistics."""

    exist: int = 0
    actual: int = 0
    theoretical: int = 0
    fixed: int = 0


# ---------------------------------------------------------------------------
# Fix logic
# ---------------------------------------------------------------------------


def _clear_existing(
    symlink_path: Path,
    mpk_content: dict[str, str],
    directory: Path,
) -> None:
    """Remove any existing non-symlink or create a missing node."""
    if symlink_path.exists() and not symlink_path.is_symlink():
        logger.debug("Removing non-symlink %s", symlink_path.name)
        if mpk_content["type_name"] == "dir":
            shutil.rmtree(symlink_path)
        else:
            symlink_path.unlink(missing_ok=True)
    elif not directory.exists():
        logger.debug("Creating missing node %s", directory.name)
        directory.touch()

    if mpk_content["type_name"] == "dir":
        try:
            symlink_path.parent.resolve().mkdir(mode=0o755, parents=True, exist_ok=True)
        except FileExistsError:
            logger.debug("%s already exists", symlink_path.name)


def _create_symlink(symlink_path: Path, symlink_rel_target: Path) -> bool:
    """Create the symlink, handling common error cases. Returns True on success."""
    try:
        symlink_path.resolve().symlink_to(symlink_rel_target)
    except FileExistsError:
        if symlink_path.is_symlink():
            logger.debug("%s is already a symlink", symlink_path.name)
        else:
            logger.debug("Skipping %s (exists but not symlink)", symlink_path.name)
        return False
    except FileNotFoundError:
        logger.warning("Target %s does not exist, skipping", symlink_rel_target)
        return False
    except NotADirectoryError:
        return _retry_with_parent_mkdir(symlink_path, symlink_rel_target)
    return True


def _retry_with_parent_mkdir(symlink_path: Path, symlink_rel_target: Path) -> bool:
    """Create parent dir and retry symlink creation."""
    logger.debug("Creating parent dir for %s", symlink_path.name)
    symlink_path.parent.unlink(missing_ok=True)
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        symlink_path.resolve().symlink_to(symlink_rel_target)
    except OSError:
        return False
    return True


def _attempt_fix(
    symlink_path: Path,
    symlink_rel_target: Path,
    mpk_content: dict[str, str],
    directory: Path,
) -> bool:
    """Attempt to fix a single symlink. Returns True on success."""
    _clear_existing(symlink_path, mpk_content, directory)

    if not _create_symlink(symlink_path, symlink_rel_target):
        return False

    # Verify the fix
    try:
        new_target = symlink_path.readlink()
    except OSError:
        return False
    return new_target == symlink_rel_target


def _process_mpk_file(
    mpk_file: Path,
    stats: _SymlinkStats,
    *,
    fix: bool,
) -> None:
    """Process a single mpk file for symlink verification/repair."""
    try:
        mpk_raw = load_mpk(mpk_file)
    except ValueError as e:
        logger.warning("Skipping %s: %s", mpk_file, e)
        return

    mpk_content = _get_mpk_info(mpk_raw)
    if "N/A" in mpk_content.values():
        return

    stats.theoretical += 1
    directory = _mpkfile_to_dir(mpk_file)
    symlink_path = _compute_symlink_path(mpk_content=mpk_content, mpk_as_dir=directory)
    symlink_rel_target = Path(os.path.relpath(directory, symlink_path)[3:])

    if symlink_path.exists():
        stats.exist += 1
    if symlink_path.is_symlink():
        stats.actual += 1

    if not fix:
        return

    if _attempt_fix(symlink_path, symlink_rel_target, mpk_content, directory):
        stats.fixed += 1
        logger.debug("  Fixed: %s", symlink_path.name)
    else:
        logger.debug("  Could not fix: %s", symlink_path.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the symlink verification tool."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.path:
        parser.print_help()
        raise SystemExit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    path = _resolve_storage_path(args)
    logger.info("Checking symlinks at %s", path)

    stats = _SymlinkStats()

    for node_path in path.glob("*/*/nodes"):
        mpks = find_all_mpks(node_path)
        for mpk_file in tqdm(mpks, leave=False, desc="Verifying symlinks"):
            _process_mpk_file(mpk_file, stats, fix=args.fix)

    _log_results(stats, fix=args.fix)


def _resolve_storage_path(args: argparse.Namespace) -> Path:
    """Determine the storage path based on --metadata or --data flag.

    Returns
    -------
    Path
        The resolved storage directory path.

    Raises
    ------
    SystemExit
        If neither --metadata nor --data is specified.
    NotADirectoryError
        If the resolved path does not exist.

    """
    if args.metadata:
        path = Path(args.path, args.metadata_subdir)
    elif args.data:
        path = Path(args.path, args.data_subdir)
    else:
        msg = "Specify whether to check --metadata or --data"
        raise SystemExit(msg)

    if not path.exists() or not path.is_dir():
        msg = f"Invalid OCIS path: {path}"
        raise NotADirectoryError(msg)
    return path


def _log_results(stats: _SymlinkStats, *, fix: bool) -> None:
    """Log the final verification results.

    Raises
    ------
    SystemExit
        Exit code 2 if symlinks are still incorrect after verification.

    """
    logger.info("\n--- Results ---")
    logger.info("Exist:       %d", stats.exist)
    logger.info("Actual:      %d", stats.actual)
    logger.info("Theoretical: %d", stats.theoretical)
    if fix:
        logger.info("Fixed:       %d", stats.fixed)

    if stats.exist != stats.theoretical or stats.actual != stats.theoretical:
        logger.warning("Some symlinks may still be incorrect.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
