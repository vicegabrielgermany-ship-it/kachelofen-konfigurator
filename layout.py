"""Modul 2 – Layout Engine.

Setzt aus Geometrie + Assets das 2D-Composite des Ofens zusammen:
Kachelraster im Halbverband, Fugenbild, Aussparung und Einsetzen der
Eisenwaren.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from assets import AssetBibliothek, KachelAsset, platzhalter_fixture, platzhalter_kachel
from config import OfenKonfiguration
from geometry import FixtureBox, OfenGeometrie, Zelle, baue_geometrie


@dataclass
class Leinwand:
    """Umrechnung mm -> Pixel für eine konkrete Ofengeometrie."""

    breite_px: int
    hoehe_px: int
    px_pro_mm: float
    hoehe_mm: float

    @classmethod
    def fuer(cls, geo: OfenGeometrie, px_pro_mm: float | None = None) -> "Leinwand":
        s = px_pro_mm or geo.konfiguration.px_pro_mm
        return cls(
            breite_px=max(1, int(round(geo.breite_mm * s))),
            hoehe_px=max(1, int(round(geo.hoehe_mm * s))),
            px_pro_mm=s,
            hoehe_mm=geo.hoehe_mm,
        )

    def rechteck(self, x_mm: float, y_mm: float,
                 b_mm: float, h_mm: float) -> tuple[int, int, int, int]:
        """mm-Rechteck (Ursprung links unten) -> Pixel-Box (links, oben, rechts, unten)."""
        links = int(round(x_mm * self.px_pro_mm))
        rechts = int(round((x_mm + b_mm) * self.px_pro_mm))
        oben = int(round((self.hoehe_mm - (y_mm + h_mm)) * self.px_pro_mm))
        unten = int(round((self.hoehe_mm - y_mm) * self.px_pro_mm))
        return links, oben, max(rechts, links + 1), max(unten, oben + 1)


class KachelCache:
    """Skaliert Kachelbilder nur einmal pro Zielgröße."""

    def __init__(self, bibliothek: AssetBibliothek) -> None:
        self.bibliothek = bibliothek
        self._cache: dict[tuple, Image.Image] = {}
        self._fallback = platzhalter_kachel()

    def hole(self, zelle: Zelle, breite: int, hoehe: int) -> Image.Image:
        schluessel = (zelle.kachel_key, zelle.halb_key, zelle.art, breite, hoehe)
        if schluessel in self._cache:
            return self._cache[schluessel]

        asset = self.bibliothek.kachel(zelle.kachel_key)
        quelle = asset.bild if asset else self._fallback

        if zelle.art != "voll":
            halb_asset = (self.bibliothek.kacheln.get(zelle.halb_key)
                          if zelle.halb_key else None)
            if halb_asset is not None:
                quelle = halb_asset.bild
            else:
                seite = "links" if zelle.art == "halb_links" else "rechts"
                traeger = asset or KachelAsset("fallback", self._fallback)
                quelle = traeger.haelfte(seite)

        bild = quelle.resize((max(breite, 1), max(hoehe, 1)), Image.LANCZOS)
        self._cache[schluessel] = bild
        return bild


def _fixture_bild(bibliothek: AssetBibliothek, box: FixtureBox) -> Image.Image:
    asset = bibliothek.fixture(box.fixture.asset_key)
    if asset is not None:
        return asset.bild
    return platzhalter_fixture(beschriftung=box.fixture.name)


def _stanze(leinwand: Leinwand, boxen: list[FixtureBox]) -> Image.Image:
    """Maske: weiß = Kachelraster bleibt, schwarz = Aussparung."""
    maske = Image.new("L", (leinwand.breite_px, leinwand.hoehe_px), 255)
    zeichner = ImageDraw.Draw(maske)
    for box in boxen:
        x, y, b, h = box.aussparung_mm()
        zeichner.rectangle(leinwand.rechteck(x, y, b, h), fill=0)
    return maske


def rendere_composite(
    konfiguration: OfenKonfiguration,
    bibliothek: AssetBibliothek | None = None,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
    mit_fixtures: bool = True,
) -> Image.Image:
    """Baut das vollständige 2D-Composite des Ofens (RGBA, transparenter Grund)."""
    bibliothek = bibliothek or AssetBibliothek()
    geo = geo or baue_geometrie(konfiguration)
    leinwand = Leinwand.fuer(geo, px_pro_mm)
    fuge_px = konfiguration.fuge_mm * leinwand.px_pro_mm

    bild = Image.new("RGBA", (leinwand.breite_px, leinwand.hoehe_px), (0, 0, 0, 0))

    # 1) Fugenbild: die belegten Flächen einfärben, damit zwischen den
    #    Kacheln der Fugenmörtel sichtbar wird.
    fugen = Image.new("RGBA", bild.size, (0, 0, 0, 0))
    zeichner = ImageDraw.Draw(fugen)
    fugenfarbe = _farbe(konfiguration.fugenfarbe)
    for z in geo.zellen:
        zeichner.rectangle(
            leinwand.rechteck(z.x_mm, z.y_mm, z.breite_mm, z.hoehe_mm),
            fill=fugenfarbe,
        )
    bild.alpha_composite(fugen)

    # 2) Kacheln, jeweils um eine halbe Fuge eingerückt.
    cache = KachelCache(bibliothek)
    for z in geo.zellen:
        l, o, r, u = leinwand.rechteck(
            z.x_mm + konfiguration.fuge_mm / 2.0,
            z.y_mm + konfiguration.fuge_mm / 2.0,
            max(z.breite_mm - konfiguration.fuge_mm, 0.1),
            max(z.hoehe_mm - konfiguration.fuge_mm, 0.1),
        )
        kachel = cache.hole(z, r - l, u - o)
        bild.alpha_composite(kachel, (l, o))

    # 3) Eisenwaren: Raster ausstanzen, Rahmenfuge füllen, Element einsetzen.
    if mit_fixtures and geo.boxen:
        maske = _stanze(leinwand, geo.boxen)
        alpha = bild.getchannel("A")
        alpha = Image.composite(alpha, Image.new("L", bild.size, 0), maske)
        bild.putalpha(alpha)

        # Die Aussparung ist Mörtelfuge, nicht Loch – sonst blitzt der
        # Hintergrund rings um die Tür durch.
        rahmen = Image.new("RGBA", bild.size, (0, 0, 0, 0))
        rahmen_zeichner = ImageDraw.Draw(rahmen)
        for box in geo.boxen:
            x, y, b, h = box.aussparung_mm()
            rahmen_zeichner.rectangle(
                leinwand.rechteck(x, y, b, h), fill=fugenfarbe
            )
        rahmen.alpha_composite(bild)
        bild = rahmen

        for box in geo.boxen:
            l, o, r, u = leinwand.rechteck(
                box.x_mm, box.y_mm, box.breite_mm, box.hoehe_mm
            )
            element = _fixture_bild(bibliothek, box).resize(
                (max(r - l, 1), max(u - o, 1)), Image.LANCZOS
            )
            bild.alpha_composite(element, (l, o))

    return bild


def _farbe(wert: str) -> tuple[int, int, int, int]:
    wert = wert.strip().lstrip("#")
    if len(wert) == 3:
        wert = "".join(c * 2 for c in wert)
    if len(wert) != 6:
        return (74, 65, 58, 255)
    return (int(wert[0:2], 16), int(wert[2:4], 16), int(wert[4:6], 16), 255)


def auf_hintergrund(bild: Image.Image,
                    farbe: tuple[int, int, int] = (244, 241, 236)) -> Image.Image:
    """Legt das Composite für die Vorschau auf eine deckende Fläche."""
    grund = Image.new("RGBA", bild.size, (*farbe, 255))
    grund.alpha_composite(bild)
    return grund.convert("RGB")
