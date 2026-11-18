"""Lettura della pagina dell'annuncio di casa.it: i dati stanno in __NEXT_DATA__, props.pageProps.pdp."""
import json
import re

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import come_voci, controlla_id

_NEXT_DATA = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NON_INDICATO = {"", "non indicato", "non indicata"}


def dati_da_html(html: str) -> dict:
    trovato = _NEXT_DATA.search(html)
    pdp = json.loads(trovato.group(1))["props"]["pageProps"].get("pdp") if trovato else None
    if not pdp:
        raise ErrorePortale("la pagina non è un annuncio di casa.it: manca __NEXT_DATA__ con pdp")
    return pdp


def _testo(valore) -> str:
    """Un valore di featureLists: un numero, un testo, o {main, integrative} con value e unità prima e dopo."""
    if isinstance(valore, list):
        return " ".join(t for t in map(_testo, valore) if t)
    if isinstance(valore, dict):
        if "main" in valore:
            principale, aggiunte = _testo(valore["main"]), _testo(valore.get("integrative") or [])
            return f"{principale} ({aggiunte})" if aggiunte else principale
        return " ".join(str(x) for x in (valore.get("munitBefore"), valore.get("value"), valore.get("munitAfter"))
                        if x not in (None, ""))
    return "" if valore is None else str(valore)


def _indicato(testo: str | None) -> str | None:
    return None if testo is None or testo.strip().lower() in _NON_INDICATO else testo


def estrai_dettaglio(pdp: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(pdp["id"], id_atteso)
    voci = {voce["label"]: ", ".join(t for t in map(_testo, voce.get("values") or []) if t)
            for elenco in (pdp.get("features") or {}).get("featureLists") or [] for voce in elenco.get("items") or []}
    return Dettaglio(
        descrizione=((pdp.get("description") or {}).get("it") or {}).get("text"),
        piano=_indicato(str((pdp.get("floor") or {}).get("value") or "")),
        classe_energetica=_indicato((pdp.get("energyClass") or {}).get("class")),
        stato=voci.get("Condizioni immobile"),
        caratteristiche=come_voci(voci.items()),
        letto_il=letto_il or adesso_utc(),
    )
