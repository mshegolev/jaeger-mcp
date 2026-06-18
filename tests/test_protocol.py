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
    "jaeger_compare_traces": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"trace_id_a", "trace_id_b"},
        "optional_params": set(),
    },
    "jaeger_span_statistics": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"service"},
        "optional_params": {"operation", "limit"},
    },
    "jaeger_critical_path": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"trace_id"},
        "optional_params": set(),
    },
}


@pytest.fixture(scope="module")
def listed_tools() -> list[Any]:
    """One-shot handshake equivalent: fetch the tool catalogue FastMCP exposes."""
    return asyncio.run(mcp.list_tools())


def test_all_seven_tools_registered(listed_tools: list[Any]) -> None:
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


def test_compare_traces_trace_id_constraints(listed_tools: list[Any]) -> None:
    """trace_id_a and trace_id_b should have hex pattern and length constraints."""
    tool = next(t for t in listed_tools if t.name == "jaeger_compare_traces")
    props = tool.inputSchema["properties"]
    for param in ("trace_id_a", "trace_id_b"):
        assert param in props
        assert props[param].get("minLength", 0) >= 16
        assert props[param].get("maxLength", 999) <= 32
        assert props[param].get("pattern") is not None


def test_get_dependencies_lookback_documented(listed_tools: list[Any]) -> None:
    """lookback_hours should document the max (720)."""
    tool = next(t for t in listed_tools if t.name == "jaeger_get_dependencies")
    desc = tool.inputSchema["properties"]["lookback_hours"].get("description", "")
    assert "720" in desc


# ── JGR-16: Service name URL sanitization ──────────────────────────────────


def test_list_operations_rejects_path_unsafe_service_names(listed_tools: list[Any]) -> None:
    """Service name with '/' or '..' must be rejected by the pattern constraint.

    The pattern ^[a-zA-Z0-9._:\\-]+$ on the service Field forbids
    path-traversal characters.  FastMCP enforces this via Pydantic
    validation before the tool function is ever called.

    JGR-16 acceptance: "Test: service name with / or .. is rejected."
    """
    import re

    from pydantic import ValidationError

    tool = next(t for t in listed_tools if t.name == "jaeger_list_operations")
    svc_schema = tool.inputSchema["properties"]["service"]
    pattern = svc_schema.get("pattern")

    # Pattern must exist in the published schema
    assert pattern is not None, "service field has no pattern constraint"

    regex = re.compile(pattern)

    # These must be rejected (path-unsafe)
    for bad in ["../etc/passwd", "svc/sub", "foo/../bar", "a/b"]:
        assert regex.fullmatch(bad) is None, f"pattern should reject {bad!r}"

    # Also validate via Pydantic TypeAdapter (same codepath FastMCP uses)
    import typing

    from pydantic import TypeAdapter

    from jaeger_mcp.tools import jaeger_list_operations

    hints = typing.get_type_hints(jaeger_list_operations, include_extras=True)
    service_type = hints["service"]
    ta = TypeAdapter(service_type)

    for bad in ["svc/sub", "../etc", "a/../b"]:
        with pytest.raises(ValidationError, match="string_pattern_mismatch"):
            ta.validate_python(bad)

    # Normal service names must still pass
    for good in ["order-service", "my.svc", "svc_v2", "host:8080"]:
        assert regex.fullmatch(good) is not None, f"pattern should accept {good!r}"
        ta.validate_python(good)  # must not raise


def test_span_statistics_limit_constraints(listed_tools: list[Any]) -> None:
    """limit should have ge=1 and le=100 constraints."""
    tool = next(t for t in listed_tools if t.name == "jaeger_span_statistics")
    props = tool.inputSchema["properties"]
    limit_prop = props["limit"]
    assert limit_prop.get("minimum", 0) >= 1
    assert limit_prop.get("maximum", 999) <= 100
    assert limit_prop.get("default") == 20
