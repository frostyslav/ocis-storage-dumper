"""Tests for symlink_verify module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import msgpack
import pytest

from ocis_dumper.symlink_verify import (
    _build_parser,
    _clear_existing,
    _compute_symlink_path,
    _create_symlink,
    _get_mpk_info,
    _mpkfile_to_dir,
    _process_mpk_file,
    _resolve_storage_path,
    _SymlinkStats,
    main,
)


def _write_mpk(path: Path, data: dict) -> None:
    """Write a msgpack file at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        msgpack.pack(data, f)


# ---------------------------------------------------------------------------
# Tests for _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Tests for argument parser construction."""

    def test_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["/some/path", "--data"])
        assert args.path == "/some/path"
        assert args.data is True
        assert args.metadata is False
        assert args.fix is False
        assert args.verbose is False

    def test_fix_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["/path", "-d", "-f"])
        assert args.fix is True

    def test_metadata_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["/path", "-m"])
        assert args.metadata is True
        assert args.data is False


# ---------------------------------------------------------------------------
# Tests for _get_mpk_info
# ---------------------------------------------------------------------------


class TestGetMpkInfo:
    """Tests for mpk info extraction."""

    def test_file_type(self) -> None:
        data = {
            b"user.ocis.name": b"doc.pdf",
            b"user.ocis.parentid": b"parent123",
            b"user.ocis.type": b"1",
        }
        result = _get_mpk_info(data)
        assert result["name"] == "doc.pdf"
        assert result["parentid"] == "parent123"
        assert result["type"] == "1"
        assert result["type_name"] == "file"

    def test_dir_type(self) -> None:
        data = {
            b"user.ocis.name": b"folder",
            b"user.ocis.parentid": b"parent456",
            b"user.ocis.type": b"2",
        }
        result = _get_mpk_info(data)
        assert result["type_name"] == "dir"

    def test_unknown_type(self) -> None:
        data = {
            b"user.ocis.name": b"weird",
            b"user.ocis.parentid": b"p",
            b"user.ocis.type": b"99",
        }
        result = _get_mpk_info(data)
        assert result["type_name"] == "N/A"

    def test_missing_fields(self) -> None:
        result = _get_mpk_info({})
        assert result["name"] == "N/A"
        assert result["parentid"] == "N/A"
        assert result["type_name"] == "N/A"


# ---------------------------------------------------------------------------
# Tests for _mpkfile_to_dir
# ---------------------------------------------------------------------------


class TestMpkfileToDir:
    """Tests for mpk file to directory conversion."""

    def test_strips_extension(self, tmp_path: Path) -> None:
        mpk = tmp_path / "ab" / "cd" / "node123.mpk"
        result = _mpkfile_to_dir(mpk)
        assert result == tmp_path / "ab" / "cd" / "node123"

    def test_preserves_parent(self, tmp_path: Path) -> None:
        mpk = tmp_path / "deep" / "path" / "file.mpk"
        result = _mpkfile_to_dir(mpk)
        assert result.parent == tmp_path / "deep" / "path"


# ---------------------------------------------------------------------------
# Tests for _compute_symlink_path
# ---------------------------------------------------------------------------


class TestComputeSymlinkPath:
    """Tests for symlink path computation."""

    def test_file_type(self, tmp_path: Path) -> None:
        mpk_as_dir = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "rest"
        mpk_as_dir.mkdir(parents=True)
        # Make it a file so is_dir() is False and is_file() is True
        file_path = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "asfile"
        file_path.touch()

        content = {
            "name": "test.txt",
            "parentid": "aabbccdd11223344rest",
            "type": "1",
            "type_name": "file",
        }
        result = _compute_symlink_path(content, file_path)
        assert "test.txt" in str(result)

    def test_dir_type_existing_dir(self, tmp_path: Path) -> None:
        dir_path = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "rest"
        dir_path.mkdir(parents=True)

        content = {
            "name": "subfolder",
            "parentid": "aabbccdd11223344rest",
            "type": "2",
            "type_name": "dir",
        }
        result = _compute_symlink_path(content, dir_path)
        assert "subfolder" in str(result)

    def test_unknown_type_raises(self, tmp_path: Path) -> None:
        content = {
            "name": "x",
            "parentid": "aabbccdd11223344rest",
            "type": "99",
            "type_name": "unknown",
        }
        with pytest.raises(NotADirectoryError, match="unexpected type"):
            _compute_symlink_path(content, tmp_path / "fake")


