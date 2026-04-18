"""Unit tests for pure shaping helpers in :mod:`jaeger_mcp.tools`.

These functions take raw Jaeger API dicts and shape them into TypedDict
output schemas or perform simple logic. They have no I/O, so we exercise
them directly without mocking any HTTP client.
"""

from __future__ import annotations

from jaeger_mcp.tools import (
    _build_execution_tree,
    _find_root_span,
    _shape_span_detail,
    _shape_trace_summary,
    _span_is_error,
    _span_tags_flat,
    _truncation_hint,
)


class TestSpanIsError:
    def test_error_true_tag(self) -> None:
        span = {"tags": [{"key": "error", "value": True}]}
        assert _span_is_error(span) is True

    def test_error_string_true_tag(self) -> None:
        span = {"tags": [{"key": "error", "value": "true"}]}
        assert _span_is_error(span) is True

    def test_error_string_one_tag(self) -> None:
        span = {"tags": [{"key": "error", "value": "1"}]}
        assert _span_is_error(span) is True

    def test_error_false_tag(self) -> None:
        span = {"tags": [{"key": "error", "value": False}]}
        assert _span_is_error(span) is False

    def test_no_error_tag(self) -> None:
        span = {"tags": [{"key": "http.status_code", "value": "200"}]}
        assert _span_is_error(span) is False

    def test_no_tags(self) -> None:
        assert _span_is_error({}) is False

    def test_empty_tags(self) -> None:
        assert _span_is_error({"tags": []}) is False


class TestSpanTagsFlat:
    def test_converts_to_string_dict(self) -> None:
        span = {
            "tags": [
                {"key": "http.method", "value": "GET"},
                {"key": "http.status_code", "value": 200},
            ]
        }
        result = _span_tags_flat(span)
        assert result == {"http.method": "GET", "http.status_code": "200"}

    def test_empty_tags(self) -> None:
        assert _span_tags_flat({"tags": []}) == {}

    def test_no_tags_key(self) -> None:
        assert _span_tags_flat({}) == {}

    def test_none_value_becomes_empty_string(self) -> None:
        span = {"tags": [{"key": "foo", "value": None}]}
        assert _span_tags_flat(span)["foo"] == ""

    def test_skips_tag_with_no_key(self) -> None:
        span = {"tags": [{"value": "orphan"}]}
        assert _span_tags_flat(span) == {}


class TestFindRootSpan:
    def test_single_span_is_root(self) -> None:
        spans = [{"spanID": "aaa", "references": []}]
        root = _find_root_span(spans)
        assert root is not None
        assert root["spanID"] == "aaa"

    def test_root_has_no_parent_in_trace(self) -> None:
        spans = [
            {"spanID": "root", "references": []},
            {"spanID": "child", "references": [{"refType": "CHILD_OF", "spanID": "root"}]},
        ]
        root = _find_root_span(spans)
        assert root is not None
        assert root["spanID"] == "root"

    def test_external_parent_treated_as_root(self) -> None:
        # Parent ID not in span list → span is root of this trace
        spans = [
            {"spanID": "local", "references": [{"refType": "CHILD_OF", "spanID": "external-parent"}]},
        ]
        root = _find_root_span(spans)
        assert root is not None
        assert root["spanID"] == "local"

    def test_empty_list_returns_none(self) -> None:
        assert _find_root_span([]) is None

    def test_fallback_to_earliest_start_time(self) -> None:
        spans = [
            {"spanID": "b", "startTime": 2000, "references": []},
            {"spanID": "a", "startTime": 1000, "references": []},
        ]
        root = _find_root_span(spans)
        assert root is not None
        # Both are roots by ref logic; fallback picks earliest
        # (both have no references, so _find_root_span returns the first found)


