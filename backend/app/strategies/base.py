"""BaseStrategy: the shared evaluation flow.

`evaluate()` runs the steps named in the project brief, in order:

1. analyze()             collect the features the strategy looks at
2. validate()            decide a direction and list the checks
3. calculate_entry()     entry candidate (only if every required check passed)
4. calculate_stop_loss() stop candidate
5. calculate_target()    target candidate
6. explain()             assemble reasons, blockers, confidence

Shared candidate rules (CandidateGeometry): the stop must be between
`min_stop_atr` and `max_stop_atr` from the entry (a too-tight stop is
widened to the minimum; a too-wide one blocks the setup), and the target
must offer at least `min_reward_risk`.

A strategy never raises into the caller: an unexpected error becomes an
ineligible result whose blocker names the error, so one failing
strategy cannot take the others down.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import ClassVar

from app.market_intelligence.utils import clamp
from app.price_action.support_resistance import PriceLevel
from app.strategies.config import CandidateGeometry
from app.strategies.context import StrategyContext
from app.strategies.schemas import (
    FeatureValue,
    StrategyCheck,
    StrategyName,
    StrategyResult,
    TradeDirection,
)

Features = dict[str, FeatureValue]


def check(
    key: str,
    label: str,
    passed: bool,
    detail: str,
    *,
    required: bool = False,
    weight: float = 0.0,
) -> StrategyCheck:
    return StrategyCheck(
        key=key, label=label, passed=bool(passed), required=required, weight=weight, detail=detail
    )


def side(direction: TradeDirection) -> float:
    """+1 for long, -1 for short, 0 otherwise: multiply a price move by
    this to get the move "in the trade's favour"."""

    return {TradeDirection.LONG: 1.0, TradeDirection.SHORT: -1.0}.get(direction, 0.0)


class BaseStrategy(ABC):
    name: ClassVar[StrategyName]
    #: Bump when the strategy's logic changes. Threshold changes are
    #: captured automatically by the config hash in `version_id`.
    version: ClassVar[str] = "1.0.0"
    #: The strategy's concept as ordered (stage label, check key) pairs;
    #: `stage` in the result is the last stage whose check passed in an
    #: unbroken run from the start.
    stages: ClassVar[tuple[tuple[str, str], ...]] = ()

    def __init__(self, geometry: CandidateGeometry, weights: dict[str, float]) -> None:
        self.geometry = geometry
        self.weights = weights

    @property
    def version_id(self) -> str:
        """ "<semver>+<first 8 hex of the SHA-256 of the strategy config>",
        so a signal can always be traced to the exact logic version and
        parameters that produced it."""

        config = getattr(self, "config", None)
        payload = config.model_dump_json() if config is not None else ""
        digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
        return f"{self.version}+{digest}"

    # --- steps implemented by each strategy ------------------------------------

    @abstractmethod
    def analyze(self, ctx: StrategyContext) -> Features:
        """Collect the features this strategy uses."""

    @abstractmethod
    def validate(
        self, ctx: StrategyContext, features: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        """Pick a direction and evaluate every condition."""

    @abstractmethod
    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, features: Features
    ) -> tuple[float, str]:
        """Entry candidate price and how it was derived."""

    @abstractmethod
    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, features: Features
    ) -> tuple[float, str]:
        """Protective level the setup is wrong beyond, and its basis."""

    def calculate_target(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        entry: float,
        stop: float,
        features: Features,
    ) -> tuple[float, str]:
        """Default: the nearest support/resistance level beyond the entry
        that offers at least `min_reward_risk`, else `default_reward_risk`
        times the risk. Strategies with a natural target override this."""

        risk = abs(entry - stop)
        sign = side(direction)
        levels: list[PriceLevel] = (
            ctx.price_action.support_resistance.levels
            if ctx.price_action.support_resistance is not None
            else []
        )
        beyond = sorted(
            (lvl for lvl in levels if (lvl.price - entry) * sign > 0),
            key=lambda lvl: abs(lvl.price - entry),
        )
        if beyond:
            first = beyond[0]
            features["first_obstacle"] = first.label
            features["first_obstacle_rr"] = round(abs(first.price - entry) / risk, 2)
        for level in beyond:
            if abs(level.price - entry) >= self.geometry.min_reward_risk * risk:
                return level.price, f"{level.label} (structure)"
        return (
            entry + sign * self.geometry.default_reward_risk * risk,
            f"{self.geometry.default_reward_risk:g}R (no structure level far enough)",
        )

    # --- shared flow ---------------------------------------------------------------

    def evaluate(
        self,
        ctx: StrategyContext,
        allowed_directions: list[TradeDirection] | None = None,
    ) -> StrategyResult:
        try:
            return self._evaluate(ctx, allowed_directions)
        except Exception as exc:  # noqa: BLE001 - a broken strategy must not break routing
            return StrategyResult(
                strategy=self.name,
                strategy_version=self.version_id,
                symbol=ctx.symbol,
                timeframe=ctx.timeframe,
                eligible=False,
                direction=TradeDirection.NONE,
                blockers=[f"Strategy error: {type(exc).__name__}: {exc}"],
                data_as_of=ctx.latest.timestamp if ctx.candles else None,
                timestamp=ctx.now,
            )

    def _evaluate(
        self, ctx: StrategyContext, allowed_directions: list[TradeDirection] | None
    ) -> StrategyResult:
        # Round once, so the features returned and the check details built
        # from them show the same readable numbers (prices to 2 decimals,
        # the NSE tick being 0.05).
        features = {
            k: round(v, 2) if isinstance(v, float) else v for k, v in self.analyze(ctx).items()
        }
        direction, checks = self.validate(ctx, features)

        if allowed_directions is not None and direction != TradeDirection.NONE:
            permitted = direction in allowed_directions
            checks.append(
                check(
                    "direction_allowed",
                    "Direction allowed by router",
                    permitted,
                    f"{direction.value} "
                    + ("permitted" if permitted else "not permitted")
                    + f" for {ctx.regime.regime.value}",
                    required=True,
                )
            )

        entry = stop = target = None
        entry_basis = stop_basis = target_basis = None
        geometry_blockers: list[str] = []
        ready = direction != TradeDirection.NONE and all(c.passed for c in checks if c.required)
        if ready:
            entry, entry_basis = self.calculate_entry(ctx, direction, features)
            stop, stop_basis = self.calculate_stop_loss(ctx, direction, entry, features)
            sign = side(direction)
            distance = (entry - stop) * sign
            minimum = self.geometry.min_stop_atr * ctx.atr
            if distance < minimum:
                stop = entry - sign * minimum
                stop_basis = f"{stop_basis}, widened to {self.geometry.min_stop_atr:g} ATR"
                distance = minimum
            if distance > self.geometry.max_stop_atr * ctx.atr:
                geometry_blockers.append(
                    f"Stop is {distance / ctx.atr:.1f} ATR away "
                    f"(max {self.geometry.max_stop_atr:g} ATR)"
                )
            target, target_basis = self.calculate_target(ctx, direction, entry, stop, features)
            reward = (target - entry) * sign
            if reward / distance < self.geometry.min_reward_risk - 1e-9:
                geometry_blockers.append(
                    f"Target offers {reward / distance:.2f}R "
                    f"(min {self.geometry.min_reward_risk:g}R)"
                )

        return self.explain(
            ctx,
            direction,
            checks,
            features,
            (entry, entry_basis),
            (stop, stop_basis),
            (target, target_basis),
            geometry_blockers,
        )

    def explain(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        checks: list[StrategyCheck],
        features: Features,
        entry: tuple[float | None, str | None],
        stop: tuple[float | None, str | None],
        target: tuple[float | None, str | None],
        geometry_blockers: list[str],
    ) -> StrategyResult:
        blockers = [f"{c.label}: {c.detail}" for c in checks if c.required and not c.passed]
        if direction == TradeDirection.NONE and not blockers:
            blockers.append("No direction for this setup")
        blockers += geometry_blockers
        eligible = not blockers

        total = sum(c.weight for c in checks) or 1.0
        score = 100.0 * sum(c.weight for c in checks if c.passed) / total
        reasons = [f"{c.label}: {c.detail}" for c in checks if c.passed]

        stage = None
        for label, key in self.stages:
            match = next((c for c in checks if c.key == key), None)
            if match is None or not match.passed:
                break
            stage = label
        if eligible:
            stage = "ENTRY_CANDIDATE"

        entry_price, entry_basis = entry
        stop_price, stop_basis = stop
        target_price, target_basis = target
        risk = reward = rr = None
        if (
            eligible
            and entry_price is not None
            and stop_price is not None
            and target_price is not None
        ):
            risk = abs(entry_price - stop_price)
            reward = abs(target_price - entry_price)
            rr = reward / risk if risk else None

        def rounded(value: float | None) -> float | None:
            return round(value, 2) if value is not None else None

        return StrategyResult(
            strategy=self.name,
            strategy_version=self.version_id,
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
            eligible=eligible,
            direction=direction,
            entry_candidate=rounded(entry_price) if eligible else None,
            stop_candidate=rounded(stop_price) if eligible else None,
            target_candidate=rounded(target_price) if eligible else None,
            risk_per_unit=rounded(risk),
            reward_per_unit=rounded(reward),
            reward_risk=round(rr, 2) if rr is not None else None,
            entry_basis=entry_basis if eligible else None,
            stop_basis=stop_basis if eligible else None,
            target_basis=target_basis if eligible else None,
            confidence=round(clamp(score), 1) if eligible else 0.0,
            setup_score=round(clamp(score), 1),
            stage=stage,
            checks=checks,
            reasons=reasons,
            blockers=blockers,
            features=features,
            data_as_of=ctx.latest.timestamp,
            timestamp=ctx.now,
        )
