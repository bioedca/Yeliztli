"""Marker for responses the patient-presentability guard replaced (#2297, #2519).

:func:`backend.analysis.pharmacogenomics.is_patient_presentable_response_payload`
fails closed: a route whose assembled response it refuses serves an empty one
instead. Without a marker that empty fallback is indistinguishable from a
sample with genuinely nothing to show, so a page would tell the user there are
no results -- and to run annotation, which cannot help.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

# The ``detail.code`` a refused single-item response carries. The page can then
# say the item is withheld instead of missing, without offering a retry that
# cannot succeed. It never says what was held.
PATIENT_RESPONSE_WITHHELD_CODE = "patient_response_withheld"


class WithheldMarker(BaseModel):
    """Base for list responses whose empty fallback can stand in for a refused one.

    ``withheld`` is True only on that fallback, so the page can say the results
    are withheld instead of that there are none. It never says what was held.
    """

    withheld: bool = False


def withheld_not_found(message: str) -> HTTPException:
    """The 404 for a single assembled item the guard refused (#2520).

    The status stays 404, as before, so nothing that already treats it as
    absent changes. ``detail`` becomes ``{"code": "patient_response_withheld",
    "message": message}``, which a page can tell from a plain not-found.
    """
    return HTTPException(
        status_code=404,
        detail={"code": PATIENT_RESPONSE_WITHHELD_CODE, "message": message},
    )
