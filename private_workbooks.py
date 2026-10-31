"""Bounded process locks and atomic writes on one private shared filesystem.

These locks do not coordinate independent disks or create a database/filesystem
transaction. Existing workbook data and backups are never cleaned or reset.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import tempfile
import time


class WorkbookBusy(TimeoutError):
    pass


@contextmanager
def workbook_lock(path, timeout=5):
    target = Path(path).resolve()
    with (target.parent / (target.name + '.lock')).open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        deadline = time.monotonic()+timeout
        while True:
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline: raise WorkbookBusy('Workbook update is busy.') from None
                time.sleep(.05)
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def serialize_workbook(path, when=lambda: True):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            if not when(): return function(*args, **kwargs)
            with workbook_lock(path()): return function(*args, **kwargs)
        return run
    return decorate


def atomic_save(workbook, path):
    target = Path(path).resolve()
    fd, name = tempfile.mkstemp(prefix='.psms-workbook-', suffix=target.suffix, dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        workbook.save(str(temporary))
        os.replace(temporary, target)
    finally:
        if temporary.exists(): temporary.unlink()  # Only the uniquely owned temporary file.
