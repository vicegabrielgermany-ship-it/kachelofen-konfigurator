# Kachelofen-Konfigurator

Aus Einzelfotos historischer Kacheln und Eisenwaren entsteht ein
maßstabsgetreues 2D-Raster, eine Tiefenkarte (Depth Map) und daraus ein
fotorealistisches Verkaufsbild.

---

## Schnellstart

Voraussetzung: Python 3.10 oder neuer (`python3 --version`).

Bequemster Weg:

```bash
cd kachelofen-konfigurator
./start.sh
```

Von Hand:

```bash
cd kachelofen-konfigurator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Der Browser öffnet sich auf `http://localhost:8501`.

Wie der Konfigurator online geht, steht in [DEPLOY.md](DEPLOY.md).

### Zugang und Geheimnisse

| Variable | Wirkung |
| --- | --- |
| `APP_PASSWORT` | Ist sie gesetzt, verlangt die App ein Passwort. Lokal einfach weglassen. |
| `REPLICATE_API_TOKEN` | Zentral hinterlegter Token; das Team muss dann keinen eigenen eintragen. |
| `RENDER_LIMIT_PRO_TAG` | Obergrenze für KI-Renderings pro Tag (Standard 40, `0` = unbegrenzt). |

Lokal lassen sich die drei auch in `.streamlit/secrets.toml` ablegen –
`.streamlit/secrets.toml.beispiel` ist die Vorlage. Die Datei ist von Git
ausgeschlossen und gehört nie ins Repository.

Ohne Oberfläche, nur zum Prüfen der Engine:

```bash
python3 demo.py beispiele     # legt Composite, Tiefenkarte und Kantenkarte ab
python3 -m pytest tests -q    # Tests der handwerklichen Kernregeln
```

Für das KI-Rendering wird ein Replicate-Token gebraucht:

```bash
export REPLICATE_API_TOKEN=r8_…
```

Alternativ lässt er sich direkt im Reiter „KI-Rendering“ eintragen.

---

## Arbeitsablauf in der Oberfläche

**1 · Vorlage aus Foto** – Das Foto des ausgelegten Ofens hochladen, einen
Auswahlrahmen über einen Block legen (bei schrägen Aufnahmen mit den
Entzerrungsreglern geradeziehen), die Reihen zählen. Spaltenzahl und Verband
schlägt die App vor und legt das Raster zur Kontrolle über den entzerrten
Ausschnitt. Daraus entstehen die Sektion, die Flächenkachel und – bei Bedarf –
die Eisenwaren.

**2 · Kacheln & Eisenwaren** – Je Kacheltyp ein möglichst frontales Foto
hochladen. `rembg` stellt den Hintergrund automatisch frei, danach lässt sich
die Kachel um ±10° feinjustieren und auf das Motiv zuschneiden. Eisenwaren
(Schürtür, Aschetür, Wärmefach, Lüftungsgitter) kommen als Mehrfach-Upload
dazu; der Dateiname wird zum Kennzeichen.

**3 · Aufbau** – Sektionen von unten nach oben anlegen: Zeilenzahl,
Zeilenhöhe, Kacheltyp, Halbverband an oder aus. Darunter werden die
Eisenwaren im Raster positioniert.

**4 · Vorschau & Export** – Composite, Tiefenkarte, Kantenkarte und
Silhouettenmaske als PNG, dazu der Materialbedarf als Stückliste.

**5 · KI-Rendering** – Tiefenkarte plus Prompt gehen an Replicate; zurück
kommt der Ofen vor einer Wand, mit Schatten und unveränderter
Rastergeometrie. Hintergrund, Lichteinfall und Lichtstimmung sind wählbar.

---

## Handwerkliche Regeln im Code

| Regel | Umsetzung |
| --- | --- |
| Halbverband, 50 % Fugenversatz | `geometry.zeilen_zellen` – jede ungerade Zeile beginnt bei `kachelbreite/2` |
| Passkacheln an den Rändern | Versetzte Zeilen bekommen automatisch `halb_links` und `halb_rechts`; die Summe ergibt exakt die Ofenbreite |
| Keine vertikalen Halbkacheln | Sektionen zählen ganze Zeilen; die Zeilenhöhe ist der einzige Höhenparameter |
| Unterschiedliche Zeilenhöhen | Jede Sektion (Sockel, Mittelteil, Gesims) hat eigene `zeilenhoehe_mm` |
| Auskragendes Gesims | Sektion mit abweichender Spaltenzahl, automatisch horizontal zentriert |
| Eisenwaren im Raster | `X` Kacheln breit, `Y` Zeilen hoch; das Raster wird maskiert, die Rahmenfuge in Fugenfarbe gefüllt, das Element eingesetzt |

Passkacheln entstehen automatisch aus der Flächenkachel (rechte Hälfte für
den linken Rand, linke Hälfte für den rechten), lassen sich aber durch ein
eigenes Foto ersetzen, wenn echte Passkacheln vorliegen.

---

## Module

