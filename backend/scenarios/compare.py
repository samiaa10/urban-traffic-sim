"""Comparing and ranking scenarios, and saying why one did better.

Works on what run_scenario returns (or the same data loaded back from the
database): each item is {"name", "spec", "result"}, optionally with an
"id".

Metrics (METRICS below) each have a label, a unit and a direction. The
ranking orders scenarios by one metric, best first, breaking ties with
total travel time and then the name; scenarios without a value for the
metric (for example no emergencies, for an emergency metric) come last.

When two scenarios have the same trips (the same trip_signature: seed,
demand and surges), they are also compared trip by trip: for each trip
both runs completed, how much longer or shorter it took. That is the
strongest evidence of a disruption's effect, because everything except
the disruption is the same.
"""

LOWER, HIGHER = "lower", "higher"

METRICS = {
    "total_delay_vehicle_hours": {
        "label": "Network delay", "unit": "vehicle-hours", "better": LOWER,
        "definition": "Time lost to traffic, accidents and closures, summed over all trips",
    },
    "total_travel_vehicle_hours": {
        "label": "Total travel time", "unit": "vehicle-hours", "better": LOWER,
        "definition": "Time spent on the road by all trips",
    },
    "mean_increase_seconds": {
        "label": "Mean journey-time increase", "unit": "s", "better": LOWER,
        "definition": "Average extra time over each trip's free-flow fastest route (finished trips)",
    },
    "p95_increase_seconds": {
        "label": "95th percentile journey-time increase", "unit": "s", "better": LOWER,
        "definition": "19 trips in 20 took at most this much longer than in free flow",
    },
    "max_increase_seconds": {
        "label": "Worst journey-time increase", "unit": "s", "better": LOWER,
        "definition": "The trip that lost the most time compared with free flow",
    },
    "mean_delay_seconds": {
        "label": "Mean delay per trip", "unit": "s", "better": LOWER,
        "definition": "Average time lost to traffic, accidents and closures (finished trips)",
    },
    "completion_percent": {
        "label": "Trips completed", "unit": "%", "better": HIGHER,
        "definition": "Share of routable trips that arrived before the time limit",
    },
    "trips_unroutable": {
        "label": "Trips cut off", "unit": "trips", "better": LOWER,
        "definition": "Trips with no route at departure (closures cut them off)",
    },
    "emergency_mean_response_seconds": {
        "label": "Mean emergency response", "unit": "s", "better": LOWER,
        "definition": "Average time from an emergency to the ambulance arriving",
    },
    "emergency_worst_response_seconds": {
        "label": "Worst emergency response", "unit": "s", "better": LOWER,
        "definition": "The slowest ambulance arrival",
    },
    "emergency_within_target_percent": {
        "label": "Emergencies reached within target", "unit": "%", "better": HIGHER,
        "definition": "Share of all emergencies reached within the target time",
    },
}

DEFAULT_METRIC = "total_delay_vehicle_hours"


def metric_values(result):
    """Every metric of one result, flat (None where it does not apply)."""

    summary = result["summary"]
    emergencies = result.get("emergencies") or {}

    values = {key: summary.get(key) for key in METRICS if not key.startswith("emergency_")}
    values["emergency_mean_response_seconds"] = emergencies.get("mean_response_seconds")
    values["emergency_worst_response_seconds"] = emergencies.get("worst_response_seconds")
    values["emergency_within_target_percent"] = emergencies.get("within_target_percent")
    return values


def _sort_key(metric, values, name):
    value = values[metric]
    better = METRICS[metric]["better"]
    tie = values.get("total_travel_vehicle_hours")

    if value is None:
        return (1, 0, 0, name)

    return (
        0,
        value if better == LOWER else -value,
        tie if tie is not None else float("inf"),
        name,
    )


def _check_names(items):
    names = [item["name"] for item in items]
    if len(set(names)) != len(names):
        raise ValueError("scenarios being compared need different names")


