"""Atomic byte writes confined to one project's ``docs/to_do`` tree."""

from __future__ import annotations

import ctypes
import os
import stat
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Final, Self

__all__ = [
    "ConfinedOutputTarget",
    "OutputConfinementError",
    "acquire_confined_output",
    "atomic_write_confined_bytes",
    "atomic_write_confined_bytes_at_root",
    "create_confined_directory_exclusive",
    "create_confined_bytes_exclusive",
    "ensure_project_child_directory",
    "read_confined_bytes",
    "remove_confined_bytes_if_equal",
]


class OutputConfinementError(ValueError):
    """The requested output cannot be safely confined to ``docs/to_do``."""


_REPARSE: Final = 0x400
_WINDOWS_RESERVED: Final = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))})


def _is_link_or_reparse(path: Path) -> bool:
    details = os.stat(path, follow_symlinks=False)
    return stat.S_ISLNK(details.st_mode) or bool(getattr(details, "st_file_attributes", 0) & _REPARSE)


def _identity(path: Path) -> tuple[int, int, int | None]:
    details = os.stat(path, follow_symlinks=False)
    return details.st_dev, details.st_ino, getattr(details, "st_file_attributes", None)


def _relative_output(root: Path, value: str | Path) -> tuple[Path, tuple[str, ...]]:
    raw = str(value)
    if raw.startswith(("\\\\", "//", "\\\\?\\")):
        raise OutputConfinementError("--output must be below exact docs/to_do")
    project = root.resolve(strict=True)
    candidate = Path(raw)
    try:
        relative = candidate.relative_to(project) if candidate.is_absolute() else candidate
    except ValueError as error:
        raise OutputConfinementError("--output must be below exact docs/to_do") from error
    parts = relative.parts
    if len(parts) < 3 or parts[:2] != ("docs", "to_do") or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise OutputConfinementError("--output must be below exact docs/to_do")
    if os.name == "nt" and any(part.endswith((".", " ")) or part.partition(".")[0].upper() in _WINDOWS_RESERVED for part in parts):
        raise OutputConfinementError("--output contains an unsafe Windows path component")
    return project, tuple(parts)


def _close_guard(guard: int) -> None:
    if os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(guard)
    else:
        os.close(guard)


def _open_directory_guard(path: Path) -> int:
    before = _identity(path)
    if _is_link_or_reparse(path):
        raise OutputConfinementError("--output parent is a symlink or reparse point")
    if os.name == "nt":
        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create_file.restype = wintypes.HANDLE
        # FILE_SHARE_DELETE is deliberately omitted: the held capability pins this parent.
        handle = create_file(str(path), 0x100081, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError()
        guard = int(handle)
    else:
        guard = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if _identity(path) != before or _is_link_or_reparse(path):
            raise OutputConfinementError("--output parent changed during validation")
        if os.name != "nt":
            details = os.fstat(guard)
            if (details.st_dev, details.st_ino) != before[:2]:
                raise OutputConfinementError("--output parent changed during validation")
    except Exception:
        _close_guard(guard)
        raise
    return guard


def _open_posix_child_guard(parent_guard: int, name: str) -> int:
    guard = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_guard)
    try:
        if not stat.S_ISDIR(os.fstat(guard).st_mode):
            raise OutputConfinementError("--output parent component is not a directory")
    except Exception:
        os.close(guard)
        raise
    return guard


class ConfinedOutputTarget:
    def __init__(
        self,
        project_root: Path,
        relative_parent: tuple[str, ...],
        parent: Path,
        parent_identity: tuple[int, int, int | None],
        name: str,
        guards: tuple[int, ...],
    ):
        self.project_root = project_root
        self.relative_parent = relative_parent
        self.parent = parent
        self.parent_identity = parent_identity
        self.name = name
        self._guards = guards
        if os.name == "nt":
            paths = [project_root]
            current = project_root
            for part in relative_parent:
                current /= part
                paths.append(current)
            self._guard_identities = tuple(_identity(path) for path in paths)
        else:
            self._guard_identities = tuple(_descriptor_identity(guard) for guard in guards)

    @property
    def destination(self) -> Path:
        return self.parent / self.name

    @property
    def parent_guard(self) -> int:
        return self._guards[-1]

    def close(self) -> None:
        guards, self._guards = self._guards, ()
        for guard in reversed(guards):
            _close_guard(guard)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def write_bytes(self, data: bytes) -> None:
        _write_bytes(self, data)


