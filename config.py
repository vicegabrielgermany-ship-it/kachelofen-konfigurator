"""Datenmodell für die Ofenkonfiguration.

Alle Maße in Millimetern. Koordinatenursprung ist die linke untere Ecke
der Ofenfront, y-Achse zeigt nach oben (handwerkliche Denkweise: es wird
von unten nach oben aufgebaut).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Literal

# --------------------------------------------------------------------------
# Kacheltypen (Schlüssel im Asset-Verzeichnis)
# --------------------------------------------------------------------------
KACHEL_FLAECHE = "flaeche"
KACHEL_HALB = "halb"
KACHEL_SOCKEL = "sockel"
KACHEL_GESIMS = "gesims"
KACHEL_ECKE = "ecke"

ZellenArt = Literal["voll", "halb_links", "halb_rechts"]


@dataclass
class Sektion:
    """Ein horizontaler Abschnitt des Ofens (z. B. Sockel, Mittelteil, Gesims).

    Sektionen werden von unten nach oben in der Reihenfolge der Liste
    gestapelt. Jede Sektion besteht ausschließlich aus *vollen* Kachelreihen
    – vertikale Halbkacheln gibt es handwerklich nicht.
    """

    name: str
    zeilen: int = 4
    zeilenhoehe_mm: float = 220.0
    kachel_key: str = KACHEL_FLAECHE
    # Passkachel-Asset für die Ränder versetzter Zeilen. None -> automatisch
    # aus der Flächenkachel halbiert.
    halb_key: str | None = None
    # Halbverband aktiv? Gesimse und Sockel laufen oft ohne Versatz durch.
    versatz: bool = True
    # Abweichende Spaltenzahl (z. B. auskragendes Gesims). None -> global.
    spalten: int | None = None
    # Vorsprung gegenüber der Ofenfront in mm (positiv = tritt hervor).
    # Steuert nur die Tiefenkarte, nicht das 2D-Composite.
    tiefe_mm: float = 0.0

    def wirksame_spalten(self, global_spalten: int) -> int:
        return self.spalten if self.spalten else global_spalten

    def hoehe_mm(self) -> float:
        return self.zeilen * self.zeilenhoehe_mm


@dataclass
class Fixture:
    """Eine Eisenware (Schürtür, Aschetür, Wärmefach, Lüftungsgitter …).

    Verankert wird sie in einer Sektion: `spalte` ist die linke Kante in
    Kachelbreiten ab der linken Ofenkante, `zeile` die untere Kante in
    Zeilen ab der Unterkante der Sektion. Beide dürfen Nachkommastellen
    haben (0.5 = auf Fugenversatz gesetzt).
    """

    name: str
    asset_key: str
    sektion: str
    spalte: float = 1.0
    zeile: float = 0.0
    breite_kacheln: float = 2.0
    hoehe_zeilen: float = 2.0
    # Tiefe gegenüber der Ofenfront in mm (negativ = zurückspringend).
    tiefe_mm: float = -25.0
    # Umlaufende Fuge/Rahmenluft um das Element in mm.
    randfuge_mm: float = 3.0


@dataclass
class OfenKonfiguration:
    """Vollständige Beschreibung eines Ofens."""

    name: str = "Neuer Kachelofen"
    kachelbreite_mm: float = 220.0
    spalten: int = 4
    fuge_mm: float = 3.0
    fugenfarbe: str = "#4a413a"
    # Auflösung des Composites
    px_pro_mm: float = 1.2

    sektionen: list[Sektion] = field(
        default_factory=lambda: [
            Sektion("Sockel", zeilen=1, zeilenhoehe_mm=150.0,
                    kachel_key=KACHEL_SOCKEL, versatz=False, tiefe_mm=18.0),
            Sektion("Mittelteil", zeilen=5, zeilenhoehe_mm=220.0,
                    kachel_key=KACHEL_FLAECHE, versatz=True),
            Sektion("Gesims", zeilen=1, zeilenhoehe_mm=130.0,
                    kachel_key=KACHEL_GESIMS, versatz=False, tiefe_mm=25.0),
        ]
    )
    fixtures: list[Fixture] = field(default_factory=list)

    # ---------------- abgeleitete Maße ----------------

    @property
    def breite_mm(self) -> float:
        return self.spalten * self.kachelbreite_mm

    @property
    def hoehe_mm(self) -> float:
        return sum(s.hoehe_mm() for s in self.sektionen)

    def max_breite_mm(self) -> float:
        """Breite inkl. auskragender Sektionen (Gesims/Sockel)."""
        return max(
            [self.breite_mm]
            + [s.wirksame_spalten(self.spalten) * self.kachelbreite_mm
               for s in self.sektionen]
        )

    def sektion(self, name: str) -> Sektion:
        for s in self.sektionen:
            if s.name == name:
                return s
        raise KeyError(f"Sektion '{name}' existiert nicht")

    def sektion_unterkante_mm(self, name: str) -> float:
        y = 0.0
        for s in self.sektionen:
            if s.name == name:
                return y
            y += s.hoehe_mm()
        raise KeyError(f"Sektion '{name}' existiert nicht")

    # ---------------- Serialisierung ----------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "OfenKonfiguration":
        d = dict(d)
        d["sektionen"] = [Sektion(**s) for s in d.get("sektionen", [])]
        d["fixtures"] = [Fixture(**f) for f in d.get("fixtures", [])]
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> "OfenKonfiguration":
        return cls.from_dict(json.loads(s))
