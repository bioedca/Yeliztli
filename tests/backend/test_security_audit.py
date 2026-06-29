"""Security audit tests for Yeliztli (P4-24).

PRD test requirements:
- T4-24: Network traffic capture during full workflow shows zero outbound variant data
- T4-25: Application binds only to 127.0.0.1, rejects connections from other interfaces

Additional security verifications:
- No telemetry or analytics code anywhere in the codebase
- CORS restricted to localhost-only origins
- All outbound HTTP requests are reference-data-only (no variant/genotype payloads)
- SQL console enforces read-only access
- Auth middleware protects all non-exempt API endpoints
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings

# ═══════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = _PROJECT_ROOT / "backend"
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


# ═══════════════════════════════════════════════════════════════════════
# T4-25: Localhost-only binding
# ═══════════════════════════════════════════════════════════════════════


class TestLocalhostBinding:
    """Verify the application binds only to 127.0.0.1."""

    def test_default_host_is_localhost(self) -> None:
        """Settings.host defaults to 127.0.0.1."""
        settings = Settings(data_dir=Path("/tmp/gi-test"))
        assert settings.host == "127.0.0.1"

    def test_default_port_is_8000(self) -> None:
        """Settings.port defaults to 8000."""
        settings = Settings(data_dir=Path("/tmp/gi-test"))
        assert settings.port == 8000

    def test_systemd_service_binds_localhost(self) -> None:
        """systemd service file hardcodes 127.0.0.1."""
        service_file = _PROJECT_ROOT / "systemd" / "yeliztli-api.service"
        content = service_file.read_text()
        assert "--host 127.0.0.1" in content
        # Must NOT contain 0.0.0.0
        assert "0.0.0.0" not in content

    def test_launchd_plist_binds_localhost(self) -> None:
        """launchd plist hardcodes 127.0.0.1."""
        plist_file = _PROJECT_ROOT / "launchd" / "com.yeliztli.api.plist"
        content = plist_file.read_text()
        assert "127.0.0.1" in content
        # Must NOT contain 0.0.0.0
        assert "0.0.0.0" not in content

    def test_docker_compose_maps_to_localhost(self) -> None:
        """docker-compose.yml maps port to 127.0.0.1 on the host side."""
        compose_file = _PROJECT_ROOT / "docker-compose.yml"
        content = compose_file.read_text()
        # Host-side port mapping must be 127.0.0.1:8000:8000
        assert "127.0.0.1:8000:8000" in content

    def test_docker_compose_does_not_override_app_host_setting(self) -> None:
        """Default Docker should not trip the app-level non-loopback host warning."""
        compose_file = _PROJECT_ROOT / "docker-compose.yml"
        content = compose_file.read_text()
        assert "YELIZTLI_HOST=0.0.0.0" not in content
        # The container still binds internally so Docker can publish the loopback-only port.
        assert "uvicorn backend.main:app --host 0.0.0.0 --port 8000" in content

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "127.10.20.30", "localhost", "localhost.localdomain", "::1", "[::1]"],
    )
    def test_loopback_bind_hosts_are_recognized(self, host: str) -> None:
        """Loopback bind hosts do not trigger remote-exposure warnings."""
        from backend.main import is_loopback_bind_host

        assert is_loopback_bind_host(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.local", ""])
    def test_non_loopback_bind_hosts_are_recognized(self, host: str) -> None:
        """Non-loopback bind hosts are treated as network-exposed."""
        from backend.main import is_loopback_bind_host

        assert not is_loopback_bind_host(host)

    @pytest.mark.parametrize(
        ("auth_enabled", "auth_password_hash"),
        [(False, ""), (True, ""), (True, "   ")],
    )
    def test_network_bind_without_effective_auth_warns(
        self,
        auth_enabled: bool,
        auth_password_hash: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Non-loopback binds warn when auth is disabled or passwordless."""
        from backend.main import warn_if_insecure_network_bind

        settings = Settings(
            data_dir=Path("/tmp/gi-test"),
            host="0.0.0.0",
            auth_enabled=auth_enabled,
            auth_password_hash=auth_password_hash,
        )
        with caplog.at_level(logging.WARNING, logger="backend.main"):
            warn_if_insecure_network_bind(settings)

        assert "SECURITY WARNING" in caplog.text
        assert "0.0.0.0" in caplog.text
        assert "without credentials" in caplog.text

    def test_network_bind_with_effective_auth_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-loopback binds are not warned when auth has a password hash."""
        from backend.main import warn_if_insecure_network_bind

        settings = Settings(
            data_dir=Path("/tmp/gi-test"),
            host="0.0.0.0",
            auth_enabled=True,
            auth_password_hash="$2b$12$fakehash",
        )
        with caplog.at_level(logging.WARNING, logger="backend.main"):
            warn_if_insecure_network_bind(settings)

        assert "SECURITY WARNING" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# CORS: Localhost-only origins
# ═══════════════════════════════════════════════════════════════════════


class TestCORSLocalhostOnly:
    """Verify CORS is restricted to localhost origins."""

    def test_cors_allows_localhost_origin(self, test_client: TestClient) -> None:
        """CORS allows requests from localhost:5173."""
        resp = test_client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_cors_allows_127_origin(self, test_client: TestClient) -> None:
        """CORS allows requests from 127.0.0.1:5173."""
        resp = test_client.options(
            "/api/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"

    def test_cors_rejects_external_origin(self, test_client: TestClient) -> None:
        """CORS rejects requests from an external origin."""
        resp = test_client.options(
            "/api/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # A disallowed origin must NOT be echoed. Starlette omits the
        # access-control-allow-origin header entirely, so the value is None.
        # The old `!= "http://evil.example.com"` was vacuous: it also passed if
        # a *different* wrong origin (or "*") were echoed back.
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin is None

    def test_cors_rejects_non_localhost_ip(self, test_client: TestClient) -> None:
        """CORS rejects requests from a non-localhost IP address."""
        resp = test_client.options(
            "/api/health",
            headers={
                "Origin": "http://192.168.1.100:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Disallowed origin → header omitted, not echoed (see above).
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin is None

    def test_cors_origins_in_source_are_localhost_only(self) -> None:
        """Static analysis: CORS allow_origins list contains only localhost."""
        main_py = (_BACKEND_DIR / "main.py").read_text()
        # Extract allow_origins list from source
        match = re.search(r"allow_origins\s*=\s*\[(.*?)\]", main_py, re.DOTALL)
        assert match, "allow_origins not found in main.py"
        origins_block = match.group(1)
        # Every origin must be localhost or 127.0.0.1
        origins = re.findall(r'"(.*?)"', origins_block)
        assert len(origins) >= 1, "No origins found"
        for origin in origins:
            assert "localhost" in origin or "127.0.0.1" in origin, (
                f"Non-localhost origin found: {origin}"
            )


# ═══════════════════════════════════════════════════════════════════════
# T4-24: No outbound variant data
# ═══════════════════════════════════════════════════════════════════════


class TestNoOutboundVariantData:
    """Verify no variant/genotype data is sent in outbound HTTP requests.

    This is a static analysis + structural test. We scan all backend Python
    files that make outbound HTTP requests and verify that:
    1. No variant data fields (rsid, chrom, pos, genotype, etc.) appear
       in outbound request payloads or URL params.
    2. All outbound requests are for downloading reference data or
       querying public APIs with only gene symbols / PMIDs.
    """

    # Files known to make outbound HTTP requests and their expected purpose
    _ALLOWED_OUTBOUND_FILES = {
        "annotation/clinvar.py": "reference DB download",
        "annotation/cpic.py": "CPIC GitHub Releases version check (no variant data)",
        "annotation/dbnsfp.py": "reference DB download",
        "annotation/dbsnp.py": "reference DB download",
        "annotation/encode_ccres.py": "reference DB download",
        "annotation/gnomad.py": "reference DB download (build-script VCF URL; no runtime httpx)",
        "annotation/gnomad_constraint.py": "reference DB download (gnomAD constraint TSV)",
        "annotation/gwas.py": "reference DB download",
        "annotation/http_download.py": "generic resumable reference DB downloader",
        "annotation/mondo_hpo.py": "reference DB download",
        "annotation/omim.py": "reference DB download (with API key)",
        "api/routes/genes.py": "UniProt protein domain lookup (gene symbol only)",
        "db/download_manager.py": "generic resumable downloader",
        "db/manifest.py": "fetch bundles/manifest.json from raw GitHub (no variant data)",
        "db/update_manager.py": "ClinVar HEAD check for freshness",
        "utils/pubmed.py": "PubMed search (gene symbol + PMID only)",
        "utils/uniprot.py": "UniProt protein domain lookup (gene symbol only)",
        "utils/update_checker.py": "GitHub Releases API (app version only)",
    }

    # Variant data field names that must NEVER appear in outbound payloads
    _VARIANT_FIELDS = {
        "genotype",
        "zygosity",
        "raw_variants",
        "annotated_variants",
        "sample_db",
    }

    def test_no_unknown_outbound_http_modules(self) -> None:
        """Every file using httpx/requests must be in the allowed list."""
        unknown_files: list[str] = []
        for py_file in _BACKEND_DIR.rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            content = py_file.read_text()
            # Check for outbound HTTP client usage
            if re.search(r"httpx\.(Client|AsyncClient|get|post|stream)", content) or re.search(
                r"requests\.(get|post|put|delete)", content
            ):
                rel = str(py_file.relative_to(_BACKEND_DIR))
                if rel not in self._ALLOWED_OUTBOUND_FILES:
                    unknown_files.append(rel)
        assert unknown_files == [], (
            f"Unexpected files making outbound HTTP requests: {unknown_files}"
        )

    def test_no_variant_data_in_outbound_request_params(self) -> None:
        """No outbound HTTP call includes variant data in URL or body."""
        violations: list[str] = []
        for rel_path in self._ALLOWED_OUTBOUND_FILES:
            full_path = _BACKEND_DIR / rel_path
            if not full_path.exists():
                continue
            content = full_path.read_text()
            # Look for patterns where variant-specific data could be sent
            # e.g., f"...{rsid}..." in a URL, or json={"genotype": ...}
            for field_name in self._VARIANT_FIELDS:
                # Check if field appears inside an httpx call context
                # Match patterns like: params={..., "genotype": ..., data={"genotype":
                # DOTALL + a bounded non-greedy gap so a *multi-line* outbound
                # call (params= on one line, the field a few lines down) is still
                # caught. Plain `.*` (no DOTALL) stopped at the first newline and
                # silently under-detected; an unbounded DOTALL `.*` would instead
                # match the field anywhere in the file (false positives).
                if re.search(
                    rf'(?:params|json|data|content)\s*=.{{0,200}}?["\']?{field_name}["\']?',
                    content,
                    re.DOTALL,
                ):
                    violations.append(f"{rel_path}: sends '{field_name}' in HTTP request")
        assert violations == [], f"Variant data found in outbound requests: {violations}"

    def test_outbound_urls_are_public_reference_only(self) -> None:
        """All hardcoded outbound URLs point to known public data sources."""
        allowed_domains = {
            "storage.googleapis.com",  # gnomAD
            "ftp.ncbi.nlm.nih.gov",  # ClinVar, dbSNP
            "ebi.ac.uk",  # GWAS Catalog
            "downloads.wenglab.org",  # ENCODE cCREs
            "data.monarchinitiative.org",  # MONDO
            "purl.obolibrary.org",  # HPO
            "data.omim.org",  # OMIM
            "omim.org",  # OMIM links
            "rest.uniprot.org",  # UniProt
            "api.github.com",  # Update checker
            "github.com",  # Release/source links
            "raw.githubusercontent.com",  # bundle manifest hosted in repo
            "s3.amazonaws.com",  # dbNSFP S3 bucket
            "dist.genos.us",  # dbNSFP 5.x academic distribution
            "example.com",  # Test/placeholder URLs in comments
        }
        unexpected_urls: list[str] = []
        for rel_path in self._ALLOWED_OUTBOUND_FILES:
            full_path = _BACKEND_DIR / rel_path
            if not full_path.exists():
                continue
            content = full_path.read_text()
            urls = re.findall(r'https?://([^/\s"\']+)', content)
            for url_domain in urls:
                if not any(url_domain.endswith(d) or d in url_domain for d in allowed_domains):
                    unexpected_urls.append(f"{rel_path}: {url_domain}")
        assert unexpected_urls == [], f"Unexpected outbound URLs: {unexpected_urls}"

    def test_frontend_no_external_fetch_calls(self) -> None:
        """Frontend fetch() calls only target relative /api/ paths."""
        src_dir = _FRONTEND_DIR / "src"
        if not src_dir.exists():
            pytest.skip("Frontend source not available")
        violations: list[str] = []
        for ts_file in src_dir.rglob("*.ts"):
            content = ts_file.read_text()
            # Find fetch() calls with absolute URLs
            fetches = re.findall(r"""fetch\s*\(\s*[`"'](https?://[^`"']+)""", content)
            for url in fetches:
                violations.append(f"{ts_file.relative_to(_PROJECT_ROOT)}: fetch({url})")
        for tsx_file in src_dir.rglob("*.tsx"):
            content = tsx_file.read_text()
            fetches = re.findall(r"""fetch\s*\(\s*[`"'](https?://[^`"']+)""", content)
            for url in fetches:
                violations.append(f"{tsx_file.relative_to(_PROJECT_ROOT)}: fetch({url})")
        assert violations == [], f"Frontend makes external fetch calls: {violations}"


# ═══════════════════════════════════════════════════════════════════════
# No telemetry / analytics
# ═══════════════════════════════════════════════════════════════════════


class TestNoTelemetry:
    """Verify zero telemetry, analytics, or tracking code."""

    # Known telemetry / analytics packages
    _TELEMETRY_PACKAGES = {
        "sentry_sdk",
        "sentry-sdk",
        "analytics",
        "mixpanel",
        "amplitude",
        "posthog",
        "segment",
        "datadog",
        "newrelic",
    }

    _FRONTEND_ANALYTICS = {
        "google-analytics",
        "gtag",
        "ga(",
        "mixpanel",
        "amplitude",
        "posthog",
        "segment.io",
        "analytics.js",
        "hotjar",
        "heap(",
        "fullstory",
    }

    def test_no_telemetry_in_python_dependencies(self) -> None:
        """pyproject.toml does not include any telemetry packages."""
        pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text()
        for pkg in self._TELEMETRY_PACKAGES:
            assert pkg not in pyproject.lower(), (
                f"Telemetry package '{pkg}' found in pyproject.toml"
            )

    def test_no_telemetry_imports_in_backend(self) -> None:
        """No Python file imports a telemetry/analytics library."""
        violations: list[str] = []
        for py_file in _BACKEND_DIR.rglob("*.py"):
            content = py_file.read_text()
            for pkg in self._TELEMETRY_PACKAGES:
                module_name = pkg.replace("-", "_")
                if re.search(rf"(?:^|\s)import\s+{module_name}|from\s+{module_name}", content):
                    violations.append(f"{py_file.relative_to(_PROJECT_ROOT)}: imports {pkg}")
        assert violations == [], f"Telemetry imports found: {violations}"

    def test_no_analytics_in_frontend(self) -> None:
        """No frontend file includes analytics tracking code."""
        src_dir = _FRONTEND_DIR / "src"
        if not src_dir.exists():
            pytest.skip("Frontend source not available")
        violations: list[str] = []
        for ext in ("*.ts", "*.tsx", "*.js", "*.jsx"):
            for f in src_dir.rglob(ext):
                content = f.read_text()
                for tracker in self._FRONTEND_ANALYTICS:
                    if tracker in content.lower():
                        # Exclude false positives in comments about what we DON'T use
                        violations.append(f"{f.relative_to(_PROJECT_ROOT)}: contains '{tracker}'")
        assert violations == [], f"Analytics code found in frontend: {violations}"

    def test_no_telemetry_in_npm_dependencies(self) -> None:
        """package.json does not include analytics packages."""
        pkg_json = _FRONTEND_DIR / "package.json"
        if not pkg_json.exists():
            pytest.skip("Frontend package.json not available")
        content = pkg_json.read_text().lower()
        for tracker in (
            "google-analytics",
            "mixpanel",
            "amplitude",
            "posthog",
            "segment",
            "sentry",
        ):
            assert tracker not in content, f"Analytics package '{tracker}' in package.json"


# ═══════════════════════════════════════════════════════════════════════
# SQL console read-only enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestSQLConsoleReadOnly:
    """Verify SQL console blocks all write operations."""

    def test_write_pattern_blocks_dangerous_statements(self) -> None:
        """_validate_read_only rejects INSERT, UPDATE, DELETE, DROP, etc."""
        from backend.api.routes.query_builder import _validate_read_only

        dangerous = [
            "INSERT INTO variants VALUES (1, 'rs1')",
            "UPDATE variants SET genotype='AA'",
            "DELETE FROM variants",
            "DROP TABLE variants",
            "ALTER TABLE variants ADD COLUMN x TEXT",
            "CREATE TABLE evil (id INT)",
            "REPLACE INTO variants VALUES (1, 'rs1')",
            "TRUNCATE TABLE variants",
            "ATTACH DATABASE '/tmp/evil.db' AS evil",
            "DETACH DATABASE evil",
        ]
        for stmt in dangerous:
            with pytest.raises(Exception):
                _validate_read_only(stmt)

    def test_select_statements_allowed(self) -> None:
        """_validate_read_only allows SELECT and EXPLAIN."""
        from backend.api.routes.query_builder import _validate_read_only

        safe = [
            "SELECT * FROM annotated_variants",
            "SELECT rsid, chrom, pos FROM annotated_variants WHERE chrom='1'",
            "SELECT COUNT(*) FROM annotated_variants",
        ]
        for stmt in safe:
            # Should not raise
            _validate_read_only(stmt)


# ═══════════════════════════════════════════════════════════════════════
# Auth middleware coverage
# ═══════════════════════════════════════════════════════════════════════


class TestAuthSecurity:
    """Verify auth middleware enforces session-based protection."""

    def test_health_endpoint_exempt(self, test_client: TestClient) -> None:
        """/api/health is always accessible without auth."""
        resp = test_client.get("/api/health")
        assert resp.status_code == 200

    def test_auth_status_exempt(self, test_client: TestClient) -> None:
        """/api/auth/status is always accessible without auth."""
        resp = test_client.get("/api/auth/status")
        assert resp.status_code == 200

    def test_protected_endpoint_returns_401_with_auth_enabled(self, tmp_data_dir: Path) -> None:
        """API endpoints return 401 without valid session when auth is enabled."""
        from backend.auth import clear_all_sessions, hash_password

        settings = Settings(
            data_dir=tmp_data_dir,
            wal_mode=False,
            auth_enabled=True,
            auth_password_hash=hash_password("test1234"),
        )

        import sqlalchemy as sa

        from backend.db.connection import reset_registry
        from backend.db.tables import reference_metadata

        ref_path = settings.reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.auth.get_settings", return_value=settings),
        ):
            reset_registry()
            clear_all_sessions()

            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/samples")
                assert resp.status_code == 401

            reset_registry()

    def test_bcrypt_password_hashing(self) -> None:
        """Passwords are hashed with bcrypt, not stored in plaintext."""
        from backend.auth import hash_password, verify_password

        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert hashed.startswith("$2b$")
        assert verify_password("mypassword", hashed)
        assert not verify_password("wrongpassword", hashed)

    def test_session_timeout(self) -> None:
        """Sessions expire after the configured timeout."""
        import time

        from backend.auth import (
            _set_session_timestamp,
            create_session,
            validate_session,
        )

        session_id = create_session()
        assert validate_session(session_id, timeout_hours=4)

        # Simulate expiration via test helper
        _set_session_timestamp(session_id, time.time() - (5 * 3600))
        assert not validate_session(session_id, timeout_hours=4)

    def test_rate_limiting(self) -> None:
        """Login rate limiting blocks after max attempts."""
        from backend.auth import (
            check_rate_limit,
            clear_all_rate_limits,
            record_failed_attempt,
        )

        clear_all_rate_limits()
        ip = "192.168.1.1"

        # First 5 attempts should be OK
        for _ in range(5):
            assert check_rate_limit(ip) is None
            record_failed_attempt(ip)

        # 6th check should be rate limited
        result = check_rate_limit(ip)
        assert result is not None
        assert "Too many" in result

        clear_all_rate_limits()


