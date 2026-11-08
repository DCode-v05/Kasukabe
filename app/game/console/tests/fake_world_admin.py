"""In-memory stand-in for world's admin HTTP API (README.md's "Open
integration point"), wired up behind an httpx.MockTransport so
game/console/tests/ can exercise a full HTTP round trip - app.py's route
-> WorldAdminClient -> this fake -> back - without a running world
process (app/CONVENTIONS.md: "an httpx mock transport" is the sanctioned
stand-in for a real out-of-process dependency in tests).

This is NOT a second implementation of world/'s logic to keep in sync by
hand forever - it exists only inside tests/, is only as smart as it needs
to be to prove console's own plumbing (auth forwarding, request shaping,
response parsing, error propagation) actually works end to end, and every
piece of real decision-making (should this petition be approved, is the
population dial in range) it does is a deliberately simplified stand-in
for the real thing world/ will eventually do.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


def json_response(status_code: int, body: Any) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(body), headers={"content-type": "application/json"})


class FakeWorldAdmin:
    def __init__(self, expected_token: str = "test-operator-token") -> None:
        self.expected_token = expected_token
        self.next_id = 1
        self.petitions: dict[str, dict[str, Any]] = {}
        self.amendments: dict[str, dict[str, Any]] = {}
        self.broadcasts: list[dict[str, Any]] = []
        self.honors: list[dict[str, Any]] = []
        self.prayers: dict[str, dict[str, Any]] = {}
        self.blessings: dict[str, dict[str, Any]] = {}
        self.disasters: list[dict[str, Any]] = []
        self.miracles: list[dict[str, Any]] = []
        self.misfortunes: list[dict[str, Any]] = []
        self.dials: dict[str, Any] = {
            "target_band_min": 250,
            "target_band_max": 300,
            "death_hazard_multiplier": 1.0,
            "blessing_throttle": 1.0,
        }
        self.alerts: list[dict[str, Any]] = []
        # DESIGN.md section 3's genesis split ($500 treasury + $500 across
        # 250 agent wallets, world/ledger/money.py's GENESIS_TREASURY_USD_CENTS
        # / GENESIS_AGENT_USD_CENTS * GENESIS_AGENT_COUNT), nothing burned
        # yet - the same starting point a fresh Ledger() boots with.
        self.spend: dict[str, Any] = {
            "burned_usd_cents": 0,
            "treasury_usd_cents": 50_000,
            "wallets_usd_cents": 50_000,
            "budget_total_usd_cents": 100_000,
            "tick_index": 0,
            "sampled_at": "2026-07-31T00:00:00+00:00",
        }
        self.control: dict[str, Any] = {
            "paused": False,
            "pacing_mode": "continuous",
            "continuous_interval_s": 0.0,
            "tick_index": 0,
            "game_day": 0,
        }
        self.call_log: list[tuple[str, str]] = []

    def new_id(self, prefix: str) -> str:
        value = f"{prefix}-{self.next_id}"
        self.next_id += 1
        return value

    # -- seeding helpers used by tests -------------------------------------

    def seed_petition(
        self,
        title: str = "grow south",
        tile_count_requested: int = 10,
        filed_by_agent_id: str = "agent-mayor",
    ) -> dict[str, Any]:
        petition_id = self.new_id("petition")
        record = {
            "petition_id": petition_id,
            "filed_by_agent_id": filed_by_agent_id,
            "title": title,
            "description": "the town needs more farmland",
            "tile_count_requested": tile_count_requested,
            "filed_tick": 10,
            "status": "pending",
            "decided_tick": None,
            "decision_note": None,
        }
        self.petitions[petition_id] = record
        return record

    def seed_amendment(self, field_name: str = "tax_rate_bps") -> dict[str, Any]:
        proposal_id = self.new_id("amend")
        record = {
            "proposal_id": proposal_id,
            "field_name": field_name,
            "new_value": 500,
            "title": "raise taxes",
            "rationale": "treasury is low",
            "proposed_by_agent_id": "agent-pm",
            "proposed_tick": 20,
            "votes": {"agent-pm": True},
            "status": "passed_awaiting_president",
            "resulting_charter_version": None,
            "enacted_tick": None,
            "vetoed_tick": None,
            "veto_note": None,
        }
        self.amendments[proposal_id] = record
        return record

    def seed_prayer(self, agent_id: str = "agent-1", content: str = "please help me") -> dict[str, Any]:
        prayer_id = self.new_id("prayer")
        record = {"prayer_id": prayer_id, "agent_id": agent_id, "content": content, "offered_tick": 5}
        self.prayers[prayer_id] = record
        return record

    def seed_blessing(self, household_id: str = "household-1") -> dict[str, Any]:
        request_id = self.new_id("birth")
        record = {
            "request_id": request_id,
            "household_id": household_id,
            "parent_ids": ["agent-mom", "agent-dad"],
            "sponsor_parent_id": "agent-mom",
            "requested_at_hours": 100.0,
            "status": "pending",
            "silent_deny": False,
            "newborn_agent_id": None,
        }
        self.blessings[request_id] = record
        return record

    def seed_spend(
        self,
        burned_usd_cents: int = 0,
        treasury_usd_cents: int = 50_000,
        wallets_usd_cents: int = 50_000,
        tick_index: int = 0,
        sampled_at: str = "2026-07-31T00:00:00+00:00",
    ) -> dict[str, Any]:
        self.spend = {
            "burned_usd_cents": burned_usd_cents,
            "treasury_usd_cents": treasury_usd_cents,
            "wallets_usd_cents": wallets_usd_cents,
            "budget_total_usd_cents": burned_usd_cents + treasury_usd_cents + wallets_usd_cents,
            "tick_index": tick_index,
            "sampled_at": sampled_at,
        }
        return self.spend

    def seed_alert(self, category: str = "crime_spike", summary: str = "theft cluster near the market") -> dict[str, Any]:
        alert = {
            "alert_id": self.new_id("alert"),
            "category": category,
            "summary": summary,
            "raised_tick": 42,
            "payload": {},
        }
        self.alerts.append(alert)
        return alert

    # -- the transport handler ---------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else auth
        if token != self.expected_token:
            return json_response(401, {"detail": "bad operator token"})

        method = request.method
        path = request.url.path
        self.call_log.append((method, path))
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)
        params = dict(request.url.params)

        parts = [p for p in path.split("/") if p]
        # B7: the console also proxies world's READ-ONLY routers, which are
        # mounted at the root of the same app as /admin (world/main.py), not
        # under it - so this fake answers both shapes.
        if parts and parts[0] in ("family", "businesses", "skills", "governance"):
            return self.handle_town(method, parts, params)
        # every other path here is /admin/<tab>/<...>
        assert parts[0] == "admin", path
        tab = parts[1]

        if tab == "president":
            return self.handle_president(method, parts[2:], body)
        if tab == "god":
            return self.handle_god(method, parts[2:], body)
        if tab == "spend":
            return self.handle_spend(method, parts[2:])
        if tab == "alerts":
            return self.handle_alerts(method, params)
        if tab == "control":
            return self.handle_control(method, parts[2:], body)
        return json_response(404, {"detail": f"no such admin route: {path}"})

    # -- B7: the read-only town views ----------------------------------
    # Deliberately tiny and static: console's own plumbing is what these
    # tests prove, and the real numbers are already proven against a real
    # WorldServer in world/tests/test_family_api.py and friends.

    town_households_body = [
        {"household_id": "hh-duval", "member_ids": ["a", "b", "c"], "shape": "single-earner with dependents",
         "earners": 1, "dependents": 2, "combined_sim": 60, "daily_bill_sim": 270,
         "days_of_runway": 0, "status": "destitute"},
        {"household_id": "hh-okafor", "member_ids": ["d", "e"], "shape": "dual-earner, no dependents",
         "earners": 2, "dependents": 0, "combined_sim": 9000, "daily_bill_sim": 180,
         "days_of_runway": 50, "status": "comfortable"},
    ]
    town_businesses_body = [
        {"business_id": "biz-1", "name": "Kasukabe Grocery", "business_type": "grocery",
         "owner_agent_id": "d", "owner_name": "Dara", "balance_sim": 40, "revenue_sim": 900,
         "costs_sim": 1400, "profit_sim": -500, "wages_paid_sim": 900, "employees": 2,
         "days_of_wages_covered": 0, "trend": "cannot make payroll",
         "by_day": [{"day": 0, "revenue_sim": 900, "costs_sim": 500, "profit_sim": 400},
                    {"day": 1, "revenue_sim": 0, "costs_sim": 900, "profit_sim": -900}]},
    ]
    town_policy_treasury_body = {
        "policy": {"tax_rate_bps": 300},
        "treasury": {"balance_sim": 12000, "balance_usd_cents": 50000, "tax_collected_sim": 5000,
                     "wages_paid_sim": 1000, "pensions_paid_sim": 200, "fees_collected_sim": 100,
                     "other_out_sim": 0},
    }
    town_skills_body = [
        {"skill": "baking", "practitioners": 4, "average_proficiency": 40.0,
         "best": {"agent_id": "d", "name": "Dara", "proficiency": 90, "learned": "taught"},
         "holders": []},
    ]
    town_money_flow_body = {"total_moved_sim": 6100, "transactions": 12, "by_reason": [],
                            "into_treasury_sim": 5100, "out_of_treasury_sim": 1200,
                            "between_agents_sim": 0}
    town_wealth_body = {"richest": [], "poorest": [], "biggest_givers": [], "biggest_earners": []}
    town_terms_body = [{"pm_agent_id": "d", "pm_name": "Dara", "minister_agent_ids": [],
                        "election_id": "e1", "took_office_tick": 10, "left_office_tick": None,
                        "is_current": True, "amendments_enacted": ["a1"],
                        "amendment_titles": ["raise tax"], "treasury_at_start_sim": None}]

    def handle_town(self, method: str, parts: list[str], params: dict[str, Any]) -> httpx.Response:
        route = "/".join(parts)
        if route == "family/households":
            return json_response(200, self.town_households_body)
        if route == "family/at-risk":
            return json_response(200, self.town_households_body)
        if route == "family/wealth":
            return json_response(200, self.town_wealth_body)
        if route == "family/money-flow":
            return json_response(200, self.town_money_flow_body)
        if route == "businesses/pnl":
            return json_response(200, self.town_businesses_body)
        if route == "governance/policy-and-treasury":
            return json_response(200, self.town_policy_treasury_body)
        if route == "governance/terms":
            return json_response(200, self.town_terms_body)
        if route == "skills/town":
            return json_response(200, self.town_skills_body)
        return json_response(404, {"detail": f"no such town route: /{route}"})

    def handle_president(self, method: str, parts: list[str], body: dict[str, Any]) -> httpx.Response:
        if parts[:1] == ["petitions"] and len(parts) == 1 and method == "GET":
            pending = [p for p in self.petitions.values() if p["status"] == "pending"]
            return json_response(200, pending)
        if len(parts) == 3 and parts[0] == "petitions" and parts[2] in ("approve", "deny"):
            petition = self.petitions.get(parts[1])
            if petition is None:
                return json_response(404, {"detail": f"unknown petition {parts[1]}"})
            if petition["status"] != "pending":
                return json_response(409, {"detail": "petition already decided"})
            if parts[2] == "approve":
                petition["status"] = "approved"
                petition["decision_note"] = body.get("decision_note", "")
                petition["decided_tick"] = 30
                expansion = {
                    "edge": body["edge"],
                    "depth": body["depth"],
                    "previous_width": 100,
                    "previous_height": 100,
                    "new_width": 100 if body["edge"] == "south" else 100 + body["depth"],
                    "new_height": 100 + body["depth"] if body["edge"] == "south" else 100,
                    "new_sector_ids": [900] if body.get("new_sector_name") else [],
                    "new_sector_names": [body["new_sector_name"]] if body.get("new_sector_name") else [],
                }
                return json_response(200, {"petition": petition, "expansion": expansion})
            petition["status"] = "denied"
            petition["decision_note"] = body.get("decision_note", "")
            petition["decided_tick"] = 30
            return json_response(200, petition)

        if parts == ["amendments"] and method == "GET":
            pending = [a for a in self.amendments.values() if a["status"] == "passed_awaiting_president"]
            return json_response(200, pending)
        if len(parts) == 3 and parts[0] == "amendments" and parts[2] in ("sign", "veto"):
            amendment = self.amendments.get(parts[1])
            if amendment is None:
                return json_response(404, {"detail": f"unknown amendment {parts[1]}"})
            if amendment["status"] != "passed_awaiting_president":
                return json_response(409, {"detail": "amendment not awaiting signature"})
            if parts[2] == "sign":
                amendment["status"] = "enacted"
                amendment["enacted_tick"] = 31
                amendment["resulting_charter_version"] = 2
            else:
                amendment["status"] = "vetoed"
                amendment["vetoed_tick"] = 31
                amendment["veto_note"] = body.get("veto_note", "")
            return json_response(200, amendment)

        if parts == ["broadcast"] and method == "POST":
            record = {"broadcast_id": self.new_id("broadcast"), "message": body["message"], "issued_tick": 40}
            self.broadcasts.append(record)
            return json_response(200, record)
        if parts == ["broadcasts"] and method == "GET":
            return json_response(200, self.broadcasts)

        if parts == ["honors"] and method == "POST":
            record = {
                "honor_id": self.new_id("honor"),
                "agent_id": body["agent_id"],
                "title": body["title"],
                "citation": body.get("citation", ""),
                "granted_tick": 41,
            }
            self.honors.append(record)
            return json_response(200, record)
        if parts == ["honors"] and method == "GET":
            return json_response(200, self.honors)

        return json_response(404, {"detail": f"no such president route: {parts}"})

    def handle_god(self, method: str, parts: list[str], body: dict[str, Any]) -> httpx.Response:
        if parts == ["prayers"] and method == "GET":
            return json_response(200, list(self.prayers.values()))
        if len(parts) == 3 and parts[0] == "prayers" and parts[2] in ("grant", "silent-deny"):
            prayer = self.prayers.pop(parts[1], None)
            if prayer is None:
                return json_response(404, {"detail": f"unknown prayer {parts[1]}"})
            if parts[2] == "grant":
                return json_response(
                    200,
                    {
                        "prayer_id": prayer["prayer_id"],
                        "decision": "granted",
                        "visible_to_agent": True,
                        "decided_tick": 50,
                    },
                )
            return json_response(
                200,
                {
                    "prayer_id": prayer["prayer_id"],
                    "decision": "silently_denied",
                    "visible_to_agent": False,
                    "decided_tick": 50,
                },
            )

        if parts == ["blessings"] and method == "GET":
            return json_response(200, list(self.blessings.values()))
        if len(parts) == 3 and parts[0] == "blessings" and parts[2] in ("grant", "deny"):
            blessing = self.blessings.get(parts[1])
            if blessing is None:
                return json_response(404, {"detail": f"unknown blessing request {parts[1]}"})
            if blessing["status"] != "pending":
                return json_response(409, {"detail": "blessing request already decided"})
            if parts[2] == "grant":
                blessing["status"] = "granted"
                blessing["newborn_agent_id"] = "agent-newborn-1"
            else:
                blessing["status"] = "denied"
                blessing["silent_deny"] = body.get("silent", False)
            return json_response(200, {"request": blessing})

        if parts == ["disasters"] and method == "POST":
            record = {
                "disaster_id": self.new_id("disaster"),
                "disaster_type": body["disaster_type"],
                "scope": body["scope"],
                "magnitude": body.get("magnitude", 1.0),
                "note": body.get("note", ""),
                "triggered_tick": 60,
            }
            self.disasters.append(record)
            return json_response(200, record)
        if parts == ["disasters"] and method == "GET":
            return json_response(200, self.disasters)

        if parts == ["miracles"] and method == "POST":
            record = {
                "miracle_id": self.new_id("miracle"),
                "agent_id": body["agent_id"],
                "description": body["description"],
                "cast_tick": 61,
            }
            self.miracles.append(record)
            return json_response(200, record)
        if parts == ["miracles"] and method == "GET":
            return json_response(200, self.miracles)

        if parts == ["misfortunes"] and method == "POST":
            record = {
                "misfortune_id": self.new_id("misfortune"),
                "agent_id": body["agent_id"],
                "description": body["description"],
                "cast_tick": 62,
            }
            self.misfortunes.append(record)
            return json_response(200, record)
        if parts == ["misfortunes"] and method == "GET":
            return json_response(200, self.misfortunes)

        if parts == ["population-dials"] and method == "GET":
            return json_response(200, self.dials)
        if parts == ["population-dials"] and method == "PUT":
            for key in (
                "target_band_min",
                "target_band_max",
                "death_hazard_multiplier",
                "blessing_throttle",
            ):
                if body.get(key) is not None:
                    self.dials[key] = body[key]
            return json_response(200, self.dials)

        return json_response(404, {"detail": f"no such god route: {parts}"})

    def handle_spend(self, method: str, parts: list[str]) -> httpx.Response:
        if parts == ["status"] and method == "GET":
            return json_response(200, self.spend)
        return json_response(404, {"detail": f"no such spend route: {parts}"})

    def handle_alerts(self, method: str, params: dict[str, Any]) -> httpx.Response:
        if method != "GET":
            return json_response(404, {"detail": "no such alerts route"})
        limit = int(params.get("limit", 50))
        return json_response(200, self.alerts[:limit])

    def handle_control(self, method: str, parts: list[str], body: dict[str, Any]) -> httpx.Response:
        if parts == ["status"] and method == "GET":
            return json_response(200, self.control)
        if parts == ["pause"] and method == "POST":
            self.control["paused"] = True
            return json_response(200, self.control)
        if parts == ["resume"] and method == "POST":
            self.control["paused"] = False
            return json_response(200, self.control)
        if parts == ["tick-speed"] and method == "PUT":
            self.control["pacing_mode"] = body["pacing_mode"]
            self.control["continuous_interval_s"] = body.get("continuous_interval_s", 0.0)
            return json_response(200, self.control)
        if parts == ["checkpoint-now"] and method == "POST":
            self.control["game_day"] += 1
            return json_response(
                200,
                {
                    "tick_index": self.control["tick_index"],
                    "game_day": self.control["game_day"],
                    "written_at": "2026-07-31T00:00:00+00:00",
                },
            )
        return json_response(404, {"detail": f"no such control route: {parts}"})


def build_transport(fake: FakeWorldAdmin) -> httpx.MockTransport:
    return httpx.MockTransport(fake.handle)
