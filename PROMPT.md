# Bauauftrag für Claude Code: SchnippSchnapp

Baue in diesem Repository **SchnippSchnapp** (Repo-Name `schnippschnapp`, in der Oberfläche
„SchnippSchnapp“). Das ist eine selbst gehostete Web-App, mit der ich als einziger Nutzer im Browser
Filme aus meinem Video-Footage schneiden lasse, ohne die großen Originaldateien hochzuladen.

Sie baut auf meinem bestehenden Projekt cutter auf (https://github.com/aaronblankenburg/cutter). cutter
liefert Footage-Analyse, den Edit-Plan durch einen KI-Agenten mit dem Skill video-editing, Musik mit
Beat-Sync, Timelines (OTIO, FCP7-XML, SRT), FilmCraft-Finishing und eine Review-App.

Lies vor allem anderen in cutter:

- AGENTS.md und README.md
- skills/video-editing/
- src/cutter/, vor allem analyze.py, image_analysis.py, telemetry.py, proxies.py, timeline.py,
  filmcraft.py, feedback.py und review_app/
- Dockerfile und compose.yaml

Lies außerdem die GUI-Entwürfe in docs/design/.

Code, Kommentare und Doku schreibst du auf Englisch, im Stil von cutter. Die Texte in der
Oberfläche sind Deutsch.

## Grundsatz: Abhängigkeit statt Kopie

- SchnippSchnapp nutzt cutter als Abhängigkeit. Das Image baut auf dem cutter-Image auf
  (ghcr.io/aaronblankenburg/cutter, Version und Digest gepinnt). Kopiere keinen cutter-Code ins
  neue Repo und patche ihn dort auch nicht.
- Änderungen, die auch ohne Web-App sinnvoll sind, gehören nach cutter. Setz sie dort als eigenen
  Branch um, mit getrennten Commits, Tests und Doku. Der lokale CLI- und Plugin-Workflow von cutter
  muss unverändert funktionieren, und alle bestehenden Tests müssen grün bleiben.
- Erwartet sind dort mindestens diese vier Änderungen:
  - **a)** Original-Metadaten kommen aus einer Sidecar-Datei pro Clip, während die Analyse eine
    kleinere Stellvertreterdatei liest (siehe „Server: Analyse“).
  - **b)** Der Skill bekommt einen nicht-interaktiven Modus.
  - **c)** Der Export unterstützt ein Pfad-Mapping auf einen anderen Rechner.
  - **d)** Es gibt einen FCPXML-Writer.
- Liste vor Beginn auf, was nach cutter gehört und was nach SchnippSchnapp, und lass mich das
  bestätigen.

## Ziel-Workflow

1. **Neues Projekt anlegen.** Ich wähle im Browser meinen Footage-Ordner. Das Material hat bis zu
   4K und ist oft HEVC von Handy, DJI-Drohne oder GoPro. Der Browser transcodiert jeden Clip lokal
   auf 640 px Breite (360p) und lädt nur diese kleinen Dateien hoch. Dazu gebe ich ein:
   - einen Freitext-Prompt,
   - die Briefing-Felder: Zweck und Publikum, Ziellänge, Stil, Musikwunsch, No-gos und Personen,
     die nicht vorkommen sollen,
   - optional ein Storyboard (Text, PDF oder Bilder),
   - weitere Bilder,
   - eigene Musik, mit Pflichtangabe der Lizenz oder „eigene Musik“.
2. **Button „Entwurf schneiden“.** Auf dem Server schneidet Claude Code headless mit dem Skill
   video-editing den ersten Entwurf.
3. **Review.** Ich sehe die Fassung und kommentiere zeitgenau. Der Button „Sichtung abgeschlossen →
   neue Fassung“ sperrt die Kommentare dieser Fassung und startet den Next-Cut-Ablauf des Skills:
   Feedback umsetzen und jeden Kommentar beantworten. Das lässt sich beliebig oft wiederholen.
4. **Button „Fertig → Export“.** Ich lade ein ZIP-Paket für Premiere Pro, Final Cut Pro, DaVinci
   Resolve oder FilmCraft herunter. Seine Medienverweise zeigen auf die ORIGINALDATEIEN auf meinem
   Rechner. Final gerendert wird in meinem Schnittprogramm.

## GUI-Vorlage

