"""Marker for responses the patient-presentability guard replaced (#2297, #2519).

:func:`backend.analysis.pharmacogenomics.is_patient_presentable_response_payload`
fails closed: a route whose assembled response it refuses serves an empty one
instead. Without a marker that empty fallback is indistinguishable from a
sample with genuinely nothing to show, so a page would tell the user there are
no results -- and to run annotation, which cannot help.
"""

from __future__ import annotations

from pydantic import BaseModel


class WithheldMarker(BaseModel):
    """Base for list responses whose empty fallback can stand in for a refused one.

    ``withheld`` is True only on that fallback, so the page can say the results
    are withheld instead of that there are none. It never says what was held.
    """

    withheld: bool = False