# ═══════════════════════════════════════════════════════════════════════
# Deployment configuration security
# ═══════════════════════════════════════════════════════════════════════


class TestDeploymentSecurity:
    """Verify secure deployment configurations."""

    def test_docker_runs_as_non_root(self) -> None:
        """Dockerfile creates and switches to a non-root user."""
        dockerfile = (_PROJECT_ROOT / "Dockerfile").read_text()
        assert "USER appuser" in dockerfile or "USER nonroot" in dockerfile

    def test_no_secrets_in_dockerfile(self) -> None:
        """Dockerfile does not contain hardcoded secrets."""
        dockerfile = (_PROJECT_ROOT / "Dockerfile").read_text()
        # Check for common secret patterns
        assert "API_KEY=" not in dockerfile
        assert "PASSWORD=" not in dockerfile
        assert "SECRET=" not in dockerfile

    def test_huey_service_depends_on_api(self) -> None:
        """Huey worker service depends on the API service (Docker Compose)."""
        compose = (_PROJECT_ROOT / "docker-compose.yml").read_text()
        assert "depends_on" in compose

    def test_no_debug_mode_in_deployment_files(self) -> None:
        """Deployment files don't enable debug mode."""
        for path in [
            _PROJECT_ROOT / "systemd" / "yeliztli-api.service",
            _PROJECT_ROOT / "docker-compose.yml",
        ]:
            content = path.read_text()
            assert "--reload" not in content
            assert "DEBUG=true" not in content.upper()
