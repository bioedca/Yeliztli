#!/usr/bin/env python3
"""Validate a PR's declared route against live, trusted GitHub review state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

LEGACY_SCHEMA_MARKER = "<!-- review-route-schema:v1 -->"
SCHEMA_MARKER = "<!-- review-route-schema:v2 -->"
AUTONOMOUS_SCHEMA_MARKER = "<!-- review-route-schema:v3 -->"
REVIEW_SIGNAL_WORKFLOW = ".github/workflows/review-route-invalidation.yml"
ROUTE_RANK = {"Low": 0, "Standard": 1, "Load-bearing": 2}
COPILOT_GATE = "Copilot PR review"
CODEX_GATE = "Codex @codex review"
CODERABBIT_GATE = "Manual CodeRabbit reservation and @coderabbitai full review"
CODERABBIT_V3_GATE = "CodeRabbit structured clean review"
GREPTILE_GATE = "Greptile clean review check run"
HUMAN_GATE = "Independent human maintainer review"
BOT_GATES = (COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE)
GATES = (*BOT_GATES, HUMAN_GATE)
# Greptile is a v3-only lane. `BOT_GATES` is shared with the legacy human-gated
# schema, so adding it there would retroactively demand a fourth evidence row
# and a fourth reviewer checkbox from every open v2 pull request.
V3_BOT_GATES = (*BOT_GATES, GREPTILE_GATE)
AUTOMATED_REVIEW_CHOICES = {
    "Copilot": COPILOT_GATE,
    "Codex": CODEX_GATE,
    "CodeRabbit": CODERABBIT_GATE,
}
V3_AUTOMATED_REVIEW_CHOICES = {
    **AUTOMATED_REVIEW_CHOICES,
    "Greptile": GREPTILE_GATE,
}
BOT_ACTOR_IDS = {
    COPILOT_GATE: 175728472,  # Copilot GitHub App
    CODEX_GATE: 199175422,  # ChatGPT Codex connector GitHub App
    CODERABBIT_GATE: 136622811,  # CodeRabbit GitHub App
    GREPTILE_GATE: 165735046,  # Greptile GitHub App (`greptile-apps[bot]`)
}
# Never match Greptile on a login. A separate ordinary *User* account holds the
# bare login `greptile-apps` (id 244718400), and GraphQL reports the bot's own
# login without the `[bot]` suffix, so the two are string-identical there.
# `greptile[bot]` (id 271099122) is a third, inactive Greptile app.
GREPTILE_APP_ID = 867647
GREPTILE_CHECK_RUN_NAME = "Greptile Review"
# Greptile's own counter, in a fixed template. `M == 0` is the whole clean test:
# there is no sentence to match and no model-authored flourish to overflow, which
# is what makes this the only envelope here not keyed on provider prose.
# The reviewed-file count must be positive. Greptile applies its own path
# filters, so a pull request whose changed files are all filtered out can report
# `0 files reviewed, 0 comments added.` — a clean verdict over nothing, which on
# v3 carries a merge with no human approval behind it.
GREPTILE_CHECK_SUMMARY = re.compile(
    r"^Greptile has reviewed the Pull Request\.\n\n"
    r"(?P<files>[1-9][0-9]*) files? reviewed, "
    r"(?P<comments>0|[1-9][0-9]*) comments? added\.$"
)
# Greptile reads `greptile.json` and `.greptile/` from the pull request's own
# source branch (#2249), so a pull request can weaken the reviewer that is about
# to clear it. Trusted `main` protects the validator from that; nothing protects
# Greptile from it, so the lane refuses to review its own configuration.
GREPTILE_CONFIG_EXACT = frozenset({"greptile.json"})
GREPTILE_CONFIG_PREFIXES = (".greptile/",)
# This repository allows itself 16 Greptile reviews a month, and a re-trigger on
# every push is the cheapest way to spend them. Two is one review plus one
# re-review after fixing what it found.
GREPTILE_PR_REVIEW_CAP = 2
# Greptile bills completed reviews and says skipped ones don't count; a cancelled
# run never finished either. Everything else did the work and spent the credit,
# including a `NEUTRAL` confidence-threshold refusal and an in-flight run that
# has no conclusion yet, so the count deliberately errs high.
GREPTILE_UNBILLED_CONCLUSIONS = frozenset({"CANCELLED", "SKIPPED"})
GREPTILE_LEDGER_UNAVAILABLE = "Greptile per-pull-request check-run ledger is unavailable"
GREPTILE_LEDGER_TRUNCATED = "commit pagination cannot prove the Greptile per-pull-request budget"
GREPTILE_LEDGER_FORCE_PUSHED = (
    "Greptile per-pull-request budget cannot be proven after a force push; "
    "select another hosted reviewer"
)
# Entries are compared against `path.lower()`, so every member must be lowercase.
# `agents.md` and `claude.md` are the fleet contract that gates every other
# change, including merge authorisation. Both route tables call an edit to them
# Load-bearing, but until they were listed here the floor said `Low` -- they are
# `.md` and matched nothing else -- and the two halves of the repository
# disagreed with each other. Precedent split along exactly that line: #2197
# routed Load-bearing and #2199 routed Low, both agent-contract edits, both
# valid, because the floor is a minimum and either route sits at or above it.
# Listing them makes the classification machine-checked instead of advisory
# (#2259).
LOAD_BEARING_EXACT = {
    ".coderabbit.yaml",
    ".gitattributes",
    ".gitignore",
    ".gitmodules",
    ".graphifyignore",
    ".github/codeowners",
    ".github/copilot-instructions.md",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    "agents.md",
    "changelog.md",
    "code_of_conduct.md",
    "citation.cff",
    "claude.md",
    "contributing.md",
    "dockerfile",
    "governance.md",
    "greptile.json",
    "license",
    "makefile",
    "notice",
    "readme.md",
    "security.md",
    "support.md",
    "alembic.ini",
    "backend/config.py",
    "backend/__init__.py",
    "backend/disclaimers.py",
    "backend/logging_config.py",
    "backend/main.py",
    "docs/bundle-release-runbook.md",
    "docs/assets/img/dashboard.png",
    "docs/attribution.md",
    "docs/external-inputs-strategy.md",
    "docs/index.md",
    "docs/internal/sex_inference_threshold_validation.md",
    "docs/lai-bundle-release-runbook-env.lock.yaml",
    "docs/lai-bundle-release-runbook.md",
    "frontend/src/app.tsx",
    "frontend/.gitignore",
    "frontend/index.html",
    "frontend/knip.jsonc",
    "frontend/src/main.tsx",
    "mkdocs.yml",
    "package-lock.json",
    "package.json",
    "playwright.config.ts",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/backend/test_review_route_policy.py",
    "vulture_whitelist.py",
}
LOAD_BEARING_NAMES = {
    ".coveragerc",
    ".npmrc",
    ".nvmrc",
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    ".python-version",
    ".yarnrc",
    ".yarnrc.yml",
    "codeowners",
    "pipfile",
    "pipfile.lock",
    "conda-lock.yaml",
    "conda-lock.yml",
    "environment.yaml",
    "environment.yml",
    "mypy.ini",
    "noxfile.py",
    "poetry.lock",
    "pytest.ini",
    "renovate.json",
    "ruff.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "uv.lock",
}
LOAD_BEARING_PREFIXES = (
    ".github/",
    # Greptile reads `.greptile/` in preference to the root `greptile.json`, so
    # a file here can override the manual-only review guard. Without this the
    # override would ride in on Standard, or on Low for `.greptile/rules.md`.
    ".greptile/",
    "alembic/",
    "bundles/",
    "data/",
    "scripts/",
    "backend/analysis/",
    "backend/annotation/",
    "backend/api/",
    "backend/auth",
    "backend/data/",
    "backend/db/",
    "backend/ingestion/",
    "backend/installer",
    "backend/models/",
    "backend/query/",
    "backend/reports/",
    "backend/security",
    "backend/services/",
    "backend/tasks/",
    "backend/updater",
    "backend/utils/",
    "docs/_includes/",
    "docs/ancestry-methods",
    "docs/develop/",
    "docs/features/",
    "docs/getting-started/",
    "docs/install/",
    "docs/internal/",
    "docs/intended-use",
    "docs/maintainer/",
    "docs/modules/",
    "docs/privacy",
    "docs/release-notes/",
    "docs/science",
    # Product pages, rendered result components, result contracts, and their
    # tests can change scientific/clinical meaning even when the filename is
    # generic. Keep these structural surfaces fail-closed; routine hook and
    # styling changes outside them remain eligible for Standard.
    "frontend/scripts/",
    "frontend/src/api/",
    "frontend/src/components/",
    "frontend/src/constants/",
    "frontend/src/lib/",
    "frontend/src/pages/",
    "frontend/src/test/",
    "frontend/src/types/",
    "launchd/",
    "systemd/",
    "tests/backend/",
    "tests/e2e/",
    "tests/fixtures/",
    "tests/manual/",
)
LOAD_BEARING_TEST_TOKENS = (
    "acmg",
    "allele",
    "annotation",
    "ancestry",
    "apoe",
    "auth",
    "backup",
    "bundle",
    "cancer",
    "cadd",
    "carrier",
    "cardiovascular",
    "citation",
    "clinical",
    "clinvar",
    "config",
    "cpic",
    "database",
    "delete",
    "disclaimer",
    "ebmd",
    "evidence",
    "export",
    "finding",
    "gate",
    "gene",
    "genomic",
    "genome",
    "gnomad",
    "gwas",
    "haplogroup",
    "hgvs",
    "hla",
    "hpo",
    "imputation",
    "insilico",
    "ingestion",
    "install",
    "kinship",
    "liftover",
    "logging",
    "merge",
    "metabolic",
    "methylation",
    "migration",
    "nuclear",
    "nutrigenomics",
    "panel",
    "pathogenic",
    "parkinsons",
    "pathway",
    "pgx",
    "pharmacogen",
    "privacy",
    "provenance",
    "prs",
    "query",
    "qc",
    "reference",
    "release",
    "report",
    "revel",
    "restore",
    "risk",
    "roh",
    "sample",
    "schema",
    "security",
    "setup",
    "sex",
    "splice",
    "sql",
    "stale",
    "update",
    "updater",
    "upload",
    "variant",
    "workflow",
    "zygosity",
)
LOW_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
UTC_RESULT = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+[—-]\s+"
    r"(?P<result>COMPLETE|APPROVED)$"
)
TERMINAL_REVIEW_STATES = {"APPROVED", "COMMENTED"}
HUMAN_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
# Copilot's review body is the ONLY thing distinguishing a review that found
# nothing from one that never ran: a quota refusal is also a COMMENTED review
# with zero attached comments, from the same authenticated app, immutable and
# exact-head. 42 of the 46 Copilot submissions this repository has received are
# that refusal, so accepting on authenticated fields alone would accept them.
#
# Exactly one thing is therefore read from the body — the coverage sentence
# Copilot closes with — and nothing about where it sits. An earlier revision of
# this envelope tried to bind it to Copilot's "### Reviewed changes" section, to
# stop a pull request whose own files contain the sentence from inducing Copilot
# to echo it. Holding that line meant deciding where the section ended, which
# meant modelling every construct GitHub renders as a break: ATX and Setext
# headings, <h2>, <hr>, </details>, blockquoted and list-item headings, then
# unpaired closing tags, inline code spans, autolinks, <?processing ?> and
# <![CDATA[]]>. That is a CommonMark and raw-HTML parser, arrived at one review
# finding at a time, and each addition was a fresh chance to reject a real
# review — one already did (#2248 review rounds 3-10).
#
# The file counts are deliberately NOT compared against changedFiles any more.
# They were only ever asserted in that same prose, so a count taken from it is
# not a stronger guarantee than the prose itself; keeping the comparison bought
# no trust while forcing the parser that caused the churn. What remains is the
# provider-authored `no comments` verdict, occurring exactly once so an echo
# cannot sit alongside the real footer, on top of GitHub's own authenticated
# attached-comment count. Trailing whitespace is tolerated because markdown
# treats it as insignificant and rejecting on it would kill the lane over
# something invisible.
COPILOT_V3_COVERAGE_LINE = re.compile(
    r"(?m)^Copilot reviewed (?P<reviewed>[1-9][0-9]*) out of (?P<total>[1-9][0-9]*) "
    r"changed files in this pull request and generated "
    r"(?P<verdict>no comments|[1-9][0-9]* comments?)\.[ \t]*$"
)
COPILOT_V3_CLEAN_VERDICT = "no comments"
# Copilot can withhold low-confidence findings instead of posting them, and a
# withheld finding never becomes an attached comment. There is deliberately no
# prose scan for that: Copilot paraphrases the diff it read, so any pattern wide
# enough to catch the wording variants also rejects a clean review of a change
# that merely discusses suppression. Tracked in #2256.
CODEX_CLEAN_COMPLETION_PREFIX = "Codex Review: Didn't find any major issues."
CODEX_CLEAN_COMPLETION_MARKER = f"{CODEX_CLEAN_COMPLETION_PREFIX} What shall we delve into next?"
# The trailing flourish is unbounded on purpose. Nothing downstream reads it:
# the verdict is the prefix, the author is the app id plus the `Bot` typename,
# immutability is `created == updated` with a null `lastEditedAt`, and the head
# binding is the `**Reviewed commit:**` marker. A length cap therefore
# constrained only how verbose Codex chose to be about why it found nothing --
# and when it wrote three sentences instead of one (286 characters on PR #2254),
# its own canonical clean comment was rejected and the pull request could not
# finalize on that head. Same class as #2248: pinning provider prose kills a
# working lane silently, because a v3 validation failure publishes `pending`.
# What is still enforced is that the verdict occupies a single line, so a
# multi-paragraph comment cannot be read as a terse clean verdict. Tracked in
# #2255.
CODEX_CLEAN_COMPLETION_LINE = re.compile(
    rf"^{re.escape(CODEX_CLEAN_COMPLETION_PREFIX)}(?: [^\r\n]+)?$"
)
CODEX_REVIEWED_COMMIT_LINE = re.compile(
    r"(?m)^\*\*Reviewed commit:\*\* `(?P<sha>[0-9a-f]{10})`\s*$"
)
CODEX_TRIGGER = "@codex review"
CODERABBIT_COMPLETION_MARKER = "auto-generated comment by CodeRabbit for review status"
CODERABBIT_ACTIONABLE_COUNT = re.compile(
    r"(?m)^\*\*Actionable comments posted: (?P<count>0|[1-9][0-9]*)\*\*$"
)
CODERABBIT_IGNORED_COUNT = re.compile(
    r"(?m)^<summary>⛔ Files ignored due to path filters "
    r"\((?P<count>0|[1-9][0-9]*)\)</summary>$"
)
CODERABBIT_SELECTED_COUNT = re.compile(
    r"(?m)^<summary>📒 Files selected for processing "
    r"\((?P<count>[1-9][0-9]*)\)</summary>$"
)
HUMAN_OPINION_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
FINALIZE_COMMAND = "/validate-route"


def _gates_for_schema(schema_version: int | None) -> tuple[str, ...]:
    return V3_BOT_GATES if schema_version == 3 else GATES


def _gate_labels_for_schema(schema_version: int | None) -> tuple[str, ...]:
    if schema_version == 3:
        return (COPILOT_GATE, CODEX_GATE, CODERABBIT_V3_GATE, GREPTILE_GATE)
    return GATES


def _review_choices_for_schema(schema_version: int | None) -> dict[str, str]:
    return V3_AUTOMATED_REVIEW_CHOICES if schema_version == 3 else AUTOMATED_REVIEW_CHOICES


def _changes_greptile_config(files: list[ChangedFile]) -> bool:
    """Whether the diff can alter the configuration Greptile reviews under."""
    for changed in files:
        for path in (changed.filename, changed.previous_filename):
            if not path:
                continue
            # `lstrip("./")` would strip the leading dot of `.greptile/`.
            lowered = path.lower().removeprefix("./")
            if lowered in GREPTILE_CONFIG_EXACT or lowered.startswith(GREPTILE_CONFIG_PREFIXES):
                return True
    return False


def _is_optional_gate(gate: str, schema_version: int | None) -> bool:
    """Whether a gate may be absent from an otherwise valid body of this schema.

    Greptile joined schema v3 after v3 pull requests were already open. Making
    its checkbox and evidence row mandatory would have broken the route on every
    one of them the moment the lane merged, and each body edit needed to repair
    one re-pends that route and spends a fresh hosted review. A body written
    against the three-provider template stays valid; it simply cannot select
    Greptile.
    """
    return schema_version == 3 and gate == GREPTILE_GATE


def _present_gates(
    schema_version: int | None, evidence: dict[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the gates and labels this particular body is required to carry."""
    kept = [
        (gate, label)
        for gate, label in zip(
            _gates_for_schema(schema_version),
            _gate_labels_for_schema(schema_version),
            strict=True,
        )
        if not _is_optional_gate(gate, schema_version) or gate in evidence
    ]
    return tuple(gate for gate, _ in kept), tuple(label for _, label in kept)


