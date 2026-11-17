"""Cinematicket tools: cities, films, cinemas, showtimes, theatre and concert events, box office, search."""

from __future__ import annotations

import html
import re
import time
from typing import Annotated, Any

from pydantic import Field

from .http import API, ApiError, fetch
from .registry import tool

HOME = f"{API}/cinematicket/home"
_city_cinemas: dict[int, tuple[float, dict[int, dict]]] = {}
CITY_TTL = 600  # seconds
MAX_PAGES = 30  # cinema list pages per city (32 rows each), a safety cap


def _toman(rial: int | None) -> int | None:
    return None if rial is None else rial // 10


def _text(s: str | None, limit: int = 600) -> str | None:
    """Plain text from the site's HTML description, cut to `limit` characters."""
    if not s:
        return None
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return s[:limit] + ("..." if len(s) > limit else "")


def _names(people: list[dict] | None, n: int = 10) -> list[str]:
    return [p["full_name"] for p in (people or [])[:n]]


def _film_row(m: dict) -> dict:
    return {
        "id": m["id"],
        "name": m["name"],
        "type": m.get("type"),
        "rating": round(m["movie_rate"], 2) if m.get("movie_rate") else None,
        "rates_count": m.get("rates_count", m.get("rate_count")),
        "directors": _names(m.get("directors"), 3),
        "genres": [g["name"] for g in m.get("genres") or []],
        "age": m.get("appropriate_age"),
    }


def _session(s: dict) -> dict:
    """One showtime. remaining_capacity is null/0 on the public API (seats are only known in the seat plan)."""
    return {
        "time": s["start_at"][11:16],
        "price_min": _toman(s.get("min_price")),
        "price_max": _toman(s.get("max_price")),
        "can_order": s.get("can_order"),
        "sale_starts": s.get("sale_start_at"),
        "note": s.get("starred_text") or s.get("badge") or None,
        "session_id": s["id"],
    }


def _halls(halls: dict, day: str) -> list[dict]:
    out = []
    for h in halls.values():
        ss = [_session(s) for s in h["sessions"] if s["start_at"][:10] == day]
        if ss:
            out.append({"hall": h["name"], "sessions": sorted(ss, key=lambda s: s["time"])})
    return out


async def _city_cinema_map(city_id: int) -> dict[int, dict]:
    """Cinemas of a city by id. The film endpoint ignores its city argument, so cities are filtered with this."""
    hit = _city_cinemas.get(city_id)
    if hit and time.monotonic() - hit[0] < CITY_TTL:
        return hit[1]
    rows: dict[int, dict] = {}
    url, params = f"{HOME}/cinemas/", {"city": city_id, "page_size": 100}
    for _ in range(MAX_PAGES):
        d = await fetch(url, params)
        rows.update({c["id"]: c for c in d["results"]})
        if not d.get("next"):
            break
        url, params = d["next"], None  # the cursor URL already carries city and page size
    _city_cinemas[city_id] = (time.monotonic(), rows)
    return rows


def _cinema_row(c: dict) -> dict:
    loc = re.search(r"POINT \(([\d.]+) ([\d.]+)\)", c.get("location") or "")
    return {
        "id": c["id"],
        "name": c["name_fa"],
        "address": c.get("address"),
        "rating": round(c["rate"], 2) if c.get("rate") else None,
        "rates_count": c.get("rate_count"),
        "facilities": c.get("facility"),
        "lat": float(loc[2]) if loc else None,
        "lng": float(loc[1]) if loc else None,
    }


@tool("Cities")
async def ct_cities() -> dict[str, Any]:
    """List the cities Cinematicket sells tickets in, with the city ids other tools take.

    Tehran is 235, Mashhad 204, Isfahan 266, Shiraz 63.
    """
    d = await fetch(f"{API}/geo/cities")
    return {
        "cities": [{"id": c["id"], "name": c["name_fa"], "province": c["province"]["name_fa"]} for c in d["results"]]
    }


