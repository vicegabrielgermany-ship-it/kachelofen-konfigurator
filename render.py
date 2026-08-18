"""Modul 3b – AI Render Pipeline über Replicate.

Die Tiefenkarte aus `depthmap.py` steuert ein ControlNet-Depth-Modell, das
den Ofen fotorealistisch in einen Raum stellt, ohne die Rastergeometrie zu
verändern.

Robustheit gegen Schema-Änderungen: Statt die Eingabefelder eines Modells
hart zu verdrahten, wird das OpenAPI-Schema der aktuellen Modellversion von
Replicate gelesen und die Nutzlast darauf gefiltert. Ein Preset liefert nur
noch Kandidatennamen (`control_image` vs. `image` …) und Standardwerte.
"""

from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

REPLICATE_API = "https://api.replicate.com/v1"


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ModellPreset:
    modell: str
    beschriftung: str
    hinweis: str = ""
    # Kandidatennamen für das Steuerbild, in Prioritätsreihenfolge.
    control_felder: tuple[str, ...] = ("control_image", "image", "control_net_image")
    standardwerte: dict[str, Any] = field(default_factory=dict)


PRESETS: dict[str, ModellPreset] = {
    "flux-depth-dev": ModellPreset(
        modell="black-forest-labs/flux-depth-dev",
        beschriftung="FLUX.1 Depth [dev] – gutes Preis-Leistungs-Verhältnis",
        hinweis="Hält die Rastergeometrie sehr sauber ein. Erste Wahl.",
        standardwerte={
            "guidance": 12.0,
            "num_inference_steps": 32,
            "megapixels": "1",
            "output_format": "png",
            "num_outputs": 1,
        },
    ),
    "flux-depth-pro": ModellPreset(
        modell="black-forest-labs/flux-depth-pro",
        beschriftung="FLUX.1 Depth [pro] – höchste Qualität, teurer",
        hinweis="Für finale Verkaufsbilder.",
        standardwerte={
            "guidance": 15,
            "steps": 40,
            "output_format": "png",
        },
    ),
    "sdxl-controlnet-depth": ModellPreset(
        modell="lucataco/sdxl-controlnet-depth",
        beschriftung="SDXL + ControlNet Depth – günstig, schnell",
        hinweis="Etwas weniger detailtreu bei feinen Fugen.",
        standardwerte={
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
            "condition_scale": 0.75,
        },
    ),
}


# --------------------------------------------------------------------------
# Prompt-Bausteine
# --------------------------------------------------------------------------

BASIS_PROMPT = (
    "professionelle Innenarchitektur-Fotografie eines historischen "
    "Kachelofens, handgefertigte glasierte Keramikkacheln, gusseiserne "
    "Ofentür, präzise gleichmäßige Fugen, hoher Detailgrad, 35 mm, "
    "natürliche Schärfentiefe, keine Verzerrung der Kachelgeometrie"
)

NEGATIV_PROMPT = (
    "verzogene Kacheln, unregelmäßiges Raster, verrutschte Fugen, "
    "schwebender Ofen ohne Bodenkontakt, fehlender Schatten, "
    "Text, Wasserzeichen, Fischauge, HDR-Look, Plastikoptik, "
    "moderner Kaminofen, Metallkamin, sichtbares Feuer"
)

# Der Standard ist bewusst die nüchterne Wand: sie zeigt den Ofen, nicht die
# Einrichtung, und ist als Verkaufsbild vielseitig verwendbar.
RAUM_BAUSTEINE: dict[str, str] = {
    "Weiß-graue Wand (Standard)":
        "vor einer glatten, matt weiß-grauen Putzwand, heller "
        "Betonestrich-Boden, ruhiger neutraler Hintergrund, der Ofen steht "
        "satt auf dem Boden auf",
    "Weiße Wand, Dielenboden":
        "vor einer weiß gestrichenen Wand, heller Eichendielenboden, "
        "schlichter Sockelleiste, ruhiger Hintergrund",
    "Altbau-Wohnzimmer":
        "Altbauzimmer mit Stuckdecke, Fischgrätparkett, hohe Fenster",
    "Bauernstube":
        "getäfelte Bauernstube, Holzbalkendecke, Eckbank aus Fichte",
    "Studioaufnahme":
        "neutraler hellgrauer Studiohintergrund, nahtloser Übergang zum "
        "Boden, gleichmäßiges weiches Licht",
}

