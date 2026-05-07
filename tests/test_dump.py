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
        mpk_data: dict[bytes, bytes] = {}
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
        assert args.storage_prefix == "storage/users/spaces"

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

    def test_custom_storage_prefix(self) -> None:
        from ocis_dumper.dump import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--storage-prefix", "custom/path"])
        assert args.storage_prefix == "custom/path"


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

    def test_dry_run_lists_files(self, tmp_path: Path, caplog) -> None:
        """Dry run logs which files would be copied."""
        import logging

        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(dry_run=True)
        stats = _CopyStats()

        with caplog.at_level(logging.INFO):
            _execute_copies([(src, dst)], "testuser", args, stats)

        assert "1 would copy" in caplog.text
        assert str(dst) in caplog.text

    def test_dry_run_force_lists_all(self, tmp_path: Path, caplog) -> None:
        """Dry run with force lists all files as would-copy."""
        import logging

        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"
        dst.parent.mkdir(parents=True)
        shutil.copy2(src, dst)

        args = _make_fake_args(dry_run=True, force=True)
        stats = _CopyStats()

        with caplog.at_level(logging.INFO):
            _execute_copies([(src, dst)], "testuser", args, stats)

        assert "1 would copy" in caplog.text
        assert str(dst) in caplog.text

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
        """Info mode shows space info without processing files."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        _write_mpk(
            root_mpk,
            {
                b"user.ocis.space.name": b"TestUser",
                b"user.ocis.space.alias": b"personal/testuser",
                b"user.ocis.space.type": b"personal",
                b"user.ocis.treesize": b"1024",
            },
        )

        with patch("sys.argv", ["dump", str(tmp_path), str(tmp_path / "out"), "-i"]):
            main()

    def test_full_run_with_space(self, tmp_path: Path) -> None:
        """End-to-end run that discovers a space and copies files."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        # Create a fake OCIS structure
        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        # Create root node mpk
        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        _write_mpk(
            root_mpk,
            {
                b"user.ocis.space.name": b"TestUser",
                b"user.ocis.space.alias": b"personal/testuser",
                b"user.ocis.space.type": b"personal",
                b"user.ocis.treesize": b"1024",
            },
        )

        # Create a file node
        file_node_id = "1111111122222222333333334444444a"
        blob_id = "eeeeeeee11111111aaaaaaaa22222222"
        file_node_path = nodes_dir / fourslashes(file_node_id)
        file_node_path.mkdir(parents=True, exist_ok=True)
        file_mpk = Path(f"{file_node_path}.mpk")
        _write_mpk(
            file_mpk,
            {
                b"user.ocis.parentid": space_id.encode(),
                b"user.ocis.blobid": blob_id.encode(),
                b"user.ocis.name": b"hello.txt",
            },
        )

        # Create the blob
        blob_path = space_dir / "blobs" / fourslashes(blob_id)
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"hello world")

        outdir = tmp_path / "output"
        with patch("sys.argv", ["dump", str(tmp_path), str(outdir), "-q"]):
            main()

        # Verify the file was copied
        expected = outdir / "personal" / "testuser" / "hello.txt"
        assert expected.exists()
        assert expected.read_bytes() == b"hello world"

    def test_verbose_mode(self, tmp_path: Path) -> None:
        """Verbose mode logs file operations."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        _write_mpk(
            root_mpk,
            {
                b"user.ocis.space.name": b"TestUser",
                b"user.ocis.space.alias": b"personal/testuser",
                b"user.ocis.space.type": b"personal",
                b"user.ocis.treesize": b"1024",
            },
        )

        file_node_id = "1111111122222222333333334444444a"
        blob_id = "eeeeeeee11111111aaaaaaaa22222222"
        file_node_path = nodes_dir / fourslashes(file_node_id)
        file_node_path.mkdir(parents=True, exist_ok=True)
        file_mpk = Path(f"{file_node_path}.mpk")
        _write_mpk(
            file_mpk,
            {
                b"user.ocis.parentid": space_id.encode(),
                b"user.ocis.blobid": blob_id.encode(),
                b"user.ocis.name": b"hello.txt",
            },
        )

        blob_path = space_dir / "blobs" / fourslashes(blob_id)
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"hello world")

        outdir = tmp_path / "output"
        with patch("sys.argv", ["dump", str(tmp_path), str(outdir), "-v"]):
            main()

    def test_user_filter_skips_space(self, tmp_path: Path) -> None:
        """User filter skips non-matching spaces."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        _write_mpk(
            root_mpk,
            {
                b"user.ocis.space.name": b"TestUser",
                b"user.ocis.space.alias": b"personal/testuser",
                b"user.ocis.space.type": b"personal",
                b"user.ocis.treesize": b"0",
            },
        )

        outdir = tmp_path / "output"
        with patch(
            "sys.argv", ["dump", str(tmp_path), str(outdir), "-u", "nobody", "-q"]
        ):
            main()

        assert not outdir.exists()

    def test_corrupt_root_mpk_skipped(self, tmp_path: Path) -> None:
        """Spaces with corrupt root MPK are skipped."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        root_mpk.write_text("not valid msgpack")

        outdir = tmp_path / "output"
        with patch("sys.argv", ["dump", str(tmp_path), str(outdir), "-q"]):
            main()

    def test_dry_run_with_space(self, tmp_path: Path) -> None:
        """Dry run reports what would be copied without writing."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        root_mpk = Path(f"{root_node_path}.mpk")
        _write_mpk(
            root_mpk,
            {
                b"user.ocis.space.name": b"TestUser",
                b"user.ocis.space.alias": b"personal/testuser",
                b"user.ocis.space.type": b"personal",
                b"user.ocis.treesize": b"1024",
            },
        )

        file_node_id = "1111111122222222333333334444444a"
        blob_id = "eeeeeeee11111111aaaaaaaa22222222"
        file_node_path = nodes_dir / fourslashes(file_node_id)
        file_node_path.mkdir(parents=True, exist_ok=True)
        file_mpk = Path(f"{file_node_path}.mpk")
        _write_mpk(
            file_mpk,
            {
                b"user.ocis.parentid": space_id.encode(),
                b"user.ocis.blobid": blob_id.encode(),
                b"user.ocis.name": b"hello.txt",
            },
        )

        blob_path = space_dir / "blobs" / fourslashes(blob_id)
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"hello world")

        outdir = tmp_path / "output"
        with patch("sys.argv", ["dump", str(tmp_path), str(outdir), "-n"]):
            main()

        # Dry run should not create output
        assert not (outdir / "personal" / "testuser" / "hello.txt").exists()


