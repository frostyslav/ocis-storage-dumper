"""View the decoded contents of OCIS .mpk (MessagePack) files."""

from __future__ import annotations

import argparse
from pathlib import Path
from pprint import pprint

from tqdm import tqdm

from ocis_dumper.common import find_all_mpks, load_mpk


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="View the contents of .mpk files",
    )
    parser.add_argument(
        "mpkfile_or_dir",
        nargs="?",
        help="Path to .mpk file or directory",
    )
    parser.add_argument(
        "-s",
        "--search",
        action="store_true",
        help="Search the given directory for all mpk files",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="File to write output to (default: stdout)",
    )
    parser.add_argument("-w", "--width", type=int, default=80, help="Output width")
    return parser


def _read_single(mpk_path: Path) -> dict:
    """Read and return a single mpk file's contents."""
    if not mpk_path.exists():
        msg = f"File does not exist: {mpk_path}"
        raise FileNotFoundError(msg)
    return load_mpk(mpk_path)


def _read_directory(mpk_path: Path) -> dict[str, dict]:
    """Read all mpk files in a directory tree."""
    all_mpks = find_all_mpks(mpk_path)
    content: dict[str, dict] = {}
    for mpk in tqdm(all_mpks, leave=True, desc="Processing mpk files"):
        content[str(mpk)] = load_mpk(mpk)
    return content


def main() -> None:
    """Entry point for the mpk viewer tool."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.mpkfile_or_dir:
        parser.print_help()
        raise SystemExit(1)

    mpk_path = Path(args.mpkfile_or_dir)

    if args.search:
        if mpk_path.is_file():
            msg = "File provided, but asked to search directory"
            raise NotADirectoryError(msg)
        mpk_content = _read_directory(mpk_path)
    else:
        mpk_content = _read_single(mpk_path)

    if args.output:
        with Path(args.output).open("w") as f:
            pprint(object=mpk_content, stream=f, width=args.width)
    else:
        pprint(object=mpk_content, width=args.width)  # noqa: T203


if __name__ == "__main__":
    main()
