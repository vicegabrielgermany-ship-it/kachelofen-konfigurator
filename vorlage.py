"""Vorlage aus einem Auslege-Foto.

Beim Restaurieren wird der Ofen zuerst flach ausgelegt: Sockel, Korpus,
Gesims und Aufsatz liegen als Blöcke nebeneinander, meist schräg von oben
fotografiert. Dieses Modul macht aus so einem Foto eine Konfiguration.

Ablauf je Block:
  1. Der Bediener zieht ein Viereck um den Block (vier Eckpunkte, damit die
     Schrägaufnahme gleich mit entzerrt wird).
  2. `raster_schaetzen` zählt über Projektionsprofile die Fugen und schlägt
     Zeilen und Spalten vor.
  3. `verband_schaetzen` prüft, ob die Zeilen versetzt liegen (Halbverband)
     oder Fuge auf Fuge (Kreuzfuge).
  4. `zelle_ausschneiden` holt eine saubere Einzelkachel als Asset heraus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from assets import auf_alpha_zuschneiden, entzerren
from config import Sektion


# --------------------------------------------------------------------------
# Auswahl und Entzerrung
# --------------------------------------------------------------------------

@dataclass
class Auswahl:
    """Ein Viereck auf dem Foto, in relativen Koordinaten (0…1).

    Reihenfolge der Ecken: oben-links, oben-rechts, unten-rechts, unten-links.
    Ein Rechteck ist der Sonderfall, bei dem die Ecken achsparallel liegen.
    """

    ecken: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.08, 0.08), (0.92, 0.08),
                                 (0.92, 0.92), (0.08, 0.92)])

    @classmethod
    def rechteck(cls, x: float, y: float, breite: float,
                 hoehe: float) -> "Auswahl":
        x2, y2 = x + breite, y + hoehe
        return cls([(x, y), (x2, y), (x2, y2), (x, y2)])

    def pixel(self, groesse: tuple[int, int]) -> list[tuple[float, float]]:
        b, h = groesse
        return [(px * b, py * h) for px, py in self.ecken]

    def gueltig(self) -> bool:
        return len(self.ecken) == 4 and _flaeche(self.ecken) > 1e-4


def _flaeche(ecken: list[tuple[float, float]]) -> float:
    """Betrag der Polygonfläche (Gaußsche Trapezformel)."""
    summe = 0.0
    for (x1, y1), (x2, y2) in zip(ecken, ecken[1:] + ecken[:1]):
        summe += x1 * y2 - x2 * y1
    return abs(summe) / 2.0


def ausschnitt(bild: Image.Image, auswahl: Auswahl,
               max_kante: int = 1600) -> Image.Image:
    """Schneidet die Auswahl heraus und entzerrt sie zu einem Rechteck."""
    if not auswahl.gueltig():
        raise ValueError("Die Auswahl ist zu klein oder entartet.")
    ecken = auswahl.pixel(bild.size)
    entzerrt = entzerren(bild.convert("RGBA"), ecken)
    if max(entzerrt.size) > max_kante:
        faktor = max_kante / max(entzerrt.size)
        entzerrt = entzerrt.resize(
            (max(1, int(entzerrt.width * faktor)),
             max(1, int(entzerrt.height * faktor))), Image.LANCZOS)
    return entzerrt


def markiere(bild: Image.Image, auswahl: Auswahl,
             farbe: tuple[int, int, int] = (255, 96, 0),
             breite: int = 4) -> Image.Image:
    """Zeichnet das Auswahlviereck zur Kontrolle auf das Foto."""
    from PIL import ImageDraw

    vorschau = bild.convert("RGB").copy()
    zeichner = ImageDraw.Draw(vorschau)
    punkte = auswahl.pixel(vorschau.size)
    staerke = max(breite, int(min(vorschau.size) / 300))
    zeichner.polygon(punkte, outline=farbe, width=staerke)
    for i, (x, y) in enumerate(punkte):
        r = staerke * 2.5
        zeichner.ellipse([x - r, y - r, x + r, y + r], fill=farbe)
        zeichner.text((x + r + 2, y - r), "1234"[i], fill=farbe)
    return vorschau


def raster_zeichnen(bild: Image.Image, spalten: int, zeilen: int,
                    versatz: bool = False,
                    farbe: tuple[int, int, int] = (0, 200, 255)) -> Image.Image:
    """Legt das vermutete Kachelraster über den entzerrten Ausschnitt."""
    from PIL import ImageDraw

    vorschau = bild.convert("RGB").copy()
    zeichner = ImageDraw.Draw(vorschau)
    b, h = vorschau.size
    staerke = max(1, int(min(b, h) / 400))

    for r in range(zeilen + 1):
        y = h * r / zeilen
        zeichner.line([(0, y), (b, y)], fill=farbe, width=staerke)

    for r in range(zeilen):
        y0, y1 = h * r / zeilen, h * (r + 1) / zeilen
        # Zeilen werden von unten gezählt, deshalb der gespiegelte Index
        versetzt = versatz and ((zeilen - 1 - r) % 2 == 1)
        schritt = b / spalten
        start = schritt / 2 if versetzt else 0.0
        x = start
        while x <= b + 0.5:
            zeichner.line([(x, y0), (x, y1)], fill=farbe, width=staerke)
            x += schritt
    return vorschau


# --------------------------------------------------------------------------
# Rasterschätzung
# --------------------------------------------------------------------------

def _kantenprofil(grau: np.ndarray, achse: int) -> np.ndarray:
    """Energie der Helligkeitssprünge, projiziert auf eine Achse.

    achse=0 liefert ein Profil über die Spalten (senkrechte Fugen),
    achse=1 eines über die Zeilen (waagerechte Fugen).
    """
    # achse=0: Ableitung quer über die Spalten, Mittel über alle Zeilen
    # achse=1: Ableitung längs der Zeilen, Mittel über alle Spalten
    ableitung = np.abs(np.diff(grau, axis=1 - achse))
    profil = ableitung.mean(axis=achse).astype(np.float64)
    if profil.size < 8:
        return profil
    # Trend abziehen, damit Vignettierung das Ergebnis nicht dominiert
    fenster = max(5, profil.size // 12)
    kern = np.ones(fenster) / fenster
    geglättet = np.convolve(profil, kern, mode="same")
    profil = profil - geglättet
    return profil - profil.mean()


def _normiert(profil: np.ndarray) -> np.ndarray:
    streuung = profil.std()
    if streuung < 1e-9:
        return np.zeros_like(profil)
    return (profil - profil.mean()) / streuung


def _kammwert(profil: np.ndarray, anzahl: int) -> float:
    """Wie gut erklärt ein Raster mit `anzahl` Kacheln die gefundenen Fugen?

    Das Profil wird in `anzahl` gleich lange Abschnitte gefaltet und
    gemittelt. Passt die Periode, liegen die Kanten aller Abschnitte
    übereinander und das Mittel behält seine Struktur; passt sie nicht,
    mittelt sich alles weg.

    Der Wert ist auf Rauschen normiert: bei strukturlosem Profil ergibt sich
    ungefähr 1,0, bei sauber getroffener Periode deutlich mehr. Anders als
    ein Vergleich „auf der Fuge gegen daneben" stört es nicht, dass Kacheln
    auch innen Kanten haben (Medaillon, Rahmen) – die wiederholen sich ja im
    selben Takt.
    """
    laenge = profil.size
    if anzahl < 2 or laenge < anzahl * 8:
        return -np.inf

    abschnitt = max(8, laenge // anzahl)
    gesamt = np.var(profil)
    if gesamt < 1e-12:
        return -np.inf

    # sauber auf ein Vielfaches von `anzahl` bringen, ohne Phasendrift
    stuetzen = np.linspace(0.0, laenge - 1.0, anzahl * abschnitt)
    gestreckt = np.interp(stuetzen, np.arange(laenge), profil)
    mittelwelle = gestreckt.reshape(anzahl, abschnitt).mean(axis=0)
    return float(anzahl * np.var(mittelwelle) / gesamt)


def _anzahl_schaetzen(profil: np.ndarray, min_kacheln: int,
                      max_kacheln: int) -> tuple[int, float]:
    """Zahl der Kacheln entlang einer Achse plus Güte zwischen 0 und 1.

    Ein Raster mit halb so vielen Kacheln erklärt die Periode genauso gut –
    es faltet nur je zwei echte Kacheln übereinander. Unter allen etwa
    gleich guten Kandidaten wird deshalb der feinste genommen. Ein doppelt
    so feines Raster fällt dagegen im Wert ab und kommt gar nicht erst in
    die Auswahl.
    """
    profil = _normiert(profil)
    if profil.size < 24 or not profil.any():
        return max(min_kacheln, 1), 0.0

    # mindestens rund 20 Pixel je Kachel, sonst wird alles zu Rauschen
    obergrenze = min(max_kacheln, profil.size // 20)
    if obergrenze < max(min_kacheln, 2):
        return max(min_kacheln, 1), 0.0

    werte = {n: _kammwert(profil, n)
             for n in range(max(min_kacheln, 2), obergrenze + 1)}
    werte = {n: w for n, w in werte.items() if np.isfinite(w)}
    if not werte:
        return max(min_kacheln, 1), 0.0

    bester = max(werte.values())
    # Unter gleich guten Kandidaten das gröbste Raster nehmen. Kacheln sind
    # innen oft symmetrisch (mittiges Medaillon, umlaufender Rahmen), dadurch
    # passt die halbe Kachelbreite scheinbar genauso gut. Ein zu grobes
    # Raster fällt beim Blick auf die Überlagerung sofort auf, ein zu feines
    # sieht dagegen plausibel aus und wird leicht übersehen.
    anzahl = min(n for n, w in werte.items() if w >= 0.82 * bester)
    if bester <= 1.25:
        # Kaum mehr als Rauschen. Das passiert regelmäßig bei drei oder vier
        # Reihen – so wenige Perioden geben statistisch nichts her. Dann
        # lieber ehrlich nichts behaupten und den Bediener eintragen lassen.
        return max(min_kacheln, 1), 0.0

    return anzahl, float(np.clip((bester - 1.0) / 4.0, 0.0, 1.0))


def raster_schaetzen(bild: Image.Image, min_kacheln: int = 2,
                     max_kacheln: int = 16) -> tuple[int, int, float]:
    """Schätzt (Spalten, Zeilen, Güte) für einen entzerrten Blockausschnitt.

    Die Güte liegt zwischen 0 und 1. Unter etwa 0,25 ist die Schätzung
    Rateraum – dann sollte der Bediener die Zahlen selbst eintragen.
    """
    grau = np.asarray(bild.convert("L"), dtype=np.float64)
    if grau.ndim != 2 or min(grau.shape) < 32:
        return max(min_kacheln, 1), max(min_kacheln, 1), 0.0

    spalten, guete_s = _anzahl_schaetzen(
        _kantenprofil(grau, 0), min_kacheln, max_kacheln)
    zeilen, guete_z = _anzahl_schaetzen(
        _kantenprofil(grau, 1), min_kacheln, max_kacheln)
    return spalten, zeilen, float(min(guete_s, guete_z))


def verband_schaetzen(bild: Image.Image, spalten: int,
                      zeilen: int) -> tuple[bool, float]:
    """Prüft, ob die Zeilen um eine halbe Kachel versetzt liegen.

    Rückgabe: (Halbverband ja/nein, Güte zwischen 0 und 1). Verglichen wird
    die Lage der senkrechten Fugen in geraden und ungeraden Zeilen.
    """
    if spalten < 2 or zeilen < 2:
        return False, 0.0

    grau = np.asarray(bild.convert("L"), dtype=np.float64)
    h, b = grau.shape
    zeilenhoehe = h / zeilen

    gerade, ungerade = [], []
    for r in range(zeilen):
        oben = int(r * zeilenhoehe)
        unten = int((r + 1) * zeilenhoehe)
        streifen = grau[oben + 2:max(unten - 2, oben + 3), :]
        if streifen.shape[0] < 3:
            continue
        profil = _kantenprofil(streifen, 0)
        if profil.size < spalten * 2:
            continue
        # von unten gezählt, wie im Rest des Programms
        (gerade if (zeilen - 1 - r) % 2 == 0 else ungerade).append(profil)

    if not gerade or not ungerade:
        return False, 0.0

    mittel_g = np.mean(gerade, axis=0)
    mittel_u = np.mean(ungerade, axis=0)
    schritt = b / spalten

    versatz_0 = _profilaehnlichkeit(mittel_g, mittel_u, 0)
    versatz_halb = _profilaehnlichkeit(mittel_g, mittel_u, int(round(schritt / 2)))

    if max(versatz_0, versatz_halb) <= 0:
        return False, 0.0
    unterschied = abs(versatz_halb - versatz_0)
    guete = float(np.clip(unterschied / (abs(versatz_0) + abs(versatz_halb) + 1e-9),
                          0.0, 1.0))
    return bool(versatz_halb > versatz_0), guete


def _profilaehnlichkeit(a: np.ndarray, b: np.ndarray, verschiebung: int) -> float:
    if verschiebung <= 0:
        verschoben = b
    else:
        verschoben = np.roll(b, verschiebung)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(verschoben)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, verschoben) / (na * nb))


# --------------------------------------------------------------------------
# Einzelteile herausschneiden
# --------------------------------------------------------------------------

def zelle_ausschneiden(bild: Image.Image, spalten: int, zeilen: int,
                       spalte: int, zeile: int, versatz: bool = False,
                       einzug: float = 0.04) -> Image.Image:
    """Holt eine einzelne Kachel aus dem entzerrten Block.

    `zeile` wird von unten gezählt (0 = unterste Reihe), passend zum Rest
    des Programms. `einzug` schneidet den Fugenrand ab.
    """
    b, h = bild.size
    spalten = max(1, spalten)
    zeilen = max(1, zeilen)
    breite = b / spalten
    hoehe = h / zeilen

    zeile_von_oben = zeilen - 1 - int(np.clip(zeile, 0, zeilen - 1))
    versetzt = versatz and (int(zeile) % 2 == 1)
    x0 = (spalte + (0.5 if versetzt else 0.0)) * breite
    y0 = zeile_von_oben * hoehe

    dx, dy = breite * einzug, hoehe * einzug
    kasten = (x0 + dx, y0 + dy, x0 + breite - dx, y0 + hoehe - dy)
    kasten = (max(0, kasten[0]), max(0, kasten[1]),
              min(b, kasten[2]), min(h, kasten[3]))
    if kasten[2] - kasten[0] < 2 or kasten[3] - kasten[1] < 2:
        raise ValueError("Die gewählte Kachel liegt außerhalb des Ausschnitts.")
    return bild.convert("RGBA").crop([int(round(v)) for v in kasten])


def bereich_ausschneiden(bild: Image.Image, x: float, y: float,
                         breite: float, hoehe: float) -> Image.Image:
    """Freier Ausschnitt in relativen Koordinaten – für Türen und Gitter."""
    b, h = bild.size
    kasten = (int(x * b), int(y * h),
              int((x + breite) * b), int((y + hoehe) * h))
    kasten = (max(0, kasten[0]), max(0, kasten[1]),
              min(b, max(kasten[2], kasten[0] + 2)),
              min(h, max(kasten[3], kasten[1] + 2)))
    return auf_alpha_zuschneiden(bild.convert("RGBA").crop(kasten))


# --------------------------------------------------------------------------
# Ergebnis
# --------------------------------------------------------------------------

@dataclass
class Blockbefund:
    """Was aus einem Blockausschnitt herausgelesen wurde."""

    name: str
    spalten: int
    zeilen: int
    versatz: bool
    guete_raster: float
    guete_verband: float
    seitenverhaeltnis: float      # Breite/Höhe einer Kachel im Ausschnitt
    spalten_geschaetzt: bool = True
    zeilen_geschaetzt: bool = True

    def zeilenhoehe_mm(self, kachelbreite_mm: float) -> float:
        return round(kachelbreite_mm / max(self.seitenverhaeltnis, 0.05), 1)

    def sektion(self, kachelbreite_mm: float, kachel_key: str,
                global_spalten: int, tiefe_mm: float = 0.0) -> Sektion:
        return Sektion(
            name=self.name,
            zeilen=self.zeilen,
            zeilenhoehe_mm=self.zeilenhoehe_mm(kachelbreite_mm),
            kachel_key=kachel_key,
            versatz=self.versatz,
            spalten=None if self.spalten == global_spalten else self.spalten,
            tiefe_mm=tiefe_mm,
        )

    def hinweis(self) -> str:
        teile = [f"{self.spalten} Spalten × {self.zeilen} Zeilen"]
        teile.append("Halbverband" if self.versatz else "Kreuzfuge (kein Versatz)")
        if self.spalten_geschaetzt and self.guete_raster < 0.25:
            teile.append("Spaltenzahl unsicher – bitte am Raster prüfen")
        if self.zeilen_geschaetzt:
            teile.append("Zeilenzahl geraten – bitte eintragen")
        if self.guete_verband < 0.12:
            teile.append("Verband unsicher – bitte am Raster prüfen")
        return " · ".join(teile)


def block_auswerten(entzerrt: Image.Image, name: str,
                    zeilen: int | None = None,
                    spalten: int | None = None,
                    min_kacheln: int = 2,
                    max_kacheln: int = 16) -> Blockbefund:
    """Führt Raster- und Verbandschätzung für einen entzerrten Block zusammen.

    `zeilen` und `spalten` dürfen vorgegeben werden – dann wird nur der Rest
    geschätzt. In der Praxis zählt der Bediener die Reihen schneller und
    sicherer, als ein Schätzer es bei drei oder vier Reihen je könnte.
    """
    geschaetzt_s, geschaetzt_z, guete = raster_schaetzen(
        entzerrt, min_kacheln, max_kacheln)
    zeilen_final = int(zeilen) if zeilen else geschaetzt_z
    roh_spalten = int(spalten) if spalten else geschaetzt_s

    if spalten:
        # Vorgabe respektieren, nur den Verband bestimmen
        versatz, guete_v = verband_schaetzen(entzerrt, roh_spalten, zeilen_final)
        spalten_final = roh_spalten
    else:
        spalten_final, versatz, guete_v = _spalten_und_verband(
            entzerrt, roh_spalten, zeilen_final)

    b, h = entzerrt.size
    seitenverhaeltnis = ((b / max(spalten_final, 1))
                         / max(h / max(zeilen_final, 1), 1e-6))
    return Blockbefund(
        name=name, spalten=spalten_final, zeilen=zeilen_final, versatz=versatz,
        guete_raster=1.0 if spalten else guete, guete_verband=guete_v,
        seitenverhaeltnis=float(seitenverhaeltnis),
        spalten_geschaetzt=spalten is None,
        zeilen_geschaetzt=zeilen is None,
    )


def _spalten_und_verband(entzerrt: Image.Image, roh_spalten: int,
                         zeilen: int) -> tuple[int, bool, float]:
    """Löst die Zweideutigkeit zwischen Halbverband und doppelter Spaltenzahl.

    Im Halbverband wechseln die senkrechten Fugen zeilenweise die Phase –
    das Fugenmuster hat dann tatsächlich die halbe Kachelbreite als Periode.
    Ein Block mit sieben Kacheln je Zeile wird deshalb erst einmal als
    vierzehn Spalten erkannt. Deshalb wird die halbierte Spaltenzahl
    gegengeprüft: meldet sie einen sauberen Versatz, ist sie die richtige.
    """
    versatz_roh, guete_roh = verband_schaetzen(entzerrt, roh_spalten, zeilen)

    if roh_spalten >= 4 and roh_spalten % 2 == 0:
        haelfte = roh_spalten // 2
        versatz_halb, guete_halb = verband_schaetzen(entzerrt, haelfte, zeilen)
        if versatz_halb and guete_halb >= 0.5:
            return haelfte, True, guete_halb

    return roh_spalten, versatz_roh, guete_roh