| Datei | Aufgabe |
| --- | --- |
| `config.py` | Datenmodell: `OfenKonfiguration`, `Sektion`, `Fixture`; JSON-Import/Export |
| `geometry.py` | Rasterberechnung, Versatz, Passkacheln, Fixture-Boxen, Stückliste, Plausibilitätsprüfung |
| `assets.py` | Modul 1: Freistellen (`rembg`), Perspektivkorrektur, Rotation, Alpha-Crop, Asset-Bibliothek |
| `layout.py` | Modul 2: Composite mit Pillow, Fugenbild, Maskierung, Einsetzen der Eisenwaren |
| `depthmap.py` | Modul 3a: Tiefenkarte, Kantenkarte, Silhouettenmaske |
| `vorlage.py` | Auslege-Foto auswerten: entzerren, Raster und Verband schätzen, Kacheln ausschneiden |
| `render.py` | Modul 3b: Replicate-Adapter mit Schema-Introspektion |
| `auth.py` | Passwortschutz und Geheimnisse (Umgebung oder `secrets.toml`) |
| `kontingent.py` | Tageslimit für KI-Renderings über den zentralen Token |
| `app.py` | Modul 4: Streamlit-Oberfläche |

---

## Tiefenkarte

Konvention wie bei ControlNet-Depth: **hell = nah an der Kamera**, Schwarz
ist Hintergrund. Enthalten sind

* die leichte Wölbung jeder einzelnen Kachel (`kachel_woelbung_mm`),
* die vertieften Fugen (`fugen_tiefe_mm`),
* der Vorsprung von Sockel und Gesims (`tiefe_mm` je Sektion),
* die Tiefe jeder Eisenware (`tiefe_mm` je Fixture).

Die Kantenkarte (weiße Fugenlinien auf Schwarz) ist ein zweites Steuerbild
für ControlNet-Canny/Scribble, falls ein Modell die Fugen zu weich
interpretiert.

### Wand und Boden

Standardmäßig steht der Ofen in der Tiefenkarte nicht frei, sondern vor einer
Wandebene und auf einer Bodenfläche. Das ist der entscheidende Hebel für
realistische Bilder: Schwarz heißt in einer Tiefenkarte „unendlich weit weg" –
aus einem schwarzen Hintergrund baut kein Modell eine Wand mit Schlagschatten.
Damit das Kachelraster trotzdem das stärkste Signal bleibt, bekommen Ofen,
Wand und Boden getrennte Graustufenbereiche (`SzenenProfil`).

Der Regler **Dreiviertelansicht** setzt zusätzlich eine angedeutete
Seitenwange an – die Frontgeometrie bleibt dabei unangetastet und damit
maßhaltig.

---

## KI-Rendering

`render.py` spricht die Replicate-HTTP-API direkt an. Statt die
Eingabefelder eines Modells hart zu verdrahten, liest der Adapter das
OpenAPI-Schema der aktuellen Modellversion und filtert die Nutzlast darauf –
Schema-Änderungen bei Replicate brechen den Aufruf also nicht.

Voreingestellte Modelle:

| Preset | Modell | Einsatz |
| --- | --- | --- |
| `flux-depth-dev` | `black-forest-labs/flux-depth-dev` | Standard, hält die Geometrie sehr sauber |
| `flux-depth-pro` | `black-forest-labs/flux-depth-pro` | finale Verkaufsbilder |
| `sdxl-controlnet-depth` | `lucataco/sdxl-controlnet-depth` | günstig und schnell |

Weitere Modelle lassen sich in `render.PRESETS` ergänzen – nötig sind nur
der Modellname, die Kandidatennamen für das Steuerbild und ein paar
Standardwerte.

Der wichtigste Hebel für Materialtreue ist das Feld **Kachelbeschreibung**
(Farbe, Glasur, Dekor). Die Geometrie kommt aus der Tiefenkarte, die
Oberfläche aus dem Prompt.

---

## Bekannte Grenzen

* Der Konfigurator arbeitet mit der **Frontansicht**. Die Dreiviertelansicht
  ist eine Andeutung in der Tiefenkarte, keine echte 3D-Projektion. Ecköfen
  und runde Säulenöfen brauchen eine eigene Abwicklung.
* Die automatische Rastererkennung ist eine *Hilfe*, keine Messung. Die
  Spaltenzahl trifft sie bei regelmäßigen Blöcken zuverlässig, die Reihenzahl
  bei nur drei oder vier Reihen nicht – dafür gibt es zu wenige Perioden im
  Bild. Deshalb wird die Reihenzahl eingetippt und das erkannte Raster immer
  zur Kontrolle über das Foto gelegt.
* Die Perspektivkorrektur in `assets.entzerren` ist implementiert, wird in
  der Oberfläche aber nicht angeboten – die Kacheln werden ohnehin direkt
  von oben fotografiert. Bei Bedarf lässt sie sich nachrüsten.
* Das Tageskontingent liegt in einer Datei und wird auf Hugging Face Spaces
  bei jedem Neustart des Space zurückgesetzt. Als Schutz gegen Ausrutscher
  reicht das, als Abrechnungsgrundlage nicht – dafür das Replicate-Dashboard
  heranziehen.
* Die Stückliste zählt vollständig verdeckte Kacheln nicht mit; teilweise
  verdeckte werden voll gezählt, weil sie zugeschnitten werden müssen.
