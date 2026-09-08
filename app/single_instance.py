"""Refuse to start a second copy of the bot.

Windows autostart is easy to configure twice over — a Startup shortcut next to
a Task Scheduler entry — and neither mechanism knows about the other. A second
copy is silent: it connects to Max independently and forwards every message a
second time, so the damage shows up in Telegram rather than in the log.

The lock is an OS file lock, not a PID file: the kernel drops it when the
owning process dies, crash or kill included, so a leftover lock file can never
wedge the bot on the next start.
"""

import os

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


class AlreadyRunning(RuntimeError):
    """Another process already holds the lock."""


def acquire(path: str):
    """Lock `path` exclusively and return the open file backing the lock.

    The caller must keep the returned object referenced for as long as the bot
    runs — closing it, or letting it be garbage collected, releases the lock.
    """
    handle = open(path, "a+b")
    handle.seek(0)
    if not _try_lock(handle.fileno()):
        handle.close()
        raise AlreadyRunning(f"another instance already holds {path}")
    try:
        handle.truncate()
        handle.write(str(os.getpid()).encode())
        handle.flush()
    except OSError:
        # The pid is a debugging aid; failing to record it must not stop the bot.
        pass
    return handle
