"""Modul 3a – Tiefenkarte und Kantenkarte.

Zwei Steuerbilder für die Bild-KI:

* `rendere_depthmap`  – Graustufen-Tiefenkarte nach ControlNet-Konvention
  (hell = nah an der Kamera, schwarz = Hintergrund). Enthält die Wölbung
  jeder einzelnen Kachel, die vertieften Fugen, den Vorsprung von Sockel
  und Gesims sowie die Tiefe der Eisenwaren.
* `rendere_kantenkarte` – weiße Fugen- und Kantenlinien auf Schwarz, für
  ControlNet-Canny/Scribble oder als zusätzliche Struktursicherung.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from config import OfenKonfiguration
from geometry import OfenGeometrie, baue_geometrie
from layout import Leinwand


@dataclass
class TiefenProfil:
    """Steuerparameter der Tiefenkarte, alle in Millimetern."""

    kachel_woelbung_mm: float = 7.0
    fugen_tiefe_mm: float = 5.0
    # Graustufenbereich, auf den die Ofentiefe abgebildet wird.
    grau_min: int = 96
    grau_max: int = 255
    hintergrund_grau: int = 12
    # Weichzeichnung in Pixeln – vermeidet harte Treppenkanten im ControlNet.
    weichzeichnen_px: float = 1.2


def _dome(breite: int, hoehe: int) -> np.ndarray:
    """Normierte Kachelwölbung: 1.0 in der Mitte, 0.0 am Rand."""
    y, x = np.mgrid[0:hoehe, 0:breite]
    nx = (x + 0.5) / breite * 2.0 - 1.0
    ny = (y + 0.5) / hoehe * 2.0 - 1.0
    r = np.maximum(np.abs(nx), np.abs(ny))
    return np.clip(1.0 - r ** 3, 0.0, 1.0)


class _DomeCache:
    def __init__(self) -> None:
        self._c: dict[tuple[int, int], np.ndarray] = {}

    def hole(self, b: int, h: int) -> np.ndarray:
        if (b, h) not in self._c:
            self._c[(b, h)] = _dome(b, h)
        return self._c[(b, h)]


def tiefenfeld(
    konfiguration: OfenKonfiguration,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
    profil: TiefenProfil | None = None,
) -> tuple[np.ndarray, np.ndarray, Leinwand]:
    """Rechnet das rohe Tiefenfeld in mm plus Ofenmaske.

    Rückgabe: (tiefe_mm, maske_bool, leinwand)
    """
    geo = geo or baue_geometrie(konfiguration)
    profil = profil or TiefenProfil()
    leinwand = Leinwand.fuer(geo, px_pro_mm)

    H, B = leinwand.hoehe_px, leinwand.breite_px
    tiefe = np.zeros((H, B), dtype=np.float32)
    maske = np.zeros((H, B), dtype=bool)

    fuge = konfiguration.fuge_mm
    cache = _DomeCache()

    for z in geo.zellen:
        # a) Zellenfläche inkl. Fuge auf Sektionstiefe minus Fugentiefe
        l, o, r, u = leinwand.rechteck(z.x_mm, z.y_mm, z.breite_mm, z.hoehe_mm)
        l, o = max(l, 0), max(o, 0)
        r, u = min(r, B), min(u, H)
        if r <= l or u <= o:
            continue
        tiefe[o:u, l:r] = z.tiefe_mm - profil.fugen_tiefe_mm
        maske[o:u, l:r] = True

        # b) Kachelkörper mit Wölbung
        kl, ko, kr, ku = leinwand.rechteck(
            z.x_mm + fuge / 2.0,
            z.y_mm + fuge / 2.0,
            max(z.breite_mm - fuge, 0.1),
            max(z.hoehe_mm - fuge, 0.1),
        )
        kl, ko = max(kl, 0), max(ko, 0)
        kr, ku = min(kr, B), min(ku, H)
        if kr <= kl or ku <= ko:
            continue
        dome = cache.hole(kr - kl, ku - ko)
        tiefe[ko:ku, kl:kr] = z.tiefe_mm + profil.kachel_woelbung_mm * dome

    # c) Eisenwaren
    for box in geo.boxen:
        ax, ay, ab, ah = box.aussparung_mm()
        al, ao, ar, au = leinwand.rechteck(ax, ay, ab, ah)
        al, ao = max(al, 0), max(ao, 0)
        ar, au = min(ar, B), min(au, H)
        if ar > al and au > ao:
            tiefe[ao:au, al:ar] = box.fixture.tiefe_mm - profil.fugen_tiefe_mm
            maske[ao:au, al:ar] = True

        l, o, r, u = leinwand.rechteck(
            box.x_mm, box.y_mm, box.breite_mm, box.hoehe_mm
        )
        l, o = max(l, 0), max(o, 0)
        r, u = min(r, B), min(u, H)
        if r > l and u > o:
            # Türen und Gitter sind selbst leicht plastisch (Rahmenprofil).
            dome = cache.hole(r - l, u - o)
            tiefe[o:u, l:r] = box.fixture.tiefe_mm + 3.0 * dome
            maske[o:u, l:r] = True

    return tiefe, maske, leinwand


def rendere_depthmap(
    konfiguration: OfenKonfiguration,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
    profil: TiefenProfil | None = None,
) -> Image.Image:
    """Graustufen-Tiefenkarte (Modus "L"), hell = nah."""
    profil = profil or TiefenProfil()
    tiefe, maske, _ = tiefenfeld(konfiguration, geo, px_pro_mm, profil)

    grau = np.full(tiefe.shape, float(profil.hintergrund_grau), dtype=np.float32)
    if maske.any():
        werte = tiefe[maske]
        lo, hi = float(werte.min()), float(werte.max())
        spanne = max(hi - lo, 1e-6)
        norm = (tiefe - lo) / spanne
        grau[maske] = (profil.grau_min
                       + norm[maske] * (profil.grau_max - profil.grau_min))

    bild = Image.fromarray(np.clip(grau, 0, 255).astype(np.uint8), mode="L")
    if profil.weichzeichnen_px > 0:
        bild = bild.filter(ImageFilter.GaussianBlur(profil.weichzeichnen_px))
    return bild


def rendere_maske(
    konfiguration: OfenKonfiguration,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
) -> Image.Image:
    """Silhouette des Ofens als Schwarz-Weiß-Maske (für Inpainting)."""
    _, maske, _ = tiefenfeld(konfiguration, geo, px_pro_mm)
    return Image.fromarray((maske * 255).astype(np.uint8), mode="L")


def rendere_kantenkarte(
    konfiguration: OfenKonfiguration,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
    fugen_grau: int = 170,
    kanten_grau: int = 255,
) -> Image.Image:
    """Weiße Fugen- und Kantenlinien auf schwarzem Grund."""
    geo = geo or baue_geometrie(konfiguration)
    leinwand = Leinwand.fuer(geo, px_pro_mm)

    bild = Image.new("L", (leinwand.breite_px, leinwand.hoehe_px), 0)
    zeichner = ImageDraw.Draw(bild)
    staerke = max(1, int(round(konfiguration.fuge_mm * leinwand.px_pro_mm * 0.8)))

    for z in geo.zellen:
        zeichner.rectangle(
            leinwand.rechteck(z.x_mm, z.y_mm, z.breite_mm, z.hoehe_mm),
            outline=fugen_grau, width=staerke,
        )

    # Sektionsfugen und Außenkontur kräftiger
    y = 0.0
    for s in konfiguration.sektionen:
        spalten = s.wirksame_spalten(konfiguration.spalten)
        breite = spalten * konfiguration.kachelbreite_mm
        x = (geo.breite_mm - breite) / 2.0
        zeichner.rectangle(
            leinwand.rechteck(x, y, breite, s.hoehe_mm()),
            outline=kanten_grau, width=max(staerke, 2),
        )
        y += s.hoehe_mm()

    for box in geo.boxen:
        zeichner.rectangle(
            leinwand.rechteck(box.x_mm, box.y_mm, box.breite_mm, box.hoehe_mm),
            outline=kanten_grau, width=max(staerke, 2),
        )

    return bild


# --------------------------------------------------------------------------
# Szene: Wand, Boden, Blickwinkel
# --------------------------------------------------------------------------

@dataclass
class SzenenProfil:
    """Bettet den Ofen in eine Raumgeometrie ein.

    Ohne Szene steht der Ofen für die Bild-KI in einem schwarzen Nichts –
    Schwarz heißt in einer Tiefenkarte „unendlich weit weg", und daraus baut
    kein Modell eine Wand mit Schlagschatten. Mit einer Wandebene hinter dem
    Ofen und einer Bodenfläche davor bekommt das Modell die Geometrie, die es
    für Schatten und einen sicheren Stand braucht.

    Damit das Kachelraster das stärkste Signal im Bild bleibt, werden Ofen,
    Wand und Boden getrennt auf Graustufen abgebildet: der Ofen bekommt den
    hellen, weiten Bereich, Wand und Boden bleiben gedämpft. Physikalisch
    ist der Boden direkt vor der Kamera zwar näher als der Ofen – für die
    Bildsteuerung ist eine klare Freistellung aber mehr wert als die exakte
    metrische Wahrheit.

    Alle Maße in Millimetern, gemessen ab der Ofenfront.
    """

    aktiv: bool = True
    rand_seiten_mm: float = 800.0     # Wand links und rechts neben dem Ofen
    rand_oben_mm: float = 500.0       # Wand über dem Ofen
    boden_hoehe_mm: float = 850.0     # sichtbarer Boden vor dem Ofen
    wand_abstand_mm: float = 350.0    # Wand hinter der Ofenfront
    # Dreiviertelansicht: 0 = streng frontal, 0.45 = deutlich angeschnitten
    blickwinkel: float = 0.0
    ofentiefe_mm: float = 550.0       # Bautiefe, nur für die Seitenwange
    # Graustufen der Kulisse
    wand_grau: int = 46
    boden_grau_hinten: int = 52
    boden_grau_vorn: int = 112
    ofen_grau_min: int = 150
    ofen_grau_max: int = 255
    max_kante_px: int = 1536


def _ofen_grau(tiefe: np.ndarray, maske: np.ndarray,
               szene: SzenenProfil) -> np.ndarray:
    """Bildet nur das Ofenfeld auf seinen eigenen Graustufenbereich ab."""
    grau = np.zeros(tiefe.shape, dtype=np.float32)
    if not maske.any():
        return grau
    werte = tiefe[maske]
    lo, hi = float(werte.min()), float(werte.max())
    spanne = max(hi - lo, 1e-6)
    norm = (tiefe - lo) / spanne
    grau[maske] = (szene.ofen_grau_min
                   + norm[maske] * (szene.ofen_grau_max - szene.ofen_grau_min))
    return grau


def _kulisse(hoehe: int, breite: int, boden_px: int,
             szene: SzenenProfil) -> np.ndarray:
    """Wand mit anschließender Bodenfläche als Graustufenbild."""
    grau = np.full((hoehe, breite), float(szene.wand_grau), dtype=np.float32)
    if boden_px > 0:
        lauf = np.linspace(0.0, 1.0, boden_px, dtype=np.float32)[:, None]
        grau[hoehe - boden_px:, :] = (
            szene.boden_grau_hinten
            + lauf * (szene.boden_grau_vorn - szene.boden_grau_hinten)
        )
    return grau


def _seitenwange(grau: np.ndarray, maske: np.ndarray, y0: int, x0: int,
                 ofen_breite: int, ofen_hoehe: int,
                 szene: SzenenProfil, px_pro_mm: float) -> None:
    """Deutet die rechte Seitenwange für eine Dreiviertelansicht an.

    Keine echte Perspektivrechnung: ein nach hinten abdunkelndes Band, das
    oben und unten leicht einzieht. Es sagt dem Modell „hier ist Körper,
    keine Kulisse", ohne die Frontgeometrie anzutasten – das Kachelraster
    der Front bleibt exakt.
    """
    band = int(round(szene.ofentiefe_mm * szene.blickwinkel * px_pro_mm))
    if band < 3:
        return

    H, B = grau.shape
    start = x0 + ofen_breite
    band = min(band, B - start)
    if band < 3:
        return

    zeilen_mit_ofen = np.where(maske.any(axis=1))[0]
    if zeilen_mit_ofen.size == 0:
        return
    oben_lokal, unten_lokal = int(zeilen_mit_ofen[0]), int(zeilen_mit_ofen[-1])
    einzug = max(2, int(0.06 * (unten_lokal - oben_lokal)))

    for i in range(band):
        t = (i + 1) / band                       # 0 = an der Front, 1 = hinten
        spalte = start + i
        o = y0 + oben_lokal + int(t * einzug)
        u = y0 + unten_lokal - int(t * einzug)
        if u <= o:
            break
        wert = (szene.ofen_grau_max
                - t * (szene.ofen_grau_max - szene.ofen_grau_min - 20))
        grau[max(o, 0):min(u, H), spalte] = wert


def rendere_szenen_depthmap(
    konfiguration: OfenKonfiguration,
    geo: OfenGeometrie | None = None,
    px_pro_mm: float | None = None,
    profil: TiefenProfil | None = None,
    szene: SzenenProfil | None = None,
) -> Image.Image:
    """Tiefenkarte mit Wand und Boden – die Vorlage fürs KI-Rendering."""
    profil = profil or TiefenProfil()
    szene = szene or SzenenProfil()

    if not szene.aktiv:
        return rendere_depthmap(konfiguration, geo, px_pro_mm, profil)

    tiefe, maske, leinwand = tiefenfeld(konfiguration, geo, px_pro_mm, profil)
    s = leinwand.px_pro_mm

    links = int(round(szene.rand_seiten_mm * s))
    rechts = int(round(szene.rand_seiten_mm * s))
    oben = int(round(szene.rand_oben_mm * s))
    boden = int(round(szene.boden_hoehe_mm * s))

    H = leinwand.hoehe_px + oben + boden
    B = leinwand.breite_px + links + rechts

    grau = _kulisse(H, B, boden, szene)
    ofen = _ofen_grau(tiefe, maske, szene)

    ausschnitt = grau[oben:oben + leinwand.hoehe_px,
                      links:links + leinwand.breite_px]
    ausschnitt[maske] = ofen[maske]

    if szene.blickwinkel > 0.01:
        _seitenwange(grau, maske, oben, links,
                     leinwand.breite_px, leinwand.hoehe_px, szene, s)

    bild = Image.fromarray(np.clip(grau, 0, 255).astype(np.uint8), mode="L")
    if profil.weichzeichnen_px > 0:
        bild = bild.filter(ImageFilter.GaussianBlur(profil.weichzeichnen_px))

    if szene.max_kante_px and max(bild.size) > szene.max_kante_px:
        faktor = szene.max_kante_px / max(bild.size)
        bild = bild.resize((max(1, int(bild.width * faktor)),
                            max(1, int(bild.height * faktor))), Image.LANCZOS)
    return bild
