"""Report export errors for missing Playwright browser binaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from backend.reports.generator import _html_to_pdf
from backend.reports.variant_card import _html_to_pdf_single_page, _html_to_png


class _FakePlaywrightContext:
    def __init__(self) -> None:
        self.launch = AsyncMock(side_effect=PlaywrightError("Executable doesn't exist"))

    async def __aenter__(self) -> SimpleNamespace:
        return SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("renderer", "export_kind"),
    [
        (_html_to_pdf, "PDF"),
        (_html_to_pdf_single_page, "PDF"),
        (_html_to_png, "PNG"),
    ],
)
async def test_missing_playwright_chromium_reports_install_hint(
    renderer: Callable[[str], Awaitable[bytes]],
    export_kind: str,
) -> None:
    """A missing browser binary is translated to the routes' RuntimeError/503 path."""
    with patch("playwright.async_api.async_playwright", return_value=_FakePlaywrightContext()):
        with pytest.raises(RuntimeError) as exc_info:
            await renderer("<html><body>report</body></html>")

    message = str(exc_info.value)
    assert f"Playwright Chromium is required for {export_kind} generation" in message
    assert "python -m playwright install chromium" in message
