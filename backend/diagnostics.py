"""Reviewed operator-facing diagnostics for the API and worker."""

from __future__ import annotations

import contextvars
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import PosixPath, WindowsPath
from types import TracebackType

import structlog

DATABASE_BUSY = "The database is busy. Retry the operation."
STORAGE_UNAVAILABLE = "Storage is unavailable. Check the application data directory."
NETWORK_BIND_WARNING = (
    "SECURITY WARNING. Anyone who can reach this port can access samples, variants, reports, "
    "and analyses without credentials. Keep YELIZTLI_HOST on 127.0.0.1, or enable authentication "
    "and set a password before exposing the app."
)
GENERIC_FAILURE = "Operation failed"
GENERIC_EVENT = "Application diagnostic"

# Each entry owns its component, level and full explanatory wording. Only the
# typed producer can select an entry; ordinary log text never selects one.
_OPERATIONS = {
    "directory": (
        "backend.main",
        logging.WARNING,
        "The data directory could not be made private. Choose a private directory in Setup.",
    ),
    "reference_build": (
        "backend.main",
        logging.WARNING,
        "The installed reference builds differ. Check Database Health.",
    ),
    "download executor": (
        "backend.main",
        logging.ERROR,
        "FastAPI lifespan cleanup failed for download executor",
    ),
    "database registry": (
        "backend.main",
        logging.ERROR,
        "FastAPI lifespan cleanup failed for database registry",
    ),
    "network_bind": ("backend.main", logging.WARNING, NETWORK_BIND_WARNING),
    "deleted": ("backend.services.sample_delete", logging.INFO, "Sample deletion completed."),
    "missing_database": (
        "backend.services.sample_delete",
        logging.WARNING,
        "A merged sample database is unavailable.",
    ),
    "missing_provenance": (
        "backend.services.sample_delete",
        logging.WARNING,
        "A merged sample has no recorded source information.",
    ),
    "invalid_provenance": (
        "backend.services.sample_delete",
        logging.WARNING,
        "A merged sample has invalid source information.",
    ),
    "unreadable_provenance": (
        "backend.services.sample_delete",
        logging.WARNING,
        "A merged sample's source information could not be read.",
    ),
    "jobs_recovered": (
        "backend.tasks.huey_tasks",
        logging.INFO,
        "The worker recovered interrupted background jobs.",
    ),
    "invalid_upload_encoding": (
        "backend.api.routes.ingest",
        logging.WARNING,
        "The uploaded file contains invalid UTF-8 sequences that were replaced.",
    ),
    "annotation_enqueue": (
        "backend.services.sample_merge",
        logging.WARNING,
        "The merged sample was saved. Start annotation again.",
    ),
}


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticError:
    """A producer can pass a failure without invoking its formatting methods."""

    error: BaseException

    def __repr__(self) -> str:
        return GENERIC_FAILURE


@dataclass(frozen=True, slots=True)
class _Template:
    """Only a typed producer may select the explanatory text and its fields."""

    name: str
    reason: object = None
    retry_count: object = None
    context: object = None

    def __str__(self) -> str:
        # Before application configuration, emit only a fixed diagnostic.
        if self.name == "operation" and type(self.reason) is str and self.reason in _OPERATIONS:
            return _OPERATIONS[self.reason][2]
        return GENERIC_EVENT


def record_operation(reason: str, error: BaseException | None = None) -> None:
    if type(reason) is not str or reason not in _OPERATIONS:
        component = "backend"
        level = logging.ERROR if error is not None else logging.INFO
    else:
        component, level, _ = _OPERATIONS[reason]
    logging.getLogger(component).log(
        level,
        _Template("operation", reason),
        exc_info=(type(error), error, None) if error is not None else None,
    )


def warn_network_bind() -> None:
    record_operation("network_bind")


def storage_failure(
    reason: str,
    *,
    retry_count: int | None = None,
    error: BaseException | None = None,
    context: object = None,
) -> None:
    logging.getLogger("backend.storage").warning(
        _Template("storage_failure", reason, retry_count, context),
        exc_info=(type(error), error, None) if error is not None else None,
    )


def response_withheld(operation: str) -> None:
    """Record one final, reviewed refusal independently of ambient request fields."""
    if type(operation) is not str or operation not in _AUDIT_OPERATIONS:
        return

    def emit() -> None:
        _reviewed_audit.set(operation)
        structlog.get_logger("backend.api").warning(
            "patient_response_payload_withheld", caller=operation
        )

    contextvars.Context().run(emit)