LICHT_RICHTUNGEN: dict[str, str] = {
    "Von links": "das Licht fällt von links vorn ein, der Schlagschatten "
                 "des Ofens liegt rechts an der Wand",
    "Von rechts": "das Licht fällt von rechts vorn ein, der Schlagschatten "
                  "des Ofens liegt links an der Wand",
    "Von links oben (Fenster)":
        "seitliches Fensterlicht von links oben, langer weicher "
        "Schlagschatten nach rechts unten, Reliefs plastisch modelliert",
    "Frontal, weich": "weiches frontales Licht, nur ein schmaler "
                      "Kontaktschatten am Boden",
}

LICHT_STIMMUNGEN: dict[str, str] = {
    "Weiches Tageslicht": "weiches diffuses Tageslicht, neutrale Farbtemperatur",
    "Warmes Nachmittagslicht": "warmes tiefstehendes Nachmittagslicht, "
                               "goldener Ton in den Glanzlichtern",
    "Kühles Nordlicht": "kühles gleichmäßiges Nordlicht, ruhige Schatten",
    "Neutrales Studiolicht": "neutrales Studiolicht, große weiche Lichtquelle, "
                             "kontrollierte Reflexe auf der Glasur",
}

SCHATTEN_BAUSTEIN = (
    "realistischer weicher Schlagschatten an der Wand und dunklerer "
    "Kontaktschatten am Bodenansatz, physikalisch stimmige Verschattung "
    "in den Fugen und unter den Gesimsen"
)

# Alte Bezeichnung, damit gespeicherte Konfigurationen weiter funktionieren.
STIL_BAUSTEINE = RAUM_BAUSTEINE


def baue_prompt(raum: str | None = None,
                zusatz: str = "",
                kachelbeschreibung: str = "",
                lichtrichtung: str | None = None,
                lichtstimmung: str | None = None,
                mit_schatten: bool = True,
                dreiviertel: bool = False) -> str:
    """Setzt den Prompt aus Bausteinen zusammen.

    Die Geometrie kommt aus der Tiefenkarte, die Oberfläche aus dem Text.
    Deshalb steht die Kachelbeschreibung weit vorn – sie ist der stärkste
    Hebel für Materialtreue.
    """
    teile = [BASIS_PROMPT]
    if kachelbeschreibung.strip():
        teile.append(kachelbeschreibung.strip())
    teile.append("Dreiviertelansicht, leicht von der Seite aufgenommen"
                 if dreiviertel else "streng frontale Ansicht, Kamera auf "
                                     "halber Ofenhöhe")
    if raum in RAUM_BAUSTEINE:
        teile.append(RAUM_BAUSTEINE[raum])
    if lichtstimmung in LICHT_STIMMUNGEN:
        teile.append(LICHT_STIMMUNGEN[lichtstimmung])
    if lichtrichtung in LICHT_RICHTUNGEN:
        teile.append(LICHT_RICHTUNGEN[lichtrichtung])
    if mit_schatten:
        teile.append(SCHATTEN_BAUSTEIN)
    if zusatz.strip():
        teile.append(zusatz.strip())
    return ", ".join(teile)


# --------------------------------------------------------------------------
# Replicate-Client
# --------------------------------------------------------------------------

class RenderFehler(RuntimeError):
    pass


def api_token(explizit: str | None = None) -> str | None:
    return explizit or os.environ.get("REPLICATE_API_TOKEN") or None


def _requests():
    try:
        import requests  # type: ignore
        return requests
    except ImportError as exc:  # pragma: no cover
        raise RenderFehler(
            "Das Paket 'requests' fehlt. Bitte `pip install requests` ausführen."
        ) from exc


def als_data_uri(bild: Image.Image, format: str = "PNG") -> str:
    puffer = io.BytesIO()
    bild.save(puffer, format=format)
    kodiert = base64.b64encode(puffer.getvalue()).decode("ascii")
    return f"data:image/{format.lower()};base64,{kodiert}"