@dataclass(frozen=True)
class Evidence:
    applies_to: str
    head: str
    status: str


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    previous_filename: str | None = None


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _markdown_without_code(body: str) -> str:
    visible_lines: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in body.splitlines():
        if fence_char is not None:
            closing = re.fullmatch(
                rf"[ ]{{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*", line
            )
            if closing:
                fence_char = None
                fence_length = 0
            visible_lines.append("")
            continue
        opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)
        if opening:
            fence = opening.group(1)
            info = opening.group(2)
            if fence.startswith("`") and "`" in info:
                visible_lines.append(line)
                continue
            fence_char = fence[0]
            fence_length = len(fence)
            visible_lines.append("")
            continue
        visible_lines.append("" if line.startswith(("    ", "\t")) else line)
    return "\n".join(visible_lines)


def _route_structure_markdown(body: str) -> str:
    """Strip HTML comments while retaining standalone schema-marker comments."""
    schema_line = re.compile(r"(?i)^<!-- review-route-schema:v[123] -->\s*$")
    visible_lines: list[str] = []
    in_comment = False
    for line in _markdown_without_code(body).splitlines():
        if not in_comment and schema_line.fullmatch(line):
            visible_lines.append(line)
            continue
        cursor = 0
        visible: list[str] = []
        while cursor < len(line):
            if in_comment:
                close = line.find("-->", cursor)
                if close < 0:
                    cursor = len(line)
                    continue
                in_comment = False
                cursor = close + 3
                continue
            opening = line.find("<!--", cursor)
            if opening < 0:
                visible.append(line[cursor:])
                break
            visible.append(line[cursor:opening])
            close = line.find("-->", opening + 4)
            if close < 0:
                in_comment = True
                cursor = len(line)
            else:
                cursor = close + 3
        visible_lines.append("".join(visible))
    return "\n".join(visible_lines)


def _visible_markdown(body: str) -> str:
    return re.sub(r"<!--.*?-->", "", _route_structure_markdown(body), flags=re.DOTALL)


RENDER_NONCE = re.compile(r"review-route-render-bind-[0-9a-f]{48}")
RENDERED_ROUTE_ERROR = "GitHub-rendered review route is not visibly bound at the document root"


def render_probe_body(body: str, nonce: str) -> str:
    """Bind a one-use marker to the source route before GitHub renders it."""
    if RENDER_NONCE.fullmatch(nonce) is None:
        raise ValueError("render nonce has an invalid shape")
    marker = re.compile(
        r"(?mi)^## Review route[ \t]*\n(?:[ \t]*\n)*"
        r"<!-- review-route-schema:v[123] -->[ \t]*$"
    )
    structure = _route_structure_markdown(body)
    matches = list(marker.finditer(structure))
    if len(matches) != 1:
        raise ValueError("cannot bind rendered output to exactly one source route marker")
    marker_line = structure[: matches[0].end()].count("\n")
    original_lines = body.splitlines(keepends=True)
    if marker_line >= len(original_lines):
        raise ValueError("cannot bind rendered output to the source route marker")
    end = sum(len(line) for line in original_lines[:marker_line]) + len(
        original_lines[marker_line].rstrip("\n")
    )
    return f"{body[:end]}\n\n{nonce}\n{body[end:]}"