# ---------------------------------------------------------------------------
# Tests for _resolve_parent_path edge cases
# ---------------------------------------------------------------------------


class TestResolveParentPath:
    """Tests for parent path resolution edge cases."""

    def test_circular_reference(self, tmp_path: Path) -> None:
        """Circular parent references are detected and broken."""
        from ocis_dumper.dump import _PathResolver

        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"

        # Create two nodes that point to each other
        node_a_id = "aaaa111122223333444455556666777a"
        node_b_id = "bbbb111122223333444455556666777a"

        from ocis_dumper.common import fourslashes

        node_a_path = nodes_dir / fourslashes(node_a_id)
        node_a_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{node_a_path}.mpk"),
            {
                b"user.ocis.parentid": node_b_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"folderA",
            },
        )

        node_b_path = nodes_dir / fourslashes(node_b_id)
        node_b_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{node_b_path}.mpk"),
            {
                b"user.ocis.parentid": node_a_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"folderB",
            },
        )

        resolver = _PathResolver(space_id=space_id, nodes_dir=nodes_dir)
        result = resolver.resolve(node_a_id)
        # Should not infinite loop; returns partial path
        assert "folderA" in result or "folderB" in result

    def test_missing_parent_mpk(self, tmp_path: Path) -> None:
        """Missing parent MPK logs warning and returns partial path."""
        from ocis_dumper.dump import _PathResolver

        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"
        missing_parent_id = "dead111122223333444455556666777a"

        resolver = _PathResolver(space_id=space_id, nodes_dir=nodes_dir)
        result = resolver.resolve(missing_parent_id)
        assert result == "."

    def test_parent_is_space_root(self, tmp_path: Path) -> None:
        """Parent that equals space_id returns '.'."""
        from ocis_dumper.dump import _PathResolver

        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"

        resolver = _PathResolver(space_id=space_id, nodes_dir=nodes_dir)
        result = resolver.resolve(space_id)
        assert result == "."

    def test_cached_path_reused(self, tmp_path: Path) -> None:
        """Second call for same parent_id uses cache."""
        from ocis_dumper.dump import _PathResolver

        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"
        dir_id = "dddddddd11111111222222223333333a"

        from ocis_dumper.common import fourslashes

        dir_path = nodes_dir / fourslashes(dir_id)
        dir_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{dir_path}.mpk"),
            {
                b"user.ocis.parentid": space_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"Documents",
            },
        )

        resolver = _PathResolver(space_id=space_id, nodes_dir=nodes_dir)
        result1 = resolver.resolve(dir_id)
        result2 = resolver.resolve(dir_id)
        assert result1 == result2
        assert "Documents" in result1

    def test_intermediate_cache_hit(self, tmp_path: Path) -> None:
        """Resolving a child reuses the cached path of its ancestor."""
        from ocis_dumper.dump import _PathResolver

        nodes_dir = tmp_path / "nodes"
        space_id = "aabbccdd11223344aabbccdd11223344"
        grandparent_id = "aaaa111122223333444455556666777a"
        parent_id = "bbbb111122223333444455556666777a"
        child_id = "cccc111122223333444455556666777a"

        from ocis_dumper.common import fourslashes

        # grandparent -> space root
        gp_path = nodes_dir / fourslashes(grandparent_id)
        gp_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{gp_path}.mpk"),
            {
                b"user.ocis.parentid": space_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"Level1",
            },
        )

        # parent -> grandparent
        p_path = nodes_dir / fourslashes(parent_id)
        p_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{p_path}.mpk"),
            {
                b"user.ocis.parentid": grandparent_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"Level2",
            },
        )

        # child -> parent
        c_path = nodes_dir / fourslashes(child_id)
        c_path.mkdir(parents=True, exist_ok=True)
        _write_mpk(
            Path(f"{c_path}.mpk"),
            {
                b"user.ocis.parentid": parent_id.encode(),
                b"user.ocis.blobid": b"N/A",
                b"user.ocis.name": b"Level3",
            },
        )

        resolver = _PathResolver(space_id=space_id, nodes_dir=nodes_dir)

        # Resolve grandparent first — caches grandparent
        r1 = resolver.resolve(grandparent_id)
        assert r1 == "./Level1"

        # Resolve child — should hit cached grandparent mid-walk
        r2 = resolver.resolve(child_id)
        assert r2 == "./Level1/Level2/Level3"

        # Resolve parent — should be cached from intermediate caching
        r3 = resolver.resolve(parent_id)
        assert r3 == "./Level1/Level2"