# ---------------------------------------------------------------------------
# Tests for _resolve_storage_path
# ---------------------------------------------------------------------------


class TestResolveStoragePath:
    """Tests for storage path resolution."""

    def test_data_path(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "storage" / "users" / "spaces"
        data_dir.mkdir(parents=True)

        from argparse import Namespace

        args = Namespace(
            path=str(tmp_path),
            metadata=False,
            data=True,
            metadata_subdir="storage/metadata/spaces/",
            data_subdir="storage/users/spaces/",
        )
        result = _resolve_storage_path(args)
        assert result == data_dir

    def test_metadata_path(self, tmp_path: Path) -> None:
        meta_dir = tmp_path / "storage" / "metadata" / "spaces"
        meta_dir.mkdir(parents=True)

        from argparse import Namespace

        args = Namespace(
            path=str(tmp_path),
            metadata=True,
            data=False,
            metadata_subdir="storage/metadata/spaces/",
            data_subdir="storage/users/spaces/",
        )
        result = _resolve_storage_path(args)
        assert result == meta_dir

    def test_neither_raises(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(
            path=str(tmp_path),
            metadata=False,
            data=False,
            metadata_subdir="storage/metadata/spaces/",
            data_subdir="storage/users/spaces/",
        )
        with pytest.raises(SystemExit):
            _resolve_storage_path(args)

    def test_invalid_path_raises(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(
            path=str(tmp_path),
            metadata=False,
            data=True,
            metadata_subdir="storage/metadata/spaces/",
            data_subdir="storage/users/spaces/",
        )
        with pytest.raises(NotADirectoryError):
            _resolve_storage_path(args)


# ---------------------------------------------------------------------------
# Tests for _process_mpk_file
# ---------------------------------------------------------------------------


class TestProcessMpkFile:
    """Tests for individual mpk file processing."""

    def test_skips_invalid_mpk(self, tmp_path: Path) -> None:
        bad_mpk = tmp_path / "bad.mpk"
        bad_mpk.write_text("not msgpack")

        stats = _SymlinkStats()
        _process_mpk_file(bad_mpk, stats, fix=False)
        assert stats.theoretical == 0

    def test_skips_incomplete_mpk(self, tmp_path: Path) -> None:
        """MPK with N/A fields is skipped."""
        _write_mpk(tmp_path / "partial.mpk", {b"user.ocis.name": b"test"})

        stats = _SymlinkStats()
        _process_mpk_file(tmp_path / "partial.mpk", stats, fix=False)
        assert stats.theoretical == 0

    def test_counts_theoretical(self, tmp_path: Path) -> None:
        """Complete MPK increments theoretical count."""
        data = {
            b"user.ocis.name": b"file.txt",
            b"user.ocis.parentid": b"aabbccdd11223344rest",
            b"user.ocis.type": b"1",
        }
        mpk_path = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "rest.mpk"
        _write_mpk(mpk_path, data)

        stats = _SymlinkStats()
        _process_mpk_file(mpk_path, stats, fix=False)
        assert stats.theoretical == 1


# ---------------------------------------------------------------------------
# Tests for _clear_existing and _create_symlink
# ---------------------------------------------------------------------------


class TestClearExisting:
    """Tests for clearing existing non-symlinks."""

    def test_removes_file(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.touch()

        content = {"type_name": "file", "name": "x", "parentid": "p"}
        _clear_existing(target, content, tmp_path / "dir")
        assert not target.exists()

    def test_creates_missing_node(self, tmp_path: Path) -> None:
        symlink_path = tmp_path / "link"
        directory = tmp_path / "missing_node"

        content = {"type_name": "file", "name": "x", "parentid": "p"}
        _clear_existing(symlink_path, content, directory)
        assert directory.exists()


class TestCreateSymlink:
    """Tests for symlink creation."""

    def test_creates_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"

        result = _create_symlink(link, target)
        assert result is True
        assert link.is_symlink()

    def test_existing_symlink_returns_false(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"
        link.symlink_to(target)

        result = _create_symlink(link, target)
        assert result is False


# ---------------------------------------------------------------------------
# Tests for main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main entry point."""

    def test_no_args_exits(self) -> None:
        with patch("sys.argv", ["symlink_verify"]), pytest.raises(SystemExit):
            main()

    def test_no_mode_exits(self, tmp_path: Path) -> None:
        with patch("sys.argv", ["symlink_verify", str(tmp_path)]):
            with pytest.raises(SystemExit):
                main()
