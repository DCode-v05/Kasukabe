// The town's rulebook, bundled for the browser. GENERATED - DO NOT EDIT.
//   source:      world/rules/village_rules.json
//   regenerate:  python3 game/static/js/township/build_rules.py
//
// Edit the JSON, not this file. The JSON is what the world server loads and
// validates; this is a copy so the page also works opened straight off disk,
// where fetch() cannot read a file:// path. When the page is served,
// township-v2.js re-fetches /api/rules and prefers that, so a rule changed on
// a running server needs no regeneration.
const VILLAGE_RULES = {
  "_README": [
    "THE RULES OF KASUKABE. This file is the town's rulebook, kept as data so it",
    "can be read and changed without touching code. Edit it, restart the world",
    "server, and the town runs by the new rules.",
    "",
    "It is loaded by world/rules/loader.py, validated on load (a bad value fails",
    "loudly at boot rather than silently misbehaving at hour 300), and served",
    "read-only to the viewer at GET /api/rules.",
    "",
    "Every number here started life as a constant already in the code; the",
    "`_source` field on each section says where. Changing a number here changes",
    "the town. Changing a rule's SHAPE (adding a field) needs loader.py updated",
    "too, and its test will tell you so.",
    "",
    "Units: `sim` is the town's currency. `per_day` means one game-day (144",
    "ticks of 10 game-minutes). Ages are in years."
  ],
  "schema_version": "1.0.0",
  "economy": {
    "_about": "What money is, where it comes from, and what it costs to live.",
    "_source": "township-v2.js WAGE/UPKEEP_PER_DAY; world/ledger/head_tax.py",
    "upkeep_per_day_sim": 55,
    "head_tax_base_sim": 1000,
    "tax_rate_bps": 300,
    "genesis_treasury_usd": 500,
    "usd_wallet_cents_per_resident": 200,
    "wages_per_day_sim": {
      "_about": "What a day's work pays, by trade. A resident's income is this minus upkeep minus tax.",
      "doctor": 210,
      "construction_company_owner": 220,
      "grocery_store_owner": 200,
      "cafe_owner": 195,
      "pharmacist": 185,
      "banker": 175,
      "college_instructor": 170,
      "school_teacher": 160,
      "chef": 150,
      "office_administrator": 150,
      "government_clerk": 145,
      "trader": 140,
      "police_officer": 140,
      "artisan": 135,
      "firefighter": 135,
      "miner": 130,
      "nurse": 130,
      "farmer": 125,
      "innkeeper": 125,
      "baker": 120,
      "forester": 120,
      "construction_worker": 115,
      "librarian": 115,
      "shopkeeper": 110,
      "grocery_clerk": 100,
      "barista": 95,
      "waiter": 95,
      "day_laborer": 85,
      "street_sweeper": 80,
      "retired": 60,
      "college_student": 40,
      "student": 0,
      "child": 0
    },
    "pension": {
      "_about": "Paid from the age below. Government service earns a larger share.",
      "minimum_age_years": 70,
      "private_pct_of_wage": 25,
      "government_pct_of_wage": 50
    }
  },
  "market": {
    "_about": [
      "Supply and demand. Every good the town produces and consumes has a daily",
      "demand derived from population, and a supply derived from how many people",
      "actually worked at producing it that day. When supply falls short the",
      "price rises, the shortfall accumulates, and the town notices.",
      "",
      "This is the loop that makes the town react rather than merely run: a",
      "shortage raises the price, a higher price makes that trade pay better,",
      "better pay pulls workers into it (see `labour.dynamic_staffing`), and the",
      "shortage closes. If it cannot close, the consequences below apply."
    ],
    "price_response": {
      "_about": "How hard price moves when supply and demand diverge.",
      "elasticity": 0.6,
      "_elasticity_note": "price multiplier = (demand / supply) ^ elasticity, clamped below",
      "min_price_multiplier": 0.4,
      "max_price_multiplier": 4.0,
      "smoothing_days": 3,
      "_smoothing_note": "Prices move toward the target over this many days, so one bad harvest is not an instant famine price."
    },
    "goods": {
      "grain": {
        "produced_by": "farmer",
        "at": "farmland",
        "units_per_worker_day": 6,
        "demand_per_resident_day": 0.5,
        "base_price_sim": 12,
        "essential": true
      },
      "produce": {
        "produced_by": "farmer",
        "at": "orchard",
        "units_per_worker_day": 5,
        "demand_per_resident_day": 0.2,
        "base_price_sim": 14,
        "essential": true
      },
      "milk": {
        "produced_by": "farmer",
        "at": "pasture",
        "units_per_worker_day": 7,
        "demand_per_resident_day": 0.2,
        "base_price_sim": 10,
        "essential": true
      },
      "timber": {
        "produced_by": "forester",
        "at": "forest",
        "units_per_worker_day": 4,
        "demand_per_resident_day": 0.05,
        "base_price_sim": 22,
        "essential": false
      },
      "ore": {
        "produced_by": "miner",
        "at": "oreoutcrop",
        "units_per_worker_day": 3,
        "demand_per_resident_day": 0.04,
        "base_price_sim": 30,
        "essential": false
      },
      "bread": {
        "produced_by": "baker",
        "at": "bakery",
        "units_per_worker_day": 40,
        "demand_per_resident_day": 0.4,
        "base_price_sim": 6,
        "essential": true,
        "consumes": {
          "grain": 0.5
        }
      },
      "goods": {
        "produced_by": "artisan",
        "at": "tradeoffices",
        "units_per_worker_day": 3,
        "demand_per_resident_day": 0.08,
        "base_price_sim": 40,
        "essential": false,
        "consumes": {
          "timber": 0.4,
          "ore": 0.2
        }
      },
      "medicine": {
        "produced_by": "pharmacist",
        "at": "pharmacy",
        "units_per_worker_day": 12,
        "demand_per_resident_day": 0.03,
        "base_price_sim": 55,
        "essential": true
      }
    },
    "storage": {
      "_about": [
        "Food does not keep. Without this the town banked every surplus day",
        "forever and a shortage became impossible - which makes farming",
        "decorative. The larder holds a few days of what the town eats; the",
        "rest spoils. Build the granary and it holds far more, which is what",
        "makes the granary worth its 48,000 sim."
      ],
      "buffer_days": 4,
      "granary_buffer_days": 20,
      "spoilage_per_day": 0.12,
      "_spoilage_note": "fraction of the larder lost each day even inside the buffer"
    },
    "shortage": {
      "_about": "What happens when demand is not met. Checked once per game-day.",
      "deficit_ratio_watch": 0.9,
      "_watch_note": "supply/demand below this is a WATCH — surfaced to the government, no penalty yet",
      "deficit_ratio_alarm": 0.7,
      "_alarm_note": "below this is a SHORTAGE — consequences apply and the government is expected to act",
      "consecutive_days_to_escalate": 3,
      "consequences": {
        "essential_good_short": [
          {
            "effect": "illness_incidence_multiplier",
            "value": 1.35,
            "_note": "underfed and unwell people get sick more often"
          },
          {
            "effect": "unrest_points_per_day",
            "value": 2
          }
        ],
        "non_essential_good_short": [
          {
            "effect": "price_multiplier_floor",
            "value": 1.5,
            "_note": "scarcity is felt in the wallet, not the body"
          }
        ]
      },
      "surplus": {
        "_about": "The opposite case: more supply than demand, for several days.",
        "ratio_above": 1.4,
        "consequences": [
          {
            "effect": "price_multiplier_ceiling",
            "value": 0.7
          },
          {
            "effect": "labour_pull_away",
            "value": true,
            "_note": "workers drift to trades that pay better"
          }
        ]
      }
    }
  },
  "labour": {
    "_about": [
      "How many people do each job, and how that changes. The town does NOT fix",
      "its workforce at genesis and keep it forever: every day it compares the",
      "staff each service actually needs against the staff it has, and moves",
      "people. A town with 40 sick residents and 3 doctors trains more doctors."
    ],
    "dynamic_staffing": true,
    "review_every_days": 1,
    "max_moves_per_review": 4,
    "_max_moves_note": "Nobody wakes up in a different trade en masse; the workforce shifts a few people a day.",
    "retrain_days": 5,
    "_retrain_note": "How long a resident takes to become useful in the new trade. They earn the new wage only after this.",
    "staffing_targets": {
      "_about": "per_1000 is the baseline headcount per 1000 residents. `min` is a floor the town never drops below, whatever the population.",
      "doctor": {
        "per_1000": 10,
        "min": 2,
        "surge_source": "sick_residents",
        "per_surge_unit": 15
      },
      "nurse": {
        "per_1000": 24,
        "min": 4,
        "surge_source": "sick_residents",
        "per_surge_unit": 6
      },
      "pharmacist": {
        "per_1000": 4,
        "min": 1,
        "surge_source": "sick_residents",
        "per_surge_unit": 30
      },
      "police_officer": {
        "per_1000": 26,
        "min": 4,
        "surge_source": "unrest_points",
        "per_surge_unit": 25
      },
      "firefighter": {
        "per_1000": 20,
        "min": 3
      },
      "school_teacher": {
        "per_1000": 30,
        "min": 2,
        "surge_source": "minors",
        "per_surge_unit": 25
      },
      "farmer": {
        "per_1000": 62,
        "min": 8,
        "surge_source": "grain_deficit",
        "per_surge_unit": 40
      },
      "construction_worker": {
        "per_1000": 14,
        "min": 2,
        "surge_source": "approved_builds",
        "per_surge_unit": 1
      },
      "street_sweeper": {
        "per_1000": 10,
        "min": 1
      }
    },
    "recruit_from": {
      "_about": "Where new staff come from, in order of preference. The town looks for someone idle before it takes someone from another job.",
      "order": [
        "day_laborer",
        "college_student",
        "student_of_age",
        "surplus_trade",
        "any_working_age"
      ],
      "never_recruit": [
        "retired",
        "minor",
        "doctor",
        "nurse"
      ],
      "_never_note": "The first two cannot work; the last two are never poached FROM, because pulling a doctor to farm during an outbreak is the failure this whole section exists to prevent."
    }
  },
  "health": {
    "_about": "Illness, capacity, and what an outbreak does to the town.",
    "_source": "township-v2.js AILMENT_DAILY_INCIDENCE / WORK_CAPACITY_BY_AILMENT",
    "ailment_daily_incidence": {
      "short": 0.04,
      "medium": 0.01,
      "long": 0.0025,
      "terminal": 0.0008
    },
    "ailment_duration_days": {
      "short": 1,
      "medium": [
        3,
        7
      ],
      "long": 20
    },
    "work_capacity_by_ailment": {
      "short": 0.5,
      "medium": 0.3,
      "long": 0.15,
      "terminal": 0.0,
      "healthy": 1.0
    },
    "hospital_capacity": {
      "beds_per_ward": 12,
      "wards_at_genesis": 1,
      "patients_per_doctor": 15,
      "patients_per_nurse": 6
    },
    "emergency": {
      "_about": [
        "The rule the town is judged by: when many people fall ill at once, does",
        "it respond? Triggers are checked once per game-day. A triggered",
        "emergency raises staffing targets (labour.staffing_targets picks them up",
        "the same day), authorises building without the usual treasury wait, and",
        "is recorded as a government document."
      ],
      "outbreak": {
        "trigger": {
          "sick_fraction_of_population": 0.08,
          "or_sick_absolute": 20,
          "sustained_days": 2
        },
        "response": [
          {
            "action": "raise_staffing",
            "role": "doctor",
            "multiplier": 2.0
          },
          {
            "action": "raise_staffing",
            "role": "nurse",
            "multiplier": 2.0
          },
          {
            "action": "raise_staffing",
            "role": "pharmacist",
            "multiplier": 1.5
          },
          {
            "action": "authorise_build",
            "what": "hospital_ward",
            "count": 1,
            "bypass_treasury_wait": true
          },
          {
            "action": "open_document",
            "kind": "emergency_declaration"
          },
          {
            "action": "divert_treasury_pct",
            "value": 25,
            "_note": "a quarter of daily income goes to the response until it is stood down"
          }
        ],
        "stand_down": {
          "sick_fraction_below": 0.03,
          "sustained_days": 3,
          "_note": "Staff recruited during the emergency keep their trade; the town does not un-train doctors."
        }
      },
      "famine": {
        "trigger": {
          "essential_good_deficit_days": 5
        },
        "response": [
          {
            "action": "raise_staffing",
            "role": "farmer",
            "multiplier": 1.5
          },
          {
            "action": "release_granary",
            "if_built": "granary"
          },
          {
            "action": "open_document",
            "kind": "emergency_declaration"
          },
          {
            "action": "price_cap_multiplier",
            "value": 1.8,
            "_note": "the government caps what an essential may be sold for"
          }
        ],
        "stand_down": {
          "essential_supply_ratio_above": 1.0,
          "sustained_days": 3
        }
      },
      "fire": {
        "trigger": {
          "event": "structure_fire"
        },
        "response": [
          {
            "action": "dispatch",
            "role": "firefighter",
            "count": 3
          },
          {
            "action": "authorise_build",
            "what": "rebuild_destroyed",
            "count": 1,
            "bypass_treasury_wait": true
          }
        ],
        "stand_down": {
          "event": "fire_out"
        }
      },
      "unrest": {
        "trigger": {
          "unrest_points_above": 40,
          "sustained_days": 2
        },
        "response": [
          {
            "action": "raise_staffing",
            "role": "police_officer",
            "multiplier": 1.5
          },
          {
            "action": "open_document",
            "kind": "petition_review",
            "_note": "the government must answer standing petitions before this clears"
          }
        ],
        "stand_down": {
          "unrest_points_below": 15,
          "sustained_days": 3
        }
      }
    }
  },
  "government": {
    "_about": "Who governs, what they are paid, and what they must decide.",
    "_source": "world/charter/models.py (pm_seats=1, minister_seats=4); township-v2.js salaries",
    "seats": {
      "prime_minister": 1,
      "minister": 4
    },
    "salaries_per_day_sim": {
      "prime_minister": 260,
      "minister": 200,
      "government_clerk": 145,
      "police_officer": 140
    },
    "term_days": 90,
    "election": {
      "enabled": true,
      "_note": "Elections beyond the first are not yet auto-advanced — see HANDOVER §7."
    },
    "treasury": {
      "reserve_floor_sim": 2000,
      "_reserve_note": "The treasury never spends below this, except under a declared emergency.",
      "emergency_may_breach_floor": true
    },
    "documents": {
      "_about": [
        "Everything the government does leaves a document. Each has a cost to",
        "file, a decision-maker, and a deadline. A document left past its",
        "deadline counts as unanswered and adds unrest — a government that",
        "ignores its town is a government the town notices."
      ],
      "petition": {
        "_about": "Any resident may petition. Signatures come from other residents agreeing.",
        "filed_by": "any_resident",
        "file_cost_sim": 25,
        "signatures_to_consider": 15,
        "decided_by": "minister",
        "decision_deadline_days": 7,
        "outcomes": [
          "granted",
          "refused",
          "deferred"
        ],
        "unanswered_unrest_points_per_day": 3,
        "tool": "petition"
      },
      "building_permit": {
        "_about": "Nothing is built without approval. An emergency can bypass the wait, never the record.",
        "filed_by": "any_resident_or_business",
        "file_cost_sim": 120,
        "decided_by": "minister",
        "decision_deadline_days": 5,
        "requires": [
          "site_is_owned_or_public",
          "treasury_or_filer_can_fund"
        ],
        "outcomes": [
          "approved",
          "rejected",
          "approved_with_conditions"
        ],
        "emergency_bypass": {
          "allowed": true,
          "still_recorded": true
        },
        "tool": "build_structure"
      },
      "salary_warrant": {
        "_about": "The standing order that pays the seats and the clerks each day.",
        "filed_by": "prime_minister",
        "decided_by": "prime_minister",
        "recurs": "daily",
        "blocked_if_treasury_below": "treasury.reserve_floor_sim"
      },
      "emergency_declaration": {
        "_about": "Opened automatically by health.emergency triggers; closed on stand-down.",
        "filed_by": "system",
        "decided_by": "prime_minister",
        "decision_deadline_days": 1,
        "grants": [
          "bypass_treasury_wait",
          "divert_treasury_pct",
          "raise_staffing"
        ]
      },
      "complaint": {
        "filed_by": "any_resident",
        "file_cost_sim": 10,
        "decided_by": "police_officer",
        "decision_deadline_days": 3,
        "tool": "file_complaint"
      },
      "patent": {
        "_about": "An invented tool, filed for public use. See HANDOVER §6b.3.",
        "filed_by": "any_resident",
        "decided_by": "minister",
        "decision_deadline_days": 10,
        "outcomes": [
          "approved",
          "rejected",
          "revoked_later"
        ],
        "tool": "define_tool"
      }
    },
    "build_queue": {
      "_about": "What the town builds when it can afford to, cheapest first. An emergency inserts at the front.",
      "_source": "township-v2.js INITIATIVES",
      "order_by": "cost_ascending",
      "projects": [
        {
          "id": "guard",
          "name": "Town Guard",
          "cost_sim": 4000,
          "staffs": {
            "police_officer": 2
          }
        },
        {
          "id": "lamps",
          "name": "Street Lighting",
          "cost_sim": 6000
        },
        {
          "id": "wells",
          "name": "Public Wells",
          "cost_sim": 9000,
          "effect": {
            "illness_incidence_multiplier": 0.85
          }
        },
        {
          "id": "clinic",
          "name": "Second Ward",
          "cost_sim": 16000,
          "effect": {
            "hospital_beds_add": 12
          }
        },
        {
          "id": "roads",
          "name": "Paved Approaches",
          "cost_sim": 24000,
          "effect": {
            "output_multiplier": {
              "ore": 1.15,
              "timber": 1.15
            }
          }
        },
        {
          "id": "school2",
          "name": "Apprentice Hall",
          "cost_sim": 34000,
          "effect": {
            "retrain_days_multiplier": 0.6
          }
        },
        {
          "id": "granary",
          "name": "Town Granary",
          "cost_sim": 48000,
          "effect": {
            "essential_buffer_days": 20
          }
        }
      ]
    }
  },
  "survival": {
    "_about": [
      "How a resident decides what to do. These are not commands — an agent",
      "reasons from its own memory and may ignore all of it. They are the",
      "pressures the town applies, which is what makes some agents survive and",
      "others not.",
      "",
      "This section is what an agent is TOLD about its situation each day; the",
      "decision is still the model's."
    ],
    "daily_needs": {
      "food_units": 0.5,
      "_food_note": "Not eating for `hunger_days_to_illness` days makes a resident ill.",
      "hunger_days_to_illness": 3,
      "starvation_days_to_death": 12,
      "_starvation_note": "Hunger that never lets up kills. Without this a resident could go 21 days without food and merely be unwell, which makes 'survive' meaningless.",
      "shelter": "any_home_or_inn",
      "nights_without_shelter_to_illness": 2
    },
    "childcare": {
      "_about": [
        "Small children do not have a working day of their own. Every child in",
        "the town was given a day zone at genesis and the youngest were all",
        "given the park, so four-month-old babies spent the working day alone",
        "in the town park while their parents were at work. A child below the",
        "ages below goes where their guardian goes."
      ],
      "carried_until_age_years": 3,
      "_carried_note": "An infant is carried: exactly where the guardian is, never a step away.",
      "supervised_until_age_years": 6,
      "_supervised_note": "A small child stays beside a guardian rather than travelling alone.",
      "school_from_age_years": 6,
      "_school_note": "From here the school has them by day; a guardian has them the rest of the time.",
      "guardian_order": [
        "named_parent",
        "household_adult",
        "any_adult_relative"
      ],
      "_guardian_note": "First living match wins. A child with no living guardian stays at their own home - an orphan is not a reason to put a toddler on the street."
    },
    "wallet": {
      "destitute_below_sim": 200,
      "_destitute_note": "Below this a resident cannot pay upkeep and is flagged at-risk. The at-risk watchlist (family_api) is sorted by how many days of runway remain.",
      "days_destitute_to_dormant": 7
    },
    "pressures_told_to_the_agent": [
      "your balance and how many days of upkeep it covers",
      "whether you ate yesterday",
      "whether you have somewhere to sleep",
      "your health, and whether you can work today",
      "what your trade pays now, after the market moved the price",
      "which trades the town is short of, and what they pay",
      "any petition you signed and whether it was answered",
      "who in your household is not earning"
    ],
    "adaptation": {
      "_about": "What a resident may do about it. Each maps to a tool they already have.",
      "options": [
        {
          "if": "wallet_low",
          "may": "change trade to one the town is short of",
          "tool": "hire_employee / say"
        },
        {
          "if": "wallet_low",
          "may": "sell possessions",
          "tool": "sell"
        },
        {
          "if": "wallet_low",
          "may": "borrow from family",
          "tool": "transfer"
        },
        {
          "if": "food_short",
          "may": "harvest or buy food",
          "tool": "harvest / buy"
        },
        {
          "if": "no_shelter",
          "may": "move in with kin, or take a room at the inn",
          "tool": "move / buy"
        },
        {
          "if": "ill",
          "may": "go to the hospital",
          "tool": "move"
        },
        {
          "if": "town_short_of_role",
          "may": "retrain into it",
          "tool": "teach_skill"
        },
        {
          "if": "grievance",
          "may": "petition the government",
          "tool": "petition"
        },
        {
          "if": "grievance_ignored",
          "may": "organise with others",
          "tool": "register_org / say"
        },
        {
          "if": "no_tool_exists",
          "may": "invent one",
          "tool": "define_tool"
        }
      ]
    }
  },
  "unrest": {
    "_about": "One number the town carries. It rises when the town is failed and falls when it is served.",
    "starts_at": 0,
    "max": 100,
    "decay_per_day": 1.5,
    "rises_from": {
      "unanswered_petition_per_day": 3,
      "essential_shortage_per_day": 2,
      "destitute_resident_per_day": 0.1,
      "unpaid_salary_per_day": 5
    },
    "falls_from": {
      "petition_granted": 6,
      "emergency_stood_down": 10,
      "build_completed": 4
    }
  }
};
