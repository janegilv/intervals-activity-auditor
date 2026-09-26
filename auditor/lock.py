"""A lock file so two runs (say, the scheduled monitor and a manual run)
never sync into the database at the same time."""
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class AlreadyRunning(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if fcntl is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(self._fd)
                self._fd = None
                raise AlreadyRunning(f"Another run holds {self.path}")
        os.ftruncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return False