def finalize_response(presentable: bool, operation: str) -> bool:
    if not presentable:
        response_withheld(operation)
    return presentable


_reviewed_audit = contextvars.ContextVar("reviewed_response_audit", default=None)


def _neutral() -> dict[str, object]:
    return {"event": "clinical_guidance_withheld", "clinical_guidance_withheld": True}


class _Uninspectable(Exception):
    pass


def _primitive_evidence(
    value: object, budget: list[int], seen_exceptions: set[int], depth: int = 0
) -> object:
    """Inspect built-in data only. Never format a value or run a user-defined method."""
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise _Uninspectable
    kind = type(value)
    if kind is DiagnosticError:
        if not issubclass(type(value.error), BaseException):
            raise _Uninspectable
        return _primitive_evidence(value.error, budget, seen_exceptions, depth + 1)
    if value is None or kind is bool or kind is int or kind is float:
        return value
    if kind is PosixPath or kind is WindowsPath:
        # The application may pass a path object directly. A subclass remains
        # opaque so its custom formatter cannot run while preparing evidence.
        value = str(value)
        kind = str
    if kind is bytes:
        if len(value) > 8192:
            raise _Uninspectable
        # A captured helper stream is often bytes. Only strictly decodable
        # text can be checked for a hold; it is never copied into the result.
        value = value.decode("utf-8", errors="strict")
        kind = str
    if kind is str:
        if len(value) > 8192:
            raise _Uninspectable
        return value
    if kind is list or kind is tuple:
        return [_primitive_evidence(item, budget, seen_exceptions, depth + 1) for item in value]
    if kind is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str or len(key) > 512:
                raise _Uninspectable
            result[key] = _primitive_evidence(item, budget, seen_exceptions, depth + 1)
        return result
    if issubclass(kind, BaseException):
        # ASGI exception-group collapse can set a member's context to its own
        # former group. Inspect each exception once, including all group peers.
        if id(value) in seen_exceptions:
            return None
        seen_exceptions.add(id(value))
        # Built-in descriptors bypass exception subclasses' attribute hooks.
        # A custom formatter may draw on other state, which cannot be certified.
        formatter = inspect.getattr_static(kind, "__str__")
        if all(
            formatter is not known
            for known in (
                BaseException.__str__,
                OSError.__str__,
                KeyError.__str__,
                BaseExceptionGroup.__str__,
            )
        ):
            raise _Uninspectable
        parts = [
            BaseException.args.__get__(value),
            BaseException.__cause__.__get__(value),
            BaseException.__context__.__get__(value),
            BaseException.__dict__["__dict__"].__get__(value),
        ]
        if issubclass(kind, BaseExceptionGroup):
            parts.append(BaseExceptionGroup.exceptions.__get__(value))
        if issubclass(kind, OSError):
            parts.extend((OSError.filename.__get__(value), OSError.filename2.__get__(value)))
        return _primitive_evidence(parts, budget, seen_exceptions, depth + 1)
    raise _Uninspectable


_EXCEPTION_CATEGORIES = (
    (ValueError, "invalid_input"),
    (RuntimeError, "runtime_error"),
    (FileNotFoundError, "missing_file"),
    (PermissionError, "permission_denied"),
    (OSError, "storage_error"),
)


def _exception(value: object) -> BaseException | None:
    if value is True:
        return sys.exception()
    if type(value) is tuple and len(value) == 3:
        cls, exc, frame = value
        if exc is None and cls is None and frame is None:
            return None
        if cls is not type(exc) or (frame is not None and type(frame) is not TracebackType):
            raise _Uninspectable
        value = exc
    if value is None or value is False:
        return None
    if not issubclass(type(value), BaseException):
        raise _Uninspectable
    return value


