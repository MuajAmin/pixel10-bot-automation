#!/usr/bin/env python3
"""Patch SwiftShader renderer strings in-place while preserving ELF layout."""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DRIVER = "/vendor/lib64/egl/libGLES_swiftshader.so"
DEFAULT_SEARCH = "Google SwiftShader"
DEFAULT_REPLACEMENT = "Adreno (TM) 830"


@dataclass(frozen=True)
class Section:
    name: str
    offset: int
    size: int

    def contains(self, file_offset: int, length: int) -> bool:
        return self.offset <= file_offset and file_offset + length <= self.offset + self.size


def read_c_string(blob: bytes, offset: int) -> str:
    end = blob.find(b"\x00", offset)
    if end == -1:
        end = len(blob)
    return blob[offset:end].decode("ascii", errors="replace")


def parse_elf64_sections(data: bytes) -> list[Section]:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ValueError("input is not an ELF file")
    if data[4] != 2:
        raise ValueError("only ELF64 binaries are supported")
    if data[5] != 1:
        raise ValueError("only little-endian ELF binaries are supported")

    endian = "<"
    e_shoff = struct.unpack_from(endian + "Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from(endian + "H", data, 0x3A)[0]
    e_shnum = struct.unpack_from(endian + "H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from(endian + "H", data, 0x3E)[0]

    if e_shoff == 0 or e_shnum == 0:
        raise ValueError("ELF section table is missing")
    if e_shentsize < 64:
        raise ValueError("unexpected ELF64 section entry size")
    if e_shstrndx >= e_shnum:
        raise ValueError("invalid ELF section string table index")

    shstr_header = e_shoff + e_shstrndx * e_shentsize
    shstr_offset = struct.unpack_from(endian + "Q", data, shstr_header + 0x18)[0]
    shstr_size = struct.unpack_from(endian + "Q", data, shstr_header + 0x20)[0]
    shstr = data[shstr_offset : shstr_offset + shstr_size]

    sections: list[Section] = []
    for idx in range(e_shnum):
        header = e_shoff + idx * e_shentsize
        name_offset = struct.unpack_from(endian + "I", data, header)[0]
        offset = struct.unpack_from(endian + "Q", data, header + 0x18)[0]
        size = struct.unpack_from(endian + "Q", data, header + 0x20)[0]
        name = read_c_string(shstr, name_offset)
        sections.append(Section(name=name, offset=offset, size=size))
    return sections


def rodata_section(data: bytes) -> Section:
    for section in parse_elf64_sections(data):
        if section.name == ".rodata":
            return section
    raise ValueError("ELF .rodata section was not found")


def parse_offset(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value, 0)


def patch_driver(
    path: Path,
    search: bytes,
    replacement: bytes,
    offset: int | None,
    backup: bool,
    require_rodata: bool,
) -> int:
    if not path.exists():
        raise FileNotFoundError(path)
    if len(replacement) > len(search):
        raise ValueError(
            f"replacement length {len(replacement)} exceeds target length {len(search)}"
        )

    data = bytearray(path.read_bytes())
    section = rodata_section(data) if require_rodata else None

    if offset is None:
        start = section.offset if section else 0
        end = section.offset + section.size if section else len(data)
        offset = data.find(search, start, end)
        if offset == -1:
            raise ValueError(f"target bytes not found: {search!r}")
    elif data[offset : offset + len(search)] != search:
        raise ValueError(f"target bytes do not match at offset 0x{offset:x}")

    if section and not section.contains(offset, len(search)):
        raise ValueError(f"target offset 0x{offset:x} is outside .rodata")

    padded = replacement + (b"\x00" * (len(search) - len(replacement)))

    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)
        print(f"backup: {backup_path}")

    data[offset : offset + len(search)] = padded
    path.write_bytes(data)
    print(f"patched: {path}")
    print(f"offset:  0x{offset:x}")
    print(f"search:  {search.decode('ascii')}")
    print(f"replace: {replacement.decode('ascii')}")
    print(f"padding: {len(search) - len(replacement)} null byte(s)")
    return offset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch Google SwiftShader in libGLES_swiftshader.so .rodata"
    )
    parser.add_argument("--file", default=DEFAULT_DRIVER, help="driver ELF to patch")
    parser.add_argument("--search", default=DEFAULT_SEARCH, help="ASCII bytes to locate")
    parser.add_argument("--replace", default=DEFAULT_REPLACEMENT, help="ASCII replacement")
    parser.add_argument(
        "--offset",
        default=None,
        help="exact file offset to patch, e.g. 0x1234; default searches .rodata",
    )
    parser.add_argument("--no-backup", action="store_true", help="skip .bak copy")
    parser.add_argument(
        "--allow-non-rodata",
        action="store_true",
        help="allow patching outside .rodata",
    )
    args = parser.parse_args()

    try:
        patch_driver(
            path=Path(args.file),
            search=args.search.encode("ascii"),
            replacement=args.replace.encode("ascii"),
            offset=parse_offset(args.offset),
            backup=not args.no_backup,
            require_rodata=not args.allow_non_rodata,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
