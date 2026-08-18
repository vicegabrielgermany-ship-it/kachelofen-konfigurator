"""Tageskontingent für KI-Renderings.

Wenn der Replicate-Token zentral hinterlegt ist, rendert das ganze Team auf
eine Rechnung. Dieses Modul begrenzt die Zahl der Renderings pro Tag, damit
ein Versehen nicht teuer wird.

Der Zähler liegt in einer kleinen JSON-Datei. Auf gehosteten Umgebungen ohne
dauerhaften Speicher wird er beim Neustart zurückgesetzt – als Schutz gegen
Ausrutscher reicht das, als Abrechnungsgrundlage nicht.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

STANDARD_LIMIT = 40
UMGEBUNGSVARIABLE = "RENDER_LIMIT_PRO_TAG"


def _pfad() -> Path:
    eigen = os.environ.get("RENDER_ZAEHLER_PFAD")
    if eigen:
        return Path(eigen)
    basis = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return basis / "kachelofen-konfigurator" / "renderzaehler.json"


def limit() -> int:
    """0 bedeutet: keine Begrenzung."""
    roh = os.environ.get(UMGEBUNGSVARIABLE)
    if roh is None:
        return STANDARD_LIMIT
    try:
        return max(0, int(roh))
    except ValueError:
        return STANDARD_LIMIT


def _lesen() -> dict[str, int]:
    pfad = _pfad()
    if not pfad.exists():
        return {}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        return {k: int(v) for k, v in daten.items()} if isinstance(daten, dict) else {}
    except Exception:
        return {}


def _schreiben(daten: dict[str, int]) -> None:
    pfad = _pfad()
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        heute = date.today().isoformat()
        # nur den aktuellen Tag behalten – die Datei bleibt winzig
        pfad.write_text(json.dumps({heute: daten.get(heute, 0)}),
                        encoding="utf-8")
    except Exception:
        pass  # Kontingent ist Komfort, kein Muss


def verbraucht_heute() -> int:
    return _lesen().get(date.today().isoformat(), 0)


def verbleibend() -> int | None:
    """None = unbegrenzt."""
    grenze = limit()
    if grenze == 0:
        return None
    return max(0, grenze - verbraucht_heute())


def erschoepft() -> bool:
    rest = verbleibend()
    return rest is not None and rest <= 0


def verbuchen(anzahl: int = 1) -> None:
    daten = _lesen()
    heute = date.today().isoformat()
    daten[heute] = daten.get(heute, 0) + anzahl
    _schreiben(daten)


def zuruecksetzen() -> None:
    _schreiben({})