def rank(items, metric=DEFAULT_METRIC):
    """Items ordered best first by `metric`, with each one's gap to the best."""

    if metric not in METRICS:
        raise ValueError("metric must be one of " + ", ".join(METRICS))

    _check_names(items)

    flat = [(item, metric_values(item["result"])) for item in items]
    flat.sort(key=lambda pair: _sort_key(metric, pair[1], pair[0]["name"]))

    best = flat[0][1][metric] if flat else None
    ranked = []

    for position, (item, values) in enumerate(flat, start=1):
        value = values[metric]
        gap = None if value is None or best is None else abs(value - best)

        ranked.append({
            "rank": position,
            "id": item.get("id"),
            "name": item["name"],
            "value": value,
            "gap_to_best": gap,
            "gap_percent": (
                100 * gap / abs(best) if gap is not None and best not in (0, None) else None
            ),
            "metrics": values,
        })

    return ranked


# ------------------------------------------------------------
# Trip by trip
# ------------------------------------------------------------

# A trip counts as slower or faster only if it changed by more than this
CHANGE_SECONDS = 5


def paired(baseline, other):
    """Trip-by-trip comparison of two results with the same trips.

    Returns None if they do not have the same trips.
    """

    if baseline["trip_signature"] != other["trip_signature"]:
        return None

    before = baseline["trips"]
    after = other["trips"]

    if before["origin"] != after["origin"] or before["destination"] != after["destination"]:
        return None

    differences = []
    newly_cut_off = 0
    newly_unfinished = 0

    for status_a, status_b, journey_a, journey_b in zip(
        before["status"], after["status"], before["journey_seconds"], after["journey_seconds"]
    ):
        if status_a != "unroutable" and status_b == "unroutable":
            newly_cut_off += 1
        if status_a == "finished" and status_b == "unfinished":
            newly_unfinished += 1
        if status_a == "finished" and status_b == "finished":
            differences.append(journey_b - journey_a)

    slower = [d for d in differences if d > CHANGE_SECONDS]
    faster = [d for d in differences if d < -CHANGE_SECONDS]

    return {
        "trips_compared": len(differences),
        "extra_vehicle_hours": sum(differences) / 3600,
        "mean_change_seconds": sum(differences) / len(differences) if differences else None,
        "worst_increase_seconds": max(differences) if differences else None,
        "best_decrease_seconds": min(differences) if differences else None,
        "trips_slower": len(slower),
        "trips_faster": len(faster),
        "trips_unchanged": len(differences) - len(slower) - len(faster),
        "mean_slowdown_seconds": sum(slower) / len(slower) if slower else None,
        "newly_cut_off": newly_cut_off,
        "newly_unfinished": newly_unfinished,
    }


def _road_key(road):
    return tuple(sorted((
        (road["start"]["latitude"], road["start"]["longitude"]),
        (road["end"]["latitude"], road["end"]["longitude"]),
    )))


def road_changes(baseline, other, limit=5):
    """Roads whose delay changed most between two results.

    Each result keeps only its 25 most delayed roads, so a road missing
    from one side counts as 0 there: the change is exact for roads in both
    lists and a lower bound otherwise ("at least").
    """

    before = {_road_key(r): r for r in baseline["roads"]}
    after = {_road_key(r): r for r in other["roads"]}

    changes = []

    for key in set(before) | set(after):
        a = before.get(key, {}).get("delay_vehicle_seconds", 0.0)
        b = after.get(key, {}).get("delay_vehicle_seconds", 0.0)
        road = after.get(key) or before.get(key)
        changes.append({
            "start_node": road["start_node"],
            "end_node": road["end_node"],
            "start": road["start"],
            "end": road["end"],
            "before_vehicle_seconds": a,
            "after_vehicle_seconds": b,
            "change_vehicle_seconds": b - a,
            "exact": key in before and key in after,
        })

    increases = sorted(
        (c for c in changes if c["change_vehicle_seconds"] > 0),
        key=lambda c: -c["change_vehicle_seconds"],
    )[:limit]
    decreases = sorted(
        (c for c in changes if c["change_vehicle_seconds"] < 0),
        key=lambda c: c["change_vehicle_seconds"],
    )[:limit]

    return {"increases": increases, "decreases": decreases}


