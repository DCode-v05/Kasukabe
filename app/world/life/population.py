"""Population governor: turns the god-tab dials into decisions.

DESIGN.md section 5: "Population is a god-tab dial: target band (start
250-300), death-hazard multiplier (0 = deaths off), blessing throttle."
This module is the single place every death path (death.py) and the
birth queue (birth.py) go through to respect those dials, so tuning
population dynamics is "change a PopulationDials value," not "hunt
through health.py/death.py/birth.py for a hardcoded rate."
"""

from __future__ import annotations

import random

from pydantic import BaseModel

from world.life.models import PopulationDials


def roll_death(rng: random.Random, base_probability: float, dials: PopulationDials) -> bool:
    """Should a death that would otherwise happen actually happen?

    ``death_hazard_multiplier == 0`` disables death outright (DESIGN.md,
    verbatim), independent of how the death was triggered - terminal
    schedule, chronic hazard, elderly hazard, or dormancy timeout all
    funnel through here so the dial has one implementation, not four.
    """
    if dials.death_hazard_multiplier <= 0.0:
        return False
    probability = min(1.0, base_probability * dials.death_hazard_multiplier)
    return rng.random() < probability


class BirthQueueDecision(BaseModel):
    auto_hold: bool
    reason: str


def evaluate_birth_request(current_population: int, dials: PopulationDials) -> BirthQueueDecision:
    """Should a newly-submitted birth request be auto-held before it ever
    reaches the operator's god-tab queue?

    Two independent reasons to hold: the population is already at or
    above the target band's ceiling, or the operator has throttled the
    blessing queue shut (``blessing_throttle == 0``). A request that is
    not auto-held still needs the operator's explicit grant
    (DESIGN.md section 8: the baby blessing queue is grant/silent-deny,
    same shape as the prayer feed) - this function only decides whether
    it is even worth showing them, it never grants on its own.
    """
    if current_population >= dials.target_band_max:
        return BirthQueueDecision(
            auto_hold=True,
            reason=f"population {current_population} at or above target band ceiling {dials.target_band_max}",
        )
    if dials.blessing_throttle <= 0.0:
        return BirthQueueDecision(
            auto_hold=True, reason="blessing queue throttled shut by operator"
        )
    return BirthQueueDecision(auto_hold=False, reason="within target band, queue open")


def is_below_target_band(current_population: int, dials: PopulationDials) -> bool:
    """True if the town has fewer agents than the healthy target band wants."""
    return current_population < dials.target_band_min
