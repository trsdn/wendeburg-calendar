from __future__ import annotations

from pathlib import Path

import pytest

from wendeburg_calendar.parsing.jsonld_events import parse_jsonld_events
from wendeburg_calendar.parsing.peine_d1 import parse_peine_d1_event
from wendeburg_calendar.parsing.structured_html import parse_structured_events
from wendeburg_calendar.sources.peine_erleben import _is_safe_event_url

from tests.conftest import MULTI_SOURCE_FIXTURE


@pytest.mark.parametrize(
    ("profile", "fixture_name", "expected_titles"),
    [
        (
            "kulturring-peine",
            "kulturring.html",
            {"Blutspende", "Podiumsdiskussion"},
        ),
        (
            "tourismus-peine",
            "tourismus.html",
            {"Weihnachtsstadt Peine"},
        ),
        (
            "zweidorf-online",
            "zweidorf.html",
            {"Volksfest in Zweidorf", "Drachenfest"},
        ),
        (
            "kirche-lkcal",
            "kirche-gottesdienste.html",
            {"Gottesdienst"},
        ),
        (
            "kirche-bortfeld",
            "bortfeld.html",
            {"Gottesdienst unter der Eiche"},
        ),
    ],
)
def test_structured_profiles_extract_exact_events(
    profile: str,
    fixture_name: str,
    expected_titles: set[str],
):
    html = (MULTI_SOURCE_FIXTURE / fixture_name).read_text(encoding="utf-8")
    events = parse_structured_events(
        profile,
        html,
        source_id="test-source",
        source_url="https://example.test/events",
    )

    assert {event.title for event in events} == expected_titles
    assert all(event.start.tzinfo is not None for event in events)
    assert all(event.source_x_id for event in events)


def test_jsonld_parser_preserves_deeplink_and_berlin_time():
    html = (MULTI_SOURCE_FIXTURE / "peine-dorffest.html").read_text(encoding="utf-8")
    source_url = "https://www.peine-erleben.de/d1i-item-page/dorffest-101/"

    events = parse_jsonld_events(
        html,
        source_id="peine-erleben",
        source_url=source_url,
    )

    assert len(events) == 1
    event = events[0]
    assert event.title == "Dorffest Vöhrum"
    assert event.start.isoformat() == "2026-09-05T14:00:00+02:00"
    assert event.event_url == "https://www.peine-erleben.de/events/dorffest/"
    assert event.source_url == source_url
    assert event.source_x_id == "jsonld:e_101:2026-09-05T14:00:00+02:00"


def test_peine_adapter_rejects_solr_and_cms_urls_before_discovery():
    assert _is_safe_event_url(
        "https://www.peine-erleben.de/d1i-item-page/test-1/?tx_toujoudestinationoneintegration_item%5Btype%5D=2"
    )
    assert not _is_safe_event_url(
        "https://www.peine-erleben.de/search/?tx_solr%5Bq%5D=event"
    )
    assert not _is_safe_event_url(
        "https://www.peine-erleben.de/typo3/index.php"
    )


def test_jsonld_repeated_identifier_keeps_occurrences_distinct():
    html = """
    <script type="application/ld+json">
    [
      {"@context":"https://schema.org","@type":"Event","identifier":"series-1",
       "name":"Virtuelle Messe","startDate":"2026-08-28T00:00:00+02:00"},
      {"@context":"https://schema.org","@type":"Event","identifier":"series-1",
       "name":"Virtuelle Messe","startDate":"2026-08-29T00:00:00+02:00"}
    ]
    </script>
    """

    events = parse_jsonld_events(
        html,
        source_id="peine-erleben",
        source_url="https://www.peine-erleben.de/detail",
    )

    assert len(events) == 2
    assert len({event.source_x_id for event in events}) == 2


def test_kulturring_same_day_performances_keep_distinct_ids():
    html = """
    <div class="veranstaltungen">
      <div class="entryHeadDate"><b>Freitag, den 18.12.2026</b></div>
      <div class="entryBody">Veranstalter: Theater
        <div class="leftCol"><b>09:00 h</b><br>Stadttheater<br></div>
        <div class="wglListEntryDetails"><h2><a href="?vDetail=pinocchio">Pinocchio</a></h2></div>
      </div>
      <div class="entryHeadDate"><b>Freitag, den 18.12.2026</b></div>
      <div class="entryBody">Veranstalter: Theater
        <div class="leftCol"><b>11:30 h</b><br>Stadttheater<br></div>
        <div class="wglListEntryDetails"><h2><a href="?vDetail=pinocchio">Pinocchio</a></h2></div>
      </div>
    </div>
    """

    events = parse_structured_events(
        "kulturring-peine",
        html,
        source_id="kulturring",
        source_url="https://www.kulturring-peine.de/calendar",
    )

    assert len(events) == 2
    assert len({event.source_x_id for event in events}) == 2


def test_peine_d1_html_fallback_parses_exact_range_and_times():
    html = """
    <h1 class="d1i-head__title">Schnupperwochenende</h1>
    <div id="descriptionText"><div class="toujou-text-clamper__content">
      Wann findet der Kurs statt? 15.08.2026-16.08.2026.
      Beginn ist jeweils um 10 Uhr und Ende gegen 19 Uhr.
    </div></div>
    <section id="d1iAddressesSection">
      <address class="d1i-address"><div class="d1i-address__infos">
        <p class="d1i-address__info">Flugplatz Peine</p>
        <p class="d1i-address__info">31226 Peine</p>
      </div></address>
      <address class="d1i-address"><div class="d1i-address__infos">
        <h4 class="d1i-section__subtitle">Veranstalter</h4>
        <p class="d1i-address__info">Uhlenflug Peine e. V.</p>
      </div></address>
    </section>
    <section id="d1iSidebarButtonsSection">
      <a href="https://example.org/schnupperkurs">Website</a>
    </section>
    """
    source_url = "https://www.peine-erleben.de/d1i-item-page/schnupper-101314920/"

    events = parse_peine_d1_event(
        html,
        source_id="peine-erleben",
        source_url=source_url,
    )

    assert len(events) == 1
    event = events[0]
    assert event.start.isoformat() == "2026-08-15T10:00:00+02:00"
    assert event.end.isoformat() == "2026-08-16T19:00:00+02:00"
    assert event.all_day is False
    assert event.location == "Flugplatz Peine, 31226 Peine"
    assert event.organizer == "Uhlenflug Peine e. V."
    assert event.event_url == "https://example.org/schnupperkurs"
