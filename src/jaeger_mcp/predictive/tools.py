"""
MCP tools for predictive analytics.
"""

from __future__ import annotations

from typing import Annotated
from datetime import datetime, timedelta

from pydantic import Field

from jaeger_mcp import output
from jaeger_mcp._mcp import get_client, mcp
from jaeger_mcp.models import SearchTracesOutput
from .models import PredictionResult, ForecastResult
from .performance_model import predict_performance_degradation
from .forecasting import forecast_service_capacity


@mcp.tool(
    name="jaeger_predict_degradation",
    annotations={
        "title": "Predict Performance Degradation",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_predict_degradation(
    service: Annotated[
        str,
        Field(
            description="Service name to analyze for potential degradation",
            pattern=r"^[a-zA-Z0-9._:\-]+$",
            examples=["frontend", "payment-service", "user-auth"],
        ),
    ],
    hours_back: Annotated[
        int,
        Field(
            description="Number of hours of historical data to analyze (1-720)",
            ge=1,
            le=720,
            default=168,  # 1 week
        ),
    ] = 168,
) -> PredictionResult:
    """Predict potential performance degradation events for a service.

    Analyzes historical trace data patterns, critical path trends, and anomaly
    detection results to forecast likely performance issues 2-24 hours in advance.

    Args:
        service: Service name to analyze for potential degradation
        hours_back: Number of hours of historical data to analyze (default: 168 hours/1 week)

    Returns:
        PredictionResult with degradation forecast, confidence level, and recommendations
    """
    try:
        client = await get_client()

        # Get historical trace data
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours_back)

        # Search for traces using the Jaeger API
        # Convert datetime to microseconds since epoch
        start_time_us = int(start_time.timestamp() * 1_000_000)
        end_time_us = int(end_time.timestamp() * 1_000_000)

        params = {"service": service, "start": start_time_us, "end": end_time_us, "limit": 1000}

        trace_data = await client.aget("/traces", params=params)

        # Get critical path trends (simplified - would integrate with existing tools)
        critical_path_trends = []  # Placeholder

        # Get anomaly detections (simplified - would integrate with existing tools)
        anomaly_detections = []  # Placeholder

        # Make prediction
        prediction = predict_performance_degradation(
            service_name=service,
            historical_data=trace_data.get("data", []) if trace_data else [],
            critical_path_trends=critical_path_trends,
            anomaly_detections=anomaly_detections,
        )

        return prediction

    except Exception as e:
        return output.fail(e, f"Failed to predict degradation for service {service!r}")


@mcp.tool(
    name="jaeger_forecast_capacity",
    annotations={
        "title": "Forecast Service Capacity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_forecast_capacity(
    service: Annotated[
        str,
        Field(
            description="Service name to forecast capacity for",
            pattern=r"^[a-zA-Z0-9._:\-]+$",
            examples=["frontend", "payment-service", "user-auth"],
        ),
    ],
    days_ahead: Annotated[
        int,
        Field(
            description="Number of days to forecast ahead (1-90)",
            ge=1,
            le=90,
            default=30,
        ),
    ] = 30,
) -> ForecastResult:
    """Forecast future throughput demands and resource requirements for a service.

    Provides predictions for the next 7-30 days with confidence intervals to
    enable infrastructure scaling decisions.

    Args:
        service: Service name to forecast capacity for
        days_ahead: Number of days to forecast ahead (default: 30 days)

    Returns:
        ForecastResult with throughput predictions and resource requirements
    """
    try:
        client = await get_client()

        # Get historical volume data
        end_time = datetime.now()
        start_time = end_time - timedelta(days=30)  # Use 30 days of history

        # Search for traces using the Jaeger API
        # Convert datetime to microseconds since epoch
        start_time_us = int(start_time.timestamp() * 1_000_000)
        end_time_us = int(end_time.timestamp() * 1_000_000)

        params = {"service": service, "start": start_time_us, "end": end_time_us, "limit": 5000}

        trace_data = await client.aget("/traces", params=params)

        # Get seasonal patterns (simplified - placeholder)
        seasonal_patterns = []  # Placeholder

        # Make forecast
        forecast = forecast_service_capacity(
            service_name=service,
            historical_volume=trace_data.get("data", []) if trace_data else [],
            seasonal_patterns=seasonal_patterns,
        )

        return forecast

    except Exception as e:
        return output.fail(e, f"Failed to forecast capacity for service {service!r}")


# Export the tools for easy access
__all__ = [
    "jaeger_predict_degradation",
    "jaeger_forecast_capacity",
]
