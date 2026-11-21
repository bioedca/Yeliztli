"""Serve a historical synthetic sample while the browser reads the variant detail."""

import os
import runpy
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    directory = tempfile.TemporaryDirectory(prefix="yeliztli-vep-witness-")
    with directory as temporary:
        os.environ["YELIZTLI_DATA_DIR"] = temporary
        os.environ["YELIZTLI_MANIFEST_PATH"] = str(Path("bundles/manifest.json").resolve())

        import uvicorn
        from fastapi import FastAPI

        from backend.api.routes.variant_detail import router as detail_router
        from backend.api.routes.variants import router as variants_router
        from backend.db.connection import get_registry

        probe = Path(__file__).resolve().parents[1] / "probe_offline_route.py"
        with patch("socket.socket.connect", side_effect=OSError("Offline reproduction")):
            runpy.run_path(str(probe))["measure"](Path(temporary))

        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            try:
                yield
            finally:
                # Uvicorn re-raises its termination signal after ASGI shutdown.
                get_registry().dispose_all()
                directory.cleanup()

        app = FastAPI(lifespan=lifespan)
        app.include_router(variants_router, prefix="/api")
        app.include_router(detail_router, prefix="/api")

        @app.get("/witness-health")
        def health() -> dict:
            return {"ready": True}

        try:
            uvicorn.run(app, host="127.0.0.1", port=42715, log_level="error")
        finally:
            get_registry().dispose_all()


if __name__ == "__main__":
    main()