- docs/design/ enthält die abgestimmten GUI-Entwürfe:

  | Datei | Seite |
  |---|---|
  | Main | Projekte |
  | Neu | Neues Projekt |
  | Job | Status |
  | Review | Review |
  | Export | Export |

  Sie sind die verbindliche Vorlage für Aufbau, Texte, Farben, Schriften (Space Grotesk, Inter,
  JetBrains Mono), Abstände und Zustände.
- Die Dateien sind Template-HTML. `{{…}}` sind Platzhalter, `<sc-for>` und `<sc-if>` sind
  Schleifen und Bedingungen, und `renderVals()` liefert Beispieldaten. Übernimm daraus Struktur und
  Stil, aber weder die Template-Syntax noch die Beispieldaten.
- Leite zuerst Design-Tokens (Farben, Radien, Abstände, Typografie) als CSS-Variablen ab und baue
  die Seiten darauf auf.
- Manches lassen die Entwürfe offen: die Analyse-Ansicht mit Contact Sheets und Transkripten,
  Fehlerzustände und die Mobilansicht. Entscheide dort im selben Stil und zeig es mir.
- Die Review-Seite ersetzt das bisherige Frontend der cutter-Review-App. API und Datenhaltung der
  Review-App bleiben, wie sie sind.
- Das Frontend soll schlank bleiben. Server-gerendertes HTML mit wenig JavaScript oder ein kleines
  Framework ohne schweren Build reicht. Schlag vor, was am besten zur Review-App von cutter passt.

## Browser: Transcodieren, Metadaten, Upload

**Transcodieren**

- Nutze WebCodecs mit Mediabunny, oder mp4box.js plus einen Muxer.
- Lies die Originaldatei stückweise per `File.slice` und lade sie nie ganz in den RAM.
- Decodieren, auf 640 px Breite skalieren (Rotation berücksichtigen) und als H.264 kodieren, mit
  etwa 1–1,5 Mbit/s.
- Audio als AAC mit 48 kHz, etwa 160 kbit/s und der Original-Kanalzahl.
- Halte den Proxy-Vertrag aus cutters proxies.py ein: gleiche Frames zu gleichen Zeiten, keine
  fps-Änderung, Timestamps auch bei variabler Framerate (VFR) erhalten.
- Prüfe vor dem Start pro Clip `VideoDecoder.isConfigSupported()`. Nicht decodierbare Codecs und
  Container (MTS, MXF, AVI …) meldest du klar, mit Browser-Empfehlung. Überspring nichts still.

**Metadaten**

- Schreib pro Clip ein Sidecar-JSON aus dem ORIGINAL mit:
  - Dateiname und rel_path (`webkitRelativePath`)
  - Größe und mtime
  - Dauer, r_frame_rate und avg_frame_rate
  - Breite, Höhe und Rotation
  - Codec und Bittiefe
  - Creation-Time und Start-Timecode
  - Audio-Kanäle und Samplerate
  - alle Container-Tags (Gerät, ISO-6709-GPS)
  - einen Fingerabdruck: Größe plus SHA-256 der ersten und der letzten 1 MiB
- Telemetrie: Extrahier die GoPro-gpmd-Spur roh und lade sie mit hoch. DJI-.SRT-Dateien neben den
  Clips lädst du ebenfalls hoch.

**Upload**

- Der Upload ist fortsetzbar (z. B. tus) und läuft Clip für Clip schon während des Transcodierens.
- Fortschritt und Wiederaufnahme überstehen ein Neuladen des Tabs; den Status hältst du in OPFS oder
  IndexedDB.
- Halte während der Arbeit einen Wake Lock.
- Ziel sind aktuelle Chromium-Browser und Safari. Firefox nur, soweit es ohne Zusatzaufwand geht.

## Server: Analyse

- Die 360p-Dateien übernehmen die Rolle der Proxies (`proxies.table` / `timeline.use_proxies`).
  Eine eigene Proxy-Erzeugung entfällt.
- In clips.json kommen alle Original-Metadaten aus dem Sidecar. Nur der Medienpfad für die Analyse
  zeigt auf die 360p-Datei (Änderung a in cutter).
- `time_from_name()` sieht den Originalnamen. telemetry.py liest die hochgeladene gpmd-Spur bzw.
  die SRT-Datei.
