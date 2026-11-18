"""Lettura della pagina dell'annuncio di casadaprivato.it. La pagina non ha dati strutturati sull'immobile: si
leggono il sottotitolo, la descrizione e le coppie <dt>/<dd> dei riquadri DATI PRINCIPALI e COSTI."""
import re

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import come_voci, controlla_id, testo_html

_DESCRIZIONE = re.compile(r'<strong>([^<]*)</strong>\s*<div class="text-justify[^"]*">(.*?)</div>', re.S)
_VOCE = re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.S)


def dati_da_html(html: str) -> dict:
    voci = {testo_html(e): testo_html(v) for e, v in _VOCE.findall(html) if testo_html(e) and testo_html(v)}
    if "Riferimento" not in voci:
        raise ErrorePortale("la pagina non è un annuncio di casadaprivato.it: struttura cambiata o pagina sbagliata")
    trovata = _DESCRIZIONE.search(html)
    parti = [testo_html(trovata.group(1)), testo_html(trovata.group(2))] if trovata else []
    return {"id": voci["Riferimento"], "voci": voci, "descrizione": "\n\n".join(p for p in parti if p) or None}


def estrai_dettaglio(dati: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(dati["id"], id_atteso)
    return Dettaglio(
        descrizione=dati["descrizione"],
        caratteristiche=come_voci((e, v) for e, v in dati["voci"].items() if e != "Riferimento"),
        letto_il=letto_il or adesso_utc(),
    )
