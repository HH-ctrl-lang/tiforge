"""Serve the exported evidence snapshot over MCP Streamable HTTP."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .evidence import EvidenceStore, SNAPSHOT


def create_server(store, host):
    server = FastMCP("TiForge Evidence", host=host, port=8000, stateless_http=True, json_response=True)

    @server.tool()
    def list_runs(case_id: str, workflow_version: str) -> dict:
        """列出指定用例与工作流版本的已授权运行。"""
        return store.list_runs(case_id, workflow_version)

    @server.tool()
    def read_run_evidence(run_id: str, node: str | None = None) -> dict:
        """省略 node 时读取运行与异常摘要，指定节点 ID 或名称时读取节点证据与 evidence_id。"""
        return store.read_run_evidence(run_id, node)

    return server


def main():
    snapshot = Path(os.environ.get("TIFORGE_EVIDENCE_FILE", str(SNAPSHOT)))
    host = os.environ.get("TIFORGE_MCP_HOST", "127.0.0.1")
    create_server(EvidenceStore(snapshot), host).run(transport="streamable-http")


if __name__ == "__main__":
    main()
