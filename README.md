# Wendeburg Calendar

Ein kleines, adapterbasiertes Python-Tool, das öffentliche Veranstaltungs-
Listings aus Wendeburg und dem Peiner Land einsammelt ("harvest") und
daraus einen stabilen, abonnierbaren RFC-5545-Kalenderfeed
(`calendar.ics`) erzeugt.

> **Status:** MVP. Der Feed wird taeglich per GitHub Actions aktualisiert und
> per GitHub Pages veroeffentlicht.

## Oeffentlicher Kalender

Der abonnierbare Feed ist unter
`https://trsdn.github.io/wendeburg-calendar/calendar.ics` erreichbar. Die
Startseite liegt unter `https://trsdn.github.io/wendeburg-calendar/`.

Die Produktionskonfiguration in [`config.github.toml`](config.github.toml)
enthaelt alle aktuell vorgesehenen Quellen und verwendet keinen LLM-Fallback.
Der versionierte SQLite-Zustand bewahrt stabile UIDs, Sequenzen und Absagen.
Der eingecheckte Feed ist ein Fixture-Beispiel und wird durch den ersten
erfolgreichen geplanten Lauf durch Live-Daten ersetzt.

## Inhaltsverzeichnis

- [Funktionsweise im Überblick](#funktionsweise-im-überblick)
- [Aktive und zurückgestellte Quellen](#aktive-und-zurückgestellte-quellen)
- [Voraussetzungen](#voraussetzungen)
- [Installation (venv)](#installation-venv)
- [Konfiguration](#konfiguration)
- [Umgebungsvariablen (.env)](#umgebungsvariablen-env)
- [Befehle (CLI)](#befehle-cli)
- [Den Kalender abonnieren](#den-kalender-abonnieren-google-apple-outlook)
- [robots.txt-Verhalten](#robotstxt-verhalten)
- [LLM-Fallback: Sicherheit & Datenfluss](#llm-fallback-sicherheit--datenfluss)
- [Identität, SEQUENCE und Absagen](#identität-sequence-und-absagen)
- [Tests](#tests)
- [Bekannte Grenzen (MVP)](#bekannte-grenzen-mvp)

## Funktionsweise im Überblick

```
Konfiguration (TOML)
        │
        ▼
Quellen-Adapter (z. B. "wendeburg", "peine-erleben", "structured-html")
        │  nutzt ausschließlich den zentralen HarvestClient
        ▼
robots.txt-geprüfter, host-eingeschränkter HTTP-Abruf
        │
        ▼
Deterministisch: ICS → JSON-LD → strukturierte HTML-Profile
        │
        └──────── nur bei unstrukturierten Detailseiten ─────────► LLM-Fallback
        │                                                         (nur bereinigter Text)
        ▼
Lokale Pydantic-Validierung
        │
        ▼
SQLite-Reconcile-Transaktion (Identität, SEQUENCE, Missing/Cancel-Logik)
        │
        ▼
Atomarer Export nach calendar.ics (RFC 5545)
```

Jede Veranstaltungsquelle ist ein Adapter mit einer schmalen Schnittstelle
(`discover()` / `fetch_candidate()`). Eine Kandidatenressource kann null,
einen oder mehrere normalisierte Termine liefern. Dadurch lassen sich neben
Einzeltermin-Details auch ICS-Feeds und HTML-Listen mit mehreren Terminen
verlustfrei verarbeiten.

Der Wendeburg-Adapter nutzt zwei bekannte Einstiegspunkte:

- `https://www.wendeburg.de/freizeit-kultur/veranstaltungen/veranstaltungen/`
- `https://www.wendeburg.de/regional/veranstaltungen/suche.html`

Wo verfügbar, werden stabile `.ical`-Exporte der einzelnen Termine
bevorzugt (z. B.
`https://www.wendeburg.de/veranstaltungen/veranstaltung/{slug}-{event-id}-26610.ical`).
Nur wenn für einen Termin **kein** ICS auffindbar ist, wird als Fallback ein
LLM zur Extraktion aus dem (bereinigten) Seitentext genutzt.

## Aktive und zurückgestellte Quellen

`config.example.toml` enthält alle derzeit live geprüften Quellen:

| Quelle | Technik | Hinweise |
|---|---|---|
| Gemeinde Wendeburg | ICS, sonst LLM-Fallback | Zwei öffentliche Listings; `.ical` wird immer bevorzugt. |
| Peine erleben | Event-Sitemap + schema.org JSON-LD | Die interaktive Kalenderseite zeigt automatisierten Clients derzeit eine JavaScript-Sicherheitsprüfung. Der Adapter nutzt deshalb ausschließlich die in `robots.txt` angekündigte öffentliche Event-Sitemap und deren öffentlichen Detailseiten; keine Solr- oder TYPO3-Interna. |
| Kulturring Peine | strukturierte, paginierte HTML-Liste | Titel, Datum, Uhrzeit, Ort, Veranstalter und öffentlicher Detail-Link; Endzeiten sind in der Liste meist nicht vorhanden. |
| Tourismus Peine | strukturierte HTML-Karten | Kuratierte Jahres-Highlights. Datumsbereiche werden als Ganztagstermine importiert; die Seite kann auch bereits vergangene Highlights des laufenden Jahres enthalten. |
| Zweidorf Online | strukturierter Seitentext | Nur ausdrücklich datierte Einträge werden importiert. Unbestimmte Angaben wie „immer am 24. August“ werden nicht geraten. `Crawl-delay: 10` wird hostweit eingehalten. |
| Kirche Wendeburg | strukturierte Kirchenkalender-Liste | Gottesdienste und Gemeindetermine mit öffentlichen Detail-Links. |
| Kirche Bortfeld | strukturierte Monatstabelle | Es werden nur Einträge aus der Spalte „Gottesdienst in Bortfeld“ importiert, nicht Alternativtermine anderer Gemeinden. |

**Zurückgestellt:**

- `https://www.jg-bortfeld.de/`: Die Website ist erreichbar, bietet aktuell
  aber keine verlässliche öffentliche Terminliste. Sitemap und Navigation
  enthalten nur Einzel-/Archivseiten; auf der Startseite gefundene Angaben
  waren bereits vergangen. Deshalb wird keine URL oder kein Selektor
  erfunden.

Alle aktiven Quellen funktionieren deterministisch ohne
`OPENAI_API_KEY`. Ein Schlüssel wird nur benötigt, wenn eine
unstrukturierte Einzel-Detailseite tatsächlich den vorhandenen
LLM-Fallback braucht.

## Voraussetzungen

- Python **3.11 oder neuer**
- Kein Docker/Container nötig - reines venv genügt

## Installation (venv)

```bash
cd wendeburg-calendar
python3 -m venv .venv
source .venv/bin/activate        # unter Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e ".[dev]"          # installiert das Projekt + pytest
```

Danach steht sowohl das Modul (`python -m wendeburg_calendar.cli ...`) als
auch der Konsolen-Befehl `wendeburg-calendar` zur Verfügung.

## Konfiguration

Die Konfiguration ist **reines TOML**. Kopiere die mitgelieferte Beispiel-
Datei und passe sie an:

```bash
cp config.example.toml config.toml
```

`config.toml` ist bewusst in `.gitignore` eingetragen, damit lokale Pfade
nie versehentlich eingecheckt werden.

Wichtig: **alle relativen Pfade** in der Konfiguration (`database`,
`output`) werden relativ zum **Verzeichnis der Konfigurationsdatei**
aufgelöst - nicht relativ zum Arbeitsverzeichnis, aus dem die CLI
aufgerufen wird.

Die wichtigsten Abschnitte:

```toml
[general]
domain = "wendeburg-calendar.example.org"  # Basis für stabile Kalender-UIDs
database = "data/wendeburg.sqlite3"
output = "data/calendar.ics"
user_agent = "WendeburgCalendarBot/0.1 (+https://.../contact=...)"

[harvest]
missing_threshold = 3       # so oft muss ein Termin hintereinander fehlen
missing_grace_days = 7      # UND so viele Tage müssen vergangen sein
max_events_per_source = 500
request_timeout_seconds = 15
max_content_bytes = 5000000

[llm]
enabled = true
default_model = "gpt-5.6-luna"
max_input_chars = 6000

[[sources]]
id = "wendeburg"
type = "wendeburg"
enabled = true
seed_urls = ["https://www.wendeburg.de/..."]
allowed_hosts = ["www.wendeburg.de"]
min_request_delay_seconds = 0.0  # optionaler Mindestabstand pro Host
```

`allowed_hosts` ist verpflichtend und darf nicht leer sein; jeder
`seed_url`-Host muss ausdrücklich enthalten sein. Redirects und
robots.txt-Redirects werden auf dieselbe Allowlist begrenzt.
`min_request_delay_seconds` muss nichtnegativ sein. Wenn robots.txt
zusätzlich ein `Crawl-delay` vorgibt, gilt immer der strengere (größere)
Wert; auch Wiederholungsversuche werden vor jedem Request entsprechend
getaktet.

Weitere Quellen lassen sich durch zusätzliche `[[sources]]`-Blöcke
hinzufügen. Für stabile HTML-Listen wird `type = "structured-html"` mit
einem implementierten `profile` verwendet (siehe
`parsing/structured_html.py` und `sources/registry.py`).

## Umgebungsvariablen (.env)

Zugangsdaten für den LLM-Fallback werden **ausschließlich** aus der
Umgebung gelesen - nie aus Dateien, nie hartkodiert, nie geloggt.

```bash
cp .env.example .env
# .env danach mit einem echten Endpoint/Key befüllen und z. B. per
# `export $(grep -v '^#' .env | xargs)` oder einem Prozess-Manager laden.
```

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `OPENAI_API_KEY` | für LLM-Fallback ja | API-Schlüssel für einen OpenAI-kompatiblen Chat-Completions-Endpoint. Fehlt er, wird der LLM-Fallback für den Lauf automatisch deaktiviert (kein Absturz). |
| `OPENAI_BASE_URL` | nein | Basis-URL eines OpenAI-kompatiblen Endpoints (z. B. eines internen Luna-Gateways). Ohne Angabe wird die Standard-OpenAI-API genutzt. |
| `OPENAI_MODEL` | nein | Modellname. Standard: `gpt-5.6-luna`. |

`config.toml`/`config.example.toml` enthalten **keine** Secrets - nur
`default_model` als Fallback-Wert, falls `OPENAI_MODEL` nicht gesetzt ist.

## Befehle (CLI)

```bash
wendeburg-calendar --config config.toml run       # harvest + export (Standard)
wendeburg-calendar --config config.toml harvest   # nur einsammeln/aktualisieren
wendeburg-calendar --config config.toml export    # nur den aktuellen DB-Stand exportieren
```

Ohne Subcommand wird automatisch `run` ausgeführt:

```bash
wendeburg-calendar --config config.toml
```

Nützliche Optionen:

| Option | Wirkung |
|---|---|
| `--config PATH` | TOML-Konfigurationsdatei (Standard: `config.toml`) |
| `--database PATH` | überschreibt den DB-Pfad aus der Konfiguration |
| `--output PATH` | überschreibt den `.ics`-Ausgabepfad aus der Konfiguration |
| `--source SOURCE_ID` | beschränkt den Harvest auf eine Quelle (wiederholbar) |
| `--offline-fixture DIR` | bedient **alle** HTTP-Anfragen (inkl. robots.txt) aus einem lokalen Fixture-Verzeichnis statt aus dem echten Netz - siehe unten |
| `-v` / `--verbose` | ausführlichere Meldungen |

### Offline-Testlauf ohne Netzwerk

Für Demos, CI oder Debugging lässt sich der komplette Lauf ganz ohne
Internetzugriff ausführen, indem alle Antworten aus einem lokalen
Fixture-Verzeichnis (mit `manifest.json`) bedient werden:

```bash
wendeburg-calendar \
  --config tests/fixtures/wendeburg_basic/config.toml \
  --database /tmp/wendeburg-demo/wendeburg.sqlite3 \
  --output /tmp/wendeburg-demo/calendar.ics \
  --offline-fixture tests/fixtures/wendeburg_basic \
  run
```

Dieser Modus durchläuft exakt denselben Code-Pfad wie ein Live-Lauf
(inklusive robots.txt-Prüfung), ersetzt nur die tatsächliche Netzwerk-
Schicht.

Ein Fixture mit allen neuen deterministischen Quelltypen liegt unter
`tests/fixtures/multi_source/`.

## Den Kalender abonnieren (Google, Apple, Outlook)

Sobald `calendar.ics` über eine feste, für den jeweiligen Client
erreichbare URL bereitgestellt wird (z. B. per einfachem Webserver,
Cronjob + Kopie auf einen Webspace, o. ä. - **das ist bewusst nicht Teil
dieses MVP**, siehe [Bekannte Grenzen](#bekannte-grenzen-mvp)):

- **Google Kalender:** "Weitere Kalender" → "Über URL" → die `.ics`-URL
  eintragen. Google aktualisiert typischerweise alle paar Stunden.
- **Apple Kalender (macOS/iOS):** "Datei" → "Neues Kalenderabonnement"
  (macOS) bzw. Einstellungen → "Kalender" → "Account hinzufügen" →
  "Andere" → "Kalenderabo hinzufügen" (iOS), dann die `.ics`-URL angeben.
- **Outlook:** "Kalender hinzufügen" → "Aus dem Internet abonnieren" → die
  `.ics`-URL eintragen.

Wichtig: Alle drei Clients cachen/pollen periodisch - Änderungen sind also
nicht sofort, sondern erst nach dem nächsten Poll-Intervall des jeweiligen
Clients sichtbar.

## robots.txt-Verhalten

Alle HTTP-Zugriffe laufen ausschließlich über den zentralen
`HarvestClient` (`http/client.py`) - ein Adapter kann diese Prüfungen
strukturell nicht umgehen, da er niemals einen rohen HTTP-Client erhält.

- Vor jedem Abruf wird `robots.txt` des jeweiligen Hosts geprüft
  (`http/robots.py`), inklusive korrekter Gruppen-Auswahl (spezifischer
  User-Agent-Block vor `*`-Block).
- `Allow`/`Disallow` unterstützt `*`-Wildcards und terminales `$`; bei
  gleich langen Treffern gewinnt `Allow`. Damit werden unter anderem die
  Peine-Regeln `/*?id=*` und `/*?*tx_solr` zentral durchgesetzt.
- Ein `Crawl-delay` der ausgewählten User-Agent-Gruppe wird über einen
  gemeinsamen, hostweiten Rate-Limiter auf robots.txt, normale Abrufe und
  Redirect-Hops angewendet. Der dokumentierte 10-Sekunden-Abstand von
  Zweidorf wird dadurch auch über mehrere Adapter-Clients hinweg gewahrt.
- Temporäre Antworten (`429`, `500`, `502`, `503`, `504`) werden höchstens
  zweimal wiederholt (drei Versuche insgesamt). Bei `429` wird ein
  gültiges `Retry-After` (Sekunden oder HTTP-Datum) innerhalb eines
  begrenzten Wartebudgets eingehalten; permanente Fehler wie `401` und
  `403` werden nicht wiederholt.
- **Fail closed:** Jedes andere Ergebnis als ein bestätigtes `200`
  (Regeln gelten), `404` oder `410` (kein `robots.txt` vorhanden bzw.
  bestätigt entfernt → keine Einschränkung) führt zu einer **Deny-all**-
  Entscheidung für diesen Host, bis ein erfolgreicher Abruf gelingt. Das
  betrifft insbesondere Timeouts, 5xx-Fehler und Transportfehler.
- Bekannte, dokumentierte Sperren auf wendeburg.de:
  `/barrierefreiheit/barriere_melden.html`, `/portal/kontakt.html`,
  `/portal/suche.html`, `/portal/suche2.html`,
  `/portal/weiterempfehlen.html`, `/allris/___tmp/`. Für die Bots
  `WebCopier`/`HTTrack` ist die gesamte Seite gesperrt (`Disallow: /`).
- Jeder Redirect-Hop wird erneut gegen Schema (`http`/`https`),
  Host-Allowlist und `robots.txt` geprüft ("Redirect-Revalidierung") -
  ein Redirect auf einen nicht erlaubten Host wird abgelehnt.
- Ein fehlgeschlagener/blockierter/übergroßer Abruf führt **niemals** dazu,
  dass Termine als "nicht mehr vorhanden" gewertet werden (siehe
  [Identität, SEQUENCE und Absagen](#identität-sequence-und-absagen)) -
  ein solcher Lauf wird als `PARTIAL` markiert, nicht als vollständige,
  belastbare Momentaufnahme.

## LLM-Fallback: Sicherheit & Datenfluss

Der LLM-Fallback (`llm/`) kommt **ausschließlich** bei einer
unstrukturierten Einzel-Detailseite zum Einsatz, wenn weder ICS noch
schema.org-JSON-LD oder ein stabiles strukturiertes HTML-Profil greift.
ICS, JSON-LD und konfigurierte Listenprofile werden nie an das LLM
gesendet.

Sicherheitsprinzipien:

1. **Webinhalte sind nicht vertrauenswürdige Daten.** Roh-HTML wird nie an
   das Modell weitergegeben. `parsing/html_sanitize.py` entfernt
   `script`/`style`/`iframe`/`object`/`embed`/`svg` vollständig und
   extrahiert nur sichtbaren Text (keine Attribute, keine Links).
2. **Begrenzte Textlänge.** Der bereinigte Text wird auf `max_input_chars`
   (Standard 6000 Zeichen) gekürzt.
3. **Statischer System-Prompt, getrennter User-Content.** Der System-Prompt
   ist eine feste Konstante ohne jede Interpolation. Der bereinigte,
   nicht vertrauenswürdige Text steht ausschließlich in der User-Nachricht,
   klar als "UNTRUSTED PAGE CONTENT" markiert; enthaltene Anweisungen
   sollen laut Prompt ignoriert werden.
4. **Keine Tools/Tool-Calls.** An keiner Stelle werden `tools` oder
   `tool_choice` an die Chat-Completions-API übergeben.
5. **Keine Secrets/Env/Pfade/Header im Prompt.** Es werden ausschließlich
   Systemprompt + bereinigter Seitentext übertragen.
6. **Nur `http(s)`-URLs, Host-Allowlist.** Nicht-`http(s)`-Schemata werden
   abgelehnt; Quellen dürfen nur konfigurierte Hosts kontaktieren.
7. **Strikte lokale Validierung.** Unabhängig vom tatsächlich genutzten
   Antwortformat wird jede LLM-Antwort lokal gegen ein Pydantic-Modell mit
   `extra="forbid"` validiert (`llm/schemas.py`). Schlägt das fehl, wird
   der Termin einfach übersprungen (kein Absturz, kein "erratenes"
   Ergebnis).
8. **Kein Folgen von Links/ICS-Anhängen.** Da nur reiner Text an das LLM
   geht, kann es keine Links "entdecken"; ICS-`ATTACH`-Eigenschaften
   werden nie automatisch nachgeladen.

### Fähigkeits-toleranter Fallback

Nicht jeder OpenAI-kompatible Endpoint unterstützt dieselben
Antwortformate. Es wird daher eine Kette versucht:

1. **JSON Schema / Structured Outputs**
   (`response_format={"type": "json_schema", ...}`)
2. Falls das Backend dieses Format **explizit ablehnt**: **JSON-Modus**
   (`response_format={"type": "json_object"}`)
3. Falls auch das abgelehnt wird: **einfacher Chat** ganz ohne
   `response_format`

Ein "Ablehnen" wird konservativ erkannt (Fehlermeldung bezieht sich
eindeutig auf `response_format`/`json_schema`/`json_object`) - echte
Fehler (z. B. ungültiger API-Key, Netzwerkausfall) werden **nicht**
stillschweigend als "Format nicht unterstützt" fehlinterpretiert; sie
führen dazu, dass die Extraktion für diesen einen Termin fehlschlägt
(nicht der gesamte Harvest-Lauf).

## Identität, SEQUENCE und Absagen

- Jeder Termin bekommt eine interne, stabile `UUID4`. Die im Feed
  sichtbare `UID` ist immer `"<uuid>@<domain>"` - **niemals** aus Titel
  und Datum gebildet.
- Identitäts-Reihenfolge (stärkstes zuerst): **ICS-UID** → erkannte
  **X-ID** → **Quelle+kanonische URL** → schwacher **Fingerabdruck**
  (normalisierter Titel + Datum) nur als allerletzte Notlösung. Sobald ein
  stärkerer Identifikator später auftaucht, wird er demselben internen
  Termin zugeordnet, statt einen Duplikat-Termin zu erzeugen.
- Die Quell-URL ist die tatsächlich abgerufene Detail-, `.ical`- oder
  Listenressource und dient Identität/Provenienz. Mehrere Einträge einer
  Liste erhalten zusätzlich jeweils eine stabile X-ID, sodass eine
  gemeinsame Listen-URL sie nicht zusammenführt. Eine
  `URL`-Eigenschaft aus dem Quell-`VEVENT` wird separat als öffentliche
  Termin-URL gespeichert und in den Feed exportiert; sie wird niemals als
  Quell-URL-Alias verwendet.
- **SEQUENCE** wird nur erhöht, wenn sich ein **semantisches** Feld
  ändert (Titel, Start, Ende, Ganztägig-Flag, Ort, Beschreibung,
  Organisator, Status oder exportierte Termin-URL) - und dann **genau
  einmal** pro Änderung.
  Reine Abruf-Metadaten (z. B. Roh-Content-Hash der Seite,
  Extraktionsmethode/-konfidenz) erhöhen SEQUENCE **nicht**.
- Eine **explizite Absage durch die Quelle** (`STATUS:CANCELLED`) wirkt
  sofort beim nächsten Harvest, der sie beobachtet.
- **Einfaches Fehlen ≠ Absage.** Ein Termin muss in
  `missing_threshold` (Standard 3) **aufeinanderfolgenden** `COMPLETE`
  -Harvest-Läufen fehlen **und** es müssen mindestens
  `missing_grace_days` (Standard 7) Tage seit dem ersten Fehlen vergangen
  sein, bevor er automatisch als abgesagt markiert wird.
- **`PARTIAL`/`UNCHANGED`-Läufe verändern die Fehlanzahl nie.** Nur ein
  vollständiger (`COMPLETE`) Lauf zählt als belastbarer Beleg für
  Abwesenheit.
- Taucht ein Termin wieder auf, wird der Fehlzähler zurückgesetzt; war er
  zuvor wegen Abwesenheit automatisch abgesagt, wird er reaktiviert.
  Abgesagte Termine ("Tombstones") bleiben in der Datenbank und damit im
  exportierten Feed sichtbar (Status `CANCELLED`), statt einfach zu
  verschwinden.
- Identitäten bleiben bewusst **quellenspezifisch**. Es gibt derzeit keine
  globale Zusammenführung nur aufgrund ähnlicher Titel, gemeinsamer
  Veranstalter-Websites oder unscharfer Orts-/Datumsähnlichkeit. Das
  vermeidet falsche Zusammenführungen; echte Überschneidungen zwischen
  zwei Quellen können daher als zwei Feed-Einträge erscheinen.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Die Test-Suite läuft **komplett ohne Netzwerkzugriff** (Parser,
UID-Stabilität/Idempotenz, semantische Updates/SEQUENCE, ICS-Export,
LLM-Validierung/Fallback-Kette, robots.txt-Wildcards und Crawl-Delay,
Multi-Event-Ressourcen, Fehlerisolation sowie Offline-End-to-End-Tests
über `tests/fixtures/wendeburg_basic/` und
`tests/fixtures/multi_source/`).

## Bekannte Grenzen (MVP)

- **Kein Deployment/Container.** Es gibt bewusst noch keinen Dockerfile,
  keinen Scheduler/Cronjob und keinen Webserver zum Ausliefern von
  `calendar.ics` - das Tool erzeugt die Datei lokal; die Bereitstellung
  liegt aktuell beim Betreiber.
- **Keine Wiederholungsregeln (Recurrence).** Wiederkehrende Termine
  werden nicht expandiert; jedes VEVENT wird als Einzeltermin behandelt.
- **Robots-Unterstützung ist zielgerichtet, kein vollständiger
  RFC-9309-Validator.** Die für die aktiven Quellen benötigten
  User-Agent-Gruppen, Wildcards, `$`, längster Treffer, `Allow`-Tie-Break
  und `Crawl-delay` werden unterstützt.
- **LLM-Extraktion ist ein Fallback, kein Ersatz für ICS.** Die
  Konfidenz für LLM-extrahierte Termine ist bewusst niedriger und die
  Extraktion kann - trotz strikter Validierung - Termine ohne klar
  erkennbaren Titel/Start schlicht überspringen.
- **Ein Prozess, ein Lauf.** Es gibt keine Nebenläufigkeits-/Sperr-Logik
  für parallele Läufe gegen dieselbe SQLite-Datei.
- **Fingerabdruck-Kollisionen.** Der schwache Fingerabdruck (Titel+Datum)
  ist absichtlich die letzte Instanz und kann in seltenen Fällen (zwei
  echte, unabhängige Termine mit identischem Titel am selben Tag) zu
  Fehlzuordnungen führen, wenn beide zusätzlich noch keinerlei stabilen
  Identifikator besitzen.
- **Keine globale Cross-Source-Entität.** Konservative Quelltrennung wird
  falschen Zusammenführungen vorgezogen; dieselbe reale Veranstaltung kann
  bei zwei unabhängigen Quellen doppelt erscheinen.
