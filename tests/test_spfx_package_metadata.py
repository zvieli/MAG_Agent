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


def test_spfx_package_uses_independent_msal_client_metadata():
    solution = load_json(SPFX_ROOT / "config" / "package-solution.json")["solution"]
    assert solution["id"] == RELEASE["solutionId"]
    assert solution["version"] == RELEASE["solutionVersion"]
    assert "webApiPermissionRequests" not in solution

    package = load_json(SPFX_ROOT / "package.json")
    assert package["dependencies"]["@azure/msal-browser"] == "4.25.1"

    manifest = load_json(
        SPFX_ROOT / "src" / "webparts" / "magApp" / "MagAppWebPart.manifest.json"
    )
    assert manifest["supportedHosts"] == ["SharePointWebPart"]


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
    assert "apiBaseUrl" not in properties
    assert "backendResource" not in properties


def test_authentication_values_are_deployment_configuration_only():
    source_root = SPFX_ROOT / "src" / "webparts" / "magApp"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    )
    assert "AadTokenProvider" not in source
    assert "aadTokenProviderFactory" not in source
    for value in (
        "78820852-55fa-450b-908d-45c0d911e76b",
        "14b4e437-8b19-4950-9150-a46ab8d626b8",
        "api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation",
        "https://tikshuv.sharepoint.com/_forms/spfxsinglesignon.aspx",
        "https://login.microsoftonline.com/78820852-55fa-450b-908d-45c0d911e76b",
    ):
        assert value not in source


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
        assert permissions == []

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
        assert component_manifest["supportedHosts"] == ["SharePointWebPart"]
        properties = component_manifest["preconfiguredEntries"][0]["properties"]
        assert "backendResource" not in properties
        assert "apiBaseUrl" not in properties