def _destination_is_unsafe(target: ConfinedOutputTarget) -> bool:
    try:
        if os.name == "nt":
            return _is_link_or_reparse(target.destination)
        details = os.stat(target.name, dir_fd=target.parent_guard, follow_symlinks=False)
        return stat.S_ISLNK(details.st_mode) or bool(getattr(details, "st_file_attributes", 0) & _REPARSE)
    except FileNotFoundError:
        return False


def _verify_output_parent(target: ConfinedOutputTarget) -> None:
    current = target.project_root
    try:
        for index, part in enumerate((None, *target.relative_parent)):
            if part is not None:
                current /= part
            if _is_link_or_reparse(current):
                raise OutputConfinementError("--output parent is a symlink or reparse point")
            path_identity = _identity(current)
            guard_identity = target._guard_identities[index]
            handle_identity = guard_identity if os.name == "nt" else _descriptor_identity(target._guards[index])
            if path_identity != guard_identity or handle_identity != guard_identity:
                raise OutputConfinementError("--output parent changed during write")
        if current != target.parent or target._guard_identities[-1] != target.parent_identity:
            raise OutputConfinementError("--output parent changed during write")
        if _destination_is_unsafe(target):
            raise OutputConfinementError("--output destination is a symlink or reparse point")
    except (OSError, ValueError) as error:
        if isinstance(error, OutputConfinementError):
            raise
        raise OutputConfinementError("--output parent changed during write") from error


def _open_windows_temporary_descriptor(path: Path) -> int:
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x40010000, 0x1 | 0x2, None, 1, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    try:
        import msvcrt

        return msvcrt.open_osfhandle(int(handle), os.O_WRONLY | os.O_BINARY)
    except Exception:
        _close_guard(int(handle))
        raise


def _nt_set_information_file():
    """Return the explicitly declared NT rename entry point before invoking it."""
    native = ctypes.windll.ntdll.NtSetInformationFile
    native.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
    native.restype = wintypes.LONG
    return native


def _replace_output(target: ConfinedOutputTarget, temporary: Path, replacement_handle: int | None) -> None:
    if os.name != "nt":
        os.replace(temporary.name, target.name, src_dir_fd=target.parent_guard, dst_dir_fd=target.parent_guard)
        return
    if replacement_handle is None:
        raise OSError("missing replacement handle")

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]

    encoded = target.name.encode("utf-16-le")
    size = FileRenameInfo.FileName.offset + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    info = FileRenameInfo.from_buffer(buffer)
    info.ReplaceIfExists, info.RootDirectory, info.FileNameLength = 1, target.parent_guard, len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + FileRenameInfo.FileName.offset, encoded, len(encoded))

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    status = _nt_set_information_file()(replacement_handle, ctypes.byref(IoStatusBlock()), buffer, size, 10)
    if status != 0:
        raise ctypes.WinError(ctypes.windll.ntdll.RtlNtStatusToDosError(status))


def acquire_confined_output(project_root: Path, value: str | Path) -> ConfinedOutputTarget:
    root, parts = _relative_output(project_root, value)
    current = root
    guards: list[int] = []
    try:
        guards.append(_open_directory_guard(current))
        for part in parts[:-1]:
            current /= part
            if os.name == "nt":
                current.mkdir(exist_ok=True)
                guards.append(_open_directory_guard(current))
            else:
                try:
                    os.mkdir(part, dir_fd=guards[-1])
                except FileExistsError:
                    pass
                guards.append(_open_posix_child_guard(guards[-1], part))
        parent = current.resolve(strict=True)
        required = (root / "docs" / "to_do").resolve(strict=True)
        parent.relative_to(required)
        target = ConfinedOutputTarget(root, parts[:-1], parent, _identity(parent), parts[-1], tuple(guards))
        if _destination_is_unsafe(target):
            raise OutputConfinementError("--output destination is a symlink or reparse point")
        return target
    except (OSError, ValueError) as error:
        for guard in reversed(guards):
            _close_guard(guard)
        if isinstance(error, OutputConfinementError):
            raise
        raise OutputConfinementError("--output must be below exact docs/to_do") from error


