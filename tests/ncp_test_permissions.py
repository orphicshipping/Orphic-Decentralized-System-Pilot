"""Assert private test output using the platform's actual permission mechanism."""
import os
import re
import stat


def windows_dacl(path):
    """Read the DACL through Win32; never change filesystem security."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    get_security = advapi.GetNamedSecurityInfoW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(pointer), ctypes.POINTER(pointer),
                            ctypes.POINTER(pointer), ctypes.POINTER(pointer),
                            ctypes.POINTER(pointer)]
    get_security.restype = wintypes.DWORD
    to_text = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    to_text.argtypes = [pointer, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
    to_text.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    descriptor, dacl = pointer(), pointer()
    text = wintypes.LPWSTR()
    # SE_FILE_OBJECT=1, DACL_SECURITY_INFORMATION=4, SDDL_REVISION_1=1.
    error = get_security(str(path), 1, 4, None, None, ctypes.byref(dacl),
                         None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        assert dacl.value is not None, "A null DACL would grant unrestricted access"
        if not to_text(descriptor, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, pointer))
        if descriptor:
            kernel.LocalFree(descriptor)


def assert_private_directory(path):
    assert path.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        return {"mechanism": "POSIX_mode", "mode": "0700"}
    # CPython >=3.12.4 creates mkdir(0700) with a protected inheritable DACL.
    # Windows st_mode is DOS-attribute-derived and does not represent that DACL.
    # https://docs.python.org/3.12/library/os.html#os.mkdir
    # https://github.com/python/cpython/blob/v3.12.14/Modules/posixmodule.c
    sddl = windows_dacl(path)
    assert sddl.startswith("D:P"), "Directory DACL must be protected from parent inheritance"
    entries = re.findall(r"\([^()]*\)", sddl[3:])
    assert sddl == "D:P" + "".join(entries), "Unexpected DACL flags or syntax"
    assert len(entries) == 3 and set(entries) == {
        "(A;OICI;FA;;;OW)", "(A;OICI;FA;;;SY)", "(A;OICI;FA;;;BA)"
    }, "Require only owner, SYSTEM and Administrators with inheritable full control"
    return {"mechanism": "Windows_protected_DACL", "dacl_sddl": sddl,
            "protected": True, "owner_system_administrators_only": True,
            "children_inherit_restricted_acl": True}


def assert_private_file(path):
    assert path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return {"mechanism": "POSIX_mode", "mode": "0600"}
    sddl = windows_dacl(path)
    match = re.fullmatch(r"D:((?:P)?(?:AI)?)((?:\([^()]*\)){3})", sddl)
    assert match is not None, "Unexpected file DACL flags, syntax or entry count"
    entries = re.findall(r"\(A;(?:ID)?;FA;;;(OW|SY|BA)\)", match[2])
    assert len(entries) == 3 and set(entries) == {"OW", "SY", "BA"}, (
        "Require only owner, SYSTEM and Administrators; inherited grants are checked")
    return {"mechanism": "Windows_DACL", "dacl_sddl": sddl,
            "owner_system_administrators_only": True}
