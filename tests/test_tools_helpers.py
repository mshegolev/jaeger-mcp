"""Unit tests for pure shaping helpers in :mod:`jaeger_mcp.tools`.

These functions take raw Jaeger API dicts and shape them into TypedDict
output schemas or perform simple logic. They have no I/O, so we exercise
them directly without mocking any HTTP client.
"""

from __future__ import annotations

from jaeger_mcp.shaping import (
    aggregate_span_statistics as _aggregate_span_statistics,
    build_execution_tree as _build_execution_tree,
    compare_traces_diff as _compare_traces_diff,
    compute_percentile as _compute_percentile,
    find_root_span as _find_root_span,
    shape_span_detail as _shape_span_detail,
    shape_trace_summary as _shape_trace_summary,
    span_is_error as _span_is_error,
    span_match_key as _span_match_key,
    span_tags_flat as _span_tags_flat,
    truncation_hint as _truncation_hint,
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


# ── span_match_key tests ─────────────────────────────────────────────────


def _make_span(
    span_id: str,
    operation: str,
    process_id: str,
    *,
    parent_span_id: str | None = None,
    duration: int = 100_000,
    tags: list[dict] | None = None,
) -> dict:
    """Build a raw Jaeger span dict for testing."""
    refs = []
    if parent_span_id:
        refs.append({"refType": "CHILD_OF", "spanID": parent_span_id})
    return {
        "spanID": span_id,
        "operationName": operation,
        "processID": process_id,
        "startTime": 1_700_000_000_000_000,
        "duration": duration,
        "references": refs,
        "tags": tags or [],
    }


def _make_raw_trace(
    trace_id: str,
    spans: list[dict],
    processes: dict,
) -> dict:
    """Build a raw Jaeger trace dict."""
    return {
        "traceID": trace_id,
        "spans": spans,
        "processes": processes,
    }


class TestSpanMatchKey:
    def test_span_match_key_root_span(self) -> None:
        """Root span has parentOperation=None."""
        span = _make_span("s1", "GET /orders", "p1")
        processes = {"p1": {"serviceName": "order-service"}}
        span_map = {"s1": span}
        key = _span_match_key(span, processes, span_map)
        assert key == ("GET /orders", "order-service", None)

    def test_span_match_key_child_span(self) -> None:
        """Child span has parentOperation = parent's operationName."""
        root = _make_span("s1", "GET /orders", "p1")
        child = _make_span("s2", "db.query", "p2", parent_span_id="s1")
        processes = {
            "p1": {"serviceName": "order-service"},
            "p2": {"serviceName": "db-service"},
        }
        span_map = {"s1": root, "s2": child}
        key = _span_match_key(child, processes, span_map)
        assert key == ("db.query", "db-service", "GET /orders")


class TestCompareTracesDiff:
    def test_compare_traces_diff_identical(self) -> None:
        """Identical traces → 0 added, 0 removed, 0 changed, N unchanged."""
        spans = [
            _make_span("s1", "GET /orders", "p1"),
            _make_span("s2", "db.query", "p2", parent_span_id="s1"),
        ]
        processes = {
            "p1": {"serviceName": "order-service"},
            "p2": {"serviceName": "db-service"},
        }
        trace_a = _make_raw_trace("aaa", spans, processes)
        trace_b = _make_raw_trace("bbb", spans, processes)
        result = _compare_traces_diff(trace_a, trace_b)
        assert result["trace_id_a"] == "aaa"
        assert result["trace_id_b"] == "bbb"
        assert result["added_spans"] == []
        assert result["removed_spans"] == []
        assert result["changed_spans"] == []
        assert result["unchanged_count"] == 2

    def test_compare_traces_diff_completely_different(self) -> None:
        """Completely different traces → all added from B, all removed from A."""
        procs_a = {"p1": {"serviceName": "order-service"}}
        procs_b = {"p1": {"serviceName": "payment-service"}}
        trace_a = _make_raw_trace(
            "aaa",
            [_make_span("s1", "GET /orders", "p1")],
            procs_a,
        )
        trace_b = _make_raw_trace(
            "bbb",
            [_make_span("s1", "POST /payments", "p1")],
            procs_b,
        )
        result = _compare_traces_diff(trace_a, trace_b)
        assert len(result["removed_spans"]) == 1
        assert len(result["added_spans"]) == 1
        assert result["changed_spans"] == []
        assert result["unchanged_count"] == 0
        assert result["removed_spans"][0]["operation_name"] == "GET /orders"
        assert result["added_spans"][0]["operation_name"] == "POST /payments"

    def test_compare_traces_diff_changed_duration(self) -> None:
        """Same key, different duration → appears in changed_spans with correct delta."""
        processes = {
            "p1": {"serviceName": "order-service"},
            "p2": {"serviceName": "db-service"},
        }
        trace_a = _make_raw_trace(
            "aaa",
            [
                _make_span("s1", "GET /orders", "p1"),
                _make_span("s2", "db.query", "p2", parent_span_id="s1", duration=200_000),
            ],
            processes,
        )
        trace_b = _make_raw_trace(
            "bbb",
            [
                _make_span("s1", "GET /orders", "p1"),
                _make_span("s2", "db.query", "p2", parent_span_id="s1", duration=300_000),
            ],
            processes,
        )
        result = _compare_traces_diff(trace_a, trace_b)
        assert result["unchanged_count"] == 1  # root span unchanged
        assert len(result["changed_spans"]) == 1
        changed = result["changed_spans"][0]
        assert changed["operation_name"] == "db.query"
        assert changed["duration_a_us"] == 200_000
        assert changed["duration_b_us"] == 300_000
        assert changed["duration_delta_us"] == 100_000

    def test_compare_traces_diff_changed_tags(self) -> None:
        """Same key, different tags → tags_added/removed/changed populated."""
        processes = {"p1": {"serviceName": "svc"}}
        span_a = _make_span(
            "s1",
            "op",
            "p1",
            tags=[
                {"key": "http.method", "value": "GET"},
                {"key": "old_tag", "value": "old_value"},
                {"key": "changed_tag", "value": "val_a"},
            ],
        )
        span_b = _make_span(
            "s1",
            "op",
            "p1",
            tags=[
                {"key": "http.method", "value": "GET"},
                {"key": "new_tag", "value": "new_value"},
                {"key": "changed_tag", "value": "val_b"},
            ],
        )
        trace_a = _make_raw_trace("aaa", [span_a], processes)
        trace_b = _make_raw_trace("bbb", [span_b], processes)
        result = _compare_traces_diff(trace_a, trace_b)
        assert len(result["changed_spans"]) == 1
        changed = result["changed_spans"][0]
        assert "new_tag" in changed["tags_added"]
        assert "old_tag" in changed["tags_removed"]
        assert "changed_tag" in changed["tags_changed"]
        assert changed["tags_changed"]["changed_tag"] == "val_b"

    def test_compare_traces_diff_empty_traces(self) -> None:
        """Empty traces → 0 everything."""
        trace_a = _make_raw_trace("aaa", [], {})
        trace_b = _make_raw_trace("bbb", [], {})
        result = _compare_traces_diff(trace_a, trace_b)
        assert result["added_spans"] == []
        assert result["removed_spans"] == []
        assert result["changed_spans"] == []
        assert result["unchanged_count"] == 0

    def test_compare_traces_diff_added_span(self) -> None:
        """B has one extra span → appears in added_spans."""
        processes = {
            "p1": {"serviceName": "order-service"},
            "p2": {"serviceName": "db-service"},
        }
        trace_a = _make_raw_trace(
            "aaa",
            [_make_span("s1", "GET /orders", "p1")],
            processes,
        )
        trace_b = _make_raw_trace(
            "bbb",
            [
                _make_span("s1", "GET /orders", "p1"),
                _make_span("s2", "db.query", "p2", parent_span_id="s1"),
            ],
            processes,
        )
        result = _compare_traces_diff(trace_a, trace_b)
        assert result["unchanged_count"] == 1
        assert len(result["added_spans"]) == 1
        assert result["added_spans"][0]["operation_name"] == "db.query"
        assert result["removed_spans"] == []

    def test_compare_traces_diff_duplicate_spans(self) -> None:
        """Two spans with same key in both → paired by order, extras go to added/removed."""
        processes = {"p1": {"serviceName": "svc"}}
        # Two spans with same key in A and B
        trace_a = _make_raw_trace(
            "aaa",
            [
                _make_span("s1", "db.query", "p1", duration=100_000),
                _make_span("s2", "db.query", "p1", duration=200_000),
            ],
            processes,
        )
        trace_b = _make_raw_trace(
            "bbb",
            [
                _make_span("s1", "db.query", "p1", duration=100_000),
                _make_span("s2", "db.query", "p1", duration=200_000),
            ],
            processes,
        )
        result = _compare_traces_diff(trace_a, trace_b)
        # Both pairs are identical → 2 unchanged
        assert result["unchanged_count"] == 2
        assert result["added_spans"] == []
        assert result["removed_spans"] == []

    def test_compare_traces_diff_duplicate_spans_extra(self) -> None:
        """Uneven duplicates → extras go to added or removed."""
        processes = {"p1": {"serviceName": "svc"}}
        trace_a = _make_raw_trace(
            "aaa",
            [
                _make_span("s1", "db.query", "p1", duration=100_000),
                _make_span("s2", "db.query", "p1", duration=200_000),
                _make_span("s3", "db.query", "p1", duration=300_000),
            ],
            processes,
        )
        trace_b = _make_raw_trace(
            "bbb",
            [
                _make_span("s1", "db.query", "p1", duration=100_000),
            ],
            processes,
        )
        result = _compare_traces_diff(trace_a, trace_b)
        # First pair: identical → 1 unchanged
        # Two extras in A → 2 removed
        assert result["unchanged_count"] == 1
        assert len(result["removed_spans"]) == 2
        assert result["added_spans"] == []


# ── compute_percentile tests ─────────────────────────────────────────────


class TestComputePercentile:
    def test_median_odd_count(self) -> None:
        assert _compute_percentile([100, 200, 300, 400, 500], 50) == 300

    def test_p95_linear_interpolation(self) -> None:
        assert _compute_percentile([100, 200, 300, 400, 500], 95) == 480

    def test_p99_linear_interpolation(self) -> None:
        assert _compute_percentile([100, 200, 300, 400, 500], 99) == 496

    def test_empty_list_returns_zero(self) -> None:
        assert _compute_percentile([], 50) == 0

    def test_single_element(self) -> None:
        assert _compute_percentile([42], 99) == 42

    def test_two_elements_p50(self) -> None:
        assert _compute_percentile([100, 200], 50) == 150

    def test_p0_returns_minimum(self) -> None:
        assert _compute_percentile([10, 20, 30], 0) == 10

    def test_p100_returns_maximum(self) -> None:
        assert _compute_percentile([10, 20, 30], 100) == 30


# ── aggregate_span_statistics tests ──────────────────────────────────────


class TestAggregateSpanStatistics:
    def test_empty_traces_returns_empty(self) -> None:
        assert _aggregate_span_statistics([]) == []

    def test_traces_with_no_spans_returns_empty(self) -> None:
        assert _aggregate_span_statistics([{"spans": []}]) == []

    def test_single_operation_stats(self) -> None:
        traces = [
            {
                "spans": [
                    {"operationName": "GET /orders", "duration": 100, "tags": []},
                    {"operationName": "GET /orders", "duration": 200, "tags": []},
                    {"operationName": "GET /orders", "duration": 300, "tags": []},
                ]
            }
        ]
        stats = _aggregate_span_statistics(traces)
        assert len(stats) == 1
        s = stats[0]
        assert s["operation"] == "GET /orders"
        assert s["count"] == 3
        assert s["p50_duration_us"] == 200
        assert s["error_count"] == 0
        assert s["error_rate"] == 0.0

    def test_multiple_operations_sorted(self) -> None:
        traces = [
            {
                "spans": [
                    {"operationName": "POST /b", "duration": 1000, "tags": []},
                    {"operationName": "GET /a", "duration": 500, "tags": []},
                ]
            }
        ]
        stats = _aggregate_span_statistics(traces)
        assert len(stats) == 2
        assert stats[0]["operation"] == "GET /a"
        assert stats[1]["operation"] == "POST /b"

    def test_error_count_and_rate(self) -> None:
        traces = [
            {
                "spans": [
                    {"operationName": "op", "duration": 100, "tags": []},
                    {
                        "operationName": "op",
                        "duration": 200,
                        "tags": [{"key": "error", "value": "true", "type": "bool"}],
                    },
                    {"operationName": "op", "duration": 300, "tags": []},
                    {"operationName": "op", "duration": 400, "tags": []},
                ]
            }
        ]
        stats = _aggregate_span_statistics(traces)
        assert len(stats) == 1
        s = stats[0]
        assert s["error_count"] == 1
        assert s["error_rate"] == 0.25

    def test_spans_across_multiple_traces(self) -> None:
        traces = [
            {
                "spans": [
                    {"operationName": "op", "duration": 100, "tags": []},
                ]
            },
            {
                "spans": [
                    {"operationName": "op", "duration": 200, "tags": []},
                ]
            },
        ]
        stats = _aggregate_span_statistics(traces)
        assert len(stats) == 1
        assert stats[0]["count"] == 2

    def test_duration_in_microseconds(self) -> None:
        traces = [
            {
                "spans": [
                    {"operationName": "op", "duration": 123456, "tags": []},
                ]
            }
        ]
        stats = _aggregate_span_statistics(traces)
        assert stats[0]["p50_duration_us"] == 123456
        assert isinstance(stats[0]["p50_duration_us"], int)
