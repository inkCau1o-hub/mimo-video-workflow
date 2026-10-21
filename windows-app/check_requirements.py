"""Offline validation of the existing environment; never import application code."""
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pip._vendor.packaging.requirements import Requirement


def main():
    for line in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            return 1
        if not requirement.specifier.contains(installed, prereleases=True):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