def safe_event(raw: dict[str, object], component: object = None) -> dict[str, object]:
    """Project one unrendered event. Returned values are the entire allowed payload."""
    from backend.analysis.pharmacogenomics import contains_unpresentable_prescribing_identifier

    try:
        if type(raw) is not dict or any(type(key) is not str for key in raw):
            return _neutral()
        event = raw.get("event")
        template = event if type(event) is _Template else None
        operation = _reviewed_audit.get()
        if (
            operation is not None
            and type(event) is str
            and event == "patient_response_payload_withheld"
            and type(component) is str
            and component == "backend.api"
        ):
            return {
                "event": "patient_response_payload_withheld",
                "logger": "backend.api",
                "caller": operation,
            }

        exc = _exception(raw.get("exc_info"))
        inputs = dict(raw)
        inputs["exc_info"] = exc
        if template is not None:
            inputs["event"] = (
                template.name,
                template.reason,
                template.retry_count,
                template.context,
            )
        evidence = _primitive_evidence(
            [component, inputs, structlog.contextvars.get_contextvars()], [1024], set()
        )
        if contains_unpresentable_prescribing_identifier(evidence):
            return _neutral()
        if template is not None and type(template.name) is str:
            if template.name == "operation" and type(template.reason) is str:
                if template.reason in _OPERATIONS:
                    component, _, text = _OPERATIONS[template.reason]
                    return {"event": text, "logger": component}
            if template.name == "storage_failure":
                record = {"event": GENERIC_FAILURE, "logger": "backend.storage"}
                if type(template.reason) is str and template.reason in ("busy", "unavailable"):
                    record["event"] = (
                        DATABASE_BUSY if template.reason == "busy" else STORAGE_UNAVAILABLE
                    )
                    record["reason"] = template.reason
                    if type(template.retry_count) is int and 0 <= template.retry_count <= 100:
                        record["retry_count"] = template.retry_count
                return record
        if exc is None:
            for value in inputs.values():
                if type(value) is DiagnosticError:
                    exc = value.error
                    break
        if exc is not None:
            record = {"event": GENERIC_FAILURE}
            for cls, category in _EXCEPTION_CATEGORIES:
                if type(exc) is cls:
                    record["reason"] = category
                    break
            return record
        return {"event": GENERIC_EVENT}
    except Exception:
        # Classification failure cannot serialize its inputs or interrupt recovery.
        return _neutral()


# Filled only with reviewed final-response operation names, never request values.
_AUDIT_OPERATIONS = frozenset(
    {
        "backend.api.routes.admin.get_logs",
        "backend.api.routes.allergy.list_pathways",
        "backend.api.routes.allergy.pathway_detail",
        "backend.api.routes.ancestry.get_ancestry_findings",
        "backend.api.routes.ancestry.get_haplogroup_assignments",
        "backend.api.routes.ancestry.run_haplogroup",
        "backend.api.routes.apoe.list_apoe_findings",
        "backend.api.routes.array_confidence.list_array_confidence",
        "backend.api.routes.cancer._cancer_variants_response",
        "backend.api.routes.cancer.list_cancer_prs",
        "backend.api.routes.cardiovascular._cardiovascular_variants_response",
        "backend.api.routes.carrier._carrier_variants_response",
        "backend.api.routes.fh.get_fh_assessment",
        "backend.api.routes.findings._responses_are_patient_presentable",
        "backend.api.routes.findings.findings_summary",
        "backend.api.routes.fitness.list_pathways",
        "backend.api.routes.fitness.pathway_detail",
        "backend.api.routes.gene_health.list_pathways",
        "backend.api.routes.gene_health.pathway_detail",
        "backend.api.routes.gene_validity.list_gene_validity",
        "backend.api.routes.hemochromatosis.list_hemochromatosis_findings",
        "backend.api.routes.kinship.list_findings",
        "backend.api.routes.metabolic.list_metabolic_anchors",
        "backend.api.routes.metabolic.list_metabolic_prs",
        "backend.api.routes.methylation.list_pathways",
        "backend.api.routes.methylation.pathway_detail",
        "backend.api.routes.nutrigenomics.list_pathways",
        "backend.api.routes.nutrigenomics.pathway_detail",
        "backend.api.routes.parkinsons.list_findings",
        "backend.api.routes.pharma.drug_lookup",
        "backend.api.routes.pharma.gene_results",
        "backend.api.routes.pharma.medication_safety_report",
        "backend.api.routes.rare_variants.export_rare_variants_vcf",
        "backend.api.routes.rare_variants.list_rare_variant_findings",
        "backend.api.routes.risk_common.list_findings",
        "backend.api.routes.skin.list_pathways",
        "backend.api.routes.skin.pathway_detail",
        "backend.api.routes.sleep.list_pathways",
        "backend.api.routes.sleep.pathway_detail",
        "backend.api.routes.traits.list_pathways",
        "backend.api.routes.traits.list_prs",
        "backend.api.routes.traits.pathway_detail",
        "backend.api.routes.updates.get_finding_changes",
    }
)
