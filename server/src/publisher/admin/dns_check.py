import httpx


def lookup(name, kind, client=None, resolver="https://dns.google/resolve"):
    http = client or httpx.Client(timeout=15)
    try:
        response = http.get(
            resolver,
            params={"name": name, "type": kind},
            headers={"Accept": "application/dns-json"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("Status") not in (0, 3):
            raise ValueError(f"DNS resolver returned status {data.get('Status')}")
        code = {"A": 1, "NS": 2, "CNAME": 5, "TXT": 16, "AAAA": 28}[kind]
        return sorted(
            a["data"].rstrip(".")
            for a in data.get("Answer", [])
            if a["type"] == code
            and a.get("name", "").rstrip(".").lower() == name.rstrip(".").lower()
        )
    finally:
        if client is None:
            http.close()


def diff(records, query=lookup):
    results = []
    expected = {}
    for record in records:
        expected.setdefault((record["name"].rstrip("."), record["type"]), set()).add(
            record["value"].rstrip(".")
        )
    for (name, kind), values in expected.items():
        try:
            actual = set(query(name, kind))
            conflicts = {}
            if kind in {"A", "AAAA", "CNAME"}:
                for other in {"A", "AAAA", "CNAME"} - {kind}:
                    if (
                        other == "CNAME"
                        or kind == "CNAME"
                        or (name, other) not in expected
                    ):
                        found = query(name, other)
                        if found:
                            conflicts[other] = found
            results.append(
                {
                    "name": name,
                    "type": kind,
                    "expected": sorted(values),
                    "actual": sorted(actual),
                    "missing": sorted(values - actual),
                    "extra": sorted(actual - values),
                    "conflicts": conflicts,
                    "ok": actual == values and not conflicts,
                }
            )
        except (httpx.HTTPError, ValueError) as exc:
            results.append(
                {"name": name, "type": kind, "ok": False, "error": type(exc).__name__}
            )
    return {"ok": bool(results) and all(r["ok"] for r in results), "records": results}
