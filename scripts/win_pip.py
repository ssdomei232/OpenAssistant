"""Run pip with a Windows-sandbox-friendly tempfile.mkdtemp.

The DSH Windows sandbox token cannot write inside directories created with a
restrictive (0o700) mode, which is what tempfile.mkdtemp() requests.  pip uses
mkdtemp for every download/unpack step, so we drop the explicit mode here.
Usage: python scripts/win_pip.py install ...
"""
import os
import sys

_real_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *, dir_fd=None):
    if dir_fd is not None:
        return _real_mkdir(path, mode, dir_fd=dir_fd)
    return _real_mkdir(path)


os.mkdir = _mkdir

from pip._internal.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