- Die Analyse startet inkrementell, sobald Clips ankommen, nicht erst beim Klick auf den Button.
- Prüfe, ob die Schwellen in quality.py und image_analysis.py auf 360p-H.264 merklich anders
  ausfallen als auf dem Original, vor allem die Laplacian-Schärfe. Dokumentiere das Ergebnis. Passe
  die Schwellen nur an, wenn es nötig ist, und dann über cutter.toml.
- Die Review-Renderings entstehen aus den 360p-Dateien in 720p.

## Server: Claude Code als Job

**Nicht-interaktiver Skill-Modus** (Änderung b in cutter)

- Liegt eine briefing.md im Projekt, stellt der Skill keine Rückfragen. Fehlende Punkte entscheidet
  er selbst und schreibt jede Annahme in die Entscheidungsdatei, damit sie im Review sichtbar ist.
- Er fragt auch nicht vor Downloads. Die Modelle (Whisper, Erkennungsmodelle) liegen in einem
  persistenten Volume und werden beim ersten Start vorgeladen.

**Warteschlange und Aufruf**

- Die Job-Warteschlange liegt in SQLite. Pro Projekt läuft ein Job, die Parallelität ist
  konfigurierbar (Standard 1).
- Gestartet wird mit `claude -p` und `--output-format stream-json` im Projektordner. Den
  Fortschritt streamst du live in die UI.
- Jede Fassung ist eine frische Sitzung. Den Kontext liefern die Projektdateien.
- Das Modell der Jobs ist konfigurierbar (.env, mit `--model` an claude übergeben), getrennt für den
  ersten Entwurf und für Folgefassungen. Standard: Opus 5.5 für beide; Sonnet 5.5 soll für
  Folgefassungen mit kleinem Feedback einfach einstellbar sein. Das verwendete Modell steht im
  Job-Protokoll und in der UI.

**Anmeldung über mein Abo**

- Claude Code authentifiziert sich über `CLAUDE_CODE_OAUTH_TOKEN`. Den Token erzeuge ich mit
  `claude setup-token`. Er wird als Datei mit Rechten 600 oder als Docker-Secret übergeben, nie im
  Image.
- `ANTHROPIC_API_KEY` darf NICHT gesetzt sein. Prüf das beim Start und brich mit einer klaren
  Meldung ab, falls doch.
- Kein `--bare`, denn damit liest Claude Code keine OAuth-Credentials.
- Der erste Start darf nicht am Onboarding hängen (`~/.claude.json`). Lies dazu die aktuelle
  Claude-Code-Doku zu headless bzw. `-p` und halte dich an deren Flags statt an Vermutungen.

**Pausen und Ablauf**

- Erkenne Nutzungslimit- und Auth-Fehler im stream-json-Ergebnis. Der Job wird dann auf „pausiert“
  gesetzt, nicht auf „fehlgeschlagen“, und die UI zeigt das an.
- Fortgesetzt wird per Button. Die Fortsetzung startet eine frische Sitzung auf dem Stand der
  Projektdateien.
- Die UI warnt, wenn der Token in weniger als 30 Tagen abläuft. Das Ablaufdatum ist ein
  Konfigurationswert.

**Begrenzung des Agenten**

- Er läuft in einem eigenen Container, der nur das jeweilige Projekt und die Modelle sieht.
- Er hat eine Tool-Allowlist: cutter-Befehle sowie Lesen und Schreiben im Projekt.
- Turn-Limit und Timeout sind gesetzt.
- Netz-Egress gibt es nur, soweit er nötig ist. Kläre mit mir, welche Hosts die Musiksuche braucht.

**Benachrichtigung (optional)**

- Bei Job-Ende oder Pause geht eine Nachricht per MQTT raus. Host und Topic sind konfigurierbar,
  ohne Auth muss es auch gehen.

## Export mit korrekten Medienverweisen (Kernanforderung)

**Ablauf auf der Exportseite**

- Ich wähle das Betriebssystem und gebe den absoluten Pfad meines Footage-Ordners ein. Die UI
  erklärt, wie man ihn kopiert: unter Windows mit „Als Pfad kopieren“, unter macOS mit ⌥⌘C.
- Danach wähle ich den Ordner im Browser erneut, ohne Upload. Der Browser prüft alle Clips per
  Fingerabdruck gegen das Manifest. Er erkennt umbenannte oder verschobene Dateien und meldet
  fehlende, bevor exportiert wird.
