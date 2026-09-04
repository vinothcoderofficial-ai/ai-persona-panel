"""Regenerate api/app/schemas.py from schemas/*.schema.json.

Run: python scripts/gen_schemas.py   (or: make gen-types, which also runs json2ts)

CLAUDE.md rule 4 says changing a schema means regenerating both
`web/src/contracts/` and `api/app/schemas.py` in the same commit. `json2ts`
handles the TypeScript side in one command; this script is the Python side.

Why a script and not one datamodel-codegen call: pointing the generator at the
`schemas/` directory with a single .py output fails on the pinned version
0.26.5 with "Modular references require an output directory, not a file".
As soon as more than one schema file is parsed it gives every model a module
path derived from its source file's stem (see `get_module_path` in
datamodel_code_generator/model/base.py) and then refuses to collapse those
modules into one file -- whether or not they actually cross-reference each
other. Ours never do; each schema only $refs its own #/definitions/.

So: generate one module per schema, then merge. The merge dedupes the import
lines and renames cross-file class-name collisions. Today the only collision is
`Type` -- event.schema.json's `Event.type` enum and planogram.schema.json's
`Bay.type` enum are both inline enums with no $ref, so the generator names both
"Type". The later one is prefixed with its schema's title, giving `EventType`.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
OUT = ROOT / "api" / "app" / "schemas.py"
CODEGEN = ROOT / ".venv" / "Scripts" / "datamodel-codegen.exe"

# Emitted in docs/SPEC.md section 4 order, not alphabetically, so the generated
# file reads in the same order as the contract it mirrors.
SCHEMA_ORDER = [
    "planogram",
    "variant",
    "session",
    "event",
    "simresult",
    "metrics",
    "persona",
    "policy",
    "prediction",
]

HEADER = '''\
# GENERATED FILE - DO NOT HAND-EDIT.
# Regenerate with: python scripts/gen_schemas.py
#
# Source: schemas/*.schema.json, the only cross-track contract (see CLAUDE.md).
# Changing a schema means regenerating this file and web/src/contracts/ in the
# same commit.
#
# One section per schema file, in docs/SPEC.md section 4 order.
'''


def codegen_binary() -> str:
    """The pinned datamodel-codegen, preferring the venv copy on Windows."""
    if CODEGEN.exists():
        return str(CODEGEN)
    return "datamodel-codegen"


def generate_one(schema_path: Path, out_path: Path) -> str:
    """Run datamodel-codegen for a single schema and return the module source."""
    subprocess.run(
        [
            codegen_binary(),
            "--input", str(schema_path),
            "--input-file-type", "jsonschema",
            "--output-model-type", "pydantic_v2.BaseModel",
            "--output", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path.read_text()


def split_module(source: str) -> tuple[dict[str, list[str]], str]:
    """Split a generated module into {import module: [names]} and its class body.

    The generator's own banner carries a timestamp, so it is dropped -- keeping
    it would make this script's output differ on every run.
    """
    imports: dict[str, list[str]] = {}
    body_lines: list[str] = []
    for line in source.splitlines():
        if line.startswith("#"):
            continue  # generator banner, including its timestamp
        match = re.match(r"^from ([\w.]+) import (.+)$", line)
        if match:
            module, names = match.group(1), match.group(2)
            imports.setdefault(module, []).extend(n.strip() for n in names.split(","))
            continue
        body_lines.append(line)
    return imports, "\n".join(body_lines).strip("\n")


def class_names(body: str) -> set[str]:
    return set(re.findall(r"^class (\w+)", body, flags=re.MULTILINE))


def rename_class(body: str, old: str, new: str) -> str:
    """Rename a class and every reference to it, within one module's body only."""
    return re.sub(rf"\b{re.escape(old)}\b", new, body)


def schema_title(schema_path: Path) -> str:
    """The schema's `title`, used to prefix a colliding class name."""
    import json

    return json.loads(schema_path.read_text())["title"]


def render_imports(imports: dict[str, list[str]]) -> str:
    """__future__ first, then stdlib, then third-party -- each group sorted."""
    third_party = {"pydantic"}
    future = {m: n for m, n in imports.items() if m == "__future__"}
    stdlib = {m: n for m, n in imports.items() if m != "__future__" and m not in third_party}
    external = {m: n for m, n in imports.items() if m in third_party}

    blocks = []
    for group in (future, stdlib, external):
        if not group:
            continue
        lines = [
            f"from {module} import {', '.join(sorted(set(names)))}"
            for module, names in sorted(group.items())
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def main() -> int:
    merged_imports: dict[str, list[str]] = {}
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()

    with tempfile.TemporaryDirectory() as tmp:
        for name in SCHEMA_ORDER:
            schema_path = SCHEMAS / f"{name}.schema.json"
            if not schema_path.exists():
                print(f"missing schema: {schema_path}", file=sys.stderr)
                return 1
            source = generate_one(schema_path, Path(tmp) / f"{name}.py")
            imports, body = split_module(source)

            for collision in sorted(class_names(body) & seen):
                renamed = f"{schema_title(schema_path)}{collision}"
                print(f"  collision: {name}.{collision} -> {renamed}")
                body = rename_class(body, collision, renamed)

            seen |= class_names(body)
            for module, names in imports.items():
                merged_imports.setdefault(module, []).extend(names)
            sections.append((name, body))

    rule = "# " + "-" * 77
    parts = [HEADER, "", render_imports(merged_imports), ""]
    for index, (name, body) in enumerate(sections):
        # Two blank lines before each section after the first, per PEP 8.
        lead = "" if index == 0 else "\n"
        parts.append(f"{lead}{rule}\n# from schemas/{name}.schema.json\n{rule}\n")
        parts.append(body)
        parts.append("")

    OUT.write_text("\n".join(parts).rstrip("\n") + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(sections)} schemas, {len(seen)} classes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
