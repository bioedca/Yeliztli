"""Source-level recurrence guard for #1949's two indel identities.

The coordinate/database oracle cannot see hard-coded behavior fixtures.  A real
indel paired with an arbitrary SNV genotype (for example rs80357906 + ``AG``)
looks plausible to type checkers while silently contradicting the record's
alleles.  Walk Python literal dictionaries and TypeScript object fixtures so
those false identities cannot return.

Source-native exceptions remain explicit:

* rs113993960 (CFTR F508del) accepts the documented array ``A/T`` probe calls
  and I/D tokens used by :mod:`backend.analysis.carrier_status`.
* rs80357906 may use the GRCh37 mapping genotype ``GGG/GGGG``; raw-input tests
  may use an unscoreable I/D token, and export tests may exercise the equivalent
  left-anchored VCF genotype ``T/TG``.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TARGET_RSIDS = {"rs113993960", "rs80357906"}

_CFTR_F508DEL_SOURCE_GENOTYPES = {"AT", "TA", "DI", "ID", "DD", "II"}
_RS80357906_DEFAULT_GENOTYPES = {"GGG/GGGG"}
_RS80357906_PATH_EXCEPTIONS = {
    "tests/backend/test_annotation_engine.py": {"DI"},
    "tests/backend/test_vep_bundle_lookup.py": {"DI"},
    "tests/backend/test_export.py": {"T/TG"},
    "tests/backend/test_fhir_export.py": {"T/TG"},
}

_TS_RSID = re.compile(r"\brsid\s*:\s*(['\"])(rs113993960|rs80357906)\1")
_TS_GENOTYPE = re.compile(r"\bgenotype\s*:\s*(['\"])([^'\"]+)\1")


def _allowed_genotypes(
    rsid: str,
    relative_path: str,
    function_name: str | None,
) -> set[str]:
    if rsid == "rs80357906":
        return _RS80357906_DEFAULT_GENOTYPES | _RS80357906_PATH_EXCEPTIONS.get(
            relative_path, set()
        )
    allowed = set(_CFTR_F508DEL_SOURCE_GENOTYPES)
    # This named negative test deliberately characterises an unsupported call.
    if function_name == "test_cftr_f508del_unknown_indel_call_stays_unresolved":
        allowed.add("CC")
    return allowed


class _PythonFixtureVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.function_names: list[str] = []
        self.checked: Counter[str] = Counter()
        self.failures: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_names.append(node.name)
        self.generic_visit(node)
        self.function_names.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _check_binding(self, literals: dict[str, str], lineno: int) -> None:
        rsid = literals.get("rsid")
        genotype = literals.get("genotype")
        if rsid not in TARGET_RSIDS or genotype is None:
            return
        function_name = self.function_names[-1] if self.function_names else None
        allowed = _allowed_genotypes(rsid, self.relative_path, function_name)
        self.checked[rsid] += 1
        if genotype not in allowed:
            self.failures.append(
                f"{self.relative_path}:{lineno} binds {rsid} to genotype "
                f"{genotype!r}; allowed here: {sorted(allowed)}"
            )

    def visit_Dict(self, node: ast.Dict) -> None:
        literals: dict[str, str] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                literals[key.value] = value.value

        self._check_binding(literals, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        literals = {
            keyword.arg: keyword.value.value
            for keyword in node.keywords
            if keyword.arg is not None
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        }
        self._check_binding(literals, node.lineno)
        self.generic_visit(node)


def _typescript_literal_bindings(path: Path) -> list[tuple[int, str, str]]:
    """Extract same-object literal rsid/genotype pairs using field indentation."""
    lines = path.read_text(encoding="utf-8").splitlines()
    bindings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        rsid_match = _TS_RSID.search(line)
        if rsid_match is None:
            continue
        rsid = rsid_match.group(2)
        field_indent = len(line) - len(line.lstrip())

        inline_genotype = _TS_GENOTYPE.search(line)
        if inline_genotype is not None:
            bindings.append((index + 1, rsid, inline_genotype.group(2)))
            continue

        # Fixture objects consistently align their direct fields. Stop at the
        # object's closing indentation so a sibling object's genotype cannot be
        # accidentally paired with this rsid.
        opening_indent: int | None = None
        opening_line: int | None = None
        for before in range(index - 1, -1, -1):
            candidate = lines[before]
            indent = len(candidate) - len(candidate.lstrip())
            if "{" in candidate and indent < field_indent:
                opening_indent = indent
                opening_line = before
                break
        if opening_indent is None or opening_line is None:
            continue

        for candidate_index in range(opening_line + 1, len(lines)):
            candidate = lines[candidate_index]
            indent = len(candidate) - len(candidate.lstrip())
            if indent == opening_indent and candidate.lstrip().startswith("}"):
                break
            if indent != field_indent:
                continue
            genotype_match = _TS_GENOTYPE.search(candidate)
            if genotype_match is not None:
                bindings.append((index + 1, rsid, genotype_match.group(2)))
                break
    return bindings


def test_python_hardcoded_indel_genotypes_follow_identity_contract() -> None:
    checked: Counter[str] = Counter()
    failures: list[str] = []
    for root in (REPO / "backend", REPO / "tests"):
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if not any(rsid in source for rsid in TARGET_RSIDS):
                continue
            relative_path = path.relative_to(REPO).as_posix()
            visitor = _PythonFixtureVisitor(relative_path)
            visitor.visit(ast.parse(source, filename=str(path)))
            checked.update(visitor.checked)
            failures.extend(visitor.failures)

    assert checked["rs80357906"] >= 10, "rs80357906 Python fixture guard became vacuous"
    assert checked["rs113993960"] >= 5, "rs113993960 Python fixture guard became vacuous"
    assert not failures, "hard-coded Python indel identity drift:\n" + "\n".join(failures)


def test_typescript_hardcoded_indel_genotypes_follow_identity_contract() -> None:
    checked: Counter[str] = Counter()
    failures: list[str] = []
    for root in (REPO / "frontend" / "src", REPO / "tests" / "e2e"):
        paths = (*root.rglob("*.ts"), *root.rglob("*.tsx"))
        for path in paths:
            source = path.read_text(encoding="utf-8")
            if not any(rsid in source for rsid in TARGET_RSIDS):
                continue
            relative_path = path.relative_to(REPO).as_posix()
            for line, rsid, genotype in _typescript_literal_bindings(path):
                checked[rsid] += 1
                allowed = _allowed_genotypes(rsid, relative_path, None)
                if genotype not in allowed:
                    failures.append(
                        f"{relative_path}:{line} binds {rsid} to genotype {genotype!r}; "
                        f"allowed here: {sorted(allowed)}"
                    )

    assert checked["rs80357906"] >= 5, "rs80357906 TypeScript fixture guard became vacuous"
    assert checked["rs113993960"] >= 4, "rs113993960 TypeScript fixture guard became vacuous"
    assert not failures, "hard-coded TypeScript indel identity drift:\n" + "\n".join(failures)
