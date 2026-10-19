"""Backtest parameter space.

Two scopes:

* strategy  - changes how setups are detected (strategy config). Needs the
              signal replay (Phase A) to evaluate it, but one replay pass
              evaluates every strategy parameter set at once.
* execution - changes what is done with signals (score threshold, exits,
              trailing stop). Only re-runs the cheap execution replay.

Every parameter has bounds. Grids are capped (MAX_GRID_COMBINATIONS):
searching many combinations and keeping the best is the textbook way to
fit noise, and the number of combinations tried is reported with every
result.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.strategies.config import StrategyEngineConfig
from app.strategies.schemas import StrategyName

MAX_GRID_COMBINATIONS = 27


class Scope(str, Enum):
    STRATEGY = "strategy"
    EXECUTION = "execution"


class StrategyChoice(str, Enum):
    """`ALL` runs the live router (every strategy, routed by regime)."""

    ALL = "ALL"
    # Every strategy, including the four added PDF strategies, through the router.
    ALL_EXTENDED = "ALL_EXTENDED"
    TREND_CONTINUATION = StrategyName.TREND_CONTINUATION.value
    BREAKOUT_MOMENTUM = StrategyName.BREAKOUT_MOMENTUM.value
    MEAN_REVERSION = StrategyName.MEAN_REVERSION.value
    RANGE_TRADING = StrategyName.RANGE_TRADING.value
    BREAKOUT_TRADING = StrategyName.BREAKOUT_TRADING.value
    VOLUME_TRADING = StrategyName.VOLUME_TRADING.value
    MA_CROSSOVER = StrategyName.MA_CROSSOVER.value


class TrailingMode(str, Enum):
    NONE = "NONE"
    BREAKEVEN = "BREAKEVEN"  # move the stop to entry once the trade is +activation R
    ATR = "ATR"  # then trail at (best close - multiple x ATR)


class ParamSpec(BaseModel):
    key: str
    label: str
    scope: Scope
    kind: str  # "float" | "int" | "choice"
    default: float | int | str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: list[str | int] | None = None
    path: str | None = None  # strategy config attribute path, e.g. "geometry.min_reward_risk"
    description: str = ""


def _f(
    key, label, default, lo, hi, step, path=None, scope=Scope.STRATEGY, desc=""
):  # noqa: ANN001, ANN202
    return ParamSpec(
        key=key,
        label=label,
        scope=scope,
        kind="float",
        default=default,
        minimum=lo,
        maximum=hi,
        step=step,
        path=path or key,
        description=desc,
    )


ROUTER_PARAMS = [
    _f(
        "min_regime_confidence",
        "Min regime confidence",
        50.0,
        0,
        95,
        5,
        path="router.min_regime_confidence",
        desc="Below this regime confidence no strategy is routed.",
    )
]

STRATEGY_PARAMS: dict[StrategyChoice, list[ParamSpec]] = {
    StrategyChoice.ALL: ROUTER_PARAMS,
    StrategyChoice.TREND_CONTINUATION: [
        _f("min_pullback_percent", "Min pullback %", 30.0, 10, 60, 5),
        _f("max_pullback_percent", "Max pullback %", 65.0, 40, 90, 5),
        _f("min_trend_quality", "Min trend quality", 40.0, 0, 80, 5),
        _f("min_relative_strength", "Min relative strength", 55.0, 50, 80, 5),
        _f("min_alignment", "Min timeframe alignment", 50.0, 0, 90, 5),
        _f(
            "min_reward_risk",
            "Min reward:risk",
            1.5,
            1.0,
            3.0,
            0.25,
            path="geometry.min_reward_risk",
        ),
        _f(
            "stop_buffer_atr",
            "Stop buffer (ATR)",
            0.2,
            0.0,
            1.0,
            0.05,
            path="geometry.stop_buffer_atr",
        ),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.BREAKOUT_MOMENTUM: [
        _f("min_relative_volume", "Min relative volume", 1.3, 1.0, 3.0, 0.1),
        _f("max_extension_atr", "Max extension (ATR)", 1.5, 0.5, 3.0, 0.25),
        _f("expansion_range_multiple", "Expansion range multiple", 1.3, 1.0, 3.0, 0.1),
        ParamSpec(
            key="opening_range_minutes",
            label="Opening range (min)",
            scope=Scope.STRATEGY,
            kind="choice",
            default=15,
            choices=[5, 15, 30],
            path="opening_range_minutes",
        ),
        _f(
            "min_reward_risk",
            "Min reward:risk",
            1.5,
            1.0,
            3.0,
            0.25,
            path="geometry.min_reward_risk",
        ),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.MEAN_REVERSION: [
        _f("max_trend_quality", "Max trend quality", 50.0, 20, 80, 5),
        _f("min_vwap_deviation_atr", "Min VWAP deviation (ATR)", 0.5, 0.25, 2.0, 0.25),
        _f("boundary_proximity_atr", "Range-edge proximity (ATR)", 0.5, 0.25, 1.5, 0.25),
        _f("min_extension_atr", "Min extension (ATR)", 0.75, 0.25, 2.0, 0.25),
        _f(
            "min_reward_risk",
            "Min reward:risk",
            1.0,
            0.75,
            2.5,
            0.25,
            path="geometry.min_reward_risk",
        ),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.ALL_EXTENDED: ROUTER_PARAMS,
    StrategyChoice.RANGE_TRADING: [
        _f("max_adx", "Max ADX (trend filter)", 25.0, 10, 40, 5),
        _f("rsi_oversold", "RSI oversold", 35.0, 20, 45, 5),
        _f("rsi_overbought", "RSI overbought", 65.0, 55, 80, 5),
        _f("boundary_zone", "Boundary zone (share of width)", 0.25, 0.1, 0.4, 0.05),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.BREAKOUT_TRADING: [
        _f("min_relative_volume", "Min breakout volume (x)", 1.5, 1.0, 3.0, 0.25),
        _f("min_body_fraction", "Min candle body", 0.5, 0.3, 0.8, 0.1),
        _f("max_extension_atr", "Max extension (ATR)", 1.5, 0.5, 3.0, 0.25),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.VOLUME_TRADING: [
        _f("min_relative_volume", "Min relative volume (x)", 2.0, 1.25, 5.0, 0.25),
        _f("min_body_fraction", "Min candle body", 0.5, 0.3, 0.8, 0.1),
        *ROUTER_PARAMS,
    ],
    StrategyChoice.MA_CROSSOVER: [
        _f("min_adx", "Min ADX", 20.0, 10, 40, 5),
        _f("max_bars_since_cross", "Max candles since crossover", 3, 1, 10, 1).model_copy(
            update={"kind": "int"}
        ),
        *ROUTER_PARAMS,
    ],
}

EXECUTION_PARAMS: list[ParamSpec] = [
    _f(
        "min_score",
        "Min signal score",
        85.0,
        50,
        100,
        1,
        scope=Scope.EXECUTION,
        desc="Signals below this are not traded (live executable threshold: 85).",
    ),
    ParamSpec(
        key="trailing_mode",
        label="Trailing stop",
        scope=Scope.EXECUTION,
        kind="choice",
        default=TrailingMode.NONE.value,
        choices=[m.value for m in TrailingMode],
        description="NONE, BREAKEVEN (stop to entry at +activation R) or ATR (then trail by ATR).",
    ),
    _f(
        "trailing_activation_r",
        "Trailing activation (R)",
        1.0,
        0.25,
        3.0,
        0.25,
        scope=Scope.EXECUTION,
    ),
    _f(
        "trailing_atr_multiple",
        "Trailing distance (ATR)",
        1.5,
        0.5,
        4.0,
        0.25,
        scope=Scope.EXECUTION,
    ),
]

_STRATEGY_FIELD = {
    StrategyChoice.TREND_CONTINUATION: "trend_continuation",
    StrategyChoice.BREAKOUT_MOMENTUM: "breakout_momentum",
    StrategyChoice.MEAN_REVERSION: "mean_reversion",
    StrategyChoice.RANGE_TRADING: "range_trading",
    StrategyChoice.BREAKOUT_TRADING: "breakout_trading",
    StrategyChoice.VOLUME_TRADING: "volume_trading",
    StrategyChoice.MA_CROSSOVER: "ma_crossover",
}


def param_space(strategy: StrategyChoice) -> list[ParamSpec]:
    return [*STRATEGY_PARAMS[strategy], *EXECUTION_PARAMS]


def defaults(strategy: StrategyChoice) -> dict[str, Any]:
    return {p.key: p.default for p in param_space(strategy)}


class ParameterError(ValueError):
    pass


def validate_params(strategy: StrategyChoice, params: dict[str, Any]) -> dict[str, Any]:
    """Full parameter set: defaults overlaid with `params`, every value checked."""

    specs = {p.key: p for p in param_space(strategy)}
    unknown = set(params) - set(specs)
    if unknown:
        raise ParameterError(
            f"Unknown parameter(s) for {strategy.value}: {', '.join(sorted(unknown))}"
        )
    out = defaults(strategy)
    for key, value in params.items():
        spec = specs[key]
        if spec.kind == "choice":
            allowed = spec.choices or []
            if value not in allowed and str(value) not in [str(c) for c in allowed]:
                raise ParameterError(f"{key} must be one of {allowed}")
            out[key] = type(spec.default)(value)
        else:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ParameterError(f"{key} must be a number") from exc
            if spec.minimum is not None and number < spec.minimum:
                raise ParameterError(f"{key} must be >= {spec.minimum}")
            if spec.maximum is not None and number > spec.maximum:
                raise ParameterError(f"{key} must be <= {spec.maximum}")
            out[key] = int(number) if spec.kind == "int" else number
    return out


def expand_grid(
    strategy: StrategyChoice, base: dict[str, Any], grid: dict[str, list[Any]] | None
) -> list[dict[str, Any]]:
    """Every combination of the grid values over `base`, validated and capped."""

    full = validate_params(strategy, base)
    if not grid:
        return [full]
    keys = sorted(grid)
    for key in keys:
        if not grid[key]:
            raise ParameterError(f"Grid for {key} is empty")
    combos = 1
    for key in keys:
        combos *= len(grid[key])
    if combos > MAX_GRID_COMBINATIONS:
        raise ParameterError(
            f"{combos} parameter combinations requested; the limit is {MAX_GRID_COMBINATIONS}. "
            "Large searches mostly find noise that fits the past."
        )
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        out.append(validate_params(strategy, {**full, **dict(zip(keys, values, strict=True))}))
    return out


def split_scopes(
    strategy: StrategyChoice, params: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    specs = {p.key: p for p in param_space(strategy)}
    strat = {k: v for k, v in params.items() if specs[k].scope == Scope.STRATEGY}
    execu = {k: v for k, v in params.items() if specs[k].scope == Scope.EXECUTION}
    return strat, execu


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _set_path(obj: Any, path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    for part in parents:
        obj = getattr(obj, part)
    setattr(obj, leaf, value)


def engine_config_for(
    strategy: StrategyChoice, strategy_params: dict[str, Any]
) -> StrategyEngineConfig:
    """The strategy engine config a parameter set describes. A single
    strategy is tested on its own (the others are disabled) but still
    behind the regime router, exactly as live."""

    cfg = StrategyEngineConfig()
    specs = {p.key: p for p in STRATEGY_PARAMS[strategy]}
    if strategy == StrategyChoice.ALL_EXTENDED:
        for attr in _STRATEGY_FIELD.values():
            getattr(cfg, attr).enabled = True
    elif strategy != StrategyChoice.ALL:
        for choice, attr in _STRATEGY_FIELD.items():
            getattr(cfg, attr).enabled = choice == strategy
    for key, value in strategy_params.items():
        spec = specs[key]
        assert spec.path is not None
        if spec.path.startswith("router."):
            _set_path(cfg, spec.path, value)
        else:
            _set_path(getattr(cfg, _STRATEGY_FIELD[strategy]), spec.path, value)
    return cfg


class StrategyVersionInfo(BaseModel):
    strategy: str
    version_id: str  # semver + config hash, as stamped on live signals
    semver: str
    config_hash: str
    config: dict[str, Any] = Field(default_factory=dict)


def strategy_versions(
    strategy: StrategyChoice, strategy_params: dict[str, Any]
) -> list[StrategyVersionInfo]:
    """Version ids of the strategies a parameter set runs (one, or all three)."""

    from app.strategies.router import StrategyRouter

    router = StrategyRouter(engine_config_for(strategy, strategy_params))
    out = []
    for name, strat in router.strategies.items():
        if not router.enabled[name]:
            continue
        vid = strat.version_id
        semver, _, digest = vid.partition("+")
        cfg = getattr(strat, "config", None)
        out.append(
            StrategyVersionInfo(
                strategy=name.value,
                version_id=vid,
                semver=semver,
                config_hash=digest,
                config=cfg.model_dump(mode="json") if cfg is not None else {},
            )
        )
    return out
