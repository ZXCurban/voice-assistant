"""Clinic ranking by city/address query (pure, no DB)."""

from types import SimpleNamespace

from app.services import geo as geo_service


def _clinic(city: str | None, address: str | None) -> SimpleNamespace:
    return SimpleNamespace(city=city, address=address, latitude=52.0, longitude=21.0)


def test_empty_query_keeps_order() -> None:
    clinics = [_clinic("Warszawa", "Marszalkowska 1"), _clinic("Lisboa", "Av. Atlantica")]
    ranked, matched = geo_service.rank_clinics(clinics, None)
    assert ranked == clinics and matched is None
    ranked, matched = geo_service.rank_clinics(clinics, "   ")
    assert ranked == clinics and matched is None


def test_russian_city_alias_matches() -> None:
    warsaw = _clinic("Warszawa", "Marszalkowska 1, Warszawa")
    lisbon = _clinic("Lisboa", "Av. Atlantica 10, Lisboa")
    ranked, matched = geo_service.rank_clinics([lisbon, warsaw], "я из Варшавы, болит нога")
    assert matched == "warszawa"
    assert ranked[0] is warsaw


def test_portuguese_city_alias_matches() -> None:
    warsaw = _clinic("Warszawa", "Marszalkowska 1, Warszawa")
    lisbon = _clinic("Lisboa", "Av. Atlantica 10, Lisboa")
    ranked, matched = geo_service.rank_clinics([warsaw, lisbon], "estou em Lisboa")
    assert matched == "lisboa"
    assert ranked[0] is lisbon


def test_unknown_city_keeps_order_but_reports_none() -> None:
    clinics = [_clinic("Warszawa", "Marszalkowska 1"), _clinic("Lisboa", "Av. Atlantica")]
    ranked, matched = geo_service.rank_clinics(clinics, "я из Парижа")
    assert matched is None
    assert [c.city for c in ranked] == ["Warszawa", "Lisboa"]


def test_street_token_overlap_ranks_first() -> None:
    marsz = _clinic("Warszawa", "Marszalkowska 1, Warszawa")
    other = _clinic("Warszawa", "Aleje Jerozolimskie 5, Warszawa")
    ranked, _ = geo_service.rank_clinics([other, marsz], "Marszalkowska 1")
    assert ranked[0] is marsz
