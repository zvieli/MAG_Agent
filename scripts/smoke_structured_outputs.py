"""Container build smoke test for the strict extraction runtime contract."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from importlib.metadata import version

import pydantic

from src.extraction.wire_schema import create_wire_extraction_model, lint_structured_output_schema
from src.models.models import SCHEMA_CONFIG


def main() -> None:
    assert version("openai") == "2.32.0", version("openai")
    assert int(pydantic.__version__.split(".", 1)[0]) == 2, pydantic.__version__
    model = create_wire_extraction_model(SCHEMA_CONFIG)
    lint_structured_output_schema(model.model_json_schema())

    # Client construction requires deployment secrets, so the Docker build checks
    # the SDK class API directly. Runtime preflight performs the live deployment call.
    from openai.resources.chat.completions.completions import AsyncCompletions

    assert callable(getattr(AsyncCompletions, "parse", None))
    print("strict-structured-outputs-smoke: ok")


if __name__ == "__main__":
    main()
