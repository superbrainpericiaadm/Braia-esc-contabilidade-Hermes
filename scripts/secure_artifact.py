#!/usr/bin/env python3
"""Race-safe regular-file operations for the privileged kit installer."""
from __future__ import annotations

import argparse
import os
import secrets
import stat
from pathlib import PurePosixPath


def _parts(path: str) -> tuple[str, ...]:
    pure = PurePosixPath(path)
    parts = pure.parts[1:]
    if not pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("target must be a normalized absolute path")
    return parts


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    return flags | getattr(os, "O_NOFOLLOW", 0)


def _open_parent(path: str) -> tuple[int, str]:
    parts = _parts(path)
    fd = os.open("/", _directory_flags())
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _stat_target(parent_fd: int, name: str):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def validate(path: str, directory: bool = False) -> None:
    parts = _parts(path)
    fd = os.open("/", _directory_flags())
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            except FileNotFoundError:
                return
            os.close(fd)
            fd = next_fd
        info = _stat_target(fd, parts[-1])
        if info is None:
            return
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode):
            raise ValueError("target has an unsupported file type")
    finally:
        os.close(fd)


def _copy_fd(source_fd: int, destination_fd: int) -> None:
    while block := os.read(source_fd, 1024 * 1024):
        view = memoryview(block)
        while view:
            view = view[os.write(destination_fd, view):]


def _source_fd(path: str) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("source is not a regular file")
    return fd


def atomic_copy(source: str, target: str, mode: int | None = None,
                uid: int | None = None, gid: int | None = None) -> None:
    parent_fd, name = _open_parent(target)
    source_fd = _source_fd(source)
    temporary = f".{name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    temp_fd = None
    try:
        current = _stat_target(parent_fd, name)
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise ValueError("target is not a regular file")
        temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                          0o600, dir_fd=parent_fd)
        _copy_fd(source_fd, temp_fd)
        source_info = os.fstat(source_fd)
        os.fchmod(temp_fd, mode if mode is not None else stat.S_IMODE(source_info.st_mode))
        os.fchown(temp_fd, source_info.st_uid if uid is None else uid,
                  source_info.st_gid if gid is None else gid)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(source_fd)
        os.close(parent_fd)


def remove(path: str) -> None:
    parent_fd, name = _open_parent(path)
    try:
        info = _stat_target(parent_fd, name)
        if info is None:
            return
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("validate-file", "validate-directory", "remove"):
        command = sub.add_parser(action)
        command.add_argument("path")
    copy = sub.add_parser("copy")
    copy.add_argument("source")
    copy.add_argument("target")
    copy.add_argument("--mode", type=lambda value: int(value, 8))
    copy.add_argument("--uid", type=int)
    copy.add_argument("--gid", type=int)
    args = parser.parse_args()
    if args.action == "validate-file":
        validate(args.path)
    elif args.action == "validate-directory":
        validate(args.path, directory=True)
    elif args.action == "remove":
        remove(args.path)
    else:
        atomic_copy(args.source, args.target, args.mode, args.uid, args.gid)


if __name__ == "__main__":
    main()