def eingabefelder(modell: str, token: str, timeout: float = 20.0) -> set[str] | None:
    """Liest die erlaubten Eingabefelder der aktuellen Modellversion.

    Gibt None zurück, wenn das Schema nicht ermittelbar ist – dann wird die
    Nutzlast ungefiltert gesendet.
    """
    requests = _requests()
    try:
        antwort = requests.get(
            f"{REPLICATE_API}/models/{modell}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        antwort.raise_for_status()
        schema = (antwort.json()
                  .get("latest_version", {})
                  .get("openapi_schema", {})
                  .get("components", {})
                  .get("schemas", {})
                  .get("Input", {})
                  .get("properties", {}))
        return set(schema.keys()) or None
    except Exception:
        return None


def _nutzlast(preset: ModellPreset,
              prompt: str,
              control_uri: str,
              erlaubt: set[str] | None,
              negativ: str,
              seed: int | None,
              ueberschreibungen: dict[str, Any]) -> dict[str, Any]:
    daten: dict[str, Any] = dict(preset.standardwerte)
    daten.update(ueberschreibungen)
    daten["prompt"] = prompt

    for feld in ("negative_prompt", "negativ_prompt"):
        if erlaubt is None or feld in erlaubt:
            daten[feld] = negativ
            break

    if seed is not None:
        daten["seed"] = seed

    control_feld = next(
        (f for f in preset.control_felder if erlaubt is None or f in erlaubt),
        preset.control_felder[0],
    )
    daten[control_feld] = control_uri

    if erlaubt is not None:
        daten = {k: v for k, v in daten.items() if k in erlaubt}
        daten["prompt"] = prompt
        daten[control_feld] = control_uri
    return daten


def rendere(
    depthmap: Image.Image,
    prompt: str,
    preset_key: str = "flux-depth-dev",
    token: str | None = None,
    negativ_prompt: str = NEGATIV_PROMPT,
    seed: int | None = None,
    zusatzparameter: dict[str, Any] | None = None,
    timeout_s: float = 300.0,
) -> list[Image.Image]:
    """Schickt die Tiefenkarte an Replicate und liefert die Ergebnisbilder."""
    schluessel = api_token(token)
    if not schluessel:
        raise RenderFehler(
            "Kein Replicate-Token gesetzt. Entweder in der Oberfläche eintragen "
            "oder die Umgebungsvariable REPLICATE_API_TOKEN belegen."
        )
    if preset_key not in PRESETS:
        raise RenderFehler(f"Unbekanntes Preset '{preset_key}'.")

    requests = _requests()
    preset = PRESETS[preset_key]
    erlaubt = eingabefelder(preset.modell, schluessel)
    daten = _nutzlast(
        preset, prompt, als_data_uri(depthmap.convert("RGB")),
        erlaubt, negativ_prompt, seed, zusatzparameter or {},
    )

    kopf = {
        "Authorization": f"Bearer {schluessel}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",
    }
    antwort = requests.post(
        f"{REPLICATE_API}/models/{preset.modell}/predictions",
        headers=kopf, json={"input": daten}, timeout=90,
    )
    if antwort.status_code >= 400:
        raise RenderFehler(
            f"Replicate meldet {antwort.status_code}: {antwort.text[:600]}"
        )

    vorhersage = antwort.json()
    vorhersage = _warten(requests, vorhersage, kopf, timeout_s)

    if vorhersage.get("status") != "succeeded":
        raise RenderFehler(
            f"Render fehlgeschlagen ({vorhersage.get('status')}): "
            f"{vorhersage.get('error') or 'kein Fehlertext'}"
        )

    return [_lade_bild(requests, u) for u in _ausgabe_urls(vorhersage.get("output"))]


def _warten(requests, vorhersage: dict, kopf: dict, timeout_s: float) -> dict:
    start = time.monotonic()
    while vorhersage.get("status") in ("starting", "processing"):
        if time.monotonic() - start > timeout_s:
            raise RenderFehler(f"Zeitüberschreitung nach {timeout_s:.0f} s.")
        time.sleep(2.0)
        url = vorhersage.get("urls", {}).get("get")
        if not url:
            break
        antwort = requests.get(url, headers=kopf, timeout=30)
        antwort.raise_for_status()
        vorhersage = antwort.json()
    return vorhersage


def _ausgabe_urls(ausgabe: Any) -> list[str]:
    if ausgabe is None:
        return []
    if isinstance(ausgabe, str):
        return [ausgabe]
    if isinstance(ausgabe, list):
        return [x for x in ausgabe if isinstance(x, str)]
    if isinstance(ausgabe, dict):
        for schluessel in ("image", "images", "output"):
            if schluessel in ausgabe:
                return _ausgabe_urls(ausgabe[schluessel])
    return []


def _lade_bild(requests, url: str) -> Image.Image:
    antwort = requests.get(url, timeout=120)
    antwort.raise_for_status()
    return Image.open(io.BytesIO(antwort.content)).convert("RGB")
