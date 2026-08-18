# Online stellen (Streamlit Community Cloud)

Ziel: eine URL, die du im Team teilen kannst, hinter einem gemeinsamen
Passwort. Kostenlos, kein Terminal – alles im Browser.

Gebraucht werden zwei Konten, beide kostenlos und ohne Zahlungsdaten:
**GitHub** (dort liegt der Code) und **Streamlit Community Cloud** (dort
läuft die App; die Anmeldung geht über GitHub).

> **Warum nicht Hugging Face?** Dort sind seit 2026 nur noch statische
> Seiten kostenlos – alles, was Rechenleistung braucht, setzt einen
> PRO-Plan voraus. Community Cloud ist für Streamlit-Apps gemacht und
> bleibt kostenlos.

---

## Schritt 1 – Repository auf GitHub anlegen

1. <https://github.com/new>
2. **Repository name**: `kachelofen-konfigurator`
3. **Public** oder **Private** – beides funktioniert, Community Cloud
   darf auch private Repositories lesen.
4. **Create repository**

---

## Schritt 2 – Dateien hochladen

Im leeren Repository auf **uploading an existing file** (oder
`.../upload/main`).

Alle Dateien aus dem Ordner `kachelofen-konfigurator` in das Browserfenster
ziehen. Weil alle Module flach nebeneinander liegen, ist das ein einziger
Vorgang – es gibt keine Unterordner, auf die man achten müsste.

Nötig sind:

```
app.py            assets.py     auth.py       config.py
demo.py           depthmap.py   geometry.py   kontingent.py
layout.py         render.py     vorlage.py
requirements.txt  README.md
```

Der Ordner `tests` darf mit, muss aber nicht. `beispiele`, `packages.txt`
und `start.sh` werden online nicht gebraucht.

Unten auf **Commit changes** klicken.

---

## Schritt 3 – App bei Community Cloud starten

1. <https://share.streamlit.io> → **Sign in with GitHub** → Zugriff
   erlauben
2. **Create app** → **Deploy a public app from GitHub**
3. Eintragen:
   * **Repository**: `DEIN-NAME/kachelofen-konfigurator`
   * **Branch**: `main`
   * **Main file path**: `app.py`
   * **App URL**: frei wählbar, z. B. `kachelofen-konfigurator`
4. Vor dem Deploy auf **Advanced settings** → **Secrets** und dort
   eintragen:

```toml
APP_PASSWORT = "dein-team-passwort"
RENDER_LIMIT_PRO_TAG = "40"
```

Sobald ein Replicate-Token vorliegt, kommt eine dritte Zeile dazu:

```toml
REPLICATE_API_TOKEN = "r8_..."
```

5. **Deploy**

Der erste Start dauert zwei bis drei Minuten. Danach zeigt die App die
Passwortabfrage.

---

## Schritt 4 – Im Team teilen

Link und Passwort weitergeben. Nichts zu installieren, läuft auch auf dem
Tablet in der Werkstatt.

---

## Betrieb

**Die App schläft ein.** Nach einigen Tagen ohne Zugriff pausiert sie. Der
erste Aufruf danach weckt sie in etwa einer Minute wieder auf.

**Kein dauerhafter Speicher.** Hochgeladene Fotos und Konfigurationen leben
nur in der laufenden Sitzung. Deshalb der Knopf *„Konfiguration speichern
(.json)"* in der Seitenleiste – damit sichert jeder seinen Ofen lokal und
lädt ihn beim nächsten Mal wieder hoch. Auch der Renderzähler beginnt nach
einem Neustart wieder bei null.

**Änderungen ausrollen:** Geänderte Datei auf GitHub öffnen, Stift-Symbol,
Inhalt ersetzen, **Commit** – oder die Datei im Repository erneut
hochladen. Community Cloud startet die App danach automatisch neu.

**Passwort oder Token ändern:** In Community Cloud beim App-Eintrag auf
**⋮ → Settings → Secrets**. Änderungen greifen nach einem Neustart.

**Freistellen (rembg) ist online abgeschaltet.** Community Cloud hat dafür
zu wenig Arbeitsspeicher. In `requirements.txt` stehen die beiden Zeilen
auskommentiert bereit; lokal lassen sie sich einschalten. Für den üblichen
Weg – Kacheln aus dem Auslege-Foto ausschneiden – wird rembg nicht
gebraucht.

---

## Replicate (für das KI-Rendering)

Ohne Token funktionieren die Reiter 1 bis 4 vollständig; nur das
Schlussbild fehlt.

1. Konto auf <https://replicate.com> anlegen
2. Zahlungsmittel hinterlegen (Abrechnung nach Verbrauch, kein Abo)
3. Unter <https://replicate.com/account/api-tokens> einen Token erzeugen
4. Den Token als `REPLICATE_API_TOKEN` in die Secrets eintragen

Kosten: grob 0,03–0,06 € je Bild mit `flux-depth-dev`, mehr mit
`flux-depth-pro`. Zusätzlich zum Tageskontingent der App lässt sich unter
<https://replicate.com/account/billing> ein Ausgabenlimit setzen.

---

## Wenn etwas klemmt

| Symptom | Ursache und Abhilfe |
| --- | --- |
| „Error installing requirements" | Eine Zeile in `requirements.txt` passt nicht zur Python-Version der Cloud. Fehlermeldung im Log lesen, betroffene Zeile lockern. |
| `ModuleNotFoundError` | Eine der Moduldateien wurde nicht mit hochgeladen. Im Repository prüfen, ob alle elf `.py`-Dateien da sind. |
| Rote Warnung „nicht passwortgeschützt" | `APP_PASSWORT` fehlt in den Secrets. |
| „Kein Replicate-Token gesetzt" | `REPLICATE_API_TOKEN` fehlt oder ist abgelaufen. |
| App wird beim Rendern beendet | Auflösung in der Seitenleiste („Pixel je mm") senken – das ist der größte Speicherfresser. |
| Team meldet „Seite lädt ewig" | App schlief. Einmal warten, danach ist sie wach. |
