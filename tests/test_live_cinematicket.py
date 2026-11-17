import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

TEHRAN, MASHHAD = 235, 204


async def test_live_cities(client):
    r = (await client.call_tool("ct_cities", {})).structured_content
    assert {"id": TEHRAN, "name": "تهران", "province": "تهران"} in r["cities"]


async def test_live_now_showing_and_film(client):
    r = (await client.call_tool("ct_now_showing", {"limit": 3})).structured_content
    film = r["categories"][0]["films"][0]
    d = (await client.call_tool("ct_film", {"film_id": film["id"]})).structured_content
    assert d["name"] == film["name"] and d["screening"]["sessions"] > 0


async def test_live_cinemas_and_program(client):
    r = (await client.call_tool("ct_cinemas", {"city_id": MASHHAD, "limit": 5})).structured_content
    assert r["total"] > 3 and r["cinemas"][0]["name"]
    c = (await client.call_tool("ct_cinema", {"cinema_id": r["cinemas"][0]["id"]})).structured_content
    assert c["city_id"] == MASHHAD and c["dates"]


async def test_live_showtimes_city_filter(client):
    films = (await client.call_tool("ct_now_showing", {"category_id": 155, "limit": 1})).structured_content
    fid = films["categories"][0]["films"][0]["id"]
    r = (await client.call_tool("ct_showtimes", {"film_id": fid, "city_id": MASHHAD, "limit": 3})).structured_content
    ids = {
        c["id"]
        for c in (await client.call_tool("ct_cinemas", {"city_id": MASHHAD, "limit": 100})).structured_content[
            "cinemas"
        ]
    }
    assert r["cinemas_found"] > 0 and all(c["cinema_id"] in ids for c in r["cinemas"])
    s = r["cinemas"][0]["halls"][0]["sessions"][0]
    assert s["price_min"] > 0 and len(s["time"]) == 5


async def test_live_events_box_office_search(client):
    r = (await client.call_tool("ct_events", {"kind": "theater", "limit": 3})).structured_content
    assert r["total"] > 0 and r["results"]
    r = (await client.call_tool("ct_box_office", {"year": 1405, "limit": 3})).structured_content
    assert r["films"][0]["total_sale_toman"] > 0
    r = (await client.call_tool("ct_search", {"query": "بورسیه"})).structured_content
    assert r["films"]
