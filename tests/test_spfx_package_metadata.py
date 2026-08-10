"""Regression checks for the deployable SPFx authentication metadata."""

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPFX_ROOT = ROOT / "frontend" / "mag-spfx"
PACKAGE_PATH = SPFX_ROOT / "sharepoint" / "solution" / "mag-spfx.sppkg"
EXPECTED_PACKAGE_SHA256 = (
    "2708b8c4182cea796364ba0f8e726e26236be7ef12cead2ce746ebd5d0080bc3"
)
EXPECTED_BUNDLE = "ClientSideAssets/mag-app-web-part_eadc7fc94fd4022de255.js"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_spfx_package_requests_backend_delegated_scope():
    solution = load_json(SPFX_ROOT / "config" / "package-solution.json")["solution"]
    assert solution["version"] == "0.1.5.0"
    assert solution["webApiPermissionRequests"] == [
        {"resource": "MAG-Agent-Backend-API", "scope": "user_impersonation"}
    ]


def test_spfx_npm_versions_are_synchronized():
    package = load_json(SPFX_ROOT / "package.json")
    lock = load_json(SPFX_ROOT / "package-lock.json")
    assert package["version"] == "0.1.5"
    assert lock["version"] == "0.1.5"
    assert lock["packages"][""]["version"] == "0.1.5"


def test_web_part_manifest_does_not_persist_function_key():
    manifest = load_json(
        SPFX_ROOT / "src" / "webparts" / "magApp" / "MagAppWebPart.manifest.json"
    )
    properties = manifest["preconfiguredEntries"][0]["properties"]
    assert "functionKey" not in properties


def test_release_package_matches_approved_deployment_artifact():
    if not PACKAGE_PATH.is_file():
        pytest.skip("SPFx release artifact has not been generated in this checkout")
    assert hashlib.sha256(PACKAGE_PATH.read_bytes()).hexdigest() == EXPECTED_PACKAGE_SHA256

    with zipfile.ZipFile(PACKAGE_PATH) as package:
        names = set(package.namelist())
        assert EXPECTED_BUNDLE in names

        manifest = ET.fromstring(package.read("AppManifest.xml"))
        assert manifest.attrib["ProductID"] == "b071a1d2-aa81-47ac-9849-768c2e54f213"
        assert manifest.attrib["Version"] == "0.1.5.0"

        namespace = {"sp": "http://schemas.microsoft.com/sharepoint/2012/app/manifest"}
        permissions = manifest.findall(
            ".//sp:WebApiPermissionRequest",
            namespace,
        )
        assert [permission.attrib for permission in permissions] == [
            {"ResourceId": "MAG-Agent-Backend-API", "Scope": "user_impersonation"}
        ]
