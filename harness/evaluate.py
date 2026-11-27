"""Run the independent archive evaluator."""

from .core.evaluate import evaluate_archive, evaluate_run, load_cases, load_run, main


__all__ = ["evaluate_archive", "evaluate_run", "load_cases", "load_run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
