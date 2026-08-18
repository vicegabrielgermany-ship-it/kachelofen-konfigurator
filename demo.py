"""Demo-Durchlauf ohne Oberfläche: erzeugt einen Beispielofen als PNG-Satz.

    python3 demo.py [zielordner]
"""

from __future__ import annotations

import sys
from pathlib import Path

from assets import (
    AssetBibliothek, platzhalter_fixture, platzhalter_kachel,
)
from config import Fixture, OfenKonfiguration, Sektion
from depthmap import rendere_depthmap, rendere_kantenkarte
from geometry import baue_geometrie, stueckliste
from layout import auf_hintergrund, rendere_composite


def beispiel_konfiguration() -> OfenKonfiguration:
    return OfenKonfiguration(
        name="Biedermeier-Musterofen",
        kachelbreite_mm=220.0,
        spalten=4,
        fuge_mm=3.0,
        px_pro_mm=1.1,
        sektionen=[
            Sektion("Sockel", zeilen=1, zeilenhoehe_mm=160.0,
                    kachel_key="sockel", versatz=False, tiefe_mm=16.0),
            Sektion("Mittelteil", zeilen=6, zeilenhoehe_mm=220.0,
                    kachel_key="flaeche", versatz=True),
            Sektion("Gesims", zeilen=1, zeilenhoehe_mm=140.0,
                    kachel_key="gesims", versatz=False, spalten=5,
                    tiefe_mm=28.0),
        ],
        fixtures=[
            Fixture("Schürtür", "schuertuer", sektion="Mittelteil",
                    spalte=1.0, zeile=0.0, breite_kacheln=2.0,
                    hoehe_zeilen=2.0, tiefe_mm=-28.0),
            Fixture("Aschetür", "aschetuer", sektion="Sockel",
                    spalte=1.5, zeile=0.0, breite_kacheln=1.0,
                    hoehe_zeilen=1.0, tiefe_mm=-18.0),
            Fixture("Wärmefach", "waermefach", sektion="Mittelteil",
                    spalte=1.0, zeile=3.0, breite_kacheln=2.0,
                    hoehe_zeilen=1.0, tiefe_mm=-34.0),
        ],
    )


def beispiel_bibliothek() -> AssetBibliothek:
    b = AssetBibliothek()
    b.setze_kachel("flaeche", platzhalter_kachel(440, 440, (84, 118, 94)))
    b.setze_kachel("sockel", platzhalter_kachel(440, 320, (62, 88, 72)))
    b.setze_kachel("gesims", platzhalter_kachel(440, 280, (108, 142, 116)))
    b.setze_fixture("schuertuer", platzhalter_fixture(420, 420))
    b.setze_fixture("aschetuer", platzhalter_fixture(220, 200))
    b.setze_fixture("waermefach", platzhalter_fixture(420, 210))
    return b


def main() -> None:
    ziel = Path(sys.argv[1] if len(sys.argv) > 1 else "beispiele")
    ziel.mkdir(parents=True, exist_ok=True)

    k = beispiel_konfiguration()
    bibliothek = beispiel_bibliothek()
    geo = baue_geometrie(k)

    auf_hintergrund(rendere_composite(k, bibliothek, geo)).save(
        ziel / "01_composite.png")
    rendere_depthmap(k, geo).save(ziel / "02_depthmap.png")
    rendere_kantenkarte(k, geo).save(ziel / "03_kanten.png")
    (ziel / "ofen.json").write_text(k.to_json(), encoding="utf-8")

    print(f"Ofen: {geo.breite_mm:.0f} × {geo.hoehe_mm:.0f} mm, "
          f"{len(geo.zellen)} Rasterplätze, {len(geo.boxen)} Eisenwaren")
    for zeile, anzahl in stueckliste(geo).items():
        print(f"  {anzahl:>3} ×  {zeile}")
    for w in geo.warnungen():
        print(f"  ! {w}")
    print(f"Ausgabe in {ziel.resolve()}")


if __name__ == "__main__":
    main()
