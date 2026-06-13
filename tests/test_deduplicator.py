from models.lead import Lead
from pipeline.deduplicator import Deduplicator


def test_dedup_by_phone():
    leads = [
        Lead(name="Miami Strong Gym", phone="+13055046980", source="google_maps"),
        Lead(name="miamistronggym", phone="+13055046980", source="instagram"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 1
    assert removed == 1


def test_dedup_by_website_domain():
    leads = [
        Lead(name="Gym A", website="https://www.miamistronggym.com/", source="google_maps"),
        Lead(name="Gym A Official", website="http://miamistronggym.com/contact", source="facebook"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 1
    assert removed == 1


def test_dedup_by_email():
    leads = [
        Lead(name="Gym A", email="Info@Gym.com", source="google_maps"),
        Lead(name="Totally Different Name", email="info@gym.com", source="instagram"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 1
    assert removed == 1


def test_dedup_by_fuzzy_name_same_city():
    leads = [
        Lead(name="Miami Strong Gym", address="1830 N Bayshore Dr, Miami", source="google_maps"),
        Lead(name="miami strong gym!", address="Bayshore Drive 1830, Miami", source="facebook"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 1
    assert removed == 1


def test_no_fuzzy_merge_without_city():
    leads = [
        Lead(name="Miami Strong Gym", source="google_maps"),
        Lead(name="Miami Strong Gym", source="instagram"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 2
    assert removed == 0


def test_merge_fills_empty_fields():
    leads = [
        Lead(name="Miami Strong Gym", phone="+13055046980", website="https://gym.com",
             address="1830 N Bayshore Dr, Miami", category="Gimnasio", rating=4.8,
             source="google_maps"),
        Lead(name="miamistronggym", phone="+13055046980", email="info@gym.com",
             source="instagram"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert removed == 1
    merged = unique[0]
    assert merged.name == "Miami Strong Gym"
    assert merged.email == "info@gym.com"
    assert merged.phone == "+13055046980"
    assert merged.website == "https://gym.com"
    assert merged.raw_data["merged_from"] == ["google_maps", "instagram"]


def test_no_duplicates_returns_same():
    leads = [
        Lead(name="Gym A", phone="+13055046980", source="google_maps"),
        Lead(name="Gym B", phone="+13055046981", source="google_maps"),
        Lead(name="Gym C", email="c@gymc.com", source="instagram"),
    ]
    unique, removed = Deduplicator().deduplicate(leads)
    assert len(unique) == 3
    assert removed == 0
