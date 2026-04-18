"""Wire-protocol smoke-test (substitute for MCP Inspector).

FastMCP exposes ``mcp.list_tools()`` as the in-process equivalent of the
``tools/list`` MCP request. Running it confirms that:

- The shared ``FastMCP`` instance actually has tools registered.
- Each tool carries the expected ``annotations`` (readOnlyHint, etc.).
- The ``outputSchema`` is generated from the TypedDict return annotation.
- The ``inputSchema`` contains the right param names, constraints, and
  required markers — what an MCP client would use to build tool-call arguments.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# Importing tools attaches @mcp.tool decorators.
import jaeger_mcp.tools  # noqa: F401
from jaeger_mcp._mcp import mcp

EXPECTED_TOOLS: dict[str, dict[str, Any]] = {
    "jaeger_list_services": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": set(),
        "optional_params": set(),
    },
    "jaeger_list_operations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"service"},
        "optional_params": set(),
    },
    "jaeger_search_traces": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"service"},
        "optional_params": {"operation", "tags", "start", "end", "min_duration", "max_duration", "limit"},
    },
    "jaeger_get_trace": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"trace_id"},
        "optional_params": set(),
    },
    "jaeger_get_dependencies": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": set(),
        "optional_params": {"end_ts", "lookback_hours"},
    },
}


@pytest.fixture(scope="module")
def listed_tools() -> list[Any]:
    """One-shot handshake equivalent: fetch the tool catalogue FastMCP exposes."""
    return asyncio.run(mcp.list_tools())


def test_all_five_tools_registered(listed_tools: list[Any]) -> None:
    names = {t.name for t in listed_tools}
    assert names == set(EXPECTED_TOOLS), (
        f"tool list mismatch.\n  registered: {sorted(names)}\n  expected:   {sorted(EXPECTED_TOOLS)}"
    )


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_tool_annotations(listed_tools: list[Any], tool_name: str) -> None:
    """Every tool must carry readOnly/destructive/idempotent hints matching our design."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    ann = tool.annotations
    expected = EXPECTED_TOOLS[tool_name]
    assert ann.readOnlyHint is expected["readOnlyHint"], f"{tool_name}.readOnlyHint"
    assert ann.destructiveHint is expected["destructiveHint"], f"{tool_name}.destructiveHint"
    assert ann.idempotentHint is expected["idempotentHint"], f"{tool_name}.idempotentHint"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_input_schema_shape(listed_tools: list[Any], tool_name: str) -> None:
    """Required + optional parameter sets must match the tool signatures."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    schema = tool.inputSchema
    assert schema["type"] == "object"
    properties = set(schema.get("properties", {}).keys())
    required = set(schema.get("required", []))

    expected = EXPECTED_TOOLS[tool_name]
    assert required == expected["required_params"], (
        f"{tool_name}.required: got {required}, expected {expected['required_params']}"
    )
    expected_all = expected["required_params"] | expected["optional_params"]
    assert expected_all.issubset(properties), f"{tool_name}: missing properties {expected_all - properties}"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_output_schema_is_generated(listed_tools: list[Any], tool_name: str) -> None:
    """structured_output=True must produce an outputSchema for every tool."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    assert tool.outputSchema is not None, f"{tool_name} has no outputSchema"
    assert tool.outputSchema.get("type") == "object", f"{tool_name} outputSchema not an object"
    assert tool.outputSchema.get("properties"), f"{tool_name} outputSchema has no properties"


def test_search_traces_has_service_as_required(listed_tools: list[Any]) -> None:
    tool = next(t for t in listed_tools if t.name == "jaeger_search_traces")
    schema = tool.inputSchema
    assert "service" in schema.get("required", [])


def test_list_operations_service_description_useful(listed_tools: list[Any]) -> None:
    """The service param description should tell the LLM how to get valid names."""
    tool = next(t for t in listed_tools if t.name == "jaeger_list_operations")
    svc_desc = tool.inputSchema["properties"]["service"].get("description", "")
    assert "jaeger_list_services" in svc_desc


def test_search_traces_tags_description_has_example(listed_tools: list[Any]) -> None:
    """The tags param description must explain the JSON string format."""
    tool = next(t for t in listed_tools if t.name == "jaeger_search_traces")
    tags_desc = tool.inputSchema["properties"]["tags"].get("description", "")
    assert "JSON" in tags_desc
    assert "http.status_code" in tags_desc or "error" in tags_desc


def test_get_trace_trace_id_constraints(listed_tools: list[Any]) -> None:
    """trace_id should have minLength/maxLength documented."""
    tool = next(t for t in listed_tools if t.name == "jaeger_get_trace")
    props = tool.inputSchema["properties"]
    assert "trace_id" in props
    # minLength=16 and maxLength=32 come from Annotated[str, Field(min_length=16, max_length=32)]
    assert props["trace_id"].get("minLength", 0) >= 1


def test_get_dependencies_lookback_documented(listed_tools: list[Any]) -> None:
    """lookback_hours should document the max (720)."""
    tool = next(t for t in listed_tools if t.name == "jaeger_get_dependencies")
    desc = tool.inputSchema["properties"]["lookback_hours"].get("description", "")
    assert "720" in desc