# ------------------------------------------------------------
# Explanations
# ------------------------------------------------------------

def _fmt(value, unit):
    if value is None:
        return "n/a"
    if unit == "vehicle-hours":
        # Two decimals below 10: scenarios often differ by hundredths
        return f"{value:,.2f} vehicle-hours" if abs(value) < 10 else f"{value:,.1f} vehicle-hours"
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "s":
        return f"{value:,.0f} s"
    return f"{value:,.0f} {unit}"


def spec_differences(spec_a, spec_b):
    """What is set up differently between two scenarios, in words."""

    a, b = spec_a, spec_b
    notes = []

    def roads(items):
        return {frozenset((i["start"], i["destination"])) for i in items}

    for kind, label in (("closures", "road closure"), ("accidents", "accident")):
        only_b = roads(b.get(kind, [])) - roads(a.get(kind, []))
        only_a = roads(a.get(kind, [])) - roads(b.get(kind, []))
        if only_b:
            notes.append(f"{len(only_b)} {label}{'s' if len(only_b) != 1 else ''} that the other does not have")
        if only_a:
            notes.append(f"{len(only_a)} fewer {label}{'s' if len(only_a) != 1 else ''}")

    if len(b.get("surges", [])) != len(a.get("surges", [])):
        notes.append(f"{len(b.get('surges', []))} surge(s) instead of {len(a.get('surges', []))}")
    elif b.get("surges") != a.get("surges"):
        notes.append("different surges")

    if b.get("demand") != a.get("demand"):
        notes.append(
            f"{b['demand']['vehicles']} background trips instead of {a['demand']['vehicles']}"
            if b["demand"]["vehicles"] != a["demand"]["vehicles"] else "different departure times"
        )

    if b.get("rerouting", True) != a.get("rerouting", True):
        notes.append("dynamic rerouting " + ("on" if b.get("rerouting", True) else "off"))

    # Specs saved before traffic_model existed ran the simple model
    model_a = a.get("traffic_model") or "simple"
    model_b = b.get("traffic_model") or "simple"
    if model_b != model_a:
        notes.append(f"the {model_b} traffic model instead of {model_a}")

    stations_a, stations_b = a.get("stations", []), b.get("stations", [])
    if stations_a != stations_b:
        count_a = sum(s.get("ambulances", 1) for s in stations_a)
        count_b = sum(s.get("ambulances", 1) for s in stations_b)
        notes.append(
            f"{len(stations_b)} station(s) with {count_b} ambulance(s) "
            f"instead of {len(stations_a)} with {count_a}"
            if (len(stations_a), count_a) != (len(stations_b), count_b)
            else "stations in different places"
        )

    if b.get("seed") != a.get("seed"):
        notes.append("a different random seed (different trips)")

    # Disruptions beyond closures and accidents
    for kind, describe in (("floods", _flood_text), ("failures", _failure_text)):
        only_b = [x for x in b.get(kind) or [] if x not in (a.get(kind) or [])]
        only_a = [x for x in a.get(kind) or [] if x not in (b.get(kind) or [])]
        notes += [describe(x) for x in only_b]
        notes += [describe(x, absent=True) for x in only_a]

    if _signals_text(b.get("signals")) != _signals_text(a.get("signals")):
        notes.append(f"{_signals_text(b.get('signals'))} instead of {_signals_text(a.get('signals'))}")
    elif (b.get("signals") or None) != (a.get("signals") or None):
        notes.append("traffic signals set up differently")

    groups_a, groups_b = _groups(a.get("emergencies")), _groups(b.get("emergencies"))
    if groups_a != groups_b:
        count_a = sum(g.get("count", 0) for g in groups_a)
        count_b = sum(g.get("count", 0) for g in groups_b)
        notes.append(f"{count_b} emergencies instead of {count_a}" if count_a != count_b
                     else "emergencies set up differently")

    if b.get("max_seconds", 3600) != a.get("max_seconds", 3600):
        notes.append(f"a time limit of {b.get('max_seconds', 3600) / 60:.0f} min instead of "
                     f"{a.get('max_seconds', 3600) / 60:.0f} min")

    # Anything else that differs is named, never passed over: two scenarios
    # are only called the same when their settings are
    if not notes:
        other = sorted(k for k in set(_normalised(a)) | set(_normalised(b))
                       if _normalised(a).get(k) != _normalised(b).get(k))
        if other:
            notes.append("different " + ", ".join(k.replace("_", " ") for k in other) + " settings")

    return notes


