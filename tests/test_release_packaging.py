"""Exercise the archive that HACS installs into the integration directory."""

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_release_package import build_release_package


def test_hacs_archive_installs_directly_into_component(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "zm1.zip"

    build_release_package(root=root, output=output)

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert json.loads(archive.read("manifest.json"))["domain"] == "zm1"
        assert {"__init__.py", "translations/en.json", "translations/zh-Hans.json", "brand/icon.png"} <= set(names)
        assert all(not name.startswith("custom_components/") for name in names)
        assert all("__pycache__" not in name and not name.endswith((".pyc", ".DS_Store")) for name in names)


def test_mismatched_tag_does_not_replace_existing_archive(tmp_path: Path) -> None:
    output = tmp_path / "zm1.zip"
    output.write_bytes(b"previous archive")

    with pytest.raises(ValueError, match="tag"):
        build_release_package(root=Path(__file__).resolve().parents[1], output=output, tag="v99.0.0")

    assert output.read_bytes() == b"previous archive"


def test_project_version_must_match_installable_manifest(tmp_path: Path) -> None:
    component = tmp_path / "custom_components/zm1"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text('{"version": "1.2.3"}')
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.4"\n')

    with pytest.raises(ValueError, match="version"):
        build_release_package(root=tmp_path, output=tmp_path / "zm1.zip")
