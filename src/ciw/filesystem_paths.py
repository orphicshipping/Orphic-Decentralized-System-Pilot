"""Filesystem-only Windows long paths; never an evidence identity or path guard."""
from __future__ import annotations

import ntpath
import os
from pathlib import Path, PureWindowsPath


def _windows_extended_path(name: str) -> str:
    """Qualify an ordinary file path without enabling a Windows device namespace.

    Extended paths disable Win32 normalization, so normalize ordinary dot/parent
    components first and refuse names whose interpretation would otherwise change.
    This does not resolve symlinks or establish a filesystem containment boundary.
    https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
    """
    name = name.replace('/', '\\')
    if name.startswith('\\\\.\\'):
        raise ValueError('Device namespaces are not retained filesystem paths')
    if name[:8].upper() == '\\\\?\\UNC\\':
        name = '\\\\' + name[8:]
    elif name.startswith('\\\\?\\'):
        name = name[4:]
        if not (len(name) >= 3 and name[1:3] == ':\\'
                and name[0].isascii() and name[0].isalpha()):
            raise ValueError('Only ordinary drive or UNC paths may use the extended namespace')
    if name.startswith(('\\\\.\\', '\\\\?\\')):
        raise ValueError('Device namespaces are not retained filesystem paths')
    unc_parts = ()
    if name.startswith('\\\\'):
        anchor = name[2:].split('\\')[:2]
        if len(anchor) != 2 or any(part in {'', '.', '..'} for part in anchor):
            raise ValueError('UNC paths require an ordinary server and share')
        unc_parts = tuple(anchor)
    parsed = PureWindowsPath(name)
    if parsed.drive and not unc_parts:
        drive = parsed.drive
        if not (len(drive) == 2 and drive[1] == ':' and drive[0].isascii() and drive[0].isalpha()):
            raise ValueError('A Windows drive must use an ASCII letter')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    reserved.update(prefix + digit for prefix in ('COM', 'LPT') for digit in '123456789\u00b9\u00b2\u00b3')
    components = parsed.parts[1:] if parsed.anchor else parsed.parts
    for part in (*unc_parts, *components):
        if part in {'.', '..'}:
            continue
        if (part.endswith((' ', '.')) or part.split('.', 1)[0].rstrip(' ').upper() in reserved
                or any(ch in '<>:"|?*' or ord(ch) < 32 for ch in part)):
            raise ValueError('Ambiguous or reserved Windows path component')
    absolute = ntpath.abspath(name)
    if not PureWindowsPath(absolute).is_absolute():
        raise ValueError('A fully qualified filesystem path is required')
    return '\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute


def filesystem_path(path: Path) -> Path:
    """Use at a filesystem boundary, keeping caller paths/record names unchanged.

    Explicit Unicode extended paths work without registry or process-wide changes.
    POSIX paths pass through. Windows still enforces component/volume permissions
    and length limits; callers must keep their existing symlink/containment checks.
    """
    path = Path(path)
    return Path(_windows_extended_path(str(path))) if os.name == 'nt' else path
