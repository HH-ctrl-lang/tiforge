"""Run a selected version of the TiForge Harness."""

import argparse

from .core import propose
from .engine import v00, v01


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    versions = parser.add_subparsers(dest="version", required=True)
    v00.configure(versions.add_parser("v00", help="原有的双版本回归评测"))
    v01.configure(versions.add_parser("v01", help="开发版归档诊断与版本对照"))
    propose.configure(versions.add_parser("propose", help="从已有归档生成改动提案"))
    args = parser.parse_args()
    return args.run(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