def _rendered_route_errors(
    rendered_html: str,
    nonce: str,
    route: str | None,
    schema_version: int | None,
    selected_bots: list[str],
    evidence: dict[str, Evidence],
) -> list[str]:
    """Require the source-bound route controls to render as one exact root section."""
    if RENDER_NONCE.fullmatch(nonce) is None:
        return ["render nonce has an invalid shape"]

    void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    foreign_roots = {"math", "svg"}
    foreign_breakout_tags = {
        "b",
        "big",
        "blockquote",
        "body",
        "br",
        "center",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "embed",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "hr",
        "i",
        "img",
        "li",
        "listing",
        "menu",
        "meta",
        "nobr",
        "ol",
        "p",
        "pre",
        "ruby",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tt",
        "u",
        "ul",
        "var",
    }
    mathml_text_integration_points = {"mi", "mn", "mo", "ms", "mtext"}
    mathml_text_integration_exceptions = {"malignmark", "mglyph"}
    svg_html_integration_points = {"desc", "foreignobject", "title"}
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    scope_boundaries = {
        "annotation-xml",
        "applet",
        "caption",
        "desc",
        "foreignobject",
        "html",
        "marquee",
        "math",
        "mi",
        "mn",
        "mo",
        "ms",
        "mtext",
        "object",
        "svg",
        "table",
        "td",
        "template",
        "th",
        "title",
    }
    paragraph_closing_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "center",
        "details",
        "dialog",
        "dir",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "search",
        "section",
        "summary",
        "table",
        "ul",
    }
    # ``HTMLParser`` has no HTML5 insertion modes. Treat special elements as
    # conservative barriers so malformed rendered fragments fail closed.
    special_elements = (
        paragraph_closing_tags
        | scope_boundaries
        | {
            "body",
            "button",
            "colgroup",
            "dd",
            "dt",
            "form",
            "frameset",
            "head",
            "iframe",
            "li",
            "listing",
            "noembed",
            "noframes",
            "noscript",
            "plaintext",
            "script",
            "select",
            "style",
            "tbody",
            "textarea",
            "tfoot",
            "thead",
            "tr",
            "xmp",
        }
    )
    active_formatting_elements = {
        "a",
        "b",
        "big",
        "code",
        "em",
        "font",
        "i",
        "nobr",
        "s",
        "small",
        "strike",
        "strong",
        "tt",
        "u",
    }
    containment_boundaries = special_elements | active_formatting_elements
    optional_end_children = {
        "dl": {"dd", "dt"},
        "menu": {"li"},
        "ol": {"li"},
        "optgroup": {"option"},
        "ruby": {"rb", "rp", "rt", "rtc"},
        "select": {"optgroup", "option"},
        "table": {"colgroup", "tbody", "td", "tfoot", "th", "thead", "tr"},
        "tbody": {"td", "th", "tr"},
        "tfoot": {"td", "th", "tr"},
        "thead": {"td", "th", "tr"},
        "tr": {"td", "th"},
        "ul": {"li"},
    }
    for parent in paragraph_closing_tags | {"body", "form"}:
        optional_end_children.setdefault(parent, set()).add("p")

    class RenderedParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.elements: list[dict[str, Any]] = []
            self.stack: list[dict[str, Any]] = []

        def start_tag_is_foreign(
            self,
            tag: str,
            attributes: dict[str, str | None],
        ) -> bool:
            if tag in foreign_roots:
                return True
            if not self.stack or not self.stack[-1]["foreign"]:
                return False
            if tag in foreign_breakout_tags or (
                tag == "font" and {"color", "face", "size"} & attributes.keys()
            ):
                return False
            parent = self.stack[-1]
            if parent["tag"] in svg_html_integration_points:
                return False
            if (
                parent["tag"] in mathml_text_integration_points
                and tag not in mathml_text_integration_exceptions
            ):
                return False
            if parent["tag"] == "annotation-xml" and (
                (parent["attrs"].get("encoding") or "").strip().lower()
                in {"application/xhtml+xml", "text/html"}
            ):
                return False
            return True

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if tag in paragraph_closing_tags:
                self.close_open("p", containment_boundaries)
            if tag in heading_tags and self.stack and self.stack[-1]["tag"] in heading_tags:
                self.stack.pop()
            attributes: dict[str, str | None] = {}
            for name, value in attrs:
                attributes.setdefault(name.lower(), value)
            node: dict[str, Any] = {
                "attrs": attributes,
                "checkboxes": [],
                "foreign": self.start_tag_is_foreign(tag, attributes),
                "order": len(self.elements),
                "parent": self.stack[-1] if self.stack else None,
                "tag": tag,
                "text": [],
            }
            self.elements.append(node)
            if tag == "input":
                if (attributes.get("type") or "").lower() == "checkbox":
                    owner = next(
                        (ancestor for ancestor in reversed(self.stack) if ancestor["tag"] == "li"),
                        None,
                    )
                    for ancestor in reversed(self.stack):
                        if ancestor["tag"] == "ul":
                            ancestor["checkboxes"].append(
                                {
                                    "checked": "checked" in attributes,
                                    "node": node,
                                    "owner": owner,
                                    "text": [],
                                }
                            )
                            break
            if tag not in void_tags:
                self.stack.append(node)

        def close_open(self, tag: str, boundaries: set[str]) -> None:
            self.close_open_any({tag}, boundaries)

        def close_open_any(self, tags: set[str], boundaries: set[str]) -> None:
            for index in range(len(self.stack) - 1, -1, -1):
                open_tag = self.stack[index]["tag"]
                if open_tag in tags:
                    del self.stack[index:]
                    return
                if open_tag in boundaries:
                    return

        def close_end_tag(self, tag: str) -> None:
            optional = optional_end_children.get(tag, set())
            parent_index = len(self.stack) - 1
            while parent_index >= 0 and self.stack[parent_index]["tag"] in optional:
                parent_index -= 1
            if parent_index >= 0 and self.stack[parent_index]["tag"] == tag:
                del self.stack[parent_index:]

        def handle_startendtag(  # noqa
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            tag = tag.lower()
            self.handle_starttag(tag, attrs)
            if self.stack and self.stack[-1]["tag"] == tag and self.stack[-1]["foreign"]:
                self.stack.pop()

        def handle_endtag(self, tag: str) -> None:  # noqa
            tag = tag.lower()
            if tag in heading_tags:
                self.close_open_any(heading_tags, containment_boundaries)
            elif tag == "p":
                self.close_open("p", containment_boundaries)
            else:
                self.close_end_tag(tag)

        def handle_data(self, data: str) -> None:  # noqa
            if not self.stack and data.strip():
                self.elements.append(
                    {
                        "checkboxes": [],
                        "attrs": {},
                        "order": len(self.elements),
                        "parent": None,
                        "tag": "#text",
                        "text": [data],
                    }
                )
                return
            for node in self.stack:
                node["text"].append(data)
                if node["tag"] == "ul" and node["checkboxes"]:
                    node["checkboxes"][-1]["text"].append(data)

    parser = RenderedParser()
    try:
        parser.feed(rendered_html)
        parser.close()
    except (AssertionError, ValueError):
        return [RENDERED_ROUTE_ERROR]

    def text(node: dict[str, Any]) -> str:
        return re.sub(r"\s+", " ", " ".join(node["text"])).strip()

    roots = [node for node in parser.elements if node["parent"] is None]
    nonce_nodes = [node for node in roots if node["tag"] == "p" and text(node) == nonce]
    route_headings = [
        node for node in parser.elements if node["tag"] == "h2" and text(node) == "Review route"
    ]
    if len(nonce_nodes) != 1 or len(route_headings) != 1:
        return [RENDERED_ROUTE_ERROR]
    nonce_node = nonce_nodes[0]
    heading = route_headings[0]
    if heading["parent"] is not None:
        return [RENDERED_ROUTE_ERROR]

    nonce_index = next(
        (index for index, node in enumerate(roots) if node is nonce_node),
        None,
    )
    if nonce_index is None or nonce_index == 0 or roots[nonce_index - 1] is not heading:
        return [RENDERED_ROUTE_ERROR]
    following_heading_index = next(
        (
            index
            for index, node in enumerate(roots[nonce_index + 1 :], nonce_index + 1)
            if node["tag"] == "h2"
        ),
        len(roots),
    )
    section_roots = roots[nonce_index + 1 : following_heading_index]
    table_tags = {
        "table",
        "markdown-accessibility-table",
        "markdown-accessiblity-table",
    }
    root_tags = [node["tag"] for node in section_roots]
    separate_groups = root_tags[:2] == ["ul", "ul"] and len(root_tags) == 3
    combined_groups = root_tags[:1] == ["ul"] and len(root_tags) == 2
    if (
        not section_roots
        or section_roots[-1]["tag"] not in table_tags
        or (schema_version in {2, 3} and not (separate_groups or combined_groups))
        or (schema_version not in {2, 3} and root_tags != ["ul", section_roots[-1]["tag"]])
    ):
        return [RENDERED_ROUTE_ERROR]

    def descends_from(node: dict[str, Any], ancestor: dict[str, Any]) -> bool:
        parent = node["parent"]
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent["parent"]
        return False

    def exact_checklist(node: dict[str, Any], expected_count: int) -> bool:
        items = [
            candidate
            for candidate in parser.elements
            if candidate["tag"] == "li" and candidate["parent"] is node
        ]
        checkboxes = node["checkboxes"]
        if (
            len(items) != expected_count
            or len(checkboxes) != expected_count
            or [checkbox["owner"] for checkbox in checkboxes] != items
        ):
            return False
        paragraphs = [
            candidate
            for candidate in parser.elements
            if candidate["tag"] == "p" and descends_from(candidate, node)
        ]
        if paragraphs:
            if (
                len(paragraphs) != expected_count
                or [paragraph["parent"] for paragraph in paragraphs] != items
                or [checkbox["node"]["parent"] for checkbox in checkboxes] != paragraphs
            ):
                return False
        elif [checkbox["node"]["parent"] for checkbox in checkboxes] != items:
            return False
        nested_blocks = {
            "blockquote",
            "details",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ol",
            "pre",
            "table",
            "ul",
        }
        return not any(
            candidate["tag"] in nested_blocks and descends_from(candidate, node)
            for candidate in parser.elements
        )

    checklist_nodes = section_roots[:-1]
    # The rendered controls must match the source body this was parsed from,
    # which on v3 may or may not carry the optional Greptile pair.
    present_gates, present_labels = _present_gates(schema_version, evidence)
    provider_count = sum(1 for gate in present_gates if gate != HUMAN_GATE)
    if schema_version in {2, 3} and combined_groups:
        expected_checklist_sizes = [3 + provider_count]
    elif schema_version in {2, 3}:
        expected_checklist_sizes = [3, provider_count]
    else:
        expected_checklist_sizes = [3] * len(checklist_nodes)
    if any(
        not exact_checklist(node, expected_count)
        for node, expected_count in zip(
            checklist_nodes,
            expected_checklist_sizes,
            strict=True,
        )
    ):
        return [RENDERED_ROUTE_ERROR]

    def task_rows(checkboxes: list[dict[str, Any]], labels: list[str]) -> list[str] | None:
        item_labels: list[str] = []
        checked: list[str] = []
        for label, checkbox in zip(labels, checkboxes, strict=False):
            item = re.sub(r"\s+", " ", " ".join(checkbox["text"])).strip()
            item_labels.append(item.split(" —", maxsplit=1)[0])
            if checkbox["checked"]:
                checked.append(label)
        if len(checkboxes) != len(labels) or item_labels != labels:
            return None
        return checked

    route_labels = ["Low", "Standard", "Load-bearing"]
    first_checkboxes = section_roots[0]["checkboxes"]
    route_checkboxes = first_checkboxes[:3] if combined_groups else first_checkboxes
    checked_routes = task_rows(route_checkboxes, route_labels)
    if checked_routes is None or (route is not None and checked_routes != [route]):
        return [RENDERED_ROUTE_ERROR]

    if schema_version in {2, 3}:
        choices = {
            label: gate
            for label, gate in _review_choices_for_schema(schema_version).items()
            if gate in present_gates
        }
        provider_labels = list(choices)
        provider_checkboxes = (
            first_checkboxes[3:] if combined_groups else section_roots[1]["checkboxes"]
        )
        checked_providers = task_rows(provider_checkboxes, provider_labels)
        if checked_providers is None:
            return [RENDERED_ROUTE_ERROR]
        gate_to_label = {gate: label for label, gate in choices.items()}
        expected_providers = [
            gate_to_label[gate]
            for gate in _gates_for_schema(schema_version)
            if gate in selected_bots and gate in gate_to_label
        ]
        if checked_providers != expected_providers:
            return [RENDERED_ROUTE_ERROR]

    table_container = section_roots[-1]
    if table_container["tag"] == "table":
        table = table_container
    else:
        tables = [
            node
            for node in parser.elements
            if node["tag"] == "table" and node["parent"] is table_container
        ]
        if len(tables) != 1:
            return [RENDERED_ROUTE_ERROR]
        table = tables[0]

    def nearest_parent(node: dict[str, Any], tag: str) -> dict[str, Any] | None:
        parent = node["parent"]
        while parent is not None and parent["tag"] != tag:
            parent = parent["parent"]
        return parent

    rows = [
        node
        for node in parser.elements
        if node["tag"] == "tr" and nearest_parent(node, "table") is table
    ]
    rendered_rows: list[list[str]] = []
    rendered_cells: list[list[dict[str, Any]]] = []
    for row in rows:
        cells = [
            node
            for node in parser.elements
            if node["tag"] in {"td", "th"} and nearest_parent(node, "tr") is row
        ]
        rendered_cells.append(cells)
        rendered_rows.append([text(cell) for cell in cells])

    header = [
        "Required review gate",
        "Applies to",
        "Head SHA or N/A",
        "UTC time and status, or N/A",
    ]
    expected_gates = present_gates
    expected_labels = present_labels
    if (
        len(rendered_rows) != len(expected_gates) + 1
        or rendered_rows[0] != header
        or any(len(row) != 4 for row in rendered_rows[1:])
        or [row[0] for row in rendered_rows[1:]] != list(expected_labels)
    ):
        return [RENDERED_ROUTE_ERROR]
    if len(evidence) == len(expected_gates):
        expected_rows = [
            [
                label,
                _normalise_gate(evidence[gate].applies_to),
                _normalise_gate(evidence[gate].head),
                _normalise_gate(evidence[gate].status),
            ]
            for gate, label in zip(expected_gates, expected_labels, strict=True)
        ]

        def rendered_head_matches(cell: dict[str, Any], expected: str) -> bool:
            actual = text(cell)
            if actual == expected:
                return True
            if re.fullmatch(r"[0-9a-fA-F]{40}", expected) is None:
                return False
            links = [
                node
                for node in parser.elements
                if node["tag"] == "a" and descends_from(node, cell)
            ]
            if len(links) != 1:
                return False
            link = links[0]
            label = text(link)
            href = link["attrs"].get("href") or ""
            classes = (link["attrs"].get("class") or "").split()
            return (
                actual == label
                and 7 <= len(label) <= 40
                and expected.lower().startswith(label.lower())
                and "commit-link" in classes
                and re.fullmatch(
                    rf"https://github\.com/[^/]+/[^/]+/commit/{re.escape(expected)}",
                    href,
                    flags=re.IGNORECASE,
                )
                is not None
            )

        if any(
            rendered_row[0] != expected_row[0]
            or rendered_row[1] != expected_row[1]
            or not rendered_head_matches(cells[2], expected_row[2])
            or rendered_row[3] != expected_row[3]
            for rendered_row, cells, expected_row in zip(
                rendered_rows[1:], rendered_cells[1:], expected_rows, strict=True
            )
        ):
            return [RENDERED_ROUTE_ERROR]
    return []


def _normalise_gate(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("`", "").strip())


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_route_section(
    body: str,
) -> tuple[str | None, int | None, list[str], dict[str, Evidence], list[str]]:
    errors: list[str] = []
    marker_pattern = re.compile(
        r"(?mi)^## Review route\s*\n"
        r"<!-- review-route-schema:v(?P<version>[123]) -->\s*$"
    )
    marker_matches = list(marker_pattern.finditer(_route_structure_markdown(body)))
    if len(marker_matches) != 1:
        errors.append(
            "a supported review-route schema marker must appear once directly below its heading"
        )
    schema_version = int(marker_matches[0].group("version")) if len(marker_matches) == 1 else None

    visible = _visible_markdown(body)
    headings = list(re.finditer(r"(?mi)^## Review route\s*$", visible))
    if len(headings) != 1:
        errors.append("expected exactly one '## Review route' heading")
        return None, schema_version, [], {}, errors
    start = headings[0].end()
    following = re.search(r"(?mi)^##\s+", visible[start:])
    section = visible[start : start + following.start()] if following else visible[start:]
    if "<" in section:
        errors.append("raw HTML is not allowed in the review-route section")

    route_rows = re.findall(
        r"(?m)^[ ]{0,3}- \[([ xX])\] (Low|Standard|Load-bearing)\b",
        section,
    )
    counts = {name: 0 for name in ROUTE_RANK}
    selected: list[str] = []
    for mark, name in route_rows:
        counts[name] += 1
        if mark.lower() == "x":
            selected.append(name)
    if any(count != 1 for count in counts.values()):
        errors.append("expected each canonical route checkbox exactly once")
    if len(selected) != 1:
        errors.append("select exactly one review route")
    route = selected[0] if len(selected) == 1 else None

    selected_bots: list[str] = []
    if schema_version in {2, 3}:
        choices = _review_choices_for_schema(schema_version)
        # Longest-first so `Codex` cannot shadow a longer name sharing its prefix.
        alternation = "|".join(sorted(map(re.escape, choices), key=len, reverse=True))
        reviewer_rows = re.findall(
            rf"(?m)^[ ]{{0,3}}- \[([ xX])\] ({alternation})\b",
            section,
        )
        reviewer_counts = {name: 0 for name in choices}
        for mark, name in reviewer_rows:
            reviewer_counts[name] += 1
            if mark.lower() == "x":
                selected_bots.append(choices[name])
        if any(
            count > 1 if _is_optional_gate(choices[name], schema_version) else count != 1
            for name, count in reviewer_counts.items()
        ):
            errors.append("expected each automated reviewer checkbox exactly once")

    expected = {
        gate
        for gate in _gates_for_schema(schema_version)
        if not _is_optional_gate(gate, schema_version)
    }
    label_to_gate = dict(
        zip(
            _gate_labels_for_schema(schema_version),
            _gates_for_schema(schema_version),
            strict=True,
        )
    )
    gate_to_label = {gate: label for label, gate in label_to_gate.items()}
    evidence: dict[str, Evidence] = {}
    unknown: list[str] = []
    for line in section.splitlines():
        if re.match(r"^[ ]{0,3}\|", line) is None:
            continue
        cells = _table_cells(line)
        if len(cells) != 4 or cells[0] in {"Required review gate", "---"}:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        label = _normalise_gate(cells[0])
        gate = label_to_gate.get(label)
        if gate is None:
            unknown.append(label)
        elif gate in evidence:
            errors.append(f"duplicate review evidence row: {label}")
        else:
            evidence[gate] = Evidence(
                applies_to=cells[1],
                head=cells[2],
                status=cells[3],
            )
    # An optional gate is only optional while it is unused. Selecting it without
    # supplying its evidence row would otherwise leave the selection unprovable.
    missing = (expected | {gate for gate in selected_bots if gate not in expected}) - (
        evidence.keys()
    )
    if missing:
        errors.append(
            "missing review evidence rows: "
            + ", ".join(sorted(gate_to_label[gate] for gate in missing))
        )
    if unknown:
        errors.append("unknown review evidence rows: " + ", ".join(sorted(unknown)))
    return route, schema_version, selected_bots, evidence, errors


def _v3_provenance_errors(
    body: str,
    head_sha: str,
    selected_bots: list[str],
    *,
    allow_blank: bool = False,
) -> list[str]:
    errors: list[str] = []
    visible = _visible_markdown(body)
    headings = list(re.finditer(r"(?mi)^## Automated contribution provenance\s*$", visible))
    if len(headings) != 1:
        return ["expected exactly one v3 automated contribution provenance section"]
    start = headings[0].end()
    following = re.search(r"(?mi)^##\s+", visible[start:])
    section = visible[start : start + following.start()] if following else visible[start:]
    if "<" in section:
        errors.append(
            "raw HTML is not allowed in the v3 automated contribution provenance section"
        )
    field_names = (
        "Issue",
        "Exact head SHA",
        "Selected hosted reviewer",
        "Test evidence",
        "Agent claim ID",
    )
    fields: dict[str, str] = {}
    for name in field_names:
        matches = re.findall(
            rf"(?m)^[ ]{{0,3}}- {re.escape(name)}:[ \t]*(.*?)[ \t]*$",
            section,
        )
        if len(matches) != 1:
            errors.append(f"expected exactly one v3 provenance field: {name}")
        else:
            fields[name] = matches[0]

    issue = fields.get("Issue", "")
    if (issue or not allow_blank) and re.fullmatch(
        r"(?:Closes|Fixes|Resolves) #[1-9][0-9]*", issue
    ) is None:
        errors.append("v3 provenance issue must be an exact closing reference")
    exact_head = fields.get("Exact head SHA", "")
    if (exact_head or not allow_blank) and exact_head.lower() != head_sha.lower():
        errors.append("v3 provenance head does not match the current head SHA")
    provider_labels = {gate: label for label, gate in V3_AUTOMATED_REVIEW_CHOICES.items()}
    expected_provider = provider_labels.get(selected_bots[0]) if len(selected_bots) == 1 else None
    selected_provider = fields.get("Selected hosted reviewer", "")
    if (selected_provider or not allow_blank) and (
        expected_provider is None or selected_provider != expected_provider
    ):
        errors.append("v3 provenance reviewer does not match the selected hosted reviewer")
    test_evidence = fields.get("Test evidence", "")
    if (test_evidence or not allow_blank) and (not test_evidence or test_evidence == "N/A"):
        errors.append("v3 provenance test evidence must be nonempty")
    claim_id = fields.get("Agent claim ID", "")
    if (claim_id or not allow_blank) and (
        re.fullmatch(
            r"yz-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            claim_id,
            flags=re.IGNORECASE,
        )
        is None
    ):
        errors.append("v3 provenance agent claim ID must be a UUIDv4 claim")
    return errors


def needs_coderabbit_ledger(body: str) -> bool:
    """Return whether a valid v2 route selected the global CodeRabbit ledger."""
    _, schema_version, selected_bots, _, errors = _parse_route_section(body)
    return not errors and schema_version == 2 and selected_bots == [CODERABBIT_GATE]


def _is_load_bearing(path: str) -> bool:
    lowered = path.lower()
    name = Path(path).name.lower()
    return (
        lowered in LOAD_BEARING_EXACT
        or lowered.startswith(LOAD_BEARING_PREFIXES)
        or name in LOAD_BEARING_NAMES
        or name.startswith(".env")
        or name.startswith("constraints")
        or name.startswith("requirements")
        or name.startswith("dockerfile")
        or name.startswith("docker-compose")
        or name == "pyproject.toml"
        or name.endswith(("package-lock.json", "package.json", ".lock"))
        or (
            lowered.startswith(("tests/", "frontend/"))
            and any(token in lowered for token in LOAD_BEARING_TEST_TOKENS)
        )
    )


def minimum_route(files: list[ChangedFile]) -> str:
    paths = [path for item in files for path in (item.filename, item.previous_filename) if path]
    if not paths:
        raise ValueError("changed-file list is empty")
    if any(_is_load_bearing(path) for path in paths):
        return "Load-bearing"
    if all(path.endswith(LOW_SUFFIXES) for path in paths):
        return "Low"
    return "Standard"


def _actor_id(node: dict[str, Any]) -> int | None:
    author = node.get("author")
    return author.get("databaseId") if isinstance(author, dict) else None


def _connection_truncated_since(
    connection: dict[str, Any],
    *,
    time_key: str,
    since: datetime,
    inclusive: bool = False,
) -> bool:
    nodes = connection.get("nodes") or []
    if connection.get("totalCount", 0) <= len(nodes):
        return False
    if not nodes:
        return True
    times = [_parse_utc(node[time_key]) for node in nodes if node.get(time_key)]
    if not times:
        return True
    oldest = min(times)
    return oldest >= since if inclusive else oldest > since


def _v3_formal_review_is_clean(
    review: dict[str, Any],
    gate: str,
    changed_files: Any,
) -> bool:
    """Validate only the provider's small authenticated completion envelope."""
    comments = review.get("comments")
    attached_count = comments.get("totalCount") if isinstance(comments, dict) else None
    if (
        isinstance(attached_count, bool)
        or not isinstance(attached_count, int)
        or attached_count != 0
    ):
        return False
    body = review.get("body")
    if not isinstance(body, str):
        return False
    if gate == CODEX_GATE:
        return review.get("state") == "APPROVED" and body == ""
    if review.get("state") != "COMMENTED":
        return False
    if isinstance(changed_files, bool) or not isinstance(changed_files, int) or changed_files <= 0:
        return False
    if gate == COPILOT_GATE:
        # Fenced/indented code and HTML comments are blanked first, so a
        # sentence quoted in either cannot be read as the verdict. Position is
        # not considered at all — see the note on COPILOT_V3_COVERAGE_LINE for
        # why locating it structurally was abandoned.
        coverage = list(COPILOT_V3_COVERAGE_LINE.finditer(_visible_markdown(body)))
        return len(coverage) == 1 and coverage[0].group("verdict") == COPILOT_V3_CLEAN_VERDICT
    if gate != CODERABBIT_GATE:
        return False
    actionable = list(CODERABBIT_ACTIONABLE_COUNT.finditer(body))
    ignored = list(CODERABBIT_IGNORED_COUNT.finditer(body))
    selected = list(CODERABBIT_SELECTED_COUNT.finditer(body))
    marker = f"<!-- This is an {CODERABBIT_COMPLETION_MARKER} -->"
    return (
        body.startswith("**Actionable comments posted: 0**\n")
        and body.endswith(marker)
        and body.count(marker) == 1
        and len(actionable) == 1
        and body.count("Actionable comments posted:") == 1
        and actionable[0].group("count") == "0"
        and len(ignored) <= 1
        and body.lower().count("files ignored") == len(ignored)
        and all(match.group("count") == "0" for match in ignored)
        and len(selected) == 1
        and body.count("Files selected for processing") == 1
        and int(selected[0].group("count")) == changed_files
    )


def _greptile_check_runs(commit: Any) -> tuple[list[dict[str, Any]], bool]:
    """Return Greptile's check runs on a commit, and whether the view is complete."""
    if not isinstance(commit, dict) or commit.get("__typename") != "Commit":
        return [], False
    suites = commit.get("checkSuites")
    if not isinstance(suites, dict):
        return [], False
    suite_nodes = suites.get("nodes")
    suite_total = suites.get("totalCount")
    if (
        not isinstance(suite_nodes, list)
        or isinstance(suite_total, bool)
        or not isinstance(suite_total, int)
        or suite_total > len(suite_nodes)
    ):
        return [], False
    runs: list[dict[str, Any]] = []
    for suite in suite_nodes:
        if not isinstance(suite, dict):
            return [], False
        app = suite.get("app")
        # The server-side app filter only narrows the page; identity is decided
        # here, on the authenticated app id, exactly as bot reviews are.
        if not isinstance(app, dict) or app.get("databaseId") != GREPTILE_APP_ID:
            continue
        check_runs = suite.get("checkRuns")
        if not isinstance(check_runs, dict):
            return [], False
        run_nodes = check_runs.get("nodes")
        run_total = check_runs.get("totalCount")
        if (
            not isinstance(run_nodes, list)
            or isinstance(run_total, bool)
            or not isinstance(run_total, int)
            or run_total > len(run_nodes)
        ):
            return [], False
        # A non-dictionary node still counts toward `totalCount`, so silently
        # dropping one would let the length check agree while a billed run went
        # unseen. Anything unreadable makes the whole view unproven.
        if any(not isinstance(run, dict) for run in run_nodes):
            return [], False
        runs.extend(run_nodes)
    return runs, True


def _greptile_run_is_clean(run: dict[str, Any]) -> bool:
    """A completed Greptile run that added no comments.

    ``conclusion`` alone is never the verdict: a review that reported
    ``7 files reviewed, 3 comments added.`` on this repository's PR #2203 still
    concluded ``SUCCESS``. Cleanliness is the counter, and the conclusion is
    required on top of it so a confidence-threshold failure cannot pass.
    """
    if run.get("status") != "COMPLETED" or run.get("conclusion") != "SUCCESS":
        return False
    if run.get("name") != GREPTILE_CHECK_RUN_NAME:
        return False
    summary = run.get("summary")
    if not isinstance(summary, str):
        return False
    match = GREPTILE_CHECK_SUMMARY.fullmatch(summary)
    # The reviewed-file count is deliberately not compared against GitHub's
    # changed-file count. It is asserted in the same generated string as the
    # verdict, so comparing them buys no trust (#2248), and Greptile's own path
    # filters make a smaller count legitimate.
    return match is not None and match.group("comments") == "0"


def _greptile_activity(
    repository: dict[str, Any],
    head_epoch: datetime,
) -> datetime | None:
    """Read Greptile's verdict from the check run GitHub binds to the head commit.

    Greptile publishes three artifacts and only this one can carry a clean
    verdict:

    * The **formal review** exists only when Greptile has findings. Of 429
      reviews sampled across four repositories, 427 carried at least one inline
      comment and an empty body, and the only two carrying zero comments carried
      its "Your free trial has ended" billing notice. Reading
      ``comments.totalCount == 0`` as clean would therefore accept a refusal to
      review as a passing gate — the #2248/#2256 failure mode.
    * The **summary issue comment** is edited in place on every re-review, so it
      fails the immutability rule every other provider's envelope is held to.
    * The **check run** is bound to ``head_sha`` by GitHub rather than
      self-reported, is attributable to the app by id, and carries Greptile's own
      counter in a fixed template.
    """
    runs, complete = _greptile_check_runs(repository.get("greptileHeadObject"))
    if not complete or not runs:
        return None
    completions: list[tuple[datetime, dict[str, Any]]] = []
    for run in runs:
        completed_at = run.get("completedAt")
        if not isinstance(completed_at, str):
            return None
        try:
            when = _parse_utc(completed_at)
        except ValueError:
            return None
        if when > head_epoch:
            completions.append((when, run))
    if not completions:
        return None
    latest = max(when for when, _ in completions)
    if not all(_greptile_run_is_clean(run) for when, run in completions if when == latest):
        return None
    return latest


def _greptile_run_is_billed(run: dict[str, Any]) -> bool:
    """A Greptile run that consumed a review credit.

    Cleanliness is irrelevant here — a review that finds nothing costs the same
    as one that finds a bug. So is completion: an in-flight run has already been
    triggered and already committed the credit, and carries no conclusion yet.

    The name is checked because app 867647 is free to publish other check runs
    and each would inflate the count. It is checked *in addition to* the app id
    that `_greptile_check_runs` pins, never instead of it: any app holding
    `checks: write` can publish a run called `Greptile Review`.
    """
    if run.get("name") != GREPTILE_CHECK_RUN_NAME:
        return False
    return run.get("conclusion") not in GREPTILE_UNBILLED_CONCLUSIONS


def _greptile_run_spent_at(run: dict[str, Any]) -> datetime | None:
    """When a run spent its credit, or ``None`` if that cannot be established.

    ``startedAt`` first, because the credit is committed when the review is
    triggered rather than when it finishes. Preferring ``completedAt`` would
    charge a stacked child pull request for a run that began on its parent and
    only finished after the child was opened.
    """
    for key in ("startedAt", "completedAt"):
        value = run.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        try:
            return _parse_utc(value)
        except ValueError:
            return None
    return None


def _greptile_pr_budget_errors(
    repository: dict[str, Any],
    pull_request: dict[str, Any],
) -> list[str]:
    """Refuse the lane once this pull request has spent its Greptile allowance.

    Greptile leaves exactly one artifact that is one-to-one with a billed credit:
    the check run. A clean review posts no formal review and no comment, and the
    summary comment it does post for a dirty one is edited in place, so neither
    can be counted. Check runs live on the commit that was reviewed, which is why
    this reads the pull request's own commits rather than only its head.

    The count is bounded and provable, unlike a repository-wide one: every input
    is a `totalCount` this query can compare against its own page.
    """
    force_pushes = (pull_request.get("timelineItems") or {}).get("nodes") or []
    if force_pushes:
        # A force push drops commits from this connection and takes their billed
        # runs with them, leaving no truncation for the guards below to catch.
        return [GREPTILE_LEDGER_FORCE_PUSHED]
    commits = pull_request.get("greptileCommits")
    if not isinstance(commits, dict):
        return [GREPTILE_LEDGER_UNAVAILABLE]
    nodes = commits.get("nodes")
    total = commits.get("totalCount")
    if (
        not isinstance(nodes, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total > len(nodes)
    ):
        return [GREPTILE_LEDGER_TRUNCATED]
    created_raw = pull_request.get("createdAt")
    if not isinstance(created_raw, str):
        return [GREPTILE_LEDGER_TRUNCATED]
    try:
        created_at = _parse_utc(created_raw)
    except ValueError:
        return [GREPTILE_LEDGER_TRUNCATED]

    views: list[Any] = [repository.get("greptileHeadObject")]
    views.extend(node.get("commit") if isinstance(node, dict) else None for node in nodes)
    # Deduplicate on the global node id, not `databaseId`. The schema declares
    # `CheckRun.databaseId` a nullable 32-bit `Int` while GitHub returns values
    # far past that range (91710286922 on PR #2203), so a future spec-compliant
    # null there would refuse every Greptile lane. `id` is `ID!` — non-null by
    # schema — and equally unique. `CheckRun` has no `fullDatabaseId`.
    billed: set[str] = set()
    for view in views:
        runs, complete = _greptile_check_runs(view)
        if not complete:
            return [GREPTILE_LEDGER_TRUNCATED]
        for run in runs:
            if not _greptile_run_is_billed(run):
                continue
            spent_at = _greptile_run_spent_at(run)
            if spent_at is None:
                # A run that cannot be placed in time cannot be proven to belong
                # to another pull request either.
                return [GREPTILE_LEDGER_TRUNCATED]
            if spent_at < created_at:
                # A stacked branch inherits its parent's commits; those reviews
                # were charged to the parent pull request, not to this one.
                continue
            run_id = run.get("id")
            if not isinstance(run_id, str) or not run_id:
                return [GREPTILE_LEDGER_TRUNCATED]
            billed.add(run_id)
    if len(billed) > GREPTILE_PR_REVIEW_CAP:
        return [
            "Greptile per-pull-request review budget exceeded: "
            f"{len(billed)} > {GREPTILE_PR_REVIEW_CAP}"
        ]
    return []


def _bot_activity(
    pull_request: dict[str, Any],
    gate: str,
    actor_id: int,
    head_sha: str,
    head_epoch: datetime,
    codex_head_safe: bool = False,
    schema_version: int | None = None,
) -> datetime | None:
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for review in pull_request["reviews"].get("nodes") or []:
        commit = review.get("commit") or {}
        submitted = review.get("submittedAt")
        author = review.get("author") or {}
        if (
            author.get("__typename") == "Bot"
            and _actor_id(review) == actor_id
            and commit.get("oid") == head_sha
            and submitted
        ):
            submitted_at = _parse_utc(submitted)
            edited_raw = review.get("lastEditedAt")
            effective_at = max(
                submitted_at,
                _parse_utc(edited_raw) if isinstance(edited_raw, str) else submitted_at,
            )
            if effective_at > head_epoch:
                candidates.append((effective_at, review))
    signals: list[tuple[datetime, bool]] = [
        (
            when,
            "lastEditedAt" in review
            and review["lastEditedAt"] is None
            and review.get("state") in TERMINAL_REVIEW_STATES
            and (
                _v3_formal_review_is_clean(
                    review,
                    gate,
                    pull_request.get("changedFiles"),
                )
                if schema_version == 3
                else (
                    gate != CODERABBIT_GATE
                    or CODERABBIT_COMPLETION_MARKER.lower() in (review.get("body") or "").lower()
                )
            ),
        )
        for when, review in candidates
        if not (
            schema_version != 3
            and gate == CODERABBIT_GATE
            and review.get("state") in TERMINAL_REVIEW_STATES
            and not (review.get("body") or "").strip()
        )
    ]
    if gate == CODEX_GATE:
        comments = pull_request.get("comments") or {}
        for comment in comments.get("nodes") or []:
            created_raw = comment.get("createdAt")
            updated_raw = comment.get("updatedAt") or created_raw
            author = comment.get("author") or {}
            body = comment.get("body") or ""
            codex_response = author.get("__typename") == "Bot" and _actor_id(comment) == actor_id
            if not codex_response or not created_raw or not updated_raw:
                continue
            created = _parse_utc(created_raw)
            updated = _parse_utc(updated_raw)
            if updated <= head_epoch:
                continue
            database_id = comment.get("databaseId")
            if not isinstance(database_id, int):
                continue
            immutable = (
                created == updated
                and "lastEditedAt" in comment
                and comment["lastEditedAt"] is None
            )
            commit_lines = CODEX_REVIEWED_COMMIT_LINE.findall(body)
            first_lines = body.splitlines()[0:1]
            clean_completion = (
                immutable
                and created > head_epoch
                and len(first_lines) == 1
                and CODEX_CLEAN_COMPLETION_LINE.fullmatch(first_lines[0]) is not None
                and len(commit_lines) == 1
                and commit_lines[0] == head_sha[:10].lower()
                and codex_head_safe
            )
            if schema_version == 3:
                signals.append((max(created, updated), clean_completion))
            elif clean_completion:
                signals.append((created, True))
    if not signals:
        return None
    latest_at = max(when for when, _ in signals)
    return latest_at if all(valid for when, valid in signals if when == latest_at) else None


def _current_human_approval(
    pull_request: dict[str, Any], head_sha: str, head_epoch: datetime
) -> tuple[datetime | None, bool]:
    author_id = _actor_id(pull_request)
    approvals: list[datetime] = []
    active_change_request = False
    opinions = pull_request.get("latestHumanOpinions") or {}
    for review in opinions.get("nodes") or []:
        reviewer_id = _actor_id(review)
        author = review.get("author") or {}
        submitted = review.get("submittedAt")
        if (
            not isinstance(reviewer_id, int)
            or reviewer_id == author_id
            or author.get("__typename") != "User"
            or review.get("authorAssociation") not in HUMAN_ASSOCIATIONS
            or review.get("state") not in HUMAN_OPINION_STATES
            or not submitted
        ):
            continue
        if review.get("state") == "CHANGES_REQUESTED":
            active_change_request = True
            continue
        commit = review.get("commit") or {}
        when = _parse_utc(review["submittedAt"])
        if (
            review.get("state") == "APPROVED"
            and commit.get("oid") == head_sha
            and when > head_epoch
        ):
            approvals.append(when)
    return (max(approvals) if approvals else None, active_change_request)


def _coderabbit_protocol_events(
    pull_request: dict[str, Any],
    head_sha: str,
) -> list[tuple[datetime, str, int, int | None]]:
    reservation_pattern = re.compile(r"(?i)coderabbit-reservation:\s*([0-9a-f]{40})")
    raw_events: list[tuple[datetime, str, int, str | None, int | None]] = []
    for comment in pull_request["comments"].get("nodes") or []:
        body = comment.get("body") or ""
        when = _parse_utc(comment["createdAt"])
        updated = _parse_utc(comment.get("updatedAt") or comment["createdAt"])
        author = comment.get("author") or {}
        actor_id = _actor_id(comment)
        if (
            author.get("__typename") != "User"
            or not isinstance(actor_id, int)
            or comment.get("authorAssociation") not in HUMAN_ASSOCIATIONS
            or updated != when
            or comment.get("lastEditedAt") is not None
        ):
            continue
        comment_id = comment.get("databaseId")
        if not isinstance(comment_id, int):
            comment_id = None
        reservation = reservation_pattern.fullmatch(body.strip())
        if reservation:
            raw_events.append(
                (when, "reservation", actor_id, reservation.group(1).lower(), comment_id)
            )
        elif body.strip().lower() == "@coderabbitai full review":
            raw_events.append((when, "trigger", actor_id, None, comment_id))

    # A trigger carries no SHA. Attribute it to its same-maintainer reservation
    # before filtering for the current head; filtering reservations first can
    # leave a prior head's trigger looking current when a commit predates its push.
    raw_events.sort(key=lambda event: (event[0], event[4] if event[4] is not None else -1))
    pending: dict[int, tuple[datetime, str]] = {}
    protocol: list[tuple[datetime, str, int, int | None]] = []
    current_head = head_sha.lower()
    for when, kind, actor_id, reservation_sha, comment_id in raw_events:
        if kind == "reservation":
            assert reservation_sha is not None
            pending[actor_id] = (when, reservation_sha)
            if reservation_sha == current_head:
                protocol.append((when, "reservation", actor_id, comment_id))
            continue
        reservation = pending.pop(actor_id, None)
        if reservation is None:
            # An exact trusted trigger without a visible reservation is
            # unattributable, so retain it to make current-head checks fail closed.
            protocol.append((when, "trigger", actor_id, comment_id))
            continue
        _, reserved_sha = reservation
        if reserved_sha == current_head:
            protocol.append((when, "trigger", actor_id, comment_id))
    protocol.sort(key=lambda event: (event[0], event[3] if event[3] is not None else -1))
    return protocol


def _coderabbit_trigger_state(
    repository: dict[str, Any],
    pull_request: dict[str, Any],
    head_sha: str,
    review_at: datetime,
    head_epoch: datetime,
    protocol_epoch: datetime,
    protocol_label: str,
) -> list[str]:
    errors: list[str] = []
    protocol = _coderabbit_protocol_events(pull_request, head_sha)

    # GitHub timestamps have one-second resolution; immutable database IDs break
    # ties between different protocol attempts. One maintainer's reservation and
    # trigger must still occur in distinct seconds.
    protocol = [event for event in protocol if event[0] > max(head_epoch, protocol_epoch)]
    protocol.sort(key=lambda event: (event[0], event[3] if event[3] is not None else -1))
    if not protocol:
        errors.append("CodeRabbit needs a current-SHA reservation, trigger, then completed review")
        return errors
    comment_ids = [event[3] for event in protocol]
    if any(comment_id is None for comment_id in comment_ids) or len(comment_ids) != len(
        set(comment_ids)
    ):
        errors.append("CodeRabbit protocol needs unique immutable comment IDs")
        return errors
    pending_by_actor: dict[int, datetime] = {}
    reservation_queue: list[int] = []
    trigger_times: list[datetime] = []
    for when, kind, actor_id, _ in protocol:
        if kind == "reservation":
            if actor_id in pending_by_actor:
                errors.append("CodeRabbit reservations and triggers must form one-to-one pairs")
                return errors
            pending_by_actor[actor_id] = when
            reservation_queue.append(actor_id)
            continue
        if actor_id not in pending_by_actor:
            if pending_by_actor:
                errors.append("the same maintainer must reserve and trigger CodeRabbit")
            else:
                errors.append("CodeRabbit reservations and triggers must form one-to-one pairs")
            return errors
        if not reservation_queue or reservation_queue[0] != actor_id:
            errors.append("CodeRabbit triggers must follow reservation FIFO order")
            return errors
        if when == pending_by_actor[actor_id]:
            errors.append(
                "CodeRabbit needs strictly ordered reservation and trigger comments "
                f"after {protocol_label}"
            )
            return errors
        if when >= review_at:
            errors.append("CodeRabbit was triggered again after its latest completed review")
            return errors
        reservation_queue.pop(0)
        del pending_by_actor[actor_id]
        trigger_times.append(when)
    if reservation_queue or pending_by_actor or not trigger_times:
        errors.append("CodeRabbit needs a current-SHA reservation, trigger, then completed review")
        return errors

    completion_times = sorted(
        _parse_utc(review["submittedAt"])
        for review in pull_request["reviews"].get("nodes") or []
        if (review.get("author") or {}).get("__typename") == "Bot"
        and _actor_id(review) == BOT_ACTOR_IDS[CODERABBIT_GATE]
        and (review.get("commit") or {}).get("oid") == head_sha
        and review.get("submittedAt")
        and _parse_utc(review["submittedAt"]) > head_epoch
        and review.get("state") in TERMINAL_REVIEW_STATES
        and "lastEditedAt" in review
        and review["lastEditedAt"] is None
        and CODERABBIT_COMPLETION_MARKER.lower() in (review.get("body") or "").lower()
    )
    completion_index = 0
    for trigger_index, current_trigger in enumerate(trigger_times):
        while (
            completion_index < len(completion_times)
            and completion_times[completion_index] <= current_trigger
        ):
            completion_index += 1
        if completion_index >= len(completion_times):
            errors.append("each CodeRabbit trigger needs a distinct later completed review")
            return errors
        completion = completion_times[completion_index]
        completion_index += 1
        if (
            trigger_index + 1 < len(trigger_times)
            and trigger_times[trigger_index + 1] <= completion
        ):
            errors.append("wait for each CodeRabbit review completion before triggering again")
            return errors

    trigger_at = trigger_times[-1]
    window = trigger_at - timedelta(hours=1)
    recent = repository.get("recentPullRequests")
    if not isinstance(recent, dict):
        errors.append("CodeRabbit rolling-hour ledger is unavailable")
        return errors
    nodes = recent.get("nodes")
    if not isinstance(recent.get("totalCount"), int) or not isinstance(nodes, list):
        errors.append("CodeRabbit rolling-hour ledger has an invalid shape")
        return errors
    if any(
        not isinstance(node, dict) or not isinstance(node.get("updatedAt"), str) for node in nodes
    ):
        errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
        return errors
    if recent.get("totalCount", 0) > len(nodes):
        if not nodes:
            errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
        else:
            try:
                oldest_update = min(_parse_utc(node["updatedAt"]) for node in nodes)
            except ValueError:
                errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
                return errors
            if oldest_update >= window:
                errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
    trigger_count = 0
    for pr in nodes:
        comments = pr.get("comments")
        if not isinstance(comments, dict):
            errors.append("comment pagination cannot prove the CodeRabbit hourly quota")
            return errors
        comment_nodes = comments.get("nodes")
        if (
            not isinstance(comments.get("totalCount"), int)
            or not isinstance(comment_nodes, list)
            or any(
                not isinstance(comment, dict)
                or not isinstance(comment.get("createdAt"), str)
                or not isinstance(comment.get("updatedAt"), str)
                for comment in comment_nodes
            )
        ):
            errors.append("comment pagination cannot prove the CodeRabbit hourly quota")
            return errors
        try:
            if _connection_truncated_since(
                comments,
                time_key="updatedAt",
                since=window,
                inclusive=True,
            ):
                errors.append("comment pagination cannot prove the CodeRabbit hourly quota")
                continue
            trigger_count += sum(
                window <= _parse_utc(comment["createdAt"]) <= trigger_at
                and _parse_utc(comment["updatedAt"]) == _parse_utc(comment["createdAt"])
                and comment.get("lastEditedAt") is None
                and (comment.get("author") or {}).get("__typename") == "User"
                and comment.get("authorAssociation") in HUMAN_ASSOCIATIONS
                and (comment.get("body") or "").strip().lower() == "@coderabbitai full review"
                for comment in comment_nodes
            )
        except ValueError:
            errors.append("comment pagination cannot prove the CodeRabbit hourly quota")
            return errors
    if trigger_count > 5:
        errors.append(f"CodeRabbit rolling-hour trigger quota exceeded: {trigger_count} > 5")
    return errors


def _merge_connection_pages(
    context: dict[str, Any],
    pages: Any,
    *,
    connection: str,
    noun: str,
    expected_head: str,
    expected_updated_at: str,
) -> list[str]:
    """Replace a truncated connection window with a complete, bound cursor ledger.

    One implementation serves every paginated connection on the pull request.
    `reviews` and `reviewThreads` need identical proof -- that the pages describe
    the same pull request at the same head and `updatedAt`, that the cursor chain
    is unbroken and non-repeating, that no node is missing or duplicated, and
    that the merged view still contains the window the main query already saw.
    Writing that twice would put the fail-closed guarantee in two places that can
    drift apart, which is the duplication trap #2248/#2255/#2256 came from.
    `noun` only shapes the diagnostics so a failure still names its connection.
    """
    if not isinstance(pages, list) or not pages:
        return [f"{noun} pagination pages are missing or malformed"]
    context_data = context.get("data")
    repository = context_data.get("repository") if isinstance(context_data, dict) else None
    pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
    source = pull_request.get(connection) if isinstance(pull_request, dict) else None
    if not isinstance(source, dict):
        return [f"{noun} pagination source context is malformed"]
    total_count = source.get("totalCount")
    original_nodes = source.get("nodes")
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
        or not isinstance(original_nodes, list)
        or total_count < len(original_nodes)
    ):
        return [f"{noun} pagination source context is malformed"]

    original_by_id: dict[str, dict[str, Any]] = {}
    for node in original_nodes:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or not node_id or node_id in original_by_id:
            return [f"{noun} pagination source {noun} IDs are missing or duplicated"]
        original_by_id[node_id] = node

    expected_number = pull_request.get("number")
    merged_nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("errors"):
            return [f"{noun} pagination page contains GraphQL errors or malformed data"]
        page_data = page.get("data")
        page_repository = page_data.get("repository") if isinstance(page_data, dict) else None
        page_pull = (
            page_repository.get("pullRequest") if isinstance(page_repository, dict) else None
        )
        if not isinstance(page_pull, dict):
            return [f"{noun} pagination page contains GraphQL errors or malformed data"]
        page_head = page_pull.get("headRefOid")
        if (
            page_pull.get("number") != expected_number
            or not isinstance(page_head, str)
            or page_head.lower() != expected_head.lower()
            or page_pull.get("updatedAt") != expected_updated_at
        ):
            return [f"{noun} pagination pull request snapshot changed between requests"]
        page_source = page_pull.get(connection)
        if not isinstance(page_source, dict) or page_source.get("totalCount") != total_count:
            return [f"{noun} pagination total changed between requests"]
        page_nodes = page_source.get("nodes")
        page_info = page_source.get("pageInfo")
        if not isinstance(page_nodes, list) or not isinstance(page_info, dict):
            return [f"{noun} pagination page contains GraphQL errors or malformed data"]
        has_next_page = page_info.get("hasNextPage")
        end_cursor = page_info.get("endCursor")
        expected_has_next = index < len(pages) - 1
        if has_next_page is not expected_has_next:
            return [f"{noun} pagination page sequence is incomplete"]
        if expected_has_next and (not isinstance(end_cursor, str) or not end_cursor):
            return [f"{noun} pagination cursor is missing or duplicated"]
        if end_cursor is not None:
            if not isinstance(end_cursor, str) or not end_cursor or end_cursor in seen_cursors:
                return [f"{noun} pagination cursor is missing or duplicated"]
            seen_cursors.add(end_cursor)
        if not page_nodes and total_count > 0:
            return [f"{noun} pagination page sequence is incomplete"]
        for node in page_nodes:
            node_id = node.get("id") if isinstance(node, dict) else None
            if not isinstance(node_id, str) or not node_id or node_id in seen_ids:
                return [f"{noun} pagination contains a missing or duplicate {noun} ID"]
            seen_ids.add(node_id)
            merged_nodes.append(node)

    if len(merged_nodes) != total_count:
        return [f"{noun} pagination aggregate count does not match totalCount"]
    merged_by_id = {node["id"]: node for node in merged_nodes}
    if any(merged_by_id.get(node_id) != node for node_id, node in original_by_id.items()):
        return [f"{noun} pagination disagrees with the original {noun} snapshot"]

    complete = deepcopy(source)
    complete["nodes"] = merged_nodes
    pull_request[connection] = complete
    return []


def _merge_review_pages(
    context: dict[str, Any],
    pages: Any,
    *,
    expected_head: str,
    expected_updated_at: str,
) -> list[str]:
    """Replace a truncated review window with a complete, bound cursor ledger."""
    return _merge_connection_pages(
        context,
        pages,
        connection="reviews",
        noun="review",
        expected_head=expected_head,
        expected_updated_at=expected_updated_at,
    )


def _merge_thread_pages(
    context: dict[str, Any],
    pages: Any,
    *,
    expected_head: str,
    expected_updated_at: str,
) -> list[str]:
    """Replace a truncated review-thread window with a complete cursor ledger.

    Threads were the one connection with no fallback, and crossing 100 of them
    made a pull request permanently unprovable: `totalCount` only grows,
    resolving a thread does not decrement it, and the truncation guard fails
    closed on every subsequent head. #2203 died that way at 105 threads while its
    142 reviews were rescued by the sibling fallback (#2262).
    """
    return _merge_connection_pages(
        context,
        pages,
        connection="reviewThreads",
        noun="review-thread",
        expected_head=expected_head,
        expected_updated_at=expected_updated_at,
    )


def _load_repository(context: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if context.get("errors"):
        errors.append("GitHub GraphQL context contains errors")
    repository = (context.get("data") or {}).get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("pullRequest"), dict):
        errors.append("GitHub context has no pull request")
        return None, errors
    return repository, errors


def _finalizer_activity(
    pull_request: dict[str, Any],
    *,
    comment_node_id: str,
    comment_created_at: str,
    comment_actor_id: int,
    comment_actor_permission: str,
) -> tuple[datetime | None, list[str]]:
    errors: list[str] = []
    matches = [
        comment
        for comment in pull_request.get("comments", {}).get("nodes") or []
        if comment.get("id") == comment_node_id
    ]
    if len(matches) != 1:
        return None, ["finalizer comment is missing from the live pull request"]
    comment = matches[0]
    author = comment.get("author") or {}
    if (
        author.get("__typename") != "User"
        or _actor_id(comment) != comment_actor_id
        or comment.get("authorAssociation") not in HUMAN_ASSOCIATIONS
    ):
        errors.append("finalizer comment is not from the expected trusted maintainer")
    if comment_actor_permission not in {"admin", "write"}:
        errors.append("finalizer actor does not have live repository write permission")
    if (comment.get("body") or "") != FINALIZE_COMMAND:
        errors.append("finalizer comment body is not the exact validation command")
    created_raw = comment.get("createdAt")
    updated_raw = comment.get("updatedAt")
    try:
        created_at = _parse_utc(created_raw) if created_raw else None
        expected_created_at = _parse_utc(comment_created_at)
        updated_at = _parse_utc(updated_raw) if updated_raw else None
    except (TypeError, ValueError):
        return None, errors + ["finalizer comment has an invalid timestamp"]
    if created_at is None or created_at != expected_created_at:
        errors.append("finalizer comment creation time does not match the triggering event")
    if created_at is None or updated_at != created_at or comment.get("lastEditedAt") is not None:
        errors.append("finalizer comment was edited after creation")
    return created_at, errors


def validate_context(
    context: dict[str, Any],
    files: list[ChangedFile],
    *,
    now: datetime | None = None,
    expected_head: str | None = None,
    expected_draft: bool | None = None,
    expected_pr_updated_at: str | None = None,
    expected_pr_body: str | None = None,
    rendered_body: str | None = None,
    render_nonce: str | None = None,
    finalize_comment_node_id: str | None = None,
    finalize_comment_created_at: str | None = None,
    finalize_comment_actor_id: int | None = None,
    finalize_comment_actor_permission: str | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    repository, errors = _load_repository(context)
    if repository is None:
        return errors
    pull_request = repository["pullRequest"]
    head_sha = pull_request.get("headRefOid") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        errors.append("pull request head SHA is not a full 40-character SHA")
    if expected_head is not None and head_sha.lower() != expected_head.lower():
        errors.append("GitHub context head SHA changed during validation")
    if expected_draft is not None and pull_request.get("isDraft") is not expected_draft:
        errors.append("GitHub context draft state changed during validation")
    if expected_pr_body is not None and (pull_request.get("body") or "") != expected_pr_body:
        errors.append("GitHub context body changed during validation")
    if expected_pr_updated_at is not None:
        try:
            actual_updated_at = _parse_utc(pull_request["updatedAt"])
            expected_updated_at = _parse_utc(expected_pr_updated_at)
        except (KeyError, TypeError, ValueError):
            errors.append("pull request has an invalid update timestamp")
        else:
            if actual_updated_at != expected_updated_at:
                errors.append("pull request state changed during validation")
    open_pull_requests = repository.get("openPullRequests") or {}
    open_nodes = open_pull_requests.get("nodes") or []
    if open_pull_requests.get("totalCount") != len(open_nodes):
        errors.append("open pull request pagination cannot prove head uniqueness")
    if pull_request.get("state") != "OPEN":
        errors.append("pull request is not open")
    current_number = pull_request.get("number")
    same_head_numbers = [
        node.get("number")
        for node in open_nodes
        if (node.get("headRefOid") or "").lower() == head_sha.lower()
    ]
    if same_head_numbers != [current_number]:
        errors.append("current head SHA must belong to exactly one open pull request")
    finalizer_values = (
        finalize_comment_node_id,
        finalize_comment_created_at,
        finalize_comment_actor_id,
        finalize_comment_actor_permission,
    )
    has_finalizer = all(value is not None for value in finalizer_values)
    if any(value is not None for value in finalizer_values) and not has_finalizer:
        errors.append("finalizer event context is incomplete")
    commits = (pull_request.get("commits") or {}).get("nodes") or []
    if len(commits) != 1:
        return errors + ["GitHub context does not contain exactly one current head commit"]
    committed_at = _parse_utc(commits[0]["commit"]["committedDate"])
    created_at = pull_request.get("createdAt")
    if not created_at:
        return errors + ["GitHub context has no pull request creation time"]
    head_epoch = max(committed_at, _parse_utc(created_at))
    force_pushes = (pull_request.get("timelineItems") or {}).get("nodes") or []
    force_push_times = [
        _parse_utc(event["createdAt"]) for event in force_pushes if event.get("createdAt")
    ]
    if force_push_times:
        head_epoch = max(head_epoch, *force_push_times)

    route, schema_version, selected_bots, evidence, parse_errors = _parse_route_section(
        pull_request.get("body") or ""
    )
    errors.extend(parse_errors)
    if (rendered_body is None) != (render_nonce is None):
        errors.append("rendered review-route context is incomplete")
    elif rendered_body is not None and render_nonce is not None:
        errors.extend(
            _rendered_route_errors(
                rendered_body,
                render_nonce,
                route,
                schema_version,
                selected_bots,
                evidence,
            )
        )
    if pull_request.get("changedFiles") != len(files):
        errors.append("changed-file API count does not match the pull request")
    try:
        floor = minimum_route(files)
    except ValueError as exc:
        errors.append(str(exc))
        floor = None
    if route and floor and ROUTE_RANK[route] < ROUTE_RANK[floor]:
        errors.append(f"selected route {route} is below changed-path minimum {floor}")
    if schema_version == 1:
        errors.append(
            "review-route schema v1 is obsolete; migrate to v2 and select one automated reviewer"
        )
        return errors
    if schema_version == 3 and any(
        path == REVIEW_SIGNAL_WORKFLOW
        for item in files
        for path in (item.filename, item.previous_filename)
        if path is not None
    ):
        errors.append(
            "review-route schema v3 cannot modify the PR-controlled review signal workflow; "
            "use the human-gated v2 route"
        )
    if schema_version == 3:
        errors.extend(
            _v3_provenance_errors(
                pull_request.get("body") or "",
                head_sha,
                selected_bots,
                allow_blank=pull_request.get("isDraft") is True,
            )
        )
    if pull_request.get("isDraft") is True or route is None:
        return errors

    reviews = pull_request.get("reviews") or {}
    comments = pull_request.get("comments") or {}
    human_opinions = pull_request.get("latestHumanOpinions") or {}
    review_nodes = reviews.get("nodes") or []
    if reviews.get("totalCount", 0) != len(review_nodes):
        errors.append("review pagination cannot prove current-head review state")
    if _connection_truncated_since(comments, time_key="updatedAt", since=head_epoch):
        errors.append("comment pagination cannot prove current-head review state")
    if human_opinions.get("totalCount", 0) > len(human_opinions.get("nodes") or []):
        errors.append("human-opinion pagination cannot prove active review state")
    threads = pull_request.get("reviewThreads") or {}
    if threads.get("totalCount", 0) > len(threads.get("nodes") or []):
        errors.append("review-thread pagination is incomplete")
    if any(not thread.get("isResolved") for thread in threads.get("nodes") or []):
        errors.append("unresolved review threads remain")

    if len(selected_bots) != 1:
        errors.append("select exactly one automated PR reviewer")
    required = set(selected_bots)
    if schema_version != 3:
        required.add(HUMAN_GATE)
    if (
        schema_version == 2
        and CODERABBIT_GATE not in required
        and any(
            kind == "trigger" and when > head_epoch
            for when, kind, _, _ in _coderabbit_protocol_events(pull_request, head_sha)
        )
    ):
        errors.append("manual CodeRabbit triggers require selecting the CodeRabbit lane")
    body_times: dict[str, datetime] = {}
    for gate in _gates_for_schema(schema_version):
        row = evidence.get(gate)
        if row is None:
            continue
        if gate not in required:
            if row.head != "N/A" or row.status != "N/A":
                errors.append(f"nonapplicable gate must use N/A in both cells: {gate}")
            continue
        if row.head.lower() != head_sha.lower():
            errors.append(f"required gate is not bound to the current head SHA: {gate}")
        match = UTC_RESULT.fullmatch(row.status)
        expected_result = "APPROVED" if gate == HUMAN_GATE else "COMPLETE"
        if match is None or match.group("result") != expected_result:
            errors.append(
                f"required gate needs exact UTC evidence ending in {expected_result}: {gate}"
            )
            continue
        try:
            when = _parse_utc(match.group("timestamp"))
        except ValueError:
            errors.append(f"required gate has an invalid UTC timestamp: {gate}")
            continue
        if when <= head_epoch or when > now + timedelta(minutes=1):
            errors.append(
                f"required gate timestamp is outside the current-head review window: {gate}"
            )
        body_times[gate] = when

    observed: dict[str, datetime] = {}
    codex_head_object = repository.get("codexHeadObject") or {}
    codex_full_oid = codex_head_object.get("oid")
    codex_abbreviated_oid = codex_head_object.get("abbreviatedOid")
    normalized_head = head_sha.lower()
    codex_head_safe = (
        codex_head_object.get("__typename") == "Commit"
        and isinstance(codex_full_oid, str)
        and codex_full_oid.lower() == normalized_head
        and isinstance(codex_abbreviated_oid, str)
        and re.fullmatch(r"[0-9a-f]{4,10}", codex_abbreviated_oid) is not None
        and normalized_head.startswith(codex_abbreviated_oid)
    )
    for gate, actor_id in BOT_ACTOR_IDS.items():
        if gate not in required:
            continue
        if gate == GREPTILE_GATE:
            # Greptile's only clean artifact is a check run, not a review or a
            # comment, so it does not go through `_bot_activity`.
            if _changes_greptile_config(files):
                errors.append(
                    "Greptile cannot review a change to its own configuration; "
                    "select another hosted reviewer"
                )
                continue
            activity = _greptile_activity(repository, head_epoch)
        else:
            activity = _bot_activity(
                pull_request,
                gate,
                actor_id,
                head_sha,
                head_epoch,
                codex_head_safe,
                schema_version,
            )
        if activity is None:
            errors.append(f"no verified current-head GitHub activity for: {gate}")
        else:
            observed[gate] = activity
    approval, active_change_request = _current_human_approval(pull_request, head_sha, head_epoch)
    if active_change_request:
        errors.append("an active human maintainer change request remains")
    if HUMAN_GATE in required:
        if approval is None:
            errors.append("no current-head approval from an independent human collaborator")
        else:
            observed[HUMAN_GATE] = approval
    if schema_version == 3 and selected_bots:
        selected_actor = BOT_ACTOR_IDS[selected_bots[0]]
        if _actor_id(pull_request) == selected_actor:
            errors.append(
                "selected hosted reviewer must be independent of the pull request author"
            )

    for gate, activity in observed.items():
        if gate in body_times and abs((body_times[gate] - activity).total_seconds()) > 600:
            errors.append(
                f"declared evidence time does not match verified GitHub activity: {gate}"
            )

    if schema_version == 3 and GREPTILE_GATE in required:
        errors.extend(_greptile_pr_budget_errors(repository, pull_request))

    if schema_version == 2 and selected_bots == [CODERABBIT_GATE] and CODERABBIT_GATE in observed:
        errors.extend(
            _coderabbit_trigger_state(
                repository,
                pull_request,
                head_sha,
                observed[CODERABBIT_GATE],
                head_epoch,
                head_epoch,
                "the current head",
            )
        )

    gate_times = {**body_times, **observed}
    sequence = [*selected_bots, HUMAN_GATE] if schema_version != 3 else []
    if sequence and all(gate in gate_times for gate in sequence):
        times = [gate_times[gate] for gate in sequence]
        if any(left >= right for left, right in zip(times, times[1:], strict=False)):
            errors.append("selected automated review must precede human approval")
    if has_finalizer:
        finalizer_at, finalizer_errors = _finalizer_activity(
            pull_request,
            comment_node_id=finalize_comment_node_id,
            comment_created_at=finalize_comment_created_at,
            comment_actor_id=finalize_comment_actor_id,
            comment_actor_permission=finalize_comment_actor_permission,
        )
        errors.extend(finalizer_errors)
        required_times = [
            timestamp
            for gate in required
            for timestamp in (body_times.get(gate), observed.get(gate))
            if timestamp is not None
        ]
        if finalizer_at is not None and (
            finalizer_at <= head_epoch
            or any(finalizer_at <= timestamp for timestamp in required_times)
        ):
            errors.append("finalizer comment must strictly follow every required review gate")
    return errors


def _load_files(path: Path) -> list[ChangedFile]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pages = raw if raw and isinstance(raw[0], list) else [raw]
    return [
        ChangedFile(item["filename"], item.get("previous_filename"))
        for page in pages
        for item in page
    ]


def _expected_snapshot_body(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        raise TypeError("REST pull request snapshot is not an object")
    body = snapshot.get("body")
    if body is None:
        return ""
    if not isinstance(body, str):
        raise TypeError("REST pull request body is not a string")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--files", type=Path, required=True)
    parser.add_argument("--review-pages", type=Path)
    parser.add_argument("--thread-pages", type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-draft", choices=("true", "false"), required=True)
    parser.add_argument("--expected-pr-updated-at", required=True)
    parser.add_argument("--expected-pr-snapshot", type=Path, required=True)
    parser.add_argument("--rendered-body", type=Path, required=True)
    parser.add_argument("--render-nonce", required=True)
    parser.add_argument("--finalize-comment-node-id")
    parser.add_argument("--finalize-comment-created-at")
    parser.add_argument("--finalize-comment-actor-id", type=int)
    parser.add_argument("--finalize-comment-actor-permission")
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    for pages_path, noun, merge in (
        (args.review_pages, "review", _merge_review_pages),
        (args.thread_pages, "review-thread", _merge_thread_pages),
    ):
        if pages_path is None:
            continue
        try:
            fetched_pages = json.loads(pages_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"::error title=Review Route::{noun} pagination output is unreadable")
            return 1
        page_errors = merge(
            context,
            fetched_pages,
            expected_head=args.expected_head,
            expected_updated_at=args.expected_pr_updated_at,
        )
        if page_errors:
            for error in page_errors:
                print(f"::error title=Review Route::{error}")
            return 1
    expected_snapshot = json.loads(args.expected_pr_snapshot.read_text(encoding="utf-8"))
    try:
        expected_pr_body = _expected_snapshot_body(expected_snapshot)
    except TypeError:
        print("::error title=Review Route::REST pull request body has an invalid shape")
        return 1
    errors = validate_context(
        context,
        _load_files(args.files),
        expected_head=args.expected_head,
        expected_draft=args.expected_draft == "true",
        expected_pr_updated_at=args.expected_pr_updated_at,
        expected_pr_body=expected_pr_body,
        rendered_body=args.rendered_body.read_text(encoding="utf-8"),
        render_nonce=args.render_nonce,
        finalize_comment_node_id=args.finalize_comment_node_id,
        finalize_comment_created_at=args.finalize_comment_created_at,
        finalize_comment_actor_id=args.finalize_comment_actor_id,
        finalize_comment_actor_permission=args.finalize_comment_actor_permission,
    )
    if errors:
        for error in dict.fromkeys(errors):
            print(f"::error title=Review Route::{error}")
        return 1
    print("Review route is verified against the current PR head and live GitHub state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
