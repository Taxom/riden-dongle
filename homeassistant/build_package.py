"""Build a standalone HA ZIP with file checksums, using the standard library."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
PAYLOAD = (
    "custom_components", "www", "docs", "tests", "README.md", "README_RU.md",
    "LICENSE", "install.sh", "dashboard_card.yaml", "dashboard_view.yaml",
    "demo.html", "pyproject.toml", "requirements-dev.txt", "package.json",
    "package-lock.json", "build_package.py",
)


def build(output_dir: Path) -> Path:
    version = json.loads((SOURCE / "custom_components/rd6006p/manifest.json").read_text())["version"]
    payload = {}
    for name in PAYLOAD:
        source = SOURCE / name
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in files:
            relative = path.relative_to(SOURCE)
            if (
                path.is_file()
                and not any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
                and path.suffix != ".pyc"
            ):
                payload[relative.as_posix()] = path.read_bytes()
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(payload.items())
    )
    payload["SHA256SUMS.txt"] = checksums.encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"RD6006P_HA_v{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name == "install.sh" else 0o100644) << 16
            bundle.writestr(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=SOURCE.parent / "dist")
    print(build(parser.parse_args().output_dir))
