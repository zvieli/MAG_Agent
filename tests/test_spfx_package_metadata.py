"""Regression checks for the deployable SPFx authentication metadata."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPFX_ROOT = ROOT / "frontend" / "mag-spfx"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_spfx_package_requests_backend_delegated_scope():
    solution = load_json(SPFX_ROOT / "config" / "package-solution.json")["solution"]
    assert solution["version"] == "0.1.4.0"
    assert solution["webApiPermissionRequests"] == [
        {"resource": "MAG-Agent-Backend-API", "scope": "user_impersonation"}
    ]


def test_spfx_npm_versions_are_synchronized():
    package = load_json(SPFX_ROOT / "package.json")
    lock = load_json(SPFX_ROOT / "package-lock.json")
    assert package["version"] == "0.1.4"
    assert lock["version"] == "0.1.4"
    assert lock["packages"][""]["version"] == "0.1.4"


def test_web_part_manifest_does_not_persist_function_key():
    manifest = load_json(
        SPFX_ROOT / "src" / "webparts" / "magApp" / "MagAppWebPart.manifest.json"
    )
    properties = manifest["preconfiguredEntries"][0]["properties"]
    assert "functionKey" not in properties
