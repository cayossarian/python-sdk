"""Legacy setup.py shim for setuptools < 61 (PEP 621 pre-support).

Modern setuptools (>=61) reads all package metadata from pyproject.toml's
[project] table and ignores the args passed here. The explicit name/version/
packages are needed only so that older setuptools (e.g. the 59.5.0 pinned in
Yocto kirkstone) can build a wheel with correct metadata from the sdist —
without this shim the legacy build produces an UNKNOWN-0.0.0 wheel.

The version is read from src/ebus_sdk/__init__.py's __version__ (the single
source of truth), so the legacy path cannot drift from the modern build's
dynamic-attr resolution; bump it in that one place. Packages are discovered
with find_packages(where="src") to mirror pyproject's
[tool.setuptools.packages.find], so subpackages (e.g. ebus_sdk.ha) are included
automatically and this list can never drift from the modern build.
"""

import re
from pathlib import Path

from setuptools import find_packages, setup

version = re.search(
    r'^__version__ = "([^"]+)"',
    Path("src/ebus_sdk/__init__.py").read_text(encoding="utf-8"),
    re.M,
).group(1)

setup(
    name="ebus-sdk",
    version=version,
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    # PEP 561 marker (SDK-61t.4). setuptools 59.5.0 (Yocto kirkstone) drops a
    # bare marker unless it is listed here; mirrors pyproject package-data.
    package_data={"ebus_sdk": ["py.typed"]},
)
