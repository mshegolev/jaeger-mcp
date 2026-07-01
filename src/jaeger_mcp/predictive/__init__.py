"""
Predictive analytics module for jaeger-mcp.

This module provides predictive capabilities that build upon existing
advanced trace analytics features to move from reactive analysis to
proactive insights by applying predictive modeling to trace data patterns.
"""

# Import key classes and functions for easy access
try:
    from .models import ForecastResult, PredictionResult, RootCausePrediction, WarningAlert

    __all__ = ["PredictionResult", "ForecastResult", "RootCausePrediction", "WarningAlert"]
except ImportError:
    # Handle case where modules aren't fully implemented yet
    __all__ = []
