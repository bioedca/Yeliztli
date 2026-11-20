"""Launch the local task consumer with the application's operator console."""

from backend.operator_console import run_worker

if __name__ == "__main__":
    raise SystemExit(run_worker())
