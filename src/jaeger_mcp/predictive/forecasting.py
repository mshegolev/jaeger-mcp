"""
Capacity forecasting and throughput prediction.
"""

from datetime import datetime, timedelta
from typing import Any

from ..shaping import shape_trace_summary
from .models import ForecastResult


class CapacityForecastingModel:
    """Model for forecasting capacity and throughput demands."""

    def __init__(self):
        """Initialize the capacity forecasting model."""
        self.forecast_days = 30
        self.seasonal_periods = 7  # Weekly seasonality

    def forecast_capacity(
        self, service_name: str, historical_volume: list[dict], seasonal_patterns: list[dict]
    ) -> ForecastResult:
        """
        Forecast future throughput demands and resource requirements.

        Args:
            service_name: Name of the service
            historical_volume: Historical trace volume patterns
            seasonal_patterns: Seasonal trend analysis data

        Returns:
            ForecastResult with capacity predictions
        """
        # Extract throughput data from historical volume
        throughput_data = self._extract_throughput_data(historical_volume)

        if len(throughput_data) < 10:
            # Not enough data, return conservative estimate
            current_throughput = len(historical_volume) if historical_volume else 0
            return self._create_conservative_forecast(service_name, current_throughput)

        # Apply simple forecasting algorithm (moving average with trend)
        forecast_values = self._simple_forecast(throughput_data)

        # Calculate confidence intervals
        confidence_low, confidence_high = self._calculate_confidence_intervals(forecast_values)

        # Estimate resource requirements
        resource_requirements = self._estimate_resource_requirements(forecast_values)

        # Determine forecast period
        now = datetime.now()
        forecast_start = now + timedelta(days=1)
        forecast_end = now + timedelta(days=self.forecast_days)

        # Calculate mean of forecast values
        if forecast_values:
            predicted_throughput = int(sum(forecast_values) / len(forecast_values))
        else:
            predicted_throughput = 0

        return ForecastResult(
            service_name=service_name,
            forecast_period_start=forecast_start,
            forecast_period_end=forecast_end,
            predicted_throughput=predicted_throughput,
            confidence_interval_low=confidence_low,
            confidence_interval_high=confidence_high,
            resource_requirements=resource_requirements,
        )

    def _extract_throughput_data(self, historical_volume: list[dict]) -> list[int]:
        """
        Extract throughput data from historical volume patterns.

        Args:
            historical_volume: Historical trace data

        Returns:
            List of throughput values (traces per time period)
        """
        throughput = []
        # Group traces by time periods (e.g., hourly)
        time_groups = {}

        for trace in historical_volume:
            summary = shape_trace_summary(trace)
            if summary["start_time_us"] is not None:
                # Convert microseconds to hourly buckets
                hour_bucket = summary["start_time_us"] // (3600 * 1000000)
                if hour_bucket not in time_groups:
                    time_groups[hour_bucket] = 0
                time_groups[hour_bucket] += 1

        # Convert to sorted list of throughput values
        for bucket in sorted(time_groups.keys()):
            throughput.append(time_groups[bucket])

        return throughput

    def _simple_forecast(self, throughput_data: list[int]) -> list[float]:
        """
        Simple forecasting using moving average with trend.

        Args:
            throughput_data: Historical throughput data

        Returns:
            Forecasted values for next 30 days
        """
        if len(throughput_data) < 3:
            # Not enough data for trend analysis
            avg_value = sum(throughput_data) / len(throughput_data) if throughput_data else 0
            return [avg_value] * self.forecast_days

        # Calculate moving average (3-point)
        moving_avg = []
        for i in range(len(throughput_data) - 2):
            avg = sum(throughput_data[i : i + 3]) / len(throughput_data[i : i + 3])
            moving_avg.append(avg)

        # Calculate trend from moving averages
        if len(moving_avg) >= 2:
            trend = (moving_avg[-1] - moving_avg[0]) / len(moving_avg)
        else:
            trend = 0

        # Forecast next values with trend
        last_value = moving_avg[-1] if moving_avg else throughput_data[-1]
        forecast = []
        for i in range(self.forecast_days):
            forecast_value = last_value + (trend * (i + 1))
            # Ensure non-negative values
            forecast.append(max(0, forecast_value))

        return forecast

    def _calculate_confidence_intervals(self, forecast_values: list[float]) -> tuple[int, int]:
        """
        Calculate confidence intervals for forecast values.

        Args:
            forecast_values: Forecasted values

        Returns:
            Tuple of (lower_bound, upper_bound)
        """
        if not forecast_values:
            return (0, 0)

        mean_value = sum(forecast_values) / len(forecast_values)

        # Calculate standard deviation
        if len(forecast_values) > 1:
            variance = sum((x - mean_value) ** 2 for x in forecast_values) / (len(forecast_values) - 1)
            std_dev = variance**0.5
        else:
            std_dev = 0

        # 90% confidence interval (±1.645 standard deviations)
        margin = 1.645 * std_dev if std_dev > 0 else mean_value * 0.1 if mean_value > 0 else 1

        lower_bound = max(0, int(mean_value - margin))
        upper_bound = int(mean_value + margin)

        return (lower_bound, upper_bound)

    def _estimate_resource_requirements(self, forecast_values: list[float]) -> dict[str, Any]:
        """
        Estimate resource requirements based on forecasted throughput.

        Args:
            forecast_values: Forecasted throughput values

        Returns:
            Dictionary of resource requirements
        """
        if not forecast_values:
            return {}

        avg_throughput = sum(forecast_values) / len(forecast_values) if forecast_values else 0

        # Simple resource estimation model
        # These are arbitrary ratios for demonstration
        cpu_cores = max(1, int(avg_throughput / 1000))
        memory_gb = max(1, int(avg_throughput / 500))
        storage_gb = max(10, int(avg_throughput / 100))

        return {
            "cpu_cores": cpu_cores,
            "memory_gb": memory_gb,
            "storage_gb": storage_gb,
            "estimated_cost": cpu_cores * 50 + memory_gb * 20 + storage_gb * 5,  # $ per unit
        }

    def _create_conservative_forecast(self, service_name: str, current_throughput: int) -> ForecastResult:
        """
        Create a conservative forecast when insufficient data is available.

        Args:
            service_name: Name of the service
            current_throughput: Current observed throughput

        Returns:
            Conservative ForecastResult
        """
        now = datetime.now()
        forecast_start = now + timedelta(days=1)
        forecast_end = now + timedelta(days=self.forecast_days)

        # Conservative estimate: 20% growth with wide confidence interval
        predicted_throughput = int(current_throughput * 1.2) if current_throughput > 0 else 100
        confidence_low = max(0, int(predicted_throughput * 0.7))
        confidence_high = int(predicted_throughput * 1.5)

        resource_requirements = {
            "cpu_cores": max(1, int(predicted_throughput / 1000)),
            "memory_gb": max(1, int(predicted_throughput / 500)),
            "storage_gb": max(10, int(predicted_throughput / 100)),
            "estimated_cost": "Insufficient data for accurate cost estimation",
        }

        return ForecastResult(
            service_name=service_name,
            forecast_period_start=forecast_start,
            forecast_period_end=forecast_end,
            predicted_throughput=predicted_throughput,
            confidence_interval_low=confidence_low,
            confidence_interval_high=confidence_high,
            resource_requirements=resource_requirements,
        )


# Convenience function for easier access
def forecast_service_capacity(
    service_name: str, historical_volume: list[dict], seasonal_patterns: list[dict]
) -> ForecastResult:
    """
    Forecast future throughput demands and resource requirements.

    Args:
        service_name: Name of the service
        historical_volume: Historical trace volume patterns
        seasonal_patterns: Seasonal trend analysis data

    Returns:
        ForecastResult with capacity predictions
    """
    model = CapacityForecastingModel()
    return model.forecast_capacity(service_name, historical_volume, seasonal_patterns)