- Die Exportdateien werden erst beim Download erzeugt.
- Die UI empfiehlt, das ZIP in einen Unterordner `_cutter/` des Footage-Ordners zu entpacken. Dann
  gilt ein einziger Basispfad auch für Musik, Titel-PNGs, Karten und Overview-Grafiken im Paket.

**Timeline und Pfade**

- `timeline.build()` läuft OHNE Proxies, mit Original-Breite, -Höhe, -fps, -Dauer,
  -Start-Timecode und -Rotation aus dem Sidecar.
- Nur so passen die Skalierung (Basic Motion 50 % bei 4K) und die Relink-Kriterien der
  Schnittprogramme. Premieres Link Media vergleicht u. a. den Media Start, Final Cut Pro prüft
  Medientyp und Dauer.
- Die Pfaderzeugung ist eine eigene, gründlich getestete Funktion (Änderung c in cutter). Sie
  beachtet `file://localhost/` mit URL-Encoding, Windows-Laufwerksbuchstaben und die
  Unicode-Normalisierung (NFC vs. NFD unter macOS).

**Formate**

Pro Format zeigt die UI ehrlich an, was mitkommt und was ich selbst nachziehen muss.

- **a) Premiere:** FCP7-XML und SRT. Titel liegen als transparente PNGs auf einer eigenen
  Videospur, das Musik-Ducking als Lautstärke-Keyframes. Marker kennzeichnen Stabilisierung,
  Audio-Bereinigung und die Review-Entscheidungen.
- **b) Final Cut Pro:** eigener FCPXML-Writer (Änderung d), denn der OTIO-Adapter reicht nicht. Er
  schreibt Schnitte, Transform, Titel, Captions, Lautstärke-Keyframes und Marker. Stabilisierung und
  Rauschreduzierung nur, wenn die aktuelle FCPXML-DTD das hergibt; prüfe das gegen die DTD.
- **c) DaVinci Resolve:** FCPXML und/oder OTIO, je nachdem, was mehr erhält.
- **d) FilmCraft:** das von cutters `finish` erzeugte Projekt, mit Medienverweisen von den
  360p-Dateien auf die Originale umgestellt. Finde in filmcraft-cli und der FilmCraft-Doku heraus,
  ob es Relink oder Proxy-Umschaltung gibt. Falls nicht, schlag einen Weg vor und frag mich, bevor
  du das Projektformat direkt umschreibst.

**Paketinhalt und Datenschutz**

- Das Paket enthält die Lizenzangaben der Musik und eine kurze README für den Import pro Programm.
- Gesichtsvektoren (people.json) gehören nie in Review-Daten oder Exporte.
- Wird ein Projekt gelöscht, verschwinden alle seine Daten.

## Zugang

- Es gibt einen einzigen Nutzer.
- Die gesamte App ist per HTTP Basic Auth geschützt: UI, API, Uploads, Medien, Review und Export.
  Ohne Ausnahme, auch Health-Checks von außen nicht.
- Nutze dafür den Mechanismus der cutter-Review-App (Passwort-Hash, Sperre nach Fehlversuchen pro
  IP) oder eine gleichwertige kleine Umsetzung. Das Passwort setze ich mit einem einzigen Befehl.
- Die App läuft hinter meinem vorhandenen nginx auf dem Host mit Let's Encrypt und lauscht nur auf
  127.0.0.1.
- Mitliefern: einen Beispiel-Server-Block für eine Subdomain (Proxy auf den Container, große
  Uploads bzw. tus-Chunks, lange Timeouts für Uploads und Job-Streams) und einen fail2ban-Filter für
  fehlgeschlagene Logins.

## Deployment (so wenig Setup wie möglich)

**Zielmaschine:** mein Heimserver, ein Intel NUC mit Linux x86_64, Docker mit Compose und ohne GPU.

**Ziel-Ablauf:**

1. Repo klonen.
2. `.env` aus `.env.example` anpassen: Datenpfad, Port, Token-Datei, optional MQTT.
3. Passwort mit einem einzeiligen Befehl setzen.
4. `docker compose up -d`.

Mehr darf es nicht brauchen.

**Image und Dienste**

- Es gibt ein Image. CI baut es (GitHub Actions → ghcr.io), FROM dem gepinnten cutter-Image, dazu
  Node und Claude Code in fester Version.
