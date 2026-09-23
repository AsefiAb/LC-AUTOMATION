"""Keep refreshed Altametrics logins encrypted for the current Windows user."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

from altametrics_session import secret_name, validate_session


def require_local_windows():
    if os.name != 'nt' or (os.environ.get('GITHUB_ACTIONS') and (
            os.environ.get('RUNNER_ENVIRONMENT') != 'self-hosted'
            or os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION')):
        raise RuntimeError('Saved sales logins require the owner-operated Windows runner')


def path_for(region):
    require_local_windows()
    secret_name(region)  # Reject unexpected paths/regions.
    return Path(os.environ['LOCALAPPDATA']) / 'LC-Automation' / 'sales-sessions' / (region + '.bin')


def crypt(data, decrypt=False):
    require_local_windows()

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # User-scoped DPAPI, with UI forbidden. No machine-wide decryption flag.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('Windows could not unlock the saved sales login; reconnect this region')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def load(region):
    path = path_for(region)
    if path.exists():
        return validate_session(crypt(path.read_bytes(), decrypt=True).decode(), region)
    value = os.environ.get(secret_name(region))
    if not value:
        raise RuntimeError(region + ': sign in locally to connect this region')
    return validate_session(value, region)


def save(region, session):
    session = validate_session(session, region)
    path = path_for(region)
    encrypted = crypt(json.dumps(session, separators=(',', ':')).encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(path)
