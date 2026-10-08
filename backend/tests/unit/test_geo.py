"""Clinic ranking by city/address query (pure, no DB)."""

from types import SimpleNamespace

from app.services import geo as geo_service


def _clinic(city: str | None, address: str | None) -> SimpleNamespace:
    return SimpleNamespace(city=city, address=address, latitude=55.0, longitude=37.0)


def test_empty_query_keeps_order() -> None:
    clinics = [_clinic("Москва", "Тверской бульвар, 12"), _clinic("Казань", "ул. Баумана, 20")]
    ranked, matched = geo_service.rank_clinics(clinics, None)
    assert ranked == clinics and matched is None
    ranked, matched = geo_service.rank_clinics(clinics, "   ")
    assert ranked == clinics and matched is None


def test_russian_city_alias_matches() -> None:
    moscow = _clinic("Москва", "Тверской бульвар, 12, Москва")
    kazan = _clinic("Казань", "ул. Баумана, 20, Казань")
    ranked, matched = geo_service.rank_clinics([kazan, moscow], "я из Москвы, болит нога")
    assert matched == "moskva"
    assert ranked[0] is moscow


def test_spb_short_aliases_match() -> None:
    spb = _clinic("Санкт-Петербург", "Невский проспект, 45")
    moscow = _clinic("Москва", "Тверской бульвар, 12")
    for query in ("я в Питере", "лечусь в СПб", "клиника в Санкт-Петербурге"):
        ranked, matched = geo_service.rank_clinics([moscow, spb], query)
        assert matched == "sankt-peterburg"
        assert ranked[0] is spb


def test_kazan_and_novosibirsk_matches() -> None:
    kazan = _clinic("Казань", "ул. Баумана, 20")
    novosibirsk = _clinic("Новосибирск", "Красный проспект, 77")
    ranked, matched = geo_service.rank_clinics([novosibirsk, kazan], "еду из Казани")
    assert matched == "kazan"
    assert ranked[0] is kazan
    ranked, matched = geo_service.rank_clinics([kazan, novosibirsk], "живу в Новосибирске")
    assert matched == "novosibirsk"
    assert ranked[0] is novosibirsk


def test_unknown_city_keeps_order_but_reports_none() -> None:
    clinics = [_clinic("Москва", "Тверской бульвар, 12"), _clinic("Казань", "ул. Баумана, 20")]
    ranked, matched = geo_service.rank_clinics(clinics, "я из Парижа")
    assert matched is None
    assert [c.city for c in ranked] == ["Москва", "Казань"]


def test_city_mentioned_guards_stale_model_guesses() -> None:
    assert geo_service.city_mentioned("moskva", "запишите в Москве завтра")
    assert not geo_service.city_mentioned("moskva", "запишите в Казани завтра")
    assert not geo_service.city_mentioned("sankt-peterburg", "запишите в Москве завтра")
    assert geo_service.city_mentioned("sankt-peterburg", "лечусь в Питере")
    assert not geo_service.city_mentioned("paris", "запишите в Москве завтра")


def test_street_token_overlap_ranks_first() -> None:
    tverskaya = _clinic("Москва", "Тверской бульвар, 12, Москва")
    other = _clinic("Москва", "Ленинградский проспект, 5, Москва")
    ranked, _ = geo_service.rank_clinics([other, tverskaya], "Тверской бульвар, 12")
    assert ranked[0] is tverskaya
