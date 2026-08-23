import inspect
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import function_app


WRAPPER_NAME = "generated_function_entrypoints.py"
def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value.__class__.__name__ == "BindingDirection":
        return "in" if value.name == "IN" else "out"
    if hasattr(value, "value"):
        return value.value
    return value


def main() -> None:
    OUTPUT_DIR = ROOT / "src" / "operations"
    
    # Clean up existing generated function subdirectories
    if OUTPUT_DIR.exists():
        for path in OUTPUT_DIR.iterdir():
            if path.is_dir() and path.name not in ("__pycache__", "logs") and (path / "function.json").exists():
                shutil.rmtree(path, ignore_errors=True)

    wrapper_lines = [
        '"""Generated plain entry points for Azure Functions v1 metadata."""',
        "import sys",
        "from pathlib import Path",
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))",
        "import inspect",
        "import function_app",
        "",
        "",
        "def _unwrap(name):",
        "    candidate = getattr(function_app, name)",
        "    if hasattr(candidate, '_function'):",
        "        return candidate._function.get_user_function()",
        "    return candidate",
        "",
    ]

    for function in function_app.app.get_functions():
        function_name = function.get_function_name()
        function_dir = OUTPUT_DIR / function_name
        function_dir.mkdir(exist_ok=True)

        payload = _json_safe(function.get_dict_repr())
        payload["scriptFile"] = f"../{WRAPPER_NAME}"
        payload["entryPoint"] = function_name

        (function_dir / "function.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        user_function = getattr(function_app, function_name)._function.get_user_function()
        binding_names = {
            binding["name"]
            for binding in payload["bindings"]
            if binding.get("name") != "$return"
        }
        params_in_signature_order = [
            name
            for name in inspect.signature(user_function).parameters
            if name in binding_names
        ]
        params = ", ".join(params_in_signature_order)
        call_args = ", ".join(
            f"{name}={name}" for name in params_in_signature_order
        )
        original_name = f"_original_{function_name}"
        wrapper_lines.append(f"{original_name} = _unwrap({function_name!r})")
        if inspect.iscoroutinefunction(user_function):
            wrapper_lines.extend(
                [
                    f"async def {function_name}({params}):",
                    f"    return await {original_name}({call_args})",
                    "",
                ]
            )
        else:
            wrapper_lines.extend(
                [
                    f"def {function_name}({params}):",
                    f"    return {original_name}({call_args})",
                    "",
                ]
            )

    (OUTPUT_DIR / WRAPPER_NAME).write_text("\n".join(wrapper_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
