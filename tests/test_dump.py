"""Tests for dump module."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from unittest.mock import patch

import msgpack
import pytest

from ocis_dumper.dump import (
    _build_file_tree,
    _copy_one,
    _matches_user_filter,
    _needs_copy,
    _node_mpk_info,
    _space_info,
)

# ---------------------------------------------------------------------------
# Helpers for creating fake OCIS structures
# ---------------------------------------------------------------------------


def _write_mpk(path: Path, data: dict) -> None:
    """Write a msgpack file at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        msgpack.pack(data, f)


def _make_fake_args(**kwargs):
    """Create a fake argparse.Namespace with defaults."""
    from argparse import Namespace

    defaults = {
        "user": None,
        "username": None,
        "list": False,
        "dry_run": False,
        "force": False,
        "verbose": False,
        "quiet": False,
        "jobs": 2,
        "outdir": ".",
        "info": False,
        "storage_prefix": "storage/users/spaces",
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


# ---------------------------------------------------------------------------
# Tests for _space_info
# ---------------------------------------------------------------------------


class TestSpaceInfo:
    """Tests for _space_info extraction."""

    def test_extracts_all_fields(self) -> None:
        """All space metadata fields are extracted correctly."""
        mpk_data = {
            b"user.ocis.space.name": b"John Doe",
            b"user.ocis.space.alias": b"personal/jdoe",
            b"user.ocis.space.type": b"personal",
            b"user.ocis.treesize": b"5242880",
        }
        name, stype, size, unit, user = _space_info(mpk_data)
        assert name == "John Doe"
        assert stype == "personal"
        assert size == "5.0"
        assert unit == "MiB"
        assert user == "jdoe"

    def test_missing_alias_slash(self) -> None:
        """Alias without slash uses full alias as username."""
        mpk_data = {
            b"user.ocis.space.name": b"Test",
            b"user.ocis.space.alias": b"noslash",
            b"user.ocis.space.type": b"project",
            b"user.ocis.treesize": b"100",
        }
        _, _, _, _, user = _space_info(mpk_data)
        assert user == "noslash"

    def test_defaults_for_missing_keys(self) -> None:
        """Missing keys fall back to N/A or 0."""
        mpk_data = {}
        name, stype, size, unit, user = _space_info(mpk_data)
        assert name == "N/A"
        assert stype == "N/A"
        assert size == "0"
        assert unit == "bytes"


# ---------------------------------------------------------------------------
# Tests for _node_mpk_info
# ---------------------------------------------------------------------------


class TestNodeMpkInfo:
    """Tests for _node_mpk_info extraction."""

    def test_full_node_data(self) -> None:
        """All fields extracted from a complete node mpk."""
        mpk_data = {
            b"user.ocis.parentid": b"abc123def456",
            b"user.ocis.blobid": b"blob789xyz",
            b"user.ocis.name": b"document.pdf",
        }
        parent_id, blob_id, name = _node_mpk_info(mpk_data)
        assert parent_id == "abc123def456"
        assert blob_id == "blob789xyz"
        assert name == "document.pdf"

    def test_missing_parent_id(self) -> None:
        """Missing parentid returns None."""
        mpk_data = {
            b"user.ocis.blobid": b"blob123",
            b"user.ocis.name": b"file.txt",
        }
        parent_id, blob_id, name = _node_mpk_info(mpk_data)
        assert parent_id is None
        assert blob_id == "blob123"
        assert name == "file.txt"

    def test_missing_blobid_returns_na(self) -> None:
        """Missing blobid returns 'N/A' string."""
        mpk_data = {
            b"user.ocis.parentid": b"parent123",
            b"user.ocis.name": b"folder",
        }
        _, blob_id, _ = _node_mpk_info(mpk_data)
        assert blob_id == "N/A"


# ---------------------------------------------------------------------------
# Tests for _needs_copy
# ---------------------------------------------------------------------------


class TestNeedsCopy:
    """Tests for the incremental copy check."""

    def test_missing_dst_needs_copy(self, tmp_path: Path) -> None:
        """Non-existent destination always needs copy."""
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"

        assert _needs_copy(src, dst) is True

    def test_same_file_skips(self, tmp_path: Path) -> None:
        """Identical size and mtime means no copy needed."""
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"
        shutil.copy2(src, dst)

        assert _needs_copy(src, dst) is False

    def test_different_size_needs_copy(self, tmp_path: Path) -> None:
        """Different file sizes trigger a copy."""
        src = tmp_path / "src.bin"
        src.write_bytes(b"longer data here")
        dst = tmp_path / "dst.bin"
        dst.write_bytes(b"short")

        assert _needs_copy(src, dst) is True

    def test_different_mtime_needs_copy(self, tmp_path: Path) -> None:
        """Same size but different mtime triggers a copy."""
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"
        dst.write_bytes(b"data")
        # Ensure different mtime
        import os

        os.utime(dst, (time.time() - 100, time.time() - 100))

        assert _needs_copy(src, dst) is True


# ---------------------------------------------------------------------------
# Tests for _copy_one
# ---------------------------------------------------------------------------


class TestCopyOne:
    """Tests for the single-file copy function."""

    def test_copies_new_file(self, tmp_path: Path) -> None:
        """Copies a file that doesn't exist at destination."""
        src = tmp_path / "blob"
        src.write_bytes(b"file content")
        dst = tmp_path / "out" / "subdir" / "file.txt"

        filename, status, nbytes = _copy_one(src, dst, force=False)
        assert status == "copied"
        assert filename == "file.txt"
        assert nbytes == 12
        assert dst.read_bytes() == b"file content"

    def test_skips_unchanged(self, tmp_path: Path) -> None:
        """Skips copy when file is unchanged."""
        src = tmp_path / "blob"
        src.write_bytes(b"content")
        dst = tmp_path / "out" / "file.txt"
        dst.parent.mkdir(parents=True)
        shutil.copy2(src, dst)

        filename, status, nbytes = _copy_one(src, dst, force=False)
        assert status == "skipped"
        assert nbytes == 0

    def test_force_overwrites(self, tmp_path: Path) -> None:
        """Force flag causes copy even when unchanged."""
        src = tmp_path / "blob"
        src.write_bytes(b"content")
        dst = tmp_path / "out" / "file.txt"
        dst.parent.mkdir(parents=True)
        shutil.copy2(src, dst)

        filename, status, nbytes = _copy_one(src, dst, force=True)
        assert status == "copied"
        assert nbytes == 7

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Parent directories are created automatically."""
        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "a" / "b" / "c" / "file.txt"

        _copy_one(src, dst, force=False)
        assert dst.exists()

    def test_error_on_bad_source(self, tmp_path: Path) -> None:
        """Returns error status when source doesn't exist."""
        src = tmp_path / "nonexistent"
        dst = tmp_path / "out" / "file.txt"

        filename, status, nbytes = _copy_one(src, dst, force=False)
        assert status.startswith("error:")
        assert nbytes == 0


# ---------------------------------------------------------------------------
# Tests for _matches_user_filter
# ---------------------------------------------------------------------------


class TestMatchesUserFilter:
    """Tests for user filtering logic."""

    def test_no_filter_matches_all(self) -> None:
        """No filter set matches everything."""
        args = _make_fake_args()
        assert _matches_user_filter(args, "Any Name", "anyuser") is True

    def test_user_filter_matches(self) -> None:
        """User display name filter (case-insensitive)."""
        args = _make_fake_args(user="john")
        assert _matches_user_filter(args, "John Doe", "jdoe") is True

    def test_user_filter_rejects(self) -> None:
        """User filter rejects non-matching names."""
        args = _make_fake_args(user="alice")
        assert _matches_user_filter(args, "John Doe", "jdoe") is False

    def test_username_filter_matches(self) -> None:
        """Username filter (case-insensitive)."""
        args = _make_fake_args(username="jdoe")
        assert _matches_user_filter(args, "John Doe", "jdoe") is True

    def test_username_filter_rejects(self) -> None:
        """Username filter rejects non-matching usernames."""
        args = _make_fake_args(username="alice")
        assert _matches_user_filter(args, "John Doe", "jdoe") is False


# ---------------------------------------------------------------------------
# Tests for _build_file_tree
# ---------------------------------------------------------------------------


class TestBuildFileTree:
    """Tests for the file tree builder with a synthetic OCIS structure."""

    def _create_node_mpk(
        self,
        nodes_dir: Path,
        node_id: str,
        parent_id: str,
        blob_id: str,
        name: str,
    ) -> Path:
        """Create a fake node mpk file in the expected directory structure."""
        from ocis_dumper.common import fourslashes

        node_path = nodes_dir / fourslashes(node_id)
        node_path.mkdir(parents=True, exist_ok=True)
        mpk_path = Path(f"{node_path}.mpk")
        data = {
            b"user.ocis.parentid": parent_id.encode(),
            b"user.ocis.blobid": blob_id.encode(),
            b"user.ocis.name": name.encode(),
        }
        _write_mpk(mpk_path, data)
        return mpk_path

    def test_single_file_at_root(self, tmp_path: Path) -> None:
        """A single file whose parent is the space root."""
        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"

        mpk = self._create_node_mpk(
            nodes_dir,
            node_id="1111111122222222333333334444444a",
            parent_id=space_id,
            blob_id="eeeeeeee11111111aaaaaaaa22222222",
            name="readme.txt",
        )

        result = _build_file_tree([mpk], space_id, nodes_dir)
        assert "readme.txt" in result
        assert result["readme.txt"] == (".", "eeeeeeee11111111aaaaaaaa22222222")

    def test_file_in_subdirectory(self, tmp_path: Path) -> None:
        """A file nested inside a directory node."""
        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"
        dir_node_id = "dddddddd11111111222222223333333a"

        # Create the directory node (no blob, acts as parent)
        self._create_node_mpk(
            nodes_dir,
            node_id=dir_node_id,
            parent_id=space_id,
            blob_id="N/A",
            name="Documents",
        )

        # Create a file inside that directory
        file_mpk = self._create_node_mpk(
            nodes_dir,
            node_id="ffffffff11111111222222223333333a",
            parent_id=dir_node_id,
            blob_id="bbbbbbbb11111111222222223333333a",
            name="report.pdf",
        )

        result = _build_file_tree([file_mpk], space_id, nodes_dir)
        assert "report.pdf" in result
        parent_path, blob_id = result["report.pdf"]
        assert "Documents" in parent_path
        assert blob_id == "bbbbbbbb11111111222222223333333a"

    def test_skips_directory_nodes(self, tmp_path: Path) -> None:
        """Nodes without a blob_id (directories) are skipped."""
        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"

        dir_mpk = self._create_node_mpk(
            nodes_dir,
            node_id="dddddddd11111111222222223333333a",
            parent_id=space_id,
            blob_id="N/A",
            name="SomeFolder",
        )

        result = _build_file_tree([dir_mpk], space_id, nodes_dir)
        assert result == {}

    def test_handles_corrupt_mpk(self, tmp_path: Path) -> None:
        """Corrupt mpk files are skipped without crashing."""
        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"

        from ocis_dumper.common import fourslashes

        bad_node_path = nodes_dir / fourslashes("bad0bad0bad0bad0bad0bad0bad0bad0")
        bad_node_path.mkdir(parents=True, exist_ok=True)
        bad_mpk = Path(f"{bad_node_path}.mpk")
        bad_mpk.write_text("not valid msgpack")

        result = _build_file_tree([bad_mpk], space_id, nodes_dir)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests for _build_parser (argument parsing)
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Tests for the argument parser."""

    def test_defaults(self) -> None:
        from ocis_dumper.dump import _build_parser

        parser = _build_parser()
        args = parser.parse_args([])
        assert args.outdir == "."
        assert args.list is False
        assert args.dry_run is False
        assert args.force is False
        assert args.jobs == 4
        assert args.verbose is False
        assert args.quiet is False
        assert args.info is False

    def test_all_flags(self) -> None:
        from ocis_dumper.dump import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            [
                "/data",
                "/out",
                "-l",
                "-n",
                "-f",
                "-i",
                "-u",
                "John",
                "-un",
                "jdoe",
                "-j",
                "8",
                "-v",
            ]
        )
        assert args.topdir == "/data"
        assert args.outdir == "/out"
        assert args.list is True
        assert args.dry_run is True
        assert args.force is True
        assert args.info is True
        assert args.user == "John"
        assert args.username == "jdoe"
        assert args.jobs == 8
        assert args.verbose is True


# ---------------------------------------------------------------------------
# Tests for _execute_copies
# ---------------------------------------------------------------------------


class TestExecuteCopies:
    """Tests for the copy execution logic."""

    def test_dry_run_does_not_copy(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(dry_run=True)
        stats = _CopyStats()
        _execute_copies([(src, dst)], "testuser", args, stats)

        assert not dst.exists()
        assert stats.copied == 0

    def test_list_mode_does_not_copy(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(list=True)
        stats = _CopyStats()
        _execute_copies([(src, dst)], "testuser", args, stats)

        assert not dst.exists()

    def test_actual_copy(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"hello")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(jobs=1, quiet=True)
        stats = _CopyStats()
        _execute_copies([(src, dst)], "testuser", args, stats)

        assert dst.exists()
        assert dst.read_bytes() == b"hello"
        assert stats.copied == 1

    def test_skips_unchanged(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"
        dst.parent.mkdir(parents=True)
        shutil.copy2(src, dst)

        args = _make_fake_args(jobs=1, quiet=True)
        stats = _CopyStats()
        _execute_copies([(src, dst)], "testuser", args, stats)

        assert stats.skipped == 1
        assert stats.copied == 0


# ---------------------------------------------------------------------------
# Tests for _collect_copy_tasks and _count_folders
# ---------------------------------------------------------------------------


class TestCollectAndCount:
    """Tests for task collection and folder counting."""

    def test_collect_tasks_with_existing_blobs(self, tmp_path: Path) -> None:
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import _collect_copy_tasks

        blob_id = "aaaa111122223333444455556666777a"
        blob_path = tmp_path / "blobs" / fourslashes(blob_id)
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"content")

        files_and_parents = {"test.txt": (".", blob_id)}
        args = _make_fake_args(outdir=str(tmp_path / "out"))

        tasks = _collect_copy_tasks(
            files_and_parents, tmp_path, "personal", "jdoe", args
        )
        assert len(tasks) == 1
        assert tasks[0][1].name == "test.txt"

    def test_collect_tasks_skips_missing_blobs(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _collect_copy_tasks

        files_and_parents = {"ghost.txt": (".", "nonexistent_blob_id_1234567a")}
        args = _make_fake_args(outdir=str(tmp_path / "out"))

        tasks = _collect_copy_tasks(
            files_and_parents, tmp_path, "personal", "jdoe", args
        )
        assert len(tasks) == 0

    def test_count_folders(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import _count_folders

        # No blobs exist, so all entries are "folders"
        files_and_parents = {
            "dir1": (".", "fake_blob_1234567890123456789a"),
            "dir2": (".", "fake_blob_abcdef1234567890abcda"),
        }
        count = _count_folders(files_and_parents, tmp_path)
        assert count == 2


# ---------------------------------------------------------------------------
# Tests for main()
# ---------------------------------------------------------------------------


class TestMainFunction:
    """Tests for the main entry point."""

    def test_invalid_topdir_raises(self, tmp_path: Path) -> None:
        from ocis_dumper.dump import main

        with patch("sys.argv", ["dump", str(tmp_path), str(tmp_path / "out")]):
            with pytest.raises(NotADirectoryError, match="storage"):
                main()

    def test_info_mode_runs(self, tmp_path: Path) -> None:
        """Info mode with no matching spaces completes without error."""
        from ocis_dumper.dump import main

        storage = tmp_path / "storage" / "users" / "spaces"
        storage.mkdir(parents=True)

        with patch(
            "sys.argv", ["dump", str(tmp_path), str(tmp_path / "out"), "-i", "-q"]
        ):
            main()