def _write_bytes(target: ConfinedOutputTarget, data: bytes) -> None:
    _verify_output_parent(target)
    temporary = Path(f".{target.name}.{uuid.uuid4().hex}.tmp")
    replacement_handle: int | None = None
    temporary_identity: tuple[int, int] | None = None
    descriptor: int | None = None
    try:
        if os.name == "nt":
            descriptor = _open_windows_temporary_descriptor(target.parent / temporary)
        else:
            descriptor = os.open(temporary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=target.parent_guard)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            import msvcrt

            replacement_handle = msvcrt.get_osfhandle(descriptor)
        else:
            details = os.stat(temporary.name, dir_fd=target.parent_guard, follow_symlinks=False)
            temporary_identity = details.st_dev, details.st_ino
        _verify_output_parent(target)
        _replace_output(target, temporary, replacement_handle)
        if os.name != "nt":
            try:
                _verify_output_parent(target)
            except OutputConfinementError:
                try:
                    written = os.stat(target.name, dir_fd=target.parent_guard, follow_symlinks=False)
                    if temporary_identity == (written.st_dev, written.st_ino) and stat.S_ISREG(written.st_mode):
                        os.unlink(target.name, dir_fd=target.parent_guard)
                except FileNotFoundError:
                    pass
                raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if os.name == "nt":
                (target.parent / temporary).unlink(missing_ok=True)
            else:
                os.unlink(temporary.name, dir_fd=target.parent_guard)
        except FileNotFoundError:
            pass


def atomic_write_confined_bytes(project_root: Path, value: str | Path, data: bytes) -> None:
    with acquire_confined_output(project_root, value) as target:
        target.write_bytes(data)


def atomic_write_confined_bytes_at_root(project_root: Path, confined_root: Path, target: Path, data: bytes) -> None:
    """Atomically replace one mutable journal below an explicitly pinned confined root."""
    with _acquire_confined_native_output(project_root, confined_root, target, create_parents=True) as record:
        _write_bytes(record, data)


def _safe_components(parts: tuple[str, ...]) -> None:
    if not parts or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise OutputConfinementError("target must be below exact confined root")
    if os.name == "nt" and any(
        part.endswith((".", " ")) or part.partition(".")[0].upper() in _WINDOWS_RESERVED for part in parts
    ):
        raise OutputConfinementError("target contains an unsafe Windows path component")


def _relative_confined(project_root: Path, confined_root: Path, target: Path) -> tuple[Path, tuple[str, ...], tuple[str, ...]]:
    """Return project-relative exact root and target paths without resolving user input."""
    project = project_root.resolve(strict=True)
    try:
        confined = confined_root.relative_to(project) if confined_root.is_absolute() else confined_root
        candidate = target.relative_to(project) if target.is_absolute() else target
    except ValueError as error:
        raise OutputConfinementError("target must be below exact confined root") from error
    confined_parts, target_parts = tuple(confined.parts), tuple(candidate.parts)
    _safe_components(confined_parts)
    _safe_components(target_parts)
    if len(target_parts) <= len(confined_parts) or target_parts[: len(confined_parts)] != confined_parts:
        raise OutputConfinementError("target must be below exact confined root")
    return project, confined_parts, target_parts


