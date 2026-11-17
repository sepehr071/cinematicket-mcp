import pytest
from conftest import fixture

from cinematicket_mcp import tickets
from cinematicket_mcp.registry import TOOLS

pytestmark = pytest.mark.anyio
P = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_city_cache():
    tickets._city_cinemas.clear()


def test_tool_names():
    assert {fn.__name__ for fn, _ in TOOLS} == {
        "ct_cities",
        "ct_now_showing",
        "ct_film",
        "ct_cinemas",
        "ct_cinema",
        "ct_showtimes",
        "ct_upcoming",
        "ct_events",
        "ct_box_office",
        "ct_search",
    }


async def test_cities(client, api):
    api[f"{P}/geo/cities"] = fixture("cities.json")
    r = (await client.call_tool("ct_cities", {})).structured_content
    assert r["cities"][0] == {"id": 235, "name": "تهران", "province": "تهران"}


async def test_now_showing(client, api):
    api[f"{P}/cinematicket/home/movies/"] = fixture("home_movies.json")
    r = (await client.call_tool("ct_now_showing", {"category_id": 155, "limit": 2})).structured_content
    assert len(r["categories"]) == 1 and len(r["categories"][0]["films"]) == 2
    assert r["categories"][0]["films"][0]["directors"]


async def test_film(client, api):
    api[f"{P}/cinematicket/home/movies/10492/"] = fixture("film_10492.json")
    api[f"{P}/movie/new-boxoffice/"] = fixture("boxoffice_1405.json")
    r = (await client.call_tool("ct_film", {"film_id": 10492})).structured_content
    assert r["name"] == "بورسیه" and r["duration_min"] == 100 and "جواد عزتی" in r["cast"]
    assert r["screening"]["cinemas"] == 2 and r["screening"]["price_from"] == 90000
    assert r["sales"]["total_toman"] > 0


async def test_cinemas_and_city_filter(client, api):
    api[f"{P}/cinematicket/home/cinemas/"] = fixture("cinemas_204.json")
    r = (await client.call_tool("ct_cinemas", {"city_id": 204, "limit": 3})).structured_content
    assert r["total"] >= 3 and len(r["cinemas"]) == 3 and r["cinemas"][0]["lat"]


async def test_showtimes_city_filter(client, api):
    api[f"{P}/cinematicket/home/movies/10492/"] = fixture("film_10492.json")
    api[f"{P}/cinematicket/home/cinemas/"] = fixture("cinemas_204.json")  # Mashhad only: 595 in, 301 out
    r = (await client.call_tool("ct_showtimes", {"film_id": 10492, "city_id": 204})).structured_content
    assert [c["cinema_id"] for c in r["cinemas"]] == [595]
    s = r["cinemas"][0]["halls"][0]["sessions"][0]
    assert s["time"] == "15:00" and s["price_min"] == 90000 and s["can_order"] is True
    r = (await client.call_tool("ct_showtimes", {"film_id": 10492, "date": "2026-11-17"})).structured_content
    assert r["date"] == "2026-11-17" and r["available_dates"][0] == "2026-11-16"


async def test_cinema_program(client, api):
    api[f"{P}/cinematicket/home/cinemas/595/"] = fixture("cinema_595.json")
    r = (await client.call_tool("ct_cinema", {"cinema_id": 595})).structured_content
    assert r["name"] == "پردیس سینما برفی" and r["city_id"] == 204 and r["date"] == "2026-11-16"
    times = [s["time"] for f in r["program"] for h in f["halls"] for s in h["sessions"]]
    assert times and all(len(t) == 5 for t in times)


async def test_events_box_office_search_upcoming(client, api):
    api[f"{P}/movie/movies/"] = fixture("events_theater.json")
    api[f"{P}/movie/new-boxoffice/"] = fixture("boxoffice_1405.json")
    api[f"{P}/cinematicket/home/search/"] = fixture("search.json")
    api[f"{P}/cinematicket/home/future-movie/"] = {"movies": []}
    r = (await client.call_tool("ct_events", {"kind": "theater", "limit": 3})).structured_content
    assert r["results"] and r["results"][0]["type"] == "theater"
    r = (await client.call_tool("ct_box_office", {"year": 1405, "limit": 2})).structured_content
    sales = [f["total_sale_toman"] for f in r["films"]]
    assert len(sales) == 2 and sales == sorted(sales, reverse=True)
    r = (await client.call_tool("ct_search", {"query": "بورسیه"})).structured_content
    assert r["films"][0]["id"] == 10492 and r["cinemas"] == []  # no cinema has this name
    r = (await client.call_tool("ct_upcoming", {})).structured_content
    assert r == {"films": []}
