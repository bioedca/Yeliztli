"""Source-level recurrence guard for #1949's two indel identities.

The coordinate/database oracle cannot see hard-coded behavior fixtures.  A real
indel paired with an arbitrary SNV genotype (for example rs80357906 + ``AG``)
looks plausible to type checkers while silently contradicting the record's
alleles.  Walk Python literal dictionaries and TypeScript object fixtures so
those false identities cannot return.

Source-native exceptions remain explicit:

* rs113993960 (CFTR F508del) accepts the documented array ``A/T`` probe calls
  and I/D tokens used by :mod:`backend.analysis.carrier_status`.
* rs80357906 uses the GRCh37 mapping genotype ``GGG/GGGG`` except in the two
  named raw-input seed lists, which deliberately use an unscoreable I/D token.
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
_RS80357906_RAW_FIXTURE_EXCEPTIONS = {
    ("tests/backend/test_annotation_engine.py", "SEED_RAW_VARIANTS"): {"DI"},
    ("tests/backend/test_vep_bundle_lookup.py", "SEED_RAW_VARIANTS"): {"DI"},
}

_TS_RSID = re.compile(r"(?:\brsid|['\"]rsid['\"])\s*:\s*(['\"`])(rs113993960|rs80357906)\1")
_TS_GENOTYPE = re.compile(r"(?:\bgenotype|['\"]genotype['\"])\s*:\s*(['\"`])([^'\"`]+)\1")


def _allowed_genotypes(
    rsid: str,
    relative_path: str,
    function_name: str | None,
    assignment_name: str | None = None,
) -> set[str]:
    if rsid == "rs80357906":
        return _RS80357906_DEFAULT_GENOTYPES | _RS80357906_RAW_FIXTURE_EXCEPTIONS.get(
            (relative_path, assignment_name), set()
        )
    allowed = set(_CFTR_F508DEL_SOURCE_GENOTYPES)
    # This named negative test deliberately characterises an unsupported call.
    if function_name == "test_cftr_f508del_unknown_indel_call_stays_unresolved":
        allowed.add("CC")
    return allowed


class _PythonFixtureVisitor(ast.NodeVisitor):
    def __init__(
        self,
        relative_path: str,
        positional_helpers: dict[str, tuple[int, int]],
    ) -> None:
        self.relative_path = relative_path
        self.positional_helpers = positional_helpers
        self.function_names: list[str] = []
        self.assignment_names: list[str] = []
        self.checked: Counter[str] = Counter()
        self.failures: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_names.append(node.name)
        self.generic_visit(node)
        self.function_names.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        self.assignment_names.append(names[0] if len(names) == 1 else "")
        self.generic_visit(node)
        self.assignment_names.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        name = node.target.id if isinstance(node.target, ast.Name) else ""
        self.assignment_names.append(name)
        self.generic_visit(node)
        self.assignment_names.pop()

    def _check_binding(self, literals: dict[str, str], lineno: int) -> None:
        rsid = literals.get("rsid")
        genotype = literals.get("genotype")
        if rsid not in TARGET_RSIDS or genotype is None:
            return
        function_name = self.function_names[-1] if self.function_names else None
        assignment_name = self.assignment_names[-1] if self.assignment_names else None
        allowed = _allowed_genotypes(
            rsid,
            self.relative_path,
            function_name,
            assignment_name,
        )
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
        helper_name: str | None = None
        if isinstance(node.func, ast.Name):
            helper_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            helper_name = node.func.attr
        helper_positions = self.positional_helpers.get(helper_name or "")
        if helper_positions is not None:
            rsid_index, genotype_index = helper_positions
            for key, index in (("rsid", rsid_index), ("genotype", genotype_index)):
                if index >= len(node.args):
                    continue
                value = node.args[index]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    literals.setdefault(key, value.value)
        self._check_binding(literals, node.lineno)
        self.generic_visit(node)


def _positional_fixture_helpers(tree: ast.AST) -> dict[str, tuple[int, int]]:
    """Find unambiguous local helpers with positional rsid/genotype parameters."""
    candidates: dict[str, set[tuple[int, int]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        parameters = [*node.args.posonlyargs, *node.args.args]
        positions = {parameter.arg: index for index, parameter in enumerate(parameters)}
        if "rsid" not in positions or "genotype" not in positions:
            continue
        candidates.setdefault(node.name, set()).add((positions["rsid"], positions["genotype"]))
    return {name: next(iter(indexes)) for name, indexes in candidates.items() if len(indexes) == 1}


def _typescript_structure(
    source: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """Return object, comment, and string spans from a small TS lexer."""
    object_spans: list[tuple[int, int]] = []
    comment_spans: list[tuple[int, int]] = []
    string_spans: list[tuple[int, int]] = []
    stack: list[int] = []
    index = 0
    state = "code"
    quote = ""
    token_start = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                state = "line_comment"
                token_start = index
                index += 2
                continue
            if char == "/" and next_char == "*":
                state = "block_comment"
                token_start = index
                index += 2
                continue
            if char in "'\"`":
                state = "string"
                quote = char
                token_start = index
            elif char == "{":
                stack.append(index)
            elif char == "}" and stack:
                object_spans.append((stack.pop(), index))
        elif state == "string":
            if char == "\\":
                index += 2
                continue
            if char == quote:
                string_spans.append((token_start, index + 1))
                state = "code"
        elif state == "line_comment":
            if char == "\n":
                comment_spans.append((token_start, index))
                state = "code"
        elif state == "block_comment" and char == "*" and next_char == "/":
            comment_spans.append((token_start, index + 2))
            state = "code"
            index += 2
            continue
        index += 1
    if state == "line_comment":
        comment_spans.append((token_start, len(source)))
    elif state == "block_comment":
        comment_spans.append((token_start, len(source)))
    elif state == "string":
        string_spans.append((token_start, len(source)))
    return object_spans, comment_spans, string_spans


def _innermost_object(
    spans: list[tuple[int, int]],
    offset: int,
) -> tuple[int, int] | None:
    containing = (span for span in spans if span[0] < offset < span[1])
    return min(containing, key=lambda span: span[1] - span[0], default=None)


def _is_property_match(
    source: str,
    offset: int,
    comment_spans: list[tuple[int, int]],
    string_spans: list[tuple[int, int]],
) -> bool:
    if any(start <= offset < end for start, end in comment_spans):
        return False
    for start, end in string_spans:
        if not start <= offset < end:
            continue
        # A quoted property name legitimately begins at its own short string;
        # a JSON-looking snippet embedded inside a larger string does not.
        return offset == start and source[start + 1 : end - 1] in {"rsid", "genotype"}
    return True


def _typescript_literal_bindings(path: Path) -> list[tuple[int, str, str]]:
    """Extract literal rsid/genotype pairs from the same structural object."""
    source = path.read_text(encoding="utf-8")
    spans, comment_spans, string_spans = _typescript_structure(source)
    bindings: list[tuple[int, str, str]] = []
    for rsid_match in _TS_RSID.finditer(source):
        if not _is_property_match(
            source,
            rsid_match.start(),
            comment_spans,
            string_spans,
        ):
            continue
        object_span = _innermost_object(spans, rsid_match.start())
        if object_span is None:
            continue
        rsid = rsid_match.group(2)
        for genotype_match in _TS_GENOTYPE.finditer(
            source,
            object_span[0] + 1,
            object_span[1],
        ):
            if not _is_property_match(
                source,
                genotype_match.start(),
                comment_spans,
                string_spans,
            ):
                continue
            if _innermost_object(spans, genotype_match.start()) != object_span:
                continue
            line = source.count("\n", 0, rsid_match.start()) + 1
            bindings.append((line, rsid, genotype_match.group(2)))
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
            tree = ast.parse(source, filename=str(path))
            visitor = _PythonFixtureVisitor(
                relative_path,
                _positional_fixture_helpers(tree),
            )
            visitor.visit(tree)
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


def test_python_extractor_covers_positional_helpers_and_scopes_raw_exception() -> None:
    source = """
