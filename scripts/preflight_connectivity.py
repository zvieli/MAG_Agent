import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from src.operations.dependency_checker import DependencyChecker
except ModuleNotFoundError as exc:
    missing = exc.name or str(exc)
    print(
        json.dumps(
            {
                "status": "Unhealthy",
                "message": (
                    f"Missing Python dependency '{missing}'. Run this from the project "
                    "environment, for example: sudo docker compose exec function-app "
                    "python3 scripts/preflight_connectivity.py"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(1) from exc


async def main() -> int:
    checker = DependencyChecker()
    try:
        result = await checker.check_all(include_optional=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"Healthy", "Degraded"} and all(
            probe["status"] == "Healthy"
            for probe in result["dependencies"].values()
            if probe["required"]
        ) else 1
    finally:
        await checker.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
