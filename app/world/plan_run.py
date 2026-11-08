"""Plan a run from two numbers: how many residents, and how much it may spend.

Everything else follows. How many ticks that budget buys, whether an election
fits inside them, which cast file has that many people, how long it will take
on the clock - all of it was arithmetic somebody did by hand before every run,
and got wrong at least once: the first 20-resident day-2 attempt was written
to run two whole game-days on $20, which the measured rates say costs $69.

    python -m world.plan_run --agents 20 --budget 20
    python -m world.plan_run --agents 20 --budget 20 --out world/scenarios/mine.json

Prints the plan. With --out it also writes a scenario the launcher can run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TICKS_PER_DAY = 144
TICKS_PER_HOUR = TICKS_PER_DAY // 24

#: Dollars per resident-day, measured on real runs rather than assumed:
#: election8 $2.28, civic12 $2.49, election30 $1.72, trial-day2-20 $0.96.
#: The default is deliberately near the TOP of that range - a plan that
#: overshoots stops early at the cap, a plan that undershoots runs out of
#: money before the vote, which is the expensive way to be wrong.
DEFAULT_RATE_USD = 2.30

#: Wall-clock minutes per tick, measured at 20 residents with six cameras.
#: A tick waits for its slowest resident, so this barely moves with cast size.
MINUTES_PER_TICK = 1.5

#: Population -> cast file. Where a population has several, the one with the
#: most complete high street wins: a town with a bakery AND a mill can run the
#: wheat -> flour -> bread chain, and one without leaves flour piling up.
CASTS: dict[int, str] = {
    3: "world/genesis_3.json",
    6: "world/genesis_6.json",
    8: "world/genesis_8_funded.json",
    10: "world/genesis_10.json",
    12: "world/genesis_12_funded.json",
    14: "world/genesis_14.json",
    20: "world/genesis_20_funded.json",
    24: "world/genesis_24.json",
    30: "world/genesis_30_town.json",
    40: "world/genesis_realsim_40.json",
    50: "world/genesis_50_town.json",
}


def cast_for(agents: int) -> tuple[int, str]:
    """The nearest cast at or below the asked-for size, and its real size.

    Rounding DOWN, never up: a run given more residents than its budget was
    planned for runs out of money mid-election.
    """
    fits = [n for n in sorted(CASTS) if n <= agents]
    if not fits:
        smallest = min(CASTS)
        return smallest, CASTS[smallest]
    best = fits[-1]
    return best, CASTS[best]


def plan(agents: int, budget: float, rate: float, start_day: int,
         election: bool) -> dict:
    residents, genesis = cast_for(agents)

    affordable_days = budget / (residents * rate)
    ticks = int(affordable_days * TICKS_PER_DAY)

    start = start_day * TICKS_PER_DAY + 36  # dawn, the hour a town wakes

    # An election needs candidacy + campaign + voting before it can be
    # counted, and then some of the day left to see what the winners do.
    # Windows are sized to fit what the budget bought rather than fixed, and
    # never below an hour - a window shorter than that closes before a
    # resident whose turn is elsewhere can act in it.
    # CLAMPED at both ends, and each end is a different failure.
    #
    # Too short and the window closes before a resident whose turn fell
    # elsewhere can act in it: a 6-tick candidacy window is an election most
    # of the town never sees. Too long and the vote eats the whole run - on
    # $80 the unclamped arithmetic produced 13-hour windows, so the town
    # spent its entire life campaigning and the winners governed for nothing.
    # A bigger budget should buy AFTERMATH, not a longer ballot.
    usable = max(ticks - 12, 0)  # keep ~2 game-hours after the count
    window_h = min(4.0, max(1.5, (usable // 3) / TICKS_PER_HOUR))
    counted_at = start + int(window_h * TICKS_PER_HOUR) * 3 if election else start

    stop = start + ticks
    return {
        "asked_agents": agents,
        "residents": residents,
        "genesis": genesis,
        "budget": budget,
        "rate": rate,
        "start_tick": start,
        "stop_tick": stop,
        "ticks": ticks,
        "game_days": ticks / TICKS_PER_DAY,
        "start_day": start_day,
        "window_hours": window_h,
        "counted_at": counted_at,
        "election": election,
        "hours_wall": ticks * MINUTES_PER_TICK / 60,
        "fits": (not election) or (counted_at < stop),
    }


def describe(p: dict) -> str:
    out = [
        f"  residents   {p['residents']}"
        + (f"  (asked {p['asked_agents']}; nearest cast at or below)"
           if p["residents"] != p["asked_agents"] else ""),
        f"  cast        {p['genesis']}",
        f"  budget      ${p['budget']:.2f}  at ${p['rate']:.2f}/resident-day",
        f"  ticks       {p['start_tick']} -> {p['stop_tick']}  "
        f"({p['ticks']} ticks, {p['game_days']:.2f} game-days)",
        f"  starts      dawn on game-day {p['start_day']}",
        f"  wall clock  about {p['hours_wall']:.1f} hours",
    ]
    if p["election"]:
        out.append(
            f"  election    {p['window_hours']:.1f}-hour windows; "
            f"the vote is counted at tick {p['counted_at']}"
        )
        if p["window_hours"] <= 1.5 and p["fits"]:
            out.append(
                "  ** the windows are as short as they go. Most of the town "
                "will never get a turn inside one; treat the turnout as a "
                "floor, not a measurement."
            )
        if not p["fits"]:
            out.append(
                "  ** THE VOTE IS NEVER REACHED on this budget. Raise it, or "
                "cut the cast: at this rate the election alone needs "
                f"${p['residents'] * p['rate'] * (p['counted_at'] - p['start_tick']) / TICKS_PER_DAY:.2f}."
            )
    return "\n".join(out)


def scenario(p: dict, name: str) -> dict:
    views = "http://127.0.0.1:8400/township-v2.html"
    return {
        "name": name,
        "description": (
            f"{p['residents']} residents from dawn on game-day {p['start_day']}, "
            f"planned to fit ${p['budget']:.2f} at ${p['rate']:.2f} per "
            "resident-day. Written by world/plan_run.py."
        ),
        "world": {
            "genesis": p["genesis"],
            "start_tick": p["start_tick"],
            "stop_tick": p["stop_tick"],
            "budget_cap_usd": p["budget"],
            "max_game_days": max(2, int(p["game_days"]) + p["start_day"] + 1),
        },
        "charter": {
            "candidacy_window_hours": p["window_hours"],
            "campaign_period_hours": p["window_hours"],
            "voting_window_hours": p["window_hours"],
            "election_interval_hours": 24,
            "pm_seats": 1,
            "minister_seats": 2,
            "suffrage_min_age": 18,
        },
        "election": {"opens_at": "start" if p["election"] else "genesis"},
        "recording": {
            "interval_s": 2, "fps": 30, "width": 1920, "height": 1080,
            "url": f"{views}?book=election&roofs=off&camera=government:2.2",
            "views": [
                {"name": "town-with-buildings", "url": f"{views}?book=town&roofs=on"},
                {"name": "town-without-buildings", "url": f"{views}?book=town&roofs=off"},
                {"name": "live-chats", "url": f"{views}?book=groupchat&roofs=off"},
                {"name": "election", "url": f"{views}?book=election&roofs=off&camera=government:2.2"},
                {"name": "money", "url": f"{views}?book=money&roofs=on"},
                {"name": "government", "url": f"{views}?book=govern&roofs=off&camera=government:2.2"},
            ],
        },
        "report": {"out": f"../docs/runs/series/{name}"},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agents", type=int, required=True, help="how many residents")
    ap.add_argument("--budget", type=float, required=True, help="USD cap for the run")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE_USD,
                    help=f"$/resident-day (default {DEFAULT_RATE_USD}, measured 0.96-2.49)")
    ap.add_argument("--day", type=int, default=2, help="game-day to start on (default 2)")
    ap.add_argument("--no-election", action="store_true", help="skip the election")
    ap.add_argument("--name", default=None, help="run name (default: agents+budget)")
    ap.add_argument("--out", default=None, help="write the scenario here")
    args = ap.parse_args()

    p = plan(args.agents, args.budget, args.rate, args.day, not args.no_election)
    name = args.name or f"run-{p['residents']}a-{int(args.budget)}usd-day{args.day}"
    print(describe(p))
    if args.out:
        Path(args.out).write_text(
            json.dumps(scenario(p, name), indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n  scenario    {args.out}")
    raise SystemExit(0 if p["fits"] else 1)


if __name__ == "__main__":
    main()