def _acquire_confined_native_output(
    project_root: Path,
    confined_root: Path,
    target: Path,
    *,
    create_parents: bool,
) -> ConfinedOutputTarget:
    """Pin every directory from the project root through a native test target."""
    root, confined_parts, parts = _relative_confined(project_root, confined_root, target)
    confined = root.joinpath(*confined_parts)
    try:
        if not confined.is_dir() or _is_link_or_reparse(confined):
            raise OutputConfinementError("exact confined root is unavailable or unsafe")
    except OSError as error:
        raise OutputConfinementError("exact confined root is unavailable or unsafe") from error

    current = root
    guards: list[int] = []
    try:
        guards.append(_open_directory_guard(current))
        for index, part in enumerate(parts[:-1]):
            current /= part
            if index < len(confined_parts):
                if os.name == "nt":
                    guards.append(_open_directory_guard(current))
                else:
                    guards.append(_open_posix_child_guard(guards[-1], part))
                continue
            if os.name == "nt":
                if create_parents:
                    current.mkdir(exist_ok=True)
                guards.append(_open_directory_guard(current))
            else:
                if create_parents:
                    try:
                        os.mkdir(part, dir_fd=guards[-1])
                    except FileExistsError:
                        pass
                guards.append(_open_posix_child_guard(guards[-1], part))
        parent = current.resolve(strict=True)
        resolved_confined = confined.resolve(strict=True)
        parent.relative_to(resolved_confined)
        record = ConfinedOutputTarget(root, parts[:-1], parent, _identity(parent), parts[-1], tuple(guards))
        if _destination_is_unsafe(record):
            raise OutputConfinementError("target is a symlink or reparse point")
        return record
    except FileNotFoundError:
        for guard in reversed(guards):
            _close_guard(guard)
        raise
    except (OSError, ValueError) as error:
        for guard in reversed(guards):
            _close_guard(guard)
        if isinstance(error, OutputConfinementError):
            raise
        raise OutputConfinementError("target must be below exact confined root") from error


def _regular_details(details: os.stat_result) -> bool:
    return stat.S_ISREG(details.st_mode) and not bool(getattr(details, "st_file_attributes", 0) & _REPARSE)


