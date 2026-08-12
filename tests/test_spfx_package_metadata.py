"""Regression checks for the deployable SPFx authentication metadata."""

import hashlib
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPFX_ROOT = ROOT / "frontend" / "mag-spfx"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


RELEASE = load_json(SPFX_ROOT / "release-manifest.json")
PACKAGE_PATH = SPFX_ROOT / RELEASE["packagePath"]


def test_spfx_package_requests_backend_delegated_scope():
    solution = load_json(SPFX_ROOT / "config" / "package-solution.json")["solution"]
    assert solution["id"] == RELEASE["solutionId"]
    assert solution["version"] == RELEASE["solutionVersion"]
    assert solution["webApiPermissionRequests"] == [
        {"resource": "MAG-Agent-Backend-API", "scope": "user_impersonation"}
    ]


def test_spfx_npm_versions_are_synchronized():
    package = load_json(SPFX_ROOT / "package.json")
    lock = load_json(SPFX_ROOT / "package-lock.json")
    assert package["version"] == RELEASE["packageVersion"]
    assert lock["version"] == RELEASE["packageVersion"]
    assert lock["packages"][""]["version"] == RELEASE["packageVersion"]
    assert package["engines"]["node"] == ">=22.14.0 <23.0.0"
    assert lock["packages"][""]["engines"] == package["engines"]


def test_web_part_manifest_does_not_persist_function_key():
    manifest = load_json(
        SPFX_ROOT / "src" / "webparts" / "magApp" / "MagAppWebPart.manifest.json"
    )
    properties = manifest["preconfiguredEntries"][0]["properties"]
    assert "functionKey" not in properties


def test_release_package_matches_approved_deployment_artifact():
    if not PACKAGE_PATH.is_file():
        if os.environ.get("REQUIRE_SPFX_PACKAGE") == "1":
            pytest.fail(f"Required SPFx release artifact is missing: {PACKAGE_PATH}")
        pytest.skip("SPFx release artifact has not been generated in this checkout")
    assert hashlib.sha256(PACKAGE_PATH.read_bytes()).hexdigest() == RELEASE["packageSha256"]

    with zipfile.ZipFile(PACKAGE_PATH) as package:
        names = set(package.namelist())
        assert package.testzip() is None
        assert RELEASE["bundle"] in names

        manifest = ET.fromstring(package.read("AppManifest.xml"))
        assert manifest.attrib["ProductID"] == RELEASE["solutionId"]
        assert manifest.attrib["Version"] == RELEASE["solutionVersion"]
        assert manifest.attrib["SkipFeatureDeployment"] == "true"

        namespace = {"sp": "http://schemas.microsoft.com/sharepoint/2012/app/manifest"}
        permissions = manifest.findall(
            ".//sp:WebApiPermissionRequest",
            namespace,
        )
        assert [permission.attrib for permission in permissions] == [
            {"ResourceId": "MAG-Agent-Backend-API", "Scope": "user_impersonation"}
        ]

        component_file = next(
            name
            for name in names
            if name.endswith(f"/WebPart_{RELEASE['componentId']}.xml")
        )
        component_xml = ET.fromstring(package.read(component_file))
        component_namespace = {"sp": "http://schemas.microsoft.com/sharepoint/"}
        component = component_xml.find("sp:ClientSideComponent", component_namespace)
        assert component is not None
        assert component.attrib["Id"] == RELEASE["componentId"]
        component_manifest = json.loads(component.attrib["ComponentManifest"])
        assert component_manifest["version"] == RELEASE["packageVersion"]
        properties = component_manifest["preconfiguredEntries"][0]["properties"]
        assert properties["backendResource"] == (
            "api://026e9069-414c-4df4-8513-bdeb9ff301d2"
        )