def _minutes(seconds):
    return f"{seconds / 60:.0f}"


def _flood_text(flood, absent=False):
    area = flood.get("area")
    where = (f"every road within {area['radius_metres']:.0f} m of a point" if area else "")
    roads = flood.get("roads") or []
    if roads:
        where = (where + " and " if where else "") + f"{len(roads)} road{'s' if len(roads) != 1 else ''}"
    start = flood.get("start_at", 0)
    return (f"{'no' if absent else 'a'} flood ({flood.get('severity', 'closed')}) over {where}, from minute "
            f"{_minutes(start)} for {_minutes(flood.get('duration_seconds', 1800))} min")


def _failure_text(failure, absent=False):
    names = failure.get("names")
    what = ("the bridges on " + ", ".join(names)) if names else "every bridge" + (" in an area" if failure.get("area") else "")
    end = failure.get("end_at")
    when = f"from minute {_minutes(failure.get('start_at', 0))}" + (f" to minute {_minutes(end)}" if end else " to the end")
    return f"{what} in use throughout (the other closes them {when})" if absent else f"{what} out of use {when}"


def _signals_text(signals):
    if not signals or signals.get("controller", "fixed") == "none":
        return "no traffic signals"
    kind = "adaptive" if signals.get("controller") == "adaptive" else "fixed-time"
    return f"{kind} traffic signals" + (" with emergency priority" if signals.get("emergency_priority") else "")


def _groups(emergencies):
    if emergencies is None:
        return []
    groups = emergencies if isinstance(emergencies, list) else [emergencies]
    return [g for g in groups if g.get("count", 0)]


def _normalised(spec):
    """A spec with its defaults filled in, without what does not change how
    it runs (its name, and whether accessibility was also measured)."""
    from backend.scenarios.spec import ScenarioSpec

    try:
        full = ScenarioSpec.model_validate(spec).model_dump(mode="json")
    except Exception:  # noqa: BLE001 - a partial or older spec: its settings over the defaults
        full = {**ScenarioSpec().model_dump(mode="json"), **spec}
    full["traffic_model"] = full.get("traffic_model") or "simple"
    for key in ("name", "resilience"):
        full.pop(key, None)
    return full