# ---------------------------------------------------------------------------
# Tests for _find_space_nodes
# ---------------------------------------------------------------------------


class TestFindSpaceNodes:
    """Tests for space node discovery."""

    def test_finds_nodes_dirs(self, tmp_path: Path) -> None:
        """Discovers nodes directories at the expected depth."""
        from ocis_dumper.dump import _find_space_nodes

        nodes1 = tmp_path / "part1" / "part2" / "nodes"
        nodes1.mkdir(parents=True)
        nodes2 = tmp_path / "part3" / "part4" / "nodes"
        nodes2.mkdir(parents=True)

        result = list(_find_space_nodes(tmp_path))
        assert len(result) == 2

    def test_ignores_wrong_depth(self, tmp_path: Path) -> None:
        """Nodes at wrong depth are not discovered."""
        from ocis_dumper.dump import _find_space_nodes

        # Only one level deep (should be two)
        wrong = tmp_path / "part1" / "nodes"
        wrong.mkdir(parents=True)

        result = list(_find_space_nodes(tmp_path))
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests for _log_summary
# ---------------------------------------------------------------------------


class TestLogSummary:
    """Tests for summary logging."""

    def test_summary_with_bytes(self, caplog) -> None:
        """Summary includes data transferred when bytes_copied > 0."""
        import logging

        from ocis_dumper.dump import _CopyStats, _log_summary

        stats = _CopyStats()
        stats.copied = 5
        stats.skipped = 2
        stats.errors = 0
        stats.folders = 1
        stats.bytes_copied = 5 * 1024 * 1024  # 5 MiB
        stats.elapsed = 1.5

        with caplog.at_level(logging.INFO):
            _log_summary(stats)

        assert "5.0" in caplog.text
        assert "MiB" in caplog.text

    def test_summary_without_bytes(self, caplog) -> None:
        """Summary omits data line when nothing was copied."""
        import logging

        from ocis_dumper.dump import _CopyStats, _log_summary

        stats = _CopyStats()
        stats.copied = 0
        stats.skipped = 3
        stats.elapsed = 0.5

        with caplog.at_level(logging.INFO):
            _log_summary(stats)

        assert "Data:" not in caplog.text