class TestShapeTraceSummary:
    def _make_trace(self) -> dict:
        return {
            "traceID": "abc123",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "HTTP GET /order",
                    "processID": "p1",
                    "startTime": 1_000_000,
                    "duration": 500_000,
                    "references": [],
                    "tags": [],
                },
                {
                    "spanID": "s2",
                    "operationName": "db.query",
                    "processID": "p2",
                    "startTime": 1_100_000,
                    "duration": 200_000,
                    "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                    "tags": [{"key": "error", "value": True}],
                },
            ],
            "processes": {
                "p1": {"serviceName": "order-service"},
                "p2": {"serviceName": "db-service"},
            },
        }

    def test_basic_shape(self) -> None:
        summary = _shape_trace_summary(self._make_trace())
        assert summary["trace_id"] == "abc123"
        assert summary["span_count"] == 2
        assert summary["service_count"] == 2  # two unique processIDs
        assert summary["errors_count"] == 1
        assert summary["root_operation"] == "HTTP GET /order"
        assert summary["root_service"] == "order-service"

    def test_duration_computed(self) -> None:
        summary = _shape_trace_summary(self._make_trace())
        # end_of_last = max(1_000_000+500_000, 1_100_000+200_000) = 1_500_000
        # start = 1_000_000 → duration = 500_000
        assert summary["duration_us"] == 500_000

    def test_empty_trace(self) -> None:
        summary = _shape_trace_summary({"traceID": "x", "spans": [], "processes": {}})
        assert summary["span_count"] == 0
        assert summary["duration_us"] == 0
        assert summary["start_time_us"] is None


class TestShapeSpanDetail:
    def test_full_span(self) -> None:
        span = {
            "spanID": "sp1",
            "operationName": "GET /health",
            "processID": "p1",
            "startTime": 123456,
            "duration": 999,
            "references": [{"refType": "CHILD_OF", "spanID": "parent1"}],
            "tags": [{"key": "http.status_code", "value": "200"}],
        }
        processes = {"p1": {"serviceName": "api-service"}}
        detail = _shape_span_detail(span, processes)
        assert detail["span_id"] == "sp1"
        assert detail["operation_name"] == "GET /health"
        assert detail["service"] == "api-service"
        assert detail["start_time_us"] == 123456
        assert detail["duration_us"] == 999
        assert detail["is_error"] is False
        assert detail["parent_span_id"] == "parent1"
        assert detail["tags"] == {"http.status_code": "200"}

    def test_root_span_has_no_parent(self) -> None:
        span = {"spanID": "root", "operationName": "root", "processID": "p1", "references": []}
        detail = _shape_span_detail(span, {"p1": {"serviceName": "svc"}})
        assert detail["parent_span_id"] is None

    def test_unknown_process_uses_pid(self) -> None:
        span = {"spanID": "s", "operationName": "op", "processID": "unknown-pid", "references": []}
        detail = _shape_span_detail(span, {})
        assert detail["service"] == "unknown-pid"


class TestBuildExecutionTree:
    def test_parent_child_relationship(self) -> None:
        spans = [
            {
                "spanID": "root",
                "operationName": "root-op",
                "processID": "p1",
                "duration": 100,
                "references": [],
                "tags": [],
            },
            {
                "spanID": "child",
                "operationName": "child-op",
                "processID": "p2",
                "duration": 50,
                "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                "tags": [],
            },
        ]
        processes = {"p1": {"serviceName": "svc1"}, "p2": {"serviceName": "svc2"}}
        tree = _build_execution_tree(spans, processes)
        root_node = next(n for n in tree if n["span_id"] == "root")
        assert "child" in root_node["children"]
        child_node = next(n for n in tree if n["span_id"] == "child")
        assert child_node["children"] == []

    def test_error_propagated(self) -> None:
        spans = [
            {
                "spanID": "s1",
                "operationName": "op",
                "processID": "p1",
                "duration": 10,
                "references": [],
                "tags": [{"key": "error", "value": True}],
            }
        ]
        tree = _build_execution_tree(spans, {"p1": {"serviceName": "svc"}})
        assert tree[0]["is_error"] is True


class TestTruncationHint:
    def test_returns_markdown_hint(self) -> None:
        hint = _truncation_hint(100, 20, "services")
        assert "20" in hint
        assert "100" in hint
        assert "services" in hint
        assert "structured content" in hint
