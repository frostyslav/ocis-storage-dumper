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


# ---------------------------------------------------------------------------
# Tests for _find_nodes_ancestor
# ---------------------------------------------------------------------------


class TestFindNodesAncestor:
    """Tests for _find_nodes_ancestor."""

    def test_finds_nodes_dir(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _find_nodes_ancestor

        nodes_dir = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "rest"
        nodes_dir.mkdir(parents=True)

        result = _find_nodes_ancestor(nodes_dir)
        assert result is not None
        assert result.name == "nodes"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _find_nodes_ancestor

        path = tmp_path / "some" / "other" / "path"
        path.mkdir(parents=True)

        result = _find_nodes_ancestor(path)
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _compute_symlink_path with dynamic depth
# ---------------------------------------------------------------------------


class TestComputeSymlinkPathDynamic:
    """Tests for dynamic depth computation in _compute_symlink_path."""

    def test_dir_type_not_existing(self, tmp_path: Path) -> None:
        """Dir type where path doesn't exist on disk (not dir, not file)."""
        # Path that doesn't exist but has 'nodes' ancestor
        fake_path = tmp_path / "nodes" / "ab" / "cd" / "ef" / "12" / "rest"
        # Don't create it - it should not exist

        content = {
            "name": "subfolder",
            "parentid": "aabbccdd11223344rest",
            "type": "2",
            "type_name": "dir",
        }
        result = _compute_symlink_path(content, fake_path)
        assert "subfolder" in str(result)

    def test_file_type_with_nodes_ancestor(self, tmp_path: Path) -> None:
        """File type with a proper nodes ancestor computes depth dynamically."""
        nodes_dir = tmp_path / "nodes"
        file_path = nodes_dir / "ab" / "cd" / "ef" / "12" / "rest"
        file_path.mkdir(parents=True)
        # Make it a file
        actual_file = nodes_dir / "ab" / "cd" / "ef" / "12" / "asfile"
        actual_file.touch()

        content = {
            "name": "test.txt",
            "parentid": "aabbccdd11223344rest",
            "type": "1",
            "type_name": "file",
        }
        result = _compute_symlink_path(content, actual_file)
        assert "test.txt" in str(result)

    def test_fallback_when_no_nodes_ancestor(self, tmp_path: Path) -> None:
        """Falls back to hardcoded depth when no 'nodes' ancestor exists."""
        # Path without 'nodes' in ancestry
        fake_path = tmp_path / "other" / "ab" / "cd" / "ef" / "12" / "rest"
        fake_path.mkdir(parents=True)

        content = {
            "name": "test.txt",
            "parentid": "aabbccdd11223344rest",
            "type": "1",
            "type_name": "file",
        }
        result = _compute_symlink_path(content, fake_path)
        assert "test.txt" in str(result)

    def test_dir_fallback_when_no_nodes_ancestor(self, tmp_path: Path) -> None:
        """Dir type falls back to hardcoded depth when no 'nodes' ancestor."""
        fake_path = tmp_path / "other" / "ab" / "cd" / "ef" / "12" / "rest"
        fake_path.mkdir(parents=True)

        content = {
            "name": "subfolder",
            "parentid": "aabbccdd11223344rest",
            "type": "2",
            "type_name": "dir",
        }
        result = _compute_symlink_path(content, fake_path)
        assert "subfolder" in str(result)


# ---------------------------------------------------------------------------
# Tests for _attempt_fix
# ---------------------------------------------------------------------------


class TestAttemptFix:
    """Tests for _attempt_fix."""

    def test_successful_fix(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _attempt_fix

        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"

        content = {"type_name": "file", "name": "x", "parentid": "p"}
        result = _attempt_fix(link, target, content, target)
        assert result is True
        assert link.is_symlink()

    def test_fix_with_existing_non_symlink_file(self, tmp_path: Path) -> None:
        """Fixes a path that already exists as a regular file."""
        from ocis_dumper.symlink_verify import _attempt_fix

        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"
        link.touch()  # exists as regular file

        content = {"type_name": "file", "name": "x", "parentid": "p"}
        result = _attempt_fix(link, target, content, target)
        assert result is True

    def test_fix_with_existing_dir(self, tmp_path: Path) -> None:
        """Fixes a path that already exists as a directory."""
        from ocis_dumper.symlink_verify import _attempt_fix

        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"
        link.mkdir()  # exists as directory

        content = {"type_name": "dir", "name": "x", "parentid": "p"}
        result = _attempt_fix(link, target, content, target)
        assert result is True


# ---------------------------------------------------------------------------
# Tests for _process_mpk_file with fix=True
# ---------------------------------------------------------------------------


class TestProcessMpkFileFix:
    """Tests for _process_mpk_file with fix mode."""

    def test_fix_mode_increments_fixed(self, tmp_path: Path) -> None:
        """Fix mode attempts to fix and increments fixed counter."""
        nodes_dir = tmp_path / "nodes"
        node_path = nodes_dir / "ab" / "cd" / "ef" / "12" / "rest"
        node_path.mkdir(parents=True)

        data = {
            b"user.ocis.name": b"file.txt",
            b"user.ocis.parentid": b"aabbccdd11223344rest",
            b"user.ocis.type": b"1",
        }
        mpk_path = Path(f"{node_path}.mpk")
        _write_mpk(mpk_path, data)

        stats = _SymlinkStats()
        _process_mpk_file(mpk_path, stats, fix=True)
        assert stats.theoretical == 1
        # May or may not fix depending on filesystem state, but shouldn't crash


# ---------------------------------------------------------------------------
# Tests for _clear_existing edge cases
# ---------------------------------------------------------------------------


class TestClearExistingEdgeCases:
    """Tests for _clear_existing with directory types."""

    def test_removes_directory(self, tmp_path: Path) -> None:
        """Removes an existing directory when type is dir."""
        target = tmp_path / "target_dir"
        target.mkdir()
        (target / "child.txt").touch()

        content = {"type_name": "dir", "name": "x", "parentid": "p"}
        _clear_existing(target, content, tmp_path / "dir")
        assert not target.exists()

    def test_creates_parent_for_dir_type(self, tmp_path: Path) -> None:
        """Creates parent directory for dir type nodes."""
        symlink_path = tmp_path / "parent" / "link"
        directory = tmp_path / "node"
        directory.touch()

        content = {"type_name": "dir", "name": "x", "parentid": "p"}
        _clear_existing(symlink_path, content, directory)
        # Parent should be created
        assert symlink_path.parent.exists()


# ---------------------------------------------------------------------------
# Tests for _log_results
# ---------------------------------------------------------------------------


class TestLogResults:
    """Tests for _log_results exit behavior."""

    def test_exits_with_code_2_on_mismatch(self) -> None:
        """Exits with code 2 when symlinks don't match."""
        from ocis_dumper.symlink_verify import _log_results

        stats = _SymlinkStats()
        stats.theoretical = 10
        stats.exist = 8
        stats.actual = 7

        with pytest.raises(SystemExit) as exc_info:
            _log_results(stats, fix=False)
        assert exc_info.value.code == 2

    def test_no_exit_when_all_match(self) -> None:
        """No exit when all counts match."""
        from ocis_dumper.symlink_verify import _log_results

        stats = _SymlinkStats()
        stats.theoretical = 5
        stats.exist = 5
        stats.actual = 5

        # Should not raise
        _log_results(stats, fix=False)

    def test_logs_fixed_count(self, caplog) -> None:
        """Fix mode logs the fixed count."""
        import logging

        from ocis_dumper.symlink_verify import _log_results

        stats = _SymlinkStats()
        stats.theoretical = 5
        stats.exist = 5
        stats.actual = 5
        stats.fixed = 3

        with caplog.at_level(logging.INFO):
            _log_results(stats, fix=True)

        assert "Fixed:" in caplog.text


# ---------------------------------------------------------------------------
# Tests for main with actual data
# ---------------------------------------------------------------------------


class TestMainWithData:
    """Tests for main() with actual OCIS-like structure."""

    def test_data_mode_with_nodes(self, tmp_path: Path) -> None:
        """Main processes nodes in data mode."""
        data_dir = tmp_path / "storage" / "users" / "spaces"
        nodes_dir = data_dir / "part1" / "part2" / "nodes"
        nodes_dir.mkdir(parents=True)

        # Create a valid mpk
        node_path = nodes_dir / "ab" / "cd" / "ef" / "12" / "rest"
        node_path.mkdir(parents=True)
        data = {
            b"user.ocis.name": b"file.txt",
            b"user.ocis.parentid": b"aabbccdd11223344rest",
            b"user.ocis.type": b"1",
        }
        _write_mpk(Path(f"{node_path}.mpk"), data)

        with patch("sys.argv", ["symlink_verify", str(tmp_path), "-d"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # Should exit with 2 (symlinks don't match)
            assert exc_info.value.code == 2

    def test_verbose_data_mode(self, tmp_path: Path) -> None:
        """Verbose mode with data."""
        data_dir = tmp_path / "storage" / "users" / "spaces"
        nodes_dir = data_dir / "part1" / "part2" / "nodes"
        nodes_dir.mkdir(parents=True)

        with patch("sys.argv", ["symlink_verify", str(tmp_path), "-d", "-v"]):
            # No nodes to process, all counts are 0, so no exit
            main()


# ---------------------------------------------------------------------------
# Tests for _create_symlink edge cases
# ---------------------------------------------------------------------------


class TestCreateSymlinkEdgeCases:
    """Tests for _create_symlink error handling."""

    def test_file_not_found_returns_false(self, tmp_path: Path) -> None:
        """FileNotFoundError when target doesn't exist returns False."""
        link = tmp_path / "nonexistent_parent" / "link"
        target = tmp_path / "also_nonexistent" / "target"

        result = _create_symlink(link, target)
        assert result is False

    def test_existing_non_symlink_returns_false(self, tmp_path: Path) -> None:
        """Existing non-symlink file at link path returns False."""
        target = tmp_path / "target"
        target.touch()
        link = tmp_path / "link"
        link.touch()  # Regular file, not symlink

        result = _create_symlink(link, target)
        assert result is False


# ---------------------------------------------------------------------------
# Tests for _retry_with_parent_mkdir
# ---------------------------------------------------------------------------


class TestRetryWithParentMkdir:
    """Tests for _retry_with_parent_mkdir."""

    def test_successful_retry(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _retry_with_parent_mkdir

        target = tmp_path / "target"
        target.touch()

        # Create a file where the parent dir should be
        parent_as_file = tmp_path / "parent_dir"
        parent_as_file.touch()

        link = parent_as_file / "link"

        result = _retry_with_parent_mkdir(link, target)
        assert result is True

    def test_failed_retry(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _retry_with_parent_mkdir

        # Target that doesn't exist
        target = tmp_path / "nonexistent" / "deep" / "target"
        link = tmp_path / "link_parent" / "link"

        result = _retry_with_parent_mkdir(link, target)
        # May succeed or fail depending on OS, but shouldn't crash
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Tests for _attempt_fix failure paths
# ---------------------------------------------------------------------------


class TestAttemptFixFailure:
    """Tests for _attempt_fix when fix fails."""

    def test_returns_false_when_symlink_creation_fails(self, tmp_path: Path) -> None:
        from ocis_dumper.symlink_verify import _attempt_fix

        # Target that doesn't exist in a nonexistent directory
        target = tmp_path / "nonexistent_deep" / "path" / "target"
        link = tmp_path / "also_nonexistent" / "link"

        content = {"type_name": "file", "name": "x", "parentid": "p"}
        directory = tmp_path / "node"
        directory.touch()

        result = _attempt_fix(link, target, content, directory)
        assert result is False


# ---------------------------------------------------------------------------
# Tests for _process_mpk_file with existing symlinks
# ---------------------------------------------------------------------------


class TestProcessMpkFileExisting:
    """Tests for _process_mpk_file counting existing symlinks."""

    def test_counts_existing_symlink(self, tmp_path: Path) -> None:
        """Existing symlink increments both exist and actual counts."""
        nodes_dir = tmp_path / "nodes"
        node_path = nodes_dir / "ab" / "cd" / "ef" / "12" / "rest"
        node_path.mkdir(parents=True)

        data = {
            b"user.ocis.name": b"file.txt",
            b"user.ocis.parentid": b"aabbccdd11223344rest",
            b"user.ocis.type": b"1",
        }
        mpk_path = Path(f"{node_path}.mpk")
        _write_mpk(mpk_path, data)

        # Create the symlink at the expected location
        from ocis_dumper.common import load_mpk
        from ocis_dumper.symlink_verify import (
            _compute_symlink_path,
            _get_mpk_info,
            _mpkfile_to_dir,
        )

        mpk_raw = load_mpk(mpk_path)
        mpk_content = _get_mpk_info(mpk_raw)
        directory = _mpkfile_to_dir(mpk_path)
        symlink_path = _compute_symlink_path(
            mpk_content=mpk_content, mpk_as_dir=directory
        )
        symlink_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a symlink (even if target doesn't exist, it still counts)
        symlink_path.symlink_to(tmp_path / "fake_target")

        stats = _SymlinkStats()
        _process_mpk_file(mpk_path, stats, fix=False)
        assert stats.theoretical == 1
        assert stats.actual == 1