def _leaf_details(target: ConfinedOutputTarget) -> os.stat_result | None:
    try:
        if os.name == "nt":
            return os.stat(target.destination, follow_symlinks=False)
        return os.stat(target.name, dir_fd=target.parent_guard, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _open_windows_native_descriptor(path: Path, *, create: bool = False, delete: bool = False) -> int:
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    access = (0x40000000 if create else 0x80000000) | (0x00010000 if delete else 0)
    handle = create_file(str(path), access, 0x1 | 0x2 | 0x4, None, 1 if create else 3, 0x80 | 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    try:
        import msvcrt

        return msvcrt.open_osfhandle(int(handle), os.O_BINARY | (os.O_WRONLY if create else os.O_RDONLY))
    except Exception:
        _close_guard(int(handle))
        raise


def _open_native_descriptor(target: ConfinedOutputTarget, *, create: bool = False, delete: bool = False) -> int:
    if os.name == "nt":
        return _open_windows_native_descriptor(target.destination, create=create, delete=delete)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
    return os.open(target.name, flags | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=target.parent_guard)


def _install_exclusive(target: ConfinedOutputTarget, temporary: Path) -> None:
    """Link a fully synced temporary file into its final name without replacement."""
    if os.name == "nt":
        create_hard_link = ctypes.windll.kernel32.CreateHardLinkW
        create_hard_link.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID]
        create_hard_link.restype = wintypes.BOOL
        if not create_hard_link(str(target.destination), str(target.parent / temporary), None):
            error = ctypes.WinError()
            if getattr(error, "winerror", None) in {80, 183}:
                raise FileExistsError(str(error)) from error
            raise error
        return
    os.link(temporary.name, target.name, src_dir_fd=target.parent_guard, dst_dir_fd=target.parent_guard, follow_symlinks=False)


def _descriptor_identity(descriptor: int) -> tuple[int, int, int | None]:
    details = os.fstat(descriptor)
    return details.st_dev, details.st_ino, getattr(details, "st_file_attributes", None)


def _handle_identity(descriptor: int) -> tuple[int, int, int | None]:
    details = os.fstat(descriptor)
    if not _regular_details(details):
        raise OutputConfinementError("target is not a regular file")
    return _descriptor_identity(descriptor)


def _verify_regular_handle(target: ConfinedOutputTarget, descriptor: int) -> tuple[int, int, int | None]:
    identity = _handle_identity(descriptor)
    _verify_output_parent(target)
    current = _leaf_details(target)
    if current is None or not _regular_details(current):
        raise OutputConfinementError("target changed during operation")
    if (current.st_dev, current.st_ino, getattr(current, "st_file_attributes", None)) != identity:
        raise OutputConfinementError("target changed during operation")
    return identity


def _mark_windows_delete(descriptor: int) -> None:
    import msvcrt

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    mark = ctypes.windll.kernel32.SetFileInformationByHandle
    mark.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    mark.restype = wintypes.BOOL
    info = FileDispositionInfo(True)
    if not mark(msvcrt.get_osfhandle(descriptor), 4, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError()


def _remove_owned_target(
    target: ConfinedOutputTarget,
    identity: tuple[int, int, int | None],
    descriptor: int | None = None,
) -> bool:
    _verify_output_parent(target)
    current = _leaf_details(target)
    if current is None or not _regular_details(current):
        return False
    if (current.st_dev, current.st_ino, getattr(current, "st_file_attributes", None)) != identity:
        return False
    if os.name == "nt":
        if descriptor is None:
            raise OutputConfinementError("missing Windows target handle")
        _mark_windows_delete(descriptor)
        return True
    else:
        quarantine = f".{target.name}.{uuid.uuid4().hex}.rollback"
        try:
            os.rename(target.name, quarantine, src_dir_fd=target.parent_guard, dst_dir_fd=target.parent_guard)
        except FileNotFoundError:
            return False
        moved = os.stat(quarantine, dir_fd=target.parent_guard, follow_symlinks=False)
        if not _regular_details(moved) or (moved.st_dev, moved.st_ino, getattr(moved, "st_file_attributes", None)) != identity:
            return False
        os.unlink(quarantine, dir_fd=target.parent_guard)
        return True


def _read_regular_confined(target: ConfinedOutputTarget, *, delete: bool = False) -> tuple[bytes, tuple[int, int, int | None], int | None] | None:
    _verify_output_parent(target)
    descriptor: int | None = None
    keep_descriptor = False
    try:
        try:
            descriptor = _open_native_descriptor(target, delete=delete)
        except FileNotFoundError:
            return None
        identity = _verify_regular_handle(target, descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read()
        _verify_regular_handle(target, descriptor)
        if delete:
            keep_descriptor = True
            return data, identity, descriptor
        return data, identity, None
    finally:
        if descriptor is not None and not keep_descriptor:
            os.close(descriptor)


def create_confined_bytes_exclusive(
    project_root: Path,
    confined_root: Path,
    target: Path,
    data: bytes,
    *,
    return_identity: bool = False,
) -> bool | tuple[bool, tuple[int, int, int | None] | None]:
    """Create ``target`` once below a pinned root; accept only byte-identical prior output."""
    with _acquire_confined_native_output(project_root, confined_root, target, create_parents=True) as record:
        existing = _read_regular_confined(record)
        if existing is not None:
            if existing[0] != data:
                raise OutputConfinementError("existing target differs from accepted output")
            return (False, None) if return_identity else False
        temporary = Path(f".{record.name}.{uuid.uuid4().hex}.tmp")
        descriptor: int | None = None
        temporary_identity: tuple[int, int, int | None] | None = None
        installed_identity: tuple[int, int, int | None] | None = None
        try:
            if os.name == "nt":
                descriptor = _open_windows_temporary_descriptor(record.parent / temporary)
            else:
                descriptor = os.open(temporary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=record.parent_guard)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_identity = _handle_identity(descriptor)
            _verify_output_parent(record)
            try:
                _install_exclusive(record, temporary)
            except FileExistsError:
                existing = _read_regular_confined(record)
                if existing is None or existing[0] != data:
                    raise OutputConfinementError("existing target differs from accepted output")
                return (False, None) if return_identity else False
            installed_identity = temporary_identity
            os.close(descriptor)
            descriptor = None
            installed = _read_regular_confined(record)
            if installed is None or installed[0] != data or installed[1] != installed_identity:
                raise OutputConfinementError("exclusive install read-back mismatch")
            return (True, installed[1]) if return_identity else True
        except Exception as error:
            if installed_identity is not None:
                try:
                    removed = remove_confined_bytes_if_equal(
                        project_root, confined_root, target, data, expected_identity=installed_identity
                    )
                except Exception as cleanup_error:
                    raise OutputConfinementError("post-install failure preserved conflicting final") from cleanup_error
                if not removed:
                    raise OutputConfinementError("post-install failure preserved conflicting final") from error
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_identity is not None:
                try:
                    removed_temporary = remove_confined_bytes_if_equal(
                        project_root,
                        confined_root,
                        record.parent / temporary,
                        data,
                        expected_identity=temporary_identity,
                    )
                except Exception as cleanup_error:
                    removed_temporary = False
                    temporary_cleanup_error: Exception | None = cleanup_error
                else:
                    temporary_cleanup_error = None
                if not removed_temporary:
                    if installed_identity is not None:
                        try:
                            remove_confined_bytes_if_equal(
                                project_root,
                                confined_root,
                                target,
                                data,
                                expected_identity=installed_identity,
                            )
                        except Exception:
                            pass
                    if temporary_cleanup_error is not None:
                        raise OutputConfinementError("temporary cleanup conflict") from temporary_cleanup_error
                    raise OutputConfinementError("temporary cleanup conflict")


def create_confined_directory_exclusive(project_root: Path, confined_root: Path, directory: Path) -> None:
    """Create one empty directory beneath a pinned root without exposing a partial file."""
    root, confined_parts, directory_parts = _relative_confined(project_root, confined_root, directory)
    if len(directory_parts) != len(confined_parts) + 1:
        raise OutputConfinementError("directory must be directly below exact confined root")
    if directory.exists():
        raise OutputConfinementError("confined directory already exists")
    marker = directory / f".pilot-create-{uuid.uuid4().hex}.tmp"
    marker_data = b""
    create_confined_bytes_exclusive(root, root.joinpath(*confined_parts), marker, marker_data)
    try:
        removed = remove_confined_bytes_if_equal(root, root.joinpath(*confined_parts), marker, marker_data)
    except Exception as error:
        raise OutputConfinementError("confined directory marker cleanup failed") from error
    if not removed:
        raise OutputConfinementError("confined directory marker cleanup failed")


def ensure_project_child_directory(project_root: Path, directory: Path) -> None:
    """Create one direct child while the exact project parent is pinned."""
    project = project_root.resolve(strict=True)
    try:
        relative = directory.relative_to(project) if directory.is_absolute() else directory
    except ValueError as error:
        raise OutputConfinementError("directory must be a direct project child") from error
    if len(relative.parts) != 1:
        raise OutputConfinementError("directory must be a direct project child")
    _safe_components(tuple(relative.parts))
    parent_identity = _identity(project)
    parent_guard = _open_directory_guard(project)
    try:
        child = project / relative
        if child.exists():
            if not child.is_dir() or _is_link_or_reparse(child):
                raise OutputConfinementError("project child is unavailable or unsafe")
        elif os.name == "nt":
            child.mkdir()
        else:
            os.mkdir(relative.name, dir_fd=parent_guard)
        child_guard = _open_directory_guard(child)
        _close_guard(child_guard)
        if _identity(project) != parent_identity:
            raise OutputConfinementError("project changed during directory creation")
    finally:
        _close_guard(parent_guard)


def read_confined_bytes(project_root: Path, confined_root: Path, target: Path) -> bytes | None:
    """Read one regular non-reparse file under a pinned native root."""
    try:
        with _acquire_confined_native_output(project_root, confined_root, target, create_parents=False) as record:
            result = _read_regular_confined(record)
            return None if result is None else result[0]
    except FileNotFoundError:
        return None


def remove_confined_bytes_if_equal(
    project_root: Path,
    confined_root: Path,
    target: Path,
    data: bytes,
    *,
    expected_identity: tuple[int, int, int | None] | None = None,
) -> bool:
    """Remove a target only while its pinned regular-file bytes still equal ``data``."""
    try:
        with _acquire_confined_native_output(project_root, confined_root, target, create_parents=False) as record:
            result = _read_regular_confined(record, delete=os.name == "nt")
            if result is None:
                return False
            existing, identity, descriptor = result
            try:
                if existing != data or (expected_identity is not None and identity != expected_identity):
                    return False
                return _remove_owned_target(record, identity, descriptor)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
    except FileNotFoundError:
        return False
