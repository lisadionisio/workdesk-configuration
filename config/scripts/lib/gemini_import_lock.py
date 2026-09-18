"""Host-local vault-wide Gemini writer lock, shared across account routes.

The descriptor stays open through the Bash importer and its children. The lock
file is never unlinked: inode replacement would allow simultaneous writers.
This does not coordinate different hosts; scheduler ownership still must do so.
"""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys


def open_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Lock must be a regular file')
    return fd


def main():
    mode, raw_path = sys.argv[1:3]
    path = Path(raw_path)
    if mode == 'verify':
        fd = int(os.environ['WORKDESK_GEMINI_LOCK_FD'])
        actual, expected = os.fstat(fd), path.lstat()
        if not stat.S_ISREG(expected.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('Inherited lock identity mismatch')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return 0
    if mode != 'run':
        raise ValueError('Unknown lock mode')
    fd = open_lock(path)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Gemini importer already running for this vault on this host; retry after it finishes.', file=sys.stderr)
            return 2
        env = dict(os.environ, WORKDESK_GEMINI_LOCK_FD=str(fd))
        result = subprocess.run(['bash', *sys.argv[3:]], env=env, pass_fds=(fd,))
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError):
        print('Cannot establish Gemini importer lock; no import started.', file=sys.stderr)
        sys.exit(2)
