"""Lettura della pagina dell'annuncio di bakeca.it: i dati stanno nel JSON-LD di tipo Product."""
import html as _html
import json
import re

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import come_voci, controlla_id

_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def dati_da_html(html: str) -> dict:
    for blocco in _JSON_LD.findall(html):
        dati = json.loads(blocco)
        if dati.get("productID"):
            return dati
    raise ErrorePortale("la pagina non è un annuncio di bakeca.it: manca il JSON-LD con productID")


def estrai_dettaglio(dati: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(dati["productID"], id_atteso)
    voci = {v["name"]: str(v["value"]) for v in dati.get("amenityFeature") or [] if v.get("name") and v.get("value")}
    indirizzo = dati.get("address") or {}
    via, comune = indirizzo.get("streetAddress"), indirizzo.get("addressLocality")
    descrizione = dati.get("description")
    return Dettaglio(
        descrizione=_html.unescape(descrizione) if descrizione else None,
        classe_energetica=voci.get("Classe energetica"),
        caratteristiche=come_voci(voci.items()),
        letto_il=letto_il or adesso_utc(),
        indirizzo=", ".join(x for x in (via, comune) if x) or None,
    )
