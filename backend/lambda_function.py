import json, urllib.request, urllib.parse, statistics, unicodedata
from datetime import date, timedelta
import boto3

table = boto3.resource("dynamodb", region_name="ap-south-1").Table("air-cache")
VERSION = "v3"      # new version so old cached results are ignored
OFFSET = 0.5        # degrees, about 55 km, for the surrounding points


def norm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def daily_agg(times, values, fn):
    days = {}
    for t, v in zip(times, values):
        if v is not None:
            days.setdefault(t[:10], []).append(v)
    return {d: fn(v) for d, v in days.items()}


def zscore(value, values):
    sd = statistics.pstdev(values) or 1
    return (value - statistics.mean(values)) / sd


def pct(new, old):
    if not old:
        return "n/a"
    return f"{(new - old) / old * 100:+.0f}%"


def base_of(series, d):
    vals = [v for k, v in series.items() if k != d]
    return statistics.mean(vals) if vals else None


def analyse(city, d):
    day = date.fromisoformat(d)
    start = (day - timedelta(days=28)).isoformat()
    span = f"&start_date={start}&end_date={d}&timezone=auto"

    res = fetch("https://geocoding-api.open-meteo.com/v1/search?"
                + urllib.parse.urlencode({"name": city, "count": 10})).get("results") or []
    matches = [g for g in res if norm(g["name"]) == norm(city)]
    if not matches:
        raise LookupError("city not found")
    geo = max(matches, key=lambda g: g.get("population", 0))
    place = ", ".join(p for p in [geo["name"], geo.get("admin1"), geo.get("country")] if p)
    lat, lon = geo["latitude"], geo["longitude"]

    # centre + north, south, east, west points
    lats = [lat, lat + OFFSET, lat - OFFSET, lat, lat]
    lons = [lon, lon, lon, lon + OFFSET, lon - OFFSET]
    aq = fetch("https://air-quality-api.open-meteo.com/v1/air-quality?"
               f"latitude={','.join(map(str, lats))}&longitude={','.join(map(str, lons))}"
               f"&hourly=pm2_5{span}")
    if isinstance(aq, dict):
        aq = [aq]

    names = ["Centre", "North", "South", "East", "West"]
    daily = []
    for loc in aq:
        h = loc["hourly"]
        daily.append(daily_agg(h["time"], h["pm2_5"], statistics.mean))

    pm = daily[0]
    if d not in pm:
        raise ValueError("no data for date")
    base = [v for k, v in pm.items() if k != d]
    if len(base) < 7:
        raise ValueError("not enough baseline data")
    mean = statistics.mean(base)
    z = zscore(pm[d], base)
    evidence = []

    # 1. comparisons with earlier periods
    prev = (day - timedelta(days=1)).isoformat()
    wk = (day - timedelta(days=7)).isoformat()
    if prev in pm:
        evidence.append(f"Compared with the previous day ({prev}), PM2.5 changed by {pct(pm[d], pm[prev])}.")
    if wk in pm:
        evidence.append(f"Compared with the same weekday last week ({wk}), PM2.5 changed by {pct(pm[d], pm[wk])}.")

    # 2. weather evidence
    try:
        wx = fetch("https://archive-api.open-meteo.com/v1/archive?"
                   f"latitude={lat}&longitude={lon}"
                   f"&hourly=wind_speed_10m,relative_humidity_2m,precipitation{span}")
        h = wx["hourly"]
        wind = daily_agg(h["time"], h["wind_speed_10m"], statistics.mean)
        hum = daily_agg(h["time"], h["relative_humidity_2m"], statistics.mean)
        rain = daily_agg(h["time"], h["precipitation"], sum)

        bw, bh, br = base_of(wind, d), base_of(hum, d), base_of(rain, d)
        if d in wind and bw and wind[d] < 0.7 * bw:
            evidence.append(f"Wind was weak: {wind[d]:.1f} km/h vs {bw:.1f} km/h on average, "
                            "so pollutants were not dispersing well.")
        if d in hum and bh and hum[d] > 1.1 * bh:
            evidence.append(f"Humidity was higher: {hum[d]:.0f}% vs {bh:.0f}% on average, "
                            "conditions that can trap fine particles near the ground.")
        if d in rain and br is not None and rain[d] == 0 and br > 1:
            evidence.append(f"No rain on {d}, while earlier days averaged {br:.1f} mm/day, "
                            "so rain was not washing particles out of the air.")
    except Exception as e:
        print("weather unavailable:", e)
        evidence.append("Weather data is not yet available for this date, so only pollution comparisons are shown.")

    # 3. time-of-day pattern
    center = aq[0]["hourly"]
    today = [(t, v) for t, v in zip(center["time"], center["pm2_5"])
             if t.startswith(d) and v is not None]
    if today:
        t, v = max(today, key=lambda x: x[1])
        hr = int(t[11:13])
        if 6 <= hr <= 10 or 17 <= hr <= 22:
            evidence.append(f"PM2.5 peaked at {hr:02d}:00 local time ({v:.0f} µg/m³), within typical commute hours. "
                            "This is an indicator of traffic-related emissions, not proof.")
        else:
            evidence.append(f"PM2.5 peaked at {hr:02d}:00 local time ({v:.0f} µg/m³).")

    # 4. where: city vs surrounding points
    areas, ring_z = {}, []
    for name, s in zip(names, daily):
        if d in s:
            areas[name] = round(s[d], 1)
    for s in daily[1:]:
        b = [v for k, v in s.items() if k != d]
        if d in s and len(b) >= 7:
            ring_z.append(zscore(s[d], b))
    if ring_z and z > 1.5:
        rz = statistics.mean(ring_z)
        if rz > 1.5:
            evidence.append("The surrounding area (about 55 km around the city) also rose unusually, "
                            "which points to a regional episode rather than only local sources.")
        else:
            evidence.append("The surrounding area did not rise as much as the city, "
                            "which points to mostly local sources.")

    return {"city": geo["name"], "place": place, "date": d, "pm25": round(pm[d], 1),
            "baseline": round(mean, 1), "zscore": round(z, 2),
            "anomaly": (z > 2 or pm[d] > 1.5 * mean) and (pm[d] - mean) >= 10,
            "evidence": evidence, "areas": areas,
            "series": {k: round(v, 1) for k, v in sorted(pm.items())}}


def lambda_handler(event, context):
    q = event.get("queryStringParameters") or {}
    city = q.get("city", "Chennai").strip()
    d = q.get("date")
    if not d:
        return {"statusCode": 400, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "date is required"})}

    key = f"{VERSION}#{city.lower()}#{d}"
    try:
        item = table.get_item(Key={"cache_key": key}).get("Item")
        if item:
            return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
                    "body": item["body"]}
    except Exception as e:
        print("cache read failed:", e)

    try:
        result = analyse(city, d)
    except Exception as e:
        print("analysis failed:", repr(e))
        return {"statusCode": 404, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "No data for that city/date"})}

    body = json.dumps(result)
    try:
        table.put_item(Item={"cache_key": key, "body": body})
    except Exception as e:
        print("cache write failed:", e)
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": body}