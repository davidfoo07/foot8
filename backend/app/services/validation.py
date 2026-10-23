"""Data Validation Layer — range checks and cross-source checks for football data.

NBA → football ranges:
  ORtg/DRtg 90-130   → xGF/xGA per 90: 0.4-3.0
  NRtg ±25           → xGD per 90: ±2.0
  minutes 0-48       → minutes per appearance 0-90 (stoppage time excluded)
  games 1-82         → league matches 1-46 (46 covers the EFL Championship)
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# Tier 1: Range/Sanity Check Rules
VALIDATION_RULES: dict[str, dict[str, Any]] = {
    "xgf": {"min": 0.4, "max": 3.0, "description": "xG For per 90"},
    "xga": {"min": 0.4, "max": 3.0, "description": "xG Against per 90"},
    "xgd": {"min": -2.0, "max": 2.0, "description": "xG Difference per 90"},
    "player_minutes": {"min": 0.0, "max": 90.0, "description": "Minutes Per Appearance"},
    "team_squad_size": {"min": 18, "max": 30, "description": "Registered Squad Size"},
    "game_count": {"min": 1, "max": 46, "description": "League Matches Played"},
    "win_probability": {"min": 0.0, "max": 1.0, "description": "Win Probability"},
    "polymarket_price": {"min": 0.01, "max": 0.99, "description": "Market Price"},
    "minutes_share": {"min": 0.0, "max": 1.0, "description": "Minutes Share"},
    "goals_per_match": {"min": 1.8, "max": 3.8, "description": "League Goals Per Match"},
}

# Tier 2: Cross-Source Tolerances (e.g. Understat vs FBref xG)
CROSS_SOURCE_TOLERANCE: dict[str, float] = {
    "xgf": 0.15,
    "xga": 0.15,
    "xgd": 0.25,
}


class ValidationResult:
    """Result of a validation check."""

    def __init__(
        self,
        is_valid: bool,
        check_type: str,
        severity: str,
        field: str,
        message: str,
        actual_value: Any = None,
        expected_range: str = "",
    ) -> None:
        self.is_valid = is_valid
        self.check_type = check_type
        self.severity = severity  # INFO, WARNING, ERROR
        self.field = field
        self.message = message
        self.actual_value = actual_value
        self.expected_range = expected_range

    def __repr__(self) -> str:
        return f"ValidationResult({self.severity}: {self.message})"


class DataValidator:
    """Range and cross-source validation engine."""

    def __init__(self) -> None:
        self.validation_log: list[ValidationResult] = []

    def clear_log(self) -> None:
        self.validation_log = []

    def validate_range(
        self,
        field: str,
        value: float | int | None,
        source: str = "unknown",
    ) -> ValidationResult:
        """Validate a value falls within its expected range."""
        if value is None:
            result = ValidationResult(
                is_valid=False, check_type="RANGE", severity="WARNING", field=field,
                message=f"{field} is None from {source}", expected_range="not None",
            )
            self.validation_log.append(result)
            return result

        rule = VALIDATION_RULES.get(field)
        if rule is None:
            return ValidationResult(
                is_valid=True, check_type="RANGE", severity="INFO", field=field,
                message=f"No validation rule for {field}", actual_value=value,
            )

        min_val, max_val = rule["min"], rule["max"]
        if not min_val <= float(value) <= max_val:
            result = ValidationResult(
                is_valid=False, check_type="RANGE", severity="WARNING", field=field,
                message=(
                    f"{rule['description']} value {value} out of range "
                    f"[{min_val}, {max_val}] from {source}"
                ),
                actual_value=value, expected_range=f"[{min_val}, {max_val}]",
            )
            logger.warning(result.message)
            self.validation_log.append(result)
            return result

        return ValidationResult(
            is_valid=True, check_type="RANGE", severity="INFO", field=field,
            message=f"{field} = {value} is valid", actual_value=value,
            expected_range=f"[{min_val}, {max_val}]",
        )

    def validate_team_ratings(
        self,
        xgf: float | None,
        xga: float | None,
        xgd: float | None,
        source: str = "unknown",
    ) -> list[ValidationResult]:
        """Validate a complete set of team ratings."""
        results = [
            self.validate_range("xgf", xgf, source),
            self.validate_range("xga", xga, source),
            self.validate_range("xgd", xgd, source),
        ]

        # Cross-check: xGD should approximately equal xGF - xGA
        if xgf is not None and xga is not None and xgd is not None:
            expected = xgf - xga
            diff = abs(xgd - expected)
            if diff > 0.1:
                result = ValidationResult(
                    is_valid=False, check_type="RANGE", severity="WARNING",
                    field="xgd_consistency",
                    message=f"xGD ({xgd}) != xGF ({xgf}) - xGA ({xga}) = {expected:.2f}",
                    actual_value=xgd, expected_range=f"~{expected:.2f}",
                )
                self.validation_log.append(result)
                results.append(result)

        return results

    def validate_cross_source(
        self,
        field: str,
        value_a: float,
        source_a: str,
        value_b: float,
        source_b: str,
    ) -> ValidationResult:
        """Flag two sources (e.g. Understat vs FBref) disagreeing beyond tolerance."""
        tolerance = CROSS_SOURCE_TOLERANCE.get(field, 0.2)
        diff = abs(value_a - value_b)
        if diff > tolerance:
            result = ValidationResult(
                is_valid=False, check_type="CROSS_SOURCE", severity="WARNING", field=field,
                message=(
                    f"{field}: {source_a}={value_a} vs {source_b}={value_b} "
                    f"(diff {diff:.2f} > {tolerance})"
                ),
                actual_value=diff, expected_range=f"<= {tolerance}",
            )
            logger.warning(result.message)
            self.validation_log.append(result)
            return result
        return ValidationResult(
            is_valid=True, check_type="CROSS_SOURCE", severity="INFO", field=field,
            message=f"{field} agrees across sources", actual_value=diff,
        )
