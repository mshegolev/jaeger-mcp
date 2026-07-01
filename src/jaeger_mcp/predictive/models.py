"""
Data models for predictive analytics.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PredictionResult(BaseModel):
    """Result of a performance degradation prediction."""

    service_name: str = Field(..., description="Name of the service")
    predicted_degradation_time: datetime = Field(..., description="Predicted time of degradation")
    confidence_level: float = Field(..., ge=0.0, le=1.0, description="Confidence level (0.0 to 1.0)")
    contributing_factors: list[str] = Field(default_factory=list, description="Factors contributing to prediction")
    recommendations: list[str] = Field(default_factory=list, description="Recommended actions")


class ForecastResult(BaseModel):
    """Result of a capacity forecasting prediction."""

    service_name: str = Field(..., description="Name of the service")
    forecast_period_start: datetime = Field(..., description="Start of forecast period")
    forecast_period_end: datetime = Field(..., description="End of forecast period")
    predicted_throughput: int = Field(..., ge=0, description="Predicted throughput (requests per time unit)")
    confidence_interval_low: int = Field(..., ge=0, description="Lower bound of confidence interval")
    confidence_interval_high: int = Field(..., ge=0, description="Upper bound of confidence interval")
    resource_requirements: dict[str, Any] = Field(default_factory=dict, description="Resource requirements")


class RootCausePrediction(BaseModel):
    """Prediction of root cause during an incident."""

    service_name: str = Field(..., description="Name of the service")
    incident_time: datetime = Field(..., description="Time of incident")
    root_cause_candidates: list[dict[str, Any]] = Field(..., description="List of potential root causes with scores")
    confidence_scores: list[float] = Field(..., description="Confidence scores for each candidate")


class WarningAlert(BaseModel):
    """Early warning alert for potential performance issues."""

    service_name: str = Field(..., description="Name of the service")
    alert_time: datetime = Field(..., description="Time when alert was generated")
    predicted_issue_time: datetime = Field(..., description="Predicted time of performance issue")
    time_horizon_hours: int = Field(..., ge=2, le=24, description="Time horizon in hours (2-24)")
    severity_level: str = Field(..., description="Severity level (low, medium, high, critical)")
    affected_operations: list[str] = Field(default_factory=list, description="Operations likely to be affected")
    recommended_actions: list[str] = Field(default_factory=list, description="Recommended actions to prevent issue")
