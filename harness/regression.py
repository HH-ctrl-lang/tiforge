"""Compatibility module for the original v00 entry point."""

from .engine.common import check_composition, json_object, load_batch, load_cases, node_fields, read_object, read_rows
from .engine.v00 import compare, evaluate_run, main


if __name__ == "__main__":
    raise SystemExit(main())
