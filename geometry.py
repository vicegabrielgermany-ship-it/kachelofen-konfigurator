"""Geometrie-Engine: rechnet aus einer OfenKonfiguration das Kachelraster.

Handwerkliche Kernregeln, die hier abgebildet sind:

1. Halbverband: jede ungerade Zeile ist um exakt 50 % einer Kachelbreite
   horizontal versetzt.
2. An den Rändern versetzter Zeilen werden automatisch Passkacheln
   (Halbkacheln) gesetzt, damit die Außenkante bündig bleibt.
3. Es gibt keine vertikalen Halbkacheln – nur volle Zeilen.
4. Sektionen (Sockel, Mittelteil, Gesims) dürfen unterschiedliche
   Zeilenhöhen und Spaltenzahlen haben; abweichend breite Sektionen werden
   horizontal zentriert.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import Fixture, OfenKonfiguration, Sektion, ZellenArt


@dataclass(frozen=True)
class Zelle:
    """Ein Kachelplatz im Raster. Maße in mm, Ursprung links unten."""

    x_mm: float
    y_mm: float
    breite_mm: float
    hoehe_mm: float
    kachel_key: str
    halb_key: str | None
    art: ZellenArt
    sektion: str
    zeile_global: int      # laufender Zeilenindex über den ganzen Ofen, von unten
    zeile_in_sektion: int
    spalte: int
    tiefe_mm: float        # Vorsprung der Sektion

    @property
    def rechts_mm(self) -> float:
        return self.x_mm + self.breite_mm

    @property
    def oben_mm(self) -> float:
        return self.y_mm + self.hoehe_mm


@dataclass(frozen=True)
class FixtureBox:
    """Ein platziertes Sonderelement in mm-Koordinaten."""

    fixture: Fixture
    x_mm: float
    y_mm: float
    breite_mm: float
    hoehe_mm: float

    @property
    def rechts_mm(self) -> float:
        return self.x_mm + self.breite_mm

    @property
    def oben_mm(self) -> float:
        return self.y_mm + self.hoehe_mm

    def aussparung_mm(self) -> tuple[float, float, float, float]:
        """Rechteck inkl. Randfuge, das aus dem Kachelraster gestanzt wird."""
        r = self.fixture.randfuge_mm
        return (self.x_mm - r, self.y_mm - r,
                self.breite_mm + 2 * r, self.hoehe_mm + 2 * r)


@dataclass
class OfenGeometrie:
    konfiguration: OfenKonfiguration
    zellen: list[Zelle]
    boxen: list[FixtureBox]
    breite_mm: float
    hoehe_mm: float
    x_offset_mm: float   # Verschiebung der Normalbreite in der Gesamtleinwand

    def zellen_der_sektion(self, name: str) -> list[Zelle]:
        return [z for z in self.zellen if z.sektion == name]

    def warnungen(self) -> list[str]:
        """Plausibilitätsprüfungen für den Bediener."""
        w: list[str] = []
        k = self.konfiguration
        if k.spalten < 1:
            w.append("Mindestens eine Spalte erforderlich.")
        if not k.sektionen:
            w.append("Der Ofen hat keine Sektion.")
        for s in k.sektionen:
            if s.zeilen < 1:
                w.append(f"Sektion „{s.name}“ hat keine Zeile – "
                         "es gibt keine halben Zeilen.")
        namen = [s.name for s in k.sektionen]
        if len(set(namen)) != len(namen):
            w.append("Sektionsnamen müssen eindeutig sein.")

        for box in self.boxen:
            f = box.fixture
            if box.x_mm < -0.01 or box.rechts_mm > self.breite_mm + 0.01:
                w.append(f"„{f.name}“ ragt seitlich über den Ofen hinaus.")
            if box.y_mm < -0.01 or box.oben_mm > self.hoehe_mm + 0.01:
                w.append(f"„{f.name}“ ragt über die Ofenhöhe hinaus.")
        for a, b in _paare(self.boxen):
            if _ueberlappt(a, b):
                w.append(f"„{a.fixture.name}“ und „{b.fixture.name}“ "
                         "überlappen sich.")
        return w


def _paare(items: list) -> list[tuple]:
    return [(items[i], items[j])
            for i in range(len(items)) for j in range(i + 1, len(items))]


def _ueberlappt(a: FixtureBox, b: FixtureBox) -> bool:
    return not (a.rechts_mm <= b.x_mm or b.rechts_mm <= a.x_mm
                or a.oben_mm <= b.y_mm or b.oben_mm <= a.y_mm)


# --------------------------------------------------------------------------


def zeilen_zellen(
    sektion: Sektion,
    zeile_in_sektion: int,
    zeile_global: int,
    y_mm: float,
    kachelbreite_mm: float,
    spalten: int,
    x_basis_mm: float,
) -> list[Zelle]:
    """Baut eine einzelne Kachelzeile inkl. Passkacheln an den Rändern."""
    tw = kachelbreite_mm
    th = sektion.zeilenhoehe_mm
    versetzt = sektion.versatz and (zeile_global % 2 == 1)

    def mach(x: float, b: float, art: ZellenArt, spalte: int) -> Zelle:
        return Zelle(
            x_mm=x_basis_mm + x, y_mm=y_mm, breite_mm=b, hoehe_mm=th,
            kachel_key=sektion.kachel_key, halb_key=sektion.halb_key,
            art=art, sektion=sektion.name, zeile_global=zeile_global,
            zeile_in_sektion=zeile_in_sektion, spalte=spalte,
            tiefe_mm=sektion.tiefe_mm,
        )

    if not versetzt:
        return [mach(i * tw, tw, "voll", i) for i in range(spalten)]

    # Versetzte Zeile: das Muster ist um tw/2 nach rechts geschoben.
    # Links sieht man die rechte Hälfte der überstehenden Kachel,
    # rechts die linke Hälfte – zusammen exakt spalten * tw.
    zellen = [mach(0.0, tw / 2.0, "halb_links", 0)]
    for i in range(spalten - 1):
        zellen.append(mach(tw / 2.0 + i * tw, tw, "voll", i + 1))
    zellen.append(
        mach(tw / 2.0 + (spalten - 1) * tw, tw / 2.0, "halb_rechts", spalten)
    )
    return zellen


def baue_geometrie(k: OfenKonfiguration) -> OfenGeometrie:
    """Rechnet die vollständige Rastergeometrie eines Ofens aus."""
    gesamt_breite = k.max_breite_mm()
    x_offset = (gesamt_breite - k.breite_mm) / 2.0

    zellen: list[Zelle] = []
    y = 0.0
    zeile_global = 0

    for sektion in k.sektionen:
        spalten = sektion.wirksame_spalten(k.spalten)
        sektion_breite = spalten * k.kachelbreite_mm
        x_basis = (gesamt_breite - sektion_breite) / 2.0

        for r in range(sektion.zeilen):
            zellen.extend(
                zeilen_zellen(
                    sektion=sektion,
                    zeile_in_sektion=r,
                    zeile_global=zeile_global,
                    y_mm=y,
                    kachelbreite_mm=k.kachelbreite_mm,
                    spalten=spalten,
                    x_basis_mm=x_basis,
                )
            )
            y += sektion.zeilenhoehe_mm
            zeile_global += 1

    boxen = [_box(k, f, x_offset) for f in k.fixtures]

    return OfenGeometrie(
        konfiguration=k,
        zellen=zellen,
        boxen=boxen,
        breite_mm=gesamt_breite,
        hoehe_mm=k.hoehe_mm,
        x_offset_mm=x_offset,
    )


def _box(k: OfenKonfiguration, f: Fixture, x_offset_mm: float) -> FixtureBox:
    sektion = k.sektion(f.sektion)
    unterkante = k.sektion_unterkante_mm(f.sektion)
    spalten = sektion.wirksame_spalten(k.spalten)
    sektion_breite = spalten * k.kachelbreite_mm
    gesamt_breite = k.max_breite_mm()
    x_basis = (gesamt_breite - sektion_breite) / 2.0

    return FixtureBox(
        fixture=f,
        x_mm=x_basis + f.spalte * k.kachelbreite_mm,
        y_mm=unterkante + f.zeile * sektion.zeilenhoehe_mm,
        breite_mm=f.breite_kacheln * k.kachelbreite_mm,
        hoehe_mm=f.hoehe_zeilen * sektion.zeilenhoehe_mm,
    )


def stueckliste(geo: OfenGeometrie) -> dict[str, int]:
    """Zählt, wie viele Kacheln welchen Typs gebraucht werden.

    Vollständig unter einer Eisenware verschwindende Kacheln werden nicht
    mitgezählt, teilweise verdeckte schon (sie müssen zugeschnitten werden).
    """
    zaehler: dict[str, int] = {}
    for z in geo.zellen:
        if _vollstaendig_verdeckt(z, geo.boxen):
            continue
        art = "Passkachel" if z.art != "voll" else "Kachel"
        key = f"{z.sektion} – {art} ({z.kachel_key})"
        zaehler[key] = zaehler.get(key, 0) + 1
    return dict(sorted(zaehler.items()))


def _vollstaendig_verdeckt(z: Zelle, boxen: list[FixtureBox]) -> bool:
    for b in boxen:
        bx, by, bw, bh = b.aussparung_mm()
        if (bx <= z.x_mm and by <= z.y_mm
                and bx + bw >= z.rechts_mm and by + bh >= z.oben_mm):
            return True
    return False
