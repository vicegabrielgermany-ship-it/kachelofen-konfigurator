"""Modul 1 – Asset Processing.

Aus einem Handyfoto einer Kachel wird ein sauberes, transparentes und
achsparalleles PNG, das sich als Rasterbaustein verwenden lässt.

Schritte:
  1. Freistellen (rembg, optional – läuft offline)
  2. Auf die Alpha-Bounding-Box zuschneiden
  3. Optionale Perspektivkorrektur über vier Eckpunkte
  4. Optionale Feinrotation
  5. Ableiten der Passkachel (linke/rechte Hälfte)
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps


# --------------------------------------------------------------------------
# Freistellen
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _rembg_session():
    """Lädt rembg verzögert – der Import zieht ein ONNX-Modell nach."""
    from rembg import new_session  # type: ignore
    return new_session("isnet-general-use")


def rembg_verfuegbar() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


def freistellen(bild: Image.Image, alpha_matting: bool = False) -> Image.Image:
    """Entfernt den Hintergrund. Fällt bei fehlendem rembg auf das
    Originalbild zurück (mit voller Deckkraft)."""
    if not rembg_verfuegbar():
        return bild.convert("RGBA")

    from rembg import remove  # type: ignore

    puffer = io.BytesIO()
    bild.convert("RGB").save(puffer, format="PNG")
    ergebnis = remove(
        puffer.getvalue(),
        session=_rembg_session(),
        alpha_matting=alpha_matting,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=15,
        alpha_matting_erode_size=8,
    )
    return Image.open(io.BytesIO(ergebnis)).convert("RGBA")


# --------------------------------------------------------------------------
# Zuschneiden und Ausrichten
# --------------------------------------------------------------------------

def auf_alpha_zuschneiden(bild: Image.Image, schwelle: int = 12) -> Image.Image:
    """Schneidet transparente Ränder weg."""
    bild = bild.convert("RGBA")
    alpha = np.array(bild.getchannel("A"))
    maske = alpha > schwelle
    if not maske.any():
        return bild
    zeilen = np.where(maske.any(axis=1))[0]
    spalten = np.where(maske.any(axis=0))[0]
    return bild.crop((int(spalten[0]), int(zeilen[0]),
                      int(spalten[-1]) + 1, int(zeilen[-1]) + 1))


def entzerren(bild: Image.Image,
              ecken: list[tuple[float, float]],
              zielgroesse: tuple[int, int] | None = None) -> Image.Image:
    """Perspektivkorrektur über vier Eckpunkte.

    `ecken` in der Reihenfolge oben-links, oben-rechts, unten-rechts,
    unten-links, in Pixelkoordinaten des Eingabebilds.
    """
    if len(ecken) != 4:
        raise ValueError("Es werden genau vier Eckpunkte benötigt.")

    if zielgroesse is None:
        breite = int(round(max(_dist(ecken[0], ecken[1]),
                               _dist(ecken[3], ecken[2]))))
        hoehe = int(round(max(_dist(ecken[0], ecken[3]),
                              _dist(ecken[1], ecken[2]))))
        zielgroesse = (max(breite, 1), max(hoehe, 1))

    bw, bh = zielgroesse
    ziel = [(0, 0), (bw, 0), (bw, bh), (0, bh)]
    koeffizienten = _perspektiv_koeffizienten(ziel, ecken)
    return bild.convert("RGBA").transform(
        zielgroesse, Image.PERSPECTIVE, koeffizienten, Image.BICUBIC
    )


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _perspektiv_koeffizienten(ziel: list[tuple[float, float]],
                              quelle: list[tuple[float, float]]) -> list[float]:
    matrix = []
    for (zx, zy), (qx, qy) in zip(ziel, quelle):
        matrix.append([zx, zy, 1, 0, 0, 0, -qx * zx, -qx * zy])
        matrix.append([0, 0, 0, zx, zy, 1, -qy * zx, -qy * zy])
    A = np.array(matrix, dtype=np.float64)
    B = np.array(quelle, dtype=np.float64).reshape(8)
    return np.linalg.solve(A, B).tolist()


def drehen(bild: Image.Image, winkel_grad: float) -> Image.Image:
    """Feinrotation gegen den Uhrzeigersinn, danach Alpha-Crop."""
    if abs(winkel_grad) < 1e-6:
        return bild.convert("RGBA")
    gedreht = bild.convert("RGBA").rotate(
        winkel_grad, resample=Image.BICUBIC, expand=True,
        fillcolor=(0, 0, 0, 0)
    )
    return auf_alpha_zuschneiden(gedreht)


# --------------------------------------------------------------------------
# Kachel-Assets
# --------------------------------------------------------------------------

@dataclass
class KachelAsset:
    """Ein aufbereiteter Rasterbaustein."""

    key: str
    bild: Image.Image
    quelle: str = ""

    def __post_init__(self) -> None:
        self.bild = self.bild.convert("RGBA")

    @property
    def seitenverhaeltnis(self) -> float:
        return self.bild.width / max(self.bild.height, 1)

    def haelfte(self, seite: str) -> Image.Image:
        """Rechte bzw. linke Hälfte für die Passkacheln an den Rändern.

        `seite="links"` liefert die Kachel, die an der LINKEN Ofenkante
        sitzt – dort schaut die rechte Hälfte der überstehenden Kachel
        heraus. Umgekehrt für rechts.
        """
        b, h = self.bild.size
        mitte = b // 2
        if seite == "links":
            return self.bild.crop((b - mitte, 0, b, h))
        return self.bild.crop((0, 0, mitte, h))


@dataclass
class AssetBibliothek:
    """Alle Kachel- und Eisenwaren-Assets einer Session."""

    kacheln: dict[str, KachelAsset] = field(default_factory=dict)
    fixtures: dict[str, KachelAsset] = field(default_factory=dict)

    def setze_kachel(self, key: str, bild: Image.Image, quelle: str = "") -> None:
        self.kacheln[key] = KachelAsset(key, bild, quelle)

    def setze_fixture(self, key: str, bild: Image.Image, quelle: str = "") -> None:
        self.fixtures[key] = KachelAsset(key, bild, quelle)

    def kachel(self, key: str, ersatz: str | None = "flaeche") -> KachelAsset | None:
        if key in self.kacheln:
            return self.kacheln[key]
        if ersatz and ersatz in self.kacheln:
            return self.kacheln[ersatz]
        return None

    def fixture(self, key: str) -> KachelAsset | None:
        return self.fixtures.get(key)


# --------------------------------------------------------------------------
# Platzhalter für Vorschau und Tests
# --------------------------------------------------------------------------

def platzhalter_kachel(breite: int = 400, hoehe: int = 400,
                       grundton: tuple[int, int, int] = (86, 122, 96),
                       relief: bool = True) -> Image.Image:
    """Erzeugt eine synthetische Kachel mit Wölbung und Randprofil.

    Dient als Vorschau, solange noch keine Fotos hochgeladen sind, und als
    deterministischer Testeingang.
    """
    y, x = np.mgrid[0:hoehe, 0:breite]
    nx = (x - breite / 2) / (breite / 2)
    ny = (y - hoehe / 2) / (hoehe / 2)

    if relief:
        woelbung = 1.0 - 0.55 * np.clip(np.maximum(np.abs(nx), np.abs(ny)), 0, 1) ** 3
        rand = np.clip((np.maximum(np.abs(nx), np.abs(ny)) - 0.86) / 0.14, 0, 1)
        licht = woelbung - 0.30 * rand + 0.10 * (-ny) * (1 - rand)
    else:
        licht = np.ones_like(nx)

    licht = np.clip(licht, 0.35, 1.25)
    basis = np.array(grundton, dtype=np.float64).reshape(1, 1, 3)
    rgb = np.clip(basis * licht[..., None], 0, 255).astype(np.uint8)
    alpha = np.full((hoehe, breite), 255, dtype=np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")


def platzhalter_fixture(breite: int = 400, hoehe: int = 500,
                        beschriftung: str = "") -> Image.Image:
    """Synthetische gusseiserne Tür für Vorschau und Tests."""
    from PIL import ImageDraw

    bild = Image.new("RGBA", (breite, hoehe), (0, 0, 0, 0))
    zeichner = ImageDraw.Draw(bild)
    rand = max(2, min(breite, hoehe) // 24)
    zeichner.rounded_rectangle(
        [0, 0, breite - 1, hoehe - 1], radius=rand * 2,
        fill=(46, 44, 42, 255), outline=(24, 23, 22, 255), width=rand,
    )
    zeichner.rounded_rectangle(
        [rand * 3, rand * 3, breite - 1 - rand * 3, hoehe - 1 - rand * 3],
        radius=rand, outline=(96, 90, 84, 255), width=max(1, rand // 2),
    )
    knauf_r = max(4, min(breite, hoehe) // 16)
    zeichner.ellipse(
        [breite - rand * 4 - knauf_r, hoehe // 2 - knauf_r,
         breite - rand * 4 + knauf_r, hoehe // 2 + knauf_r],
        fill=(122, 112, 100, 255), outline=(30, 28, 26, 255), width=2,
    )
    if beschriftung:
        zeichner.text((rand * 4, rand * 4), beschriftung, fill=(150, 145, 138, 255))
    return bild


def als_png_bytes(bild: Image.Image) -> bytes:
    puffer = io.BytesIO()
    bild.convert("RGBA").save(puffer, format="PNG", optimize=True)
    return puffer.getvalue()


def exif_korrigiert_laden(datei) -> Image.Image:
    """Lädt ein Bild und richtet es nach EXIF-Orientierung auf."""
    bild = Image.open(datei)
    return ImageOps.exif_transpose(bild).convert("RGBA")