def explain(items, metric=DEFAULT_METRIC, road_name=None):
    """Plain-English sentences on how the scenarios rank and why.

    road_name(start, end), if given, names roads (else junction ids)."""

    if len(items) < 2:
        return ["Run at least two scenarios to compare them."]

    ranked = rank(items, metric)
    definition = METRICS[metric]
    by_name = {item["name"]: item for item in items}
    first, second = ranked[0], ranked[1]
    unit = definition["unit"]

    if first["value"] is None:
        return [f"None of these scenarios has a value for {definition['label'].lower()}."]

    sentences = [
        f"{first['name']} ranks first on {definition['label'].lower()} "
        f"({_fmt(first['value'], unit)}); {second['name']} is second"
        + (
            f" ({_fmt(second['value'], unit)}, {_fmt(second['gap_to_best'], unit)} "
            f"{'more' if definition['better'] == LOWER else 'less'}"
            + (f", {second['gap_percent']:.0f}%" if second["gap_percent"] is not None else "")
            + ")."
            if second["value"] is not None else ", with no value for it."
        )
    ]

    best_item, runner_up = by_name[first["name"]], by_name[second["name"]]
    differences = spec_differences(best_item["spec"], runner_up["spec"])

    if differences:
        sentences.append(f"Compared with {first['name']}, {second['name']} has " + "; ".join(differences) + ".")
    else:
        sentences.append(f"{first['name']} and {second['name']} are set up the same way apart from their names.")

    trip_by_trip = paired(best_item["result"], runner_up["result"])

    if trip_by_trip and trip_by_trip["trips_compared"]:
        sentences.append(
            f"Trip by trip (the same {trip_by_trip['trips_compared']} completed trips in both): "
            f"{trip_by_trip['trips_slower']} took longer in {second['name']} and "
            f"{trip_by_trip['trips_faster']} took less time (by more than {CHANGE_SECONDS} s); "
            f"in total {_fmt(abs(trip_by_trip['extra_vehicle_hours']), 'vehicle-hours')} "
            f"{'more' if trip_by_trip['extra_vehicle_hours'] >= 0 else 'less'}"
            + (
                f", worst single trip +{trip_by_trip['worst_increase_seconds']:.0f} s."
                if trip_by_trip["worst_increase_seconds"] and trip_by_trip["worst_increase_seconds"] > 0
                else "."
            )
        )
        if trip_by_trip["newly_cut_off"]:
            sentences.append(
                f"{trip_by_trip['newly_cut_off']} trip(s) had no route at all in {second['name']}."
            )
    elif best_item["result"]["trip_signature"] != runner_up["result"]["trip_signature"]:
        sentences.append(
            "They have different trips (seed, demand or surges differ), so they are compared "
            "on totals only, not trip by trip."
        )

    if metric.startswith("emergency_"):
        a = best_item["result"].get("emergencies") or {}
        b = runner_up["result"].get("emergencies") or {}
        if a.get("requested"):
            sentences.append(
                f"{first['name']} reached {a.get('arrived', 0)} of {a['requested']} emergencies "
                f"against {b.get('arrived', 0)} for {second['name']}."
            )
    else:
        changes = road_changes(best_item["result"], runner_up["result"], limit=3)
        if changes["increases"]:
            def label(change):
                name = road_name(change["start_node"], change["end_node"]) if road_name else None
                return name or f"{change['start_node']}-{change['end_node']} (junction ids)"

            parts = [
                f"{label(c)} (+{c['change_vehicle_seconds']:,.0f} vehicle-seconds)"
                for c in changes["increases"]
            ]
            sentences.append(
                f"The roads that lost the most extra time in {second['name']} are "
                + ", ".join(parts) + "."
            )

    # Closing roads does not always make things worse
    closures_first = len(best_item["spec"].get("closures", []))
    closures_second = len(runner_up["spec"].get("closures", []))
    if closures_first > closures_second and metric in ("total_delay_vehicle_hours", "mean_delay_seconds"):
        sentences.append(
            f"{first['name']} closes more roads yet loses less time: closures can move traffic "
            "off congested roads onto parallel ones (a Braess-like effect). Check journey-time "
            "increase too, which counts the detours."
        )

    return sentences


def compare(items, metric=DEFAULT_METRIC, baseline_name=None, road_name=None):
    """Ranking, explanation, and (if a baseline is named) each scenario's
    difference from the baseline, trip by trip where possible."""

    ranking = rank(items, metric)
    by_name = {item["name"]: item for item in items}

    if baseline_name is not None:
        if baseline_name not in by_name:
            raise ValueError(f"no scenario named {baseline_name!r}")
        base = by_name[baseline_name]
        base_values = metric_values(base["result"])

        for row in ranking:
            item = by_name[row["name"]]
            row["vs_baseline"] = {
                key: (
                    row["metrics"][key] - base_values[key]
                    if row["metrics"][key] is not None and base_values[key] is not None else None
                )
                for key in METRICS
            }
            row["paired"] = (
                None if item is base else paired(base["result"], item["result"])
            )

    return {
        "metric": {"key": metric, **METRICS[metric]},
        "baseline": baseline_name,
        "ranking": ranking,
        "explanation": explain(items, metric, road_name),
    }
