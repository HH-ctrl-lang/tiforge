"""Read the business result returned by a Runtime MCP node."""


def get_user_fields(node):
    outputs = node.get("outputs")
    if not isinstance(outputs, dict):
        return {}
    user_fields = outputs.get("userFields")
    return user_fields if isinstance(user_fields, dict) else outputs


def get_tool_result(node):
    node_type = str(node.get("node_type") or "").casefold()
    if node_type.rsplit(".", 1)[-1] != "mcp":
        return None
    fields = get_user_fields(node)
    if not fields:
        return None
    outputs = node["outputs"]
    result = {
        key: fields[key]
        for key in ("status", "error_code", "error_message", "call_id", "tool_version",
                    "version", "dataset_version", "scenario_id", "elapsed_ms")
        if key in fields
    }
    for source in (fields, outputs, outputs.get("systemFields")):
        if isinstance(source, dict) and isinstance(source.get("isSuccess"), bool):
            result.setdefault("transport_succeeded", source["isSuccess"])
    return result or None
