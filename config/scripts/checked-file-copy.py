#!/usr/bin/env python3
"""Shared per-file write primitive for config migration and narrow adapters."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

class ChangedTarget(RuntimeError):
    pass

def digest(path):
    path = Path(path)
    if path.is_symlink():
        raise ChangedTarget("Refusing a symlink target: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def checked_copy(src, dst, expected, backup=None):
    src, dst = Path(src), Path(dst)
    incoming = src.read_bytes()
    wanted = hashlib.sha256(incoming).hexdigest()
    observed = digest(dst)
    if observed == wanted:
        return 'no-op'
    if observed != expected:
        raise ChangedTarget("Target changed since review: " + str(dst))
    if dst.exists() and not dst.is_file():
        raise ChangedTarget("Target is not a file: " + str(dst))
    if backup is not None and dst.exists():
        backup = Path(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise ChangedTarget("Recovery snapshot already exists: " + str(backup))
        shutil.copy2(dst, backup)
        if digest(backup) != expected:
            raise ChangedTarget("Target changed while taking snapshot: " + str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.workdesk-copy-', dir=dst.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(incoming)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, src.stat().st_mode & 0o777)
        if digest(dst) != expected:
            raise ChangedTarget("Target changed before replacement: " + str(dst))
        os.replace(temp, dst)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return 'applied'