@tool("Now showing")
async def ct_now_showing(
    category_id: Annotated[
        int | None,
        Field(
            description="Only this category id (155 Iranian film, 156 foreign film, 147 theatre, 150 concert...). Omit for all."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max films per category", ge=1, le=25)] = 10,
) -> dict[str, Any]:
    """Films and shows on sale now, grouped by category, nationwide (the site's home feed).

    The feed is not filtered by city; use ct_showtimes with a city id to see what plays where.
    """
    d = await fetch(f"{HOME}/movies/", {"page_size": 25, "city": "all"})
    cats = [
        {"category_id": c["id"], "category": c["name"], "films": [_film_row(m) for m in c["movies"][:limit]]}
        for c in d
        if c["movies"] and (category_id is None or c["id"] == category_id)
    ]
    return {"categories": cats}


@tool("Film detail")
async def ct_film(
    film_id: Annotated[int, Field(description="Film or show id from ct_now_showing, ct_search or ct_events")],
) -> dict[str, Any]:
    """Cast, director, genre, rating, duration, summary and sales of one film or show, plus how widely it is screened.

    Sales (Toman) come from the box-office table and only exist for released films.
    """
    d = await fetch(f"{HOME}/movies/{film_id}/", {"city": "all"})
    m = d["movie"]
    ss = [s for day in d["cinemas"] for c in day["cinemas"] for h in c["halls"].values() for s in h["sessions"]]
    prices = [s["min_price"] for s in ss if s.get("min_price")]
    out = {
        **_film_row(m),
        "english_name": m.get("en_name"),
        "year_jalali": m.get("year"),
        "duration_min": m.get("duration"),
        "categories": [c["name"] for c in m.get("categories") or []],
        "writers": _names(m.get("writers"), 3),
        "cast": _names(m.get("actors")),
        "summary": _text(m.get("description")),
        "screening": {
            "days": [day["date"] for day in d["cinemas"]],
            "cinemas": len({c["cinema"]["id"] for day in d["cinemas"] for c in day["cinemas"]}),
            "sessions": len(ss),
            "price_from": _toman(min(prices)) if prices else None,
            "price_to": _toman(max(s["max_price"] for s in ss)) if prices else None,
        },
        "url": f"https://cinematicket.org/movie/detail/{film_id}",
    }
    if m.get("year"):
        try:
            box = await fetch(f"{API}/movie/new-boxoffice/", {"release_year": m["year"], "page_size": 100})
            row = next((r for r in box["results"] if r["id"] == film_id), None)
            if row:
                out["sales"] = {
                    "total_toman": _toman(row["total_sale"]),
                    "cinemas": row["cinemas_count"],
                    "halls": row["halls_count"],
                    "weeks_on_screen": row["number_of_show_weeks"],
                }
        except ApiError:
            pass
    return out


@tool("Cinemas by city")
async def ct_cinemas(
    city_id: Annotated[int, Field(description="City id from ct_cities")],
    limit: Annotated[int, Field(description="Max cinemas", ge=1, le=100)] = 30,
    query: Annotated[str | None, Field(description="Only names or addresses containing this text")] = None,
) -> dict[str, Any]:
    """Cinemas and venues in a city, most popular first, with rating, address, facilities and coordinates."""
    rows = [_cinema_row(c) for c in (await _city_cinema_map(city_id)).values()]
    if query:
        rows = [r for r in rows if query in r["name"] or query in (r["address"] or "")]
    return {"total": len(rows), "cinemas": rows[:limit]}


@tool("Cinema detail")
async def ct_cinema(
    cinema_id: Annotated[int, Field(description="Cinema id from ct_cinemas or ct_showtimes")],
    date: Annotated[
        str | None, Field(description="Day as YYYY-MM-DD (Gregorian). Default: first day with sessions")
    ] = None,
) -> dict[str, Any]:
    """One cinema's info (address, phone, halls, rules) and its full program for a day: films, halls, times, prices (Toman)."""
    d = await fetch(f"{HOME}/cinemas/{cinema_id}/")
    c, days = d["cinema"], d["sessions"]
    day = date or next((k for k, v in days.items() if any(v.values())), None)
    films: dict[int, dict] = {}
    for hour in (days.get(day) or {}).values():
        for fid, e in hour.items():
            f = films.setdefault(int(fid), {**_film_row(e["movie"]), "halls": {}})
            for hid, h in e["halls"].items():
                f["halls"].setdefault(hid, {"hall": h["name"], "sessions": []})["sessions"] += [
                    _session(s) for s in h["sessions"] if s["start_at"][:10] == day
                ]
    program = []
    for f in films.values():
        halls = [{**h, "sessions": sorted(h["sessions"], key=lambda s: s["time"])} for h in f.pop("halls").values()]
        program.append({**f, "halls": halls})
    return {
        **_cinema_row({**c, "location": None}),
        "lat": c["location"]["lat"] if c.get("location") else None,
        "lng": c["location"]["long"] if c.get("location") else None,
        "city_id": c.get("city"),
        "phones": c.get("telephone"),
        "description": _text(c.get("description"), 400),
        "rules": [_text(r, 200) for r in c.get("rules") or []][:5],
        "dates": list(days),
        "date": day,
        "program": program,
    }


@tool("Showtimes")
async def ct_showtimes(
    film_id: Annotated[int, Field(description="Film or show id")],
    city_id: Annotated[int | None, Field(description="City id from ct_cities. Omit for every city (large)")] = None,
    cinema_id: Annotated[int | None, Field(description="Only this cinema")] = None,
    date: Annotated[
        str | None, Field(description="Day as YYYY-MM-DD (Gregorian). Default: first day with sessions")
    ] = None,
    limit: Annotated[int, Field(description="Max cinemas", ge=1, le=60)] = 15,
) -> dict[str, Any]:
    """Where and when a film or show plays: cinemas, halls, times and ticket prices (Toman) for one day.

    Read-only. Seats left are not public (the site only shows them in the seat map, which this server never opens).
    """
    d = await fetch(f"{HOME}/movies/{film_id}/", {"city": "all"})
    days = [x["date"] for x in d["cinemas"]]
    day = date or (days[0] if days else None)
    allowed = set(await _city_cinema_map(city_id)) if city_id else None
    cinemas = []
    for x in d["cinemas"]:
        if x["date"] != day:
            continue
        for c in x["cinemas"]:
            cid = c["cinema"]["id"]
            if (allowed is not None and cid not in allowed) or (cinema_id and cid != cinema_id):
                continue
            halls = _halls(c["halls"], day)
            if halls:
                cinemas.append(
                    {
                        "cinema_id": cid,
                        "cinema": c["cinema"]["name_fa"],
                        "address": c["cinema"].get("address"),
                        "halls": halls,
                    }
                )
    return {
        "film": d["movie"]["name"],
        "available_dates": days,
        "date": day,
        "cinemas_found": len(cinemas),
        "cinemas": cinemas[:limit],
    }


@tool("Upcoming films")
async def ct_upcoming() -> dict[str, Any]:
    """Films announced as coming soon, with release dates (the site's next-programs page; it is often empty)."""
    d = await fetch(f"{HOME}/future-movie/")
    return {"films": [{**_film_row(m), "released_at": m.get("released_at")} for m in d.get("movies", [])]}


@tool("Theatre and concerts")
async def ct_events(
    kind: Annotated[
        str, Field(description="theater, concert or cinema", pattern="^(theater|concert|cinema)$")
    ] = "theater",
    query: Annotated[str | None, Field(description="Title text to search for")] = None,
    limit: Annotated[int, Field(description="Max results", ge=1, le=50)] = 20,
    page: Annotated[int, Field(description="Page number, from 1", ge=1)] = 1,
) -> dict[str, Any]:
    """List theatre plays, concerts or films with sessions on sale, with rating and director.

    Then use ct_film or ct_showtimes with the id.
    """
    params: dict[str, Any] = {"type": kind, "page_size": limit, "page": page}
    if query:
        params["search"] = query
    d = await fetch(f"{API}/movie/movies/", params)
    return {"total": d["count"], "page": page, "results": [_film_row(m) for m in d["results"][:limit]]}


@tool("Box office")
async def ct_box_office(
    year: Annotated[
        int | None, Field(description="Release year in the Jalali calendar, e.g. 1405. Omit for all-time")
    ] = None,
    limit: Annotated[int, Field(description="Max films", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Top-grossing films by total ticket sales (Toman), with audience figures, cinemas and weeks on screen."""
    params: dict[str, Any] = {"page_size": limit}
    if year:
        params["release_year"] = year
    d = await fetch(f"{API}/movie/new-boxoffice/", params)
    return {
        "total_films": d["count"],
        "films": [
            {
                "id": r["id"],
                "name": r["name"],
                "directors": _names(r.get("directors"), 2),
                "year": r.get("release_year"),
                "total_sale_toman": _toman(r["total_sale"]),
                "audience": r.get("total_audience") or None,
                "cinemas": r.get("cinemas_count"),
                "halls": r.get("halls_count"),
                "weeks": r.get("number_of_show_weeks"),
            }
            for r in d["results"][:limit]
        ],
    }


@tool("Search")
async def ct_search(
    query: Annotated[str, Field(description="Film, show or cinema name (Persian)", min_length=1)],
) -> dict[str, Any]:
    """Search films, shows and cinemas by name; returns ids for ct_film, ct_showtimes and ct_cinema."""
    d = await fetch(f"{HOME}/search/", {"search": query})
    return {
        "films": [_film_row(m) for rows in d.get("movies", {}).values() for m in rows][:15],
        "cinemas": [_cinema_row(c) for rows in d.get("cinemas", {}).values() for c in rows][:15],
    }
