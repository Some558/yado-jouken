#!/usr/bin/env python3
"""Promote validated staged data without deleting latest before the copy completes."""

import pathlib
import shutil
import tempfile

from rakuten_client import ROOT


def promote(staged: pathlib.Path, latest: pathlib.Path):
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".promote-", dir=latest.parent))
    candidate, previous = temporary / "next", temporary / "previous"
    try:
        shutil.copytree(staged, candidate)
        if latest.exists():
            latest.rename(previous)
        try:
            candidate.rename(latest)
        except OSError:
            if previous.exists():
                previous.rename(latest)
            raise
    finally:
        # If rollback itself failed, retain the only good copy for recovery.
        if not previous.exists() or latest.exists():
            shutil.rmtree(temporary)
    print("promoted data/staged → data/latest")


if __name__ == "__main__":
    promote(ROOT / "data" / "staged", ROOT / "data" / "latest")
