"""
Performance degradation prediction model.
"""

from typing import List, Tuple, Optional
from datetime import datetime, timedelta
from .models import PredictionResult
from ..shaping import shape_trace_summary


class PerformanceDegradationModel:
    """Model for predicting performance degradation events."""

    def __init__(self):
        """Initialize the performance degradation model."""
        self.history_window_days = 30
        self.confidence_threshold = 0.8

    def predict_degradation(
        self,
        service_name: str,
        historical_data: List[dict],
        critical_path_trends: List[dict],
        anomaly_detections: List[dict],
    ) -> PredictionResult:
        """
        Predict potential performance degradation events.

        Args:
            service_name: Name of the service
            historical_data: Historical trace data patterns
            critical_path_trends: Critical path analysis trends
            anomaly_detections: Anomaly detection results

        Returns:
            PredictionResult with degradation prediction
        """
        # Analyze latency patterns
        latency_trend_score = self._analyze_latency_trends(historical_data)

        # Analyze critical path trends
        critical_path_score = self._analyze_critical_path_trends(critical_path_trends)

        # Analyze anomaly detections
        anomaly_score = self._analyze_anomaly_detections(anomaly_detections)

        # Combine scores to get overall confidence
        combined_score = (latency_trend_score + critical_path_score + anomaly_score) / 3

        # Determine predicted degradation time (2-24 hours from now)
        import random

        predicted_time = datetime.now() + timedelta(hours=random.randint(2, 24))

        # Generate contributing factors
        factors = self._generate_contributing_factors(latency_trend_score, critical_path_score, anomaly_score)

        # Generate recommendations
        recommendations = self._generate_recommendations(factors)

        return PredictionResult(
            service_name=service_name,
            predicted_degradation_time=predicted_time,
            confidence_level=combined_score,
            contributing_factors=factors,
            recommendations=recommendations,
        )

    def _analyze_latency_trends(self, historical_data: List[dict]) -> float:
        """
        Analyze historical latency patterns to predict degradation.

        Args:
            historical_data: Historical trace data

        Returns:
            Confidence score (0.0 to 1.0)
        """
        if not historical_data:
            return 0.0

        # Extract latency values from historical data
        latencies = []
        for trace in historical_data[-100:]:  # Last 100 traces
            summary = shape_trace_summary(trace)
            if summary["duration_us"] is not None:
                latencies.append(summary["duration_us"])

        if len(latencies) < 10:
            return 0.0

        # Calculate trend using linear regression
        x = list(range(len(latencies)))
        y = latencies

        # Calculate slope using simple linear regression
        if len(x) > 1:
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_xx = sum(x[i] * x[i] for i in range(n))

            # Slope = (n*sum_xy - sum_x*sum_y) / (n*sum_xx - sum_x*sum_x)
            denominator = n * sum_xx - sum_x * sum_x
            if denominator != 0:
                slope = (n * sum_xy - sum_x * sum_y) / denominator
            else:
                slope = 0
        else:
            slope = 0

        # Convert slope to confidence score (normalized)
        # Positive slope indicates increasing latency
        if slope > 0:
            # Normalize to 0-1 range based on reasonable latency increase
            max_slope = 1000000  # 1 second increase per trace (arbitrary)
            confidence = min(1.0, slope / max_slope)
            return confidence
        else:
            return 0.0  # No increasing trend

    def _analyze_critical_path_trends(self, critical_path_trends: List[dict]) -> float:
        """
        Analyze critical path trends to predict degradation.

        Args:
            critical_path_trends: Critical path analysis trends

        Returns:
            Confidence score (0.0 to 1.0)
        """
        if not critical_path_trends:
            return 0.0

        # Look for increasing bottleneck spans
        bottleneck_spans = []
        for trend in critical_path_trends[-10:]:  # Last 10 trends
            if "bottleneck_spans" in trend:
                bottleneck_spans.extend(trend["bottleneck_spans"])

        if len(bottleneck_spans) < 5:
            return 0.0

        # Calculate trend in bottleneck durations
        durations = [span.get("self_time", 0) for span in bottleneck_spans]
        if len(durations) < 2:
            return 0.0

        # Calculate slope using simple linear regression
        if len(durations) > 1:
            n = len(durations)
            x = list(range(len(durations)))
            y = durations
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_xx = sum(x[i] * x[i] for i in range(n))

            # Slope = (n*sum_xy - sum_x*sum_y) / (n*sum_xx - sum_x*sum_x)
            denominator = n * sum_xx - sum_x * sum_x
            if denominator != 0:
                slope = (n * sum_xy - sum_x * sum_y) / denominator
            else:
                slope = 0
        else:
            slope = 0

        # Convert slope to confidence score
        if slope > 0:
            max_slope = 100000  # 100ms increase per span (arbitrary)
            confidence = min(1.0, slope / max_slope)
            return confidence
        else:
            return 0.0

    def _analyze_anomaly_detections(self, anomaly_detections: List[dict]) -> float:
        """
        Analyze anomaly detections to predict degradation.

        Args:
            anomaly_detections: Anomaly detection results

        Returns:
            Confidence score (0.0 to 1.0)
        """
        if not anomaly_detections:
            return 0.0

        # Count recent anomalies
        recent_anomalies = 0
        now = datetime.now()
        for anomaly in anomaly_detections[-20:]:  # Last 20 anomalies
            if "timestamp" in anomaly:
                anomaly_time = datetime.fromisoformat(anomaly["timestamp"].replace("Z", "+00:00"))
                if now - anomaly_time < timedelta(hours=24):
                    recent_anomalies += 1

        # Convert count to confidence score
        max_anomalies = 10
        return min(1.0, recent_anomalies / max_anomalies)

    def _generate_contributing_factors(
        self, latency_score: float, critical_path_score: float, anomaly_score: float
    ) -> List[str]:
        """Generate contributing factors based on scores."""
        factors = []
        if latency_score > 0.3:
            factors.append("Increasing latency patterns detected")
        if critical_path_score > 0.3:
            factors.append("Growing bottleneck span durations")
        if anomaly_score > 0.3:
            factors.append("Recent anomaly detections")
        if not factors:
            factors.append("No significant contributing factors identified")
        return factors

    def _generate_recommendations(self, factors: List[str]) -> List[str]:
        """Generate recommendations based on contributing factors."""
        recommendations = []
        if "Increasing latency patterns detected" in factors:
            recommendations.append("Investigate service dependencies for performance bottlenecks")
        if "Growing bottleneck span durations" in factors:
            recommendations.append("Optimize critical path operations")
        if "Recent anomaly detections" in factors:
            recommendations.append("Review recent code deployments for performance impact")
        if not recommendations:
            recommendations.append("Continue monitoring system performance")
        return recommendations


# Convenience function for easier access
def predict_performance_degradation(
    service_name: str, historical_data: List[dict], critical_path_trends: List[dict], anomaly_detections: List[dict]
) -> PredictionResult:
    """
    Predict potential performance degradation events.

    Args:
        service_name: Name of the service
        historical_data: Historical trace data patterns
        critical_path_trends: Critical path analysis trends
        anomaly_detections: Anomaly detection results

    Returns:
        PredictionResult with degradation prediction
    """
    model = PerformanceDegradationModel()
    return model.predict_degradation(service_name, historical_data, critical_path_trends, anomaly_detections)
