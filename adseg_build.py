"""Tiny dependency-free PEP 517 wheel builder; supports offline installation."""

import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile


DIST = "adseg_auto-1.0.0.dist-info"
METADATA = "Metadata-Version: 2.1\nName: adseg-auto\nVersion: 1.0.0\nRequires-Python: >=3.10\nLicense: MIT\nSummary: Auditable local broadcast segmentation\n"


def _metadata():
    return {DIST + "/METADATA": METADATA.encode(),
            DIST + "/LICENSE": Path("LICENSE").read_bytes(),
            DIST + "/WHEEL": b"Wheel-Version: 1.0\nGenerator: adseg-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            DIST + "/entry_points.txt": b"[console_scripts]\nadseg-auto = adseg_auto.__main__:main\n"}


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = "adseg_auto-1.0.0-py3-none-any.whl"
    files = {p.as_posix(): p.read_bytes() for p in Path("adseg_auto").glob("*.py")}
    files.update(_metadata())
    records = io.StringIO(newline="")
    writer = csv.writer(records)
    for path, content in sorted(files.items()):
        sha = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow((path, "sha256=" + sha, len(content)))
    writer.writerow((DIST + "/RECORD", "", ""))
    files[DIST + "/RECORD"] = records.getvalue().encode()
    with zipfile.ZipFile(Path(wheel_directory) / name, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content)
    return name


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    for path, content in _metadata().items():
        target = Path(metadata_directory) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return DIST


def get_requires_for_build_wheel(config_settings=None):
    return []
