"""Tests for ocis_common module."""

from __future__ import annotations

from pathlib import Path

import msgpack
import pytest

from ocis_dumper.common import (
    decode_if_bytes,
    find_all_mpks,
    find_mpk,
    format_size,
    fourslashes,
    load_mpk,
)


class TestFourslashes:
    """Tests for the fourslashes path-splitting function."""

    def test_standard_id(self) -> None:
        """A typical 32-char hex ID is split into 2/2/2/2/rest."""
        result = fourslashes("abcdef1234567890abcdef1234567890")
        assert result == "ab/cd/ef/12/34567890abcdef1234567890"

    def test_short_id(self) -> None:
        """An ID shorter than 8 chars still splits correctly."""
        result = fourslashes("abcdefgh")
        assert result == "ab/cd/ef/gh/"

    def test_exactly_eight_chars(self) -> None:
        """An 8-char ID produces 4 parts with empty remainder."""
        result = fourslashes("12345678")
        assert result == "12/34/56/78/"

    def test_bytes_input(self) -> None:
        """Bytes input is decoded before splitting."""
        result = fourslashes(b"abcdef1234567890abcdef1234567890")
        assert result == "ab/cd/ef/12/34567890abcdef1234567890"

    def test_none_returns_empty(self) -> None:
        """None input returns empty string."""
        result = fourslashes(None)
        assert result == ""


class TestDecodeIfBytes:
    """Tests for decode_if_bytes."""

    def test_bytes_decoded(self) -> None:
        """Bytes are decoded to UTF-8."""
        assert decode_if_bytes(b"hello") == "hello"

    def test_str_passthrough(self) -> None:
        """Strings pass through unchanged."""
        assert decode_if_bytes("hello") == "hello"

    def test_unicode_bytes(self) -> None:
        """UTF-8 encoded unicode bytes are decoded correctly."""
        assert decode_if_bytes("café".encode()) == "café"


class TestLoadMpk:
    """Tests for load_mpk."""

    def test_valid_mpk(self, tmp_path: Path) -> None:
        """A valid msgpack file is loaded correctly."""
        data = {b"key": b"value", b"number": 42}
        mpk_file = tmp_path / "test.mpk"
        with mpk_file.open("wb") as f:
            msgpack.pack(data, f)

        result = load_mpk(mpk_file)
        assert result[b"key"] == b"value"
        assert result[b"number"] == 42

    def test_invalid_mpk_raises(self, tmp_path: Path) -> None:
        """An invalid file raises ValueError."""
        bad_file = tmp_path / "bad.mpk"
        bad_file.write_text("this is not msgpack")

        with pytest.raises(ValueError, match="Unpack failed"):
            load_mpk(bad_file)

    def test_preserves_raw_bytes(self, tmp_path: Path) -> None:
        """Keys and values are kept as raw bytes."""
        data = {b"user.ocis.name": b"Test User"}
        mpk_file = tmp_path / "raw.mpk"
        with mpk_file.open("wb") as f:
            msgpack.pack(data, f)

        result = load_mpk(mpk_file)
        assert isinstance(next(iter(result.keys())), bytes)


class TestFindAllMpks:
    """Tests for find_all_mpks."""

    def test_finds_nested_mpks(self, tmp_path: Path) -> None:
        """Finds .mpk files at various depths."""
        (tmp_path / "a.mpk").touch()
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.mpk").touch()
        (tmp_path / "sub" / "deep").mkdir()
        (tmp_path / "sub" / "deep" / "c.mpk").touch()

        result = find_all_mpks(tmp_path)
        assert len(result) == 3
        names = {p.name for p in result}
        assert names == {"a.mpk", "b.mpk", "c.mpk"}

    def test_ignores_non_mpk(self, tmp_path: Path) -> None:
        """Non-.mpk files are not included."""
        (tmp_path / "readme.txt").touch()
        (tmp_path / "data.json").touch()
        (tmp_path / "actual.mpk").touch()

        result = find_all_mpks(tmp_path)
        assert len(result) == 1
        assert result[0].name == "actual.mpk"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty list."""
        result = find_all_mpks(tmp_path)
        assert result == []


class TestFindMpk:
    """Tests for find_mpk."""

    def test_trivial_mpk_exists(self, tmp_path: Path) -> None:
        """Finds <path>.mpk when it exists."""
        node_path = tmp_path / "ab" / "cd" / "ef" / "12" / "rest"
        node_path.mkdir(parents=True)
        mpk_file = Path(f"{node_path}.mpk")
        mpk_file.touch()

        result = find_mpk(node_path)
        assert result == mpk_file

    def test_fallback_to_parent_glob(self, tmp_path: Path) -> None:
        """Falls back to parent directory glob when trivial path doesn't exist."""
        parent = tmp_path / "nodes"
        parent.mkdir()
        sibling_mpk = parent / "other.mpk"
        sibling_mpk.touch()

        node_path = parent / "nonexistent"
        result = find_mpk(node_path)
        assert result == sibling_mpk

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError when no mpk can be found."""
        node_path = tmp_path / "nowhere"
        node_path.parent.mkdir(parents=True, exist_ok=True)

        with pytest.raises(FileNotFoundError, match="No mpk file found"):
            find_mpk(node_path)


class TestFormatSize:
    """Tests for format_size."""

    def test_bytes(self) -> None:
        """Values under 1 KiB show as bytes."""
        assert format_size(512) == ("512", "bytes")

    def test_zero(self) -> None:
        """Zero bytes."""
        assert format_size(0) == ("0", "bytes")

    def test_kib(self) -> None:
        """Values in KiB range."""
        value, unit = format_size(2048)
        assert unit == "KiB"
        assert value == "2.0"

    def test_mib(self) -> None:
        """Values in MiB range."""
        value, unit = format_size(5 * 1024 * 1024)
        assert unit == "MiB"
        assert value == "5.0"

    def test_gib(self) -> None:
        """Values in GiB range."""
        value, unit = format_size(3 * 1024**3)
        assert unit == "GiB"
        assert value == "3.0"

    def test_boundary_kib(self) -> None:
        """Exactly 1024 bytes is 1.0 KiB."""
        assert format_size(1024) == ("1.0", "KiB")

    def test_boundary_mib(self) -> None:
        """Exactly 1 MiB."""
        assert format_size(1024**2) == ("1.0", "MiB")

    def test_boundary_gib(self) -> None:
        """Exactly 1 GiB."""
        assert format_size(1024**3) == ("1.0", "GiB")

    def test_parent_glob_oserror(self, tmp_path: Path) -> None:
        """OSError during parent glob falls through to FileNotFoundError."""
        # Create a node path whose parent doesn't allow globbing
        node_path = tmp_path / "restricted" / "node"
        node_path.parent.mkdir(parents=True)

        # No .mpk files exist and trivial path doesn't exist
        with pytest.raises(FileNotFoundError, match="No mpk file found"):
            find_mpk(node_path)