# ---------------------------------------------------------------------------
# Tests for _execute_copies with verbose and error paths
# ---------------------------------------------------------------------------


class TestExecuteCopiesVerbose:
    """Tests for verbose and error paths in _execute_copies."""

    def test_verbose_logs_saved(self, tmp_path: Path, caplog) -> None:
        """Verbose mode logs 'Saved' for copied files."""
        import logging

        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(jobs=1, verbose=True, quiet=False)
        stats = _CopyStats()

        with caplog.at_level(logging.DEBUG):
            _execute_copies([(src, dst)], "testuser", args, stats)

        assert stats.copied == 1
        assert stats.bytes_copied == 4

    def test_verbose_logs_skipped(self, tmp_path: Path, caplog) -> None:
        """Verbose mode logs 'Skipped' for unchanged files."""
        import logging

        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"
        dst.parent.mkdir(parents=True)
        shutil.copy2(src, dst)

        args = _make_fake_args(jobs=1, verbose=True, quiet=False)
        stats = _CopyStats()

        with caplog.at_level(logging.DEBUG):
            _execute_copies([(src, dst)], "testuser", args, stats)

        assert stats.skipped == 1

    def test_error_on_bad_source(self, tmp_path: Path) -> None:
        """Error status is counted when source doesn't exist."""
        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "nonexistent"
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(jobs=1, quiet=True)
        stats = _CopyStats()
        _execute_copies([(src, dst)], "testuser", args, stats)

        assert stats.errors == 1


# ---------------------------------------------------------------------------
# Tests for timeout handling in _execute_copies
# ---------------------------------------------------------------------------


class TestExecuteCopiesTimeout:
    """Tests for timeout handling in _execute_copies."""

    def test_timeout_increments_errors(self, tmp_path: Path) -> None:
        """A timed-out future increments the error counter."""
        from concurrent.futures import Future
        from concurrent.futures import TimeoutError as FutureTimeoutError
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        from ocis_dumper.dump import _CopyStats, _execute_copies

        src = tmp_path / "blob"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "file.txt"

        args = _make_fake_args(jobs=1, quiet=True)
        stats = _CopyStats()

        # Create a mock future that raises TimeoutError
        mock_future = MagicMock(spec=Future)
        mock_future.result.side_effect = FutureTimeoutError()

        with mock_patch("ocis_dumper.dump.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_pool.submit.return_value = mock_future

            with mock_patch(
                "ocis_dumper.dump.as_completed", return_value=iter([mock_future])
            ):
                with mock_patch(
                    "ocis_dumper.dump.tqdm", side_effect=lambda x, **_kw: x
                ):
                    _execute_copies([(src, dst)], "testuser", args, stats)

        assert stats.errors == 1


# ---------------------------------------------------------------------------
# Tests for main() with missing root mpk (debug log path)
# ---------------------------------------------------------------------------


class TestMainMissingRootMpk:
    """Tests for main() when root mpk is missing entirely."""

    def test_missing_root_mpk_skipped(self, tmp_path: Path) -> None:
        """Spaces with no root MPK file at all are skipped."""
        from ocis_dumper.common import fourslashes
        from ocis_dumper.dump import main

        space_id = "aabbccdd11223344aabbccdd11223344"
        space_part1 = space_id[:16]
        space_part2 = space_id[16:]
        spaces_dir = tmp_path / "storage" / "users" / "spaces"
        space_dir = spaces_dir / space_part1 / space_part2
        nodes_dir = space_dir / "nodes"
        nodes_dir.mkdir(parents=True)

        # Create the node directory structure but NO mpk file
        root_node_path = nodes_dir / fourslashes(space_id)
        root_node_path.mkdir(parents=True, exist_ok=True)
        # Deliberately don't create any .mpk file

        outdir = tmp_path / "output"
        with patch("sys.argv", ["dump", str(tmp_path), str(outdir), "-v"]):
            main()
