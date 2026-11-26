"""Compatibility module for the original v01 entry point."""

from .engine.v01 import DESIGN, FORMAT_REPLY, GATE, GATE_OUTCOMES, LITERATURE, PROPOSALS, REVIEW
from .engine.v01 import audit, by_candidate, compare, evaluate, main, node_inputs


if __name__ == "__main__":
    raise SystemExit(main())