- Compose kommt mit möglichst wenigen Diensten aus: Web und Queue in einem Dienst, Agent-Jobs in
  einem separaten Container aus demselben Image.
- Wenn das ohne Docker-Socket im Web-Container nicht sauber geht, schlag die einfachste sichere
  Variante vor und begründe sie.

**Daten und Betrieb**

- Alle Daten liegen unter einem Host-Ordner (Projekte, SQLite, Uploads), dazu ein Volume für die
  Modelle.
- `restart: unless-stopped`, Healthchecks, Logs nach stdout.

**Updates und Backup**

- Updates laufen mit `docker compose pull && docker compose up -d`. Datenbankmigrationen laufen
  automatisch.
- Dokumentiere, welche Ordner ins Backup gehören und welche nicht. 360p-Uploads und Renderings
  lassen sich neu erzeugen, Kommentare und Edit-Pläne nicht.

## Tests

- **Unit-Tests** für:
  - die Pfadkodierung,
  - die Sidecar-Metadaten,
  - den Abgleich per Manifest und Fingerabdruck,
  - die Job-Zustände, einschließlich „pausiert“ bei Nutzungslimit, mit einem simulierten claude,
  - alle Export-Writer.
- **Golden-Tests** nach dem Vorbild von cutter/tests/golden/demo für FCP7-XML, FCPXML und OTIO mit
  Original-Metadaten.
- **Browser-Tests** (Playwright) für Transcodierung und Upload, mit kleinen Testclips:
  - H.264 und HEVC,
  - Hochformat mit Rotation,
  - VFR,
  - ein Clip mit Start-Timecode.
- **Checkliste docs/nle-roundtrip.md** für meinen manuellen Test:
  - Material: 4K-HEVC-Drohnenclips, Handy im Hochformat, GoPro.
  - Ablauf: Export, dann Import in Premiere, Final Cut Pro, Resolve und FilmCraft.
  - Erwartet: Schnitte frame-genau wie im Entwurf, alle Medien verknüpft.

## Vorgehen

Leg erst einen Plan mit Meilensteinen und der Aufteilung zwischen cutter und SchnippSchnapp vor und
warte auf mein OK. Vorschlag für die Meilensteine:

| Meilenstein | Inhalt | Modell |
|---|---|---|
| M0 | Plan, Aufteilung cutter / SchnippSchnapp, Architektur | Opus 5.5 (Plan-Modus) |
| M1 | Browser-Transcodierung, Sidecar und Upload; Machbarkeit an meinem echten Material | Opus 5.5 |
| M2 | Analyse auf 360p mit Sidecar-Metadaten | Sonnet 5.5 |
| M3 | Export mit korrekten Pfaden (zuerst Premiere) und Roundtrip-Test | Opus 5.5 |
| M4 | Claude-Code-Jobs und nicht-interaktiver Skill-Modus | Opus 5.5 |
| M5 | Review-Buttons und GUI nach docs/design/ | Sonnet 5.5 |
| M6 | FCPXML-, Resolve- und FilmCraft-Export | Opus 5.5 |
| M7 | Deployment, Härtung und Doku | Sonnet 5.5 |

**Modellwahl.** Die Spalte „Modell“ ist verbindlich. Du kannst das Modell nicht selbst wechseln, das
mache ich mit /model. Deshalb gilt:

- Nenne zu Beginn jedes Meilensteins das vorgesehene Modell. Läufst du gerade auf einem anderen,
  sag es mir und warte, bis ich gewechselt habe.
- Teile Meilensteine so, dass ein Modellwechsel an einer sauberen Stelle passiert: Tests grün,
  Stand committet, offene Punkte in docs/progress.md notiert. Eine neue Sitzung muss dort ohne
  Vorwissen weitermachen können.
- Stößt du in einem Sonnet-Meilenstein auf ein Problem, das tiefer geht als erwartet (z. B.
  Zeitbasis, Codecs, Relink-Kriterien), schlag mir vor, für diesen Teil auf Opus zu wechseln.
- Fable-Modelle nur, wenn ich es ausdrücklich sage.

Nach jedem Meilenstein lässt du die Tests laufen, berichtest kurz und sammelst offene Fragen. Bei
Unklarheiten fragst du, statt zu raten. Das gilt vor allem für:

- das FilmCraft-Projektformat,
- die Egress-Allowlist,
- alles, was cutters bestehenden Workflow verändert.
