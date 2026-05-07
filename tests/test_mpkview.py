"""Tests for mpkview module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import msgpack
import pytest

from ocis_dumper.mpkview import _build_parser, _read_directory, _read_single, main


def _write_mpk(path: Path, data: dict) -> None:
    """Write a msgpack file at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        msgpack.pack(data, f)


class TestBuildParser:
    """Tests for argument parser construction."""

    def test_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["somefile.mpk"])
        assert args.mpkfile_or_dir == "somefile.mpk"
        assert args.search is False
        assert args.output is None
        assert args.width == 80

    def test_search_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["-s", "/some/dir"])
        assert args.search is True

    def test_output_and_width(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["file.mpk", "-o", "out.txt", "-w", "120"])
        assert args.output == "out.txt"
        assert args.width == 120


class TestReadSingle:
    """Tests for _read_single."""

    def test_reads_valid_file(self, tmp_path: Path) -> None:
        data = {b"key": b"value"}
        mpk_file = tmp_path / "test.mpk"
        _write_mpk(mpk_file, data)

        result = _read_single(mpk_file)
        assert result[b"key"] == b"value"

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="does not exist"):
            _read_single(tmp_path / "nope.mpk")


class TestReadDirectory:
    """Tests for _read_directory."""

    def test_reads_all_mpks(self, tmp_path: Path) -> None:
        _write_mpk(tmp_path / "a.mpk", {b"name": b"a"})
        _write_mpk(tmp_path / "sub" / "b.mpk", {b"name": b"b"})

        result = _read_directory(tmp_path)
        assert len(result) == 2
        values = [v[b"name"] for v in result.values()]
        assert b"a" in values
        assert b"b" in values

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = _read_directory(tmp_path)
        assert result == {}


class TestMain:
    """Tests for the main entry point."""

    def test_no_args_exits(self) -> None:
        with patch("sys.argv", ["mpkview"]), pytest.raises(SystemExit):
            main()

    def test_search_on_file_raises(self, tmp_path: Path) -> None:
        mpk_file = tmp_path / "test.mpk"
        _write_mpk(mpk_file, {b"k": b"v"})

        with patch("sys.argv", ["mpkview", "-s", str(mpk_file)]):
            with pytest.raises(NotADirectoryError):
                main()

    def test_single_file_stdout(self, tmp_path: Path, capsys) -> None:
        mpk_file = tmp_path / "test.mpk"
        _write_mpk(mpk_file, {b"hello": b"world"})

        with patch("sys.argv", ["mpkview", str(mpk_file)]):
            main()

        captured = capsys.readouterr()
        assert b"hello" in captured.out.encode() or "hello" in captured.out

    def test_output_to_file(self, tmp_path: Path) -> None:
        mpk_file = tmp_path / "test.mpk"
        _write_mpk(mpk_file, {b"data": b"123"})
        out_file = tmp_path / "output.txt"

        with patch("sys.argv", ["mpkview", str(mpk_file), "-o", str(out_file)]):
            main()

        assert out_file.exists()
        content = out_file.read_text()
        assert "data" in content

    def test_search_directory(self, tmp_path: Path, capsys) -> None:
        _write_mpk(tmp_path / "a.mpk", {b"file": b"a"})
        _write_mpk(tmp_path / "b.mpk", {b"file": b"b"})

        with patch("sys.argv", ["mpkview", "-s", str(tmp_path)]):
            main()

        captured = capsys.readouterr()
        assert "file" in captured.out