def _v(rsid, chrom, pos, genotype):
    return {"rsid": rsid, "genotype": genotype}

_v("rs113993960", "7", 117199645, "CT")
"""
    tree = ast.parse(source)
    visitor = _PythonFixtureVisitor("tests/example.py", _positional_fixture_helpers(tree))
    visitor.visit(tree)
    assert visitor.checked == Counter({"rs113993960": 1})
    assert len(visitor.failures) == 1
    assert "genotype 'CT'" in visitor.failures[0]

    raw_path = "tests/backend/test_annotation_engine.py"
    assert "DI" in _allowed_genotypes(
        "rs80357906",
        raw_path,
        None,
        "SEED_RAW_VARIANTS",
    )
    assert "DI" not in _allowed_genotypes(
        "rs80357906",
        raw_path,
        None,
        "ANNOTATED_VARIANTS",
    )
    assert "T/TG" not in _allowed_genotypes("rs80357906", "tests/backend/test_export.py", None)


def test_typescript_extractor_handles_literal_syntax_without_crossing_objects(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.ts"
    fixture.write_text(
        """
const quoted = {
  "genotype": "AG",
  "rsid": "rs80357906",
}
const sameLine = { rsid: `rs113993960`,
  genotype: `CT`,
}
const nestedSibling = {
  rsid: "rs80357906",
  nested: { genotype: "AG" },
  genotype: "GGG/GGGG",
}
const commentsAndStrings = {
  // rsid: "rs113993960",
  rsid: "rs80357906",
  // genotype: "AG",
  genotype: "GGG/GGGG",
  note: 'embedded "rsid": "rs113993960", "genotype": "CT"',
}
""",
        encoding="utf-8",
    )
    assert [(rsid, genotype) for _, rsid, genotype in _typescript_literal_bindings(fixture)] == [
        ("rs80357906", "AG"),
        ("rs113993960", "CT"),
        ("rs80357906", "GGG/GGGG"),
        ("rs80357906", "GGG/GGGG"),
    ]
