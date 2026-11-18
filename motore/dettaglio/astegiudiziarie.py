"""Lettura della scheda di un lotto di astegiudiziarie.it. La scheda è sul sito, non sull'API su cui cerca il client.
Non ha dati strutturati: si leggono le coppie etichetta-valore dei riquadri dettagliLotto__item (lotto, beni,
vendita), che portano l'offerta minima, il piano, la disponibilità e i metri quadri che la lista non ha."""
import re

from ..clienti.astegiudiziarie import SITO
from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import come_voci, controlla_id, testo_html

ORIGINE = SITO
_ID = re.compile(r'<link rel="canonical" href="[^"]*-l(\d+)-p\d+"')
_VOCE = re.compile(r'dettagliLotto__item"[^>]*>\s*<p>\s*<strong>(.*?)</strong>(.*?)</p>', re.S)
_DESCRIZIONE = re.compile(r'<p data-pvp-bene-descrizioneIT="">(.*?)</p>', re.S)
_VUOTO = "-"


def dati_da_html(html: str) -> dict:
    trovato = _ID.search(html)
    if not trovato:
        raise ErrorePortale("la pagina non è la scheda di un lotto di astegiudiziarie.it: struttura cambiata")
    voci = []
    for etichetta, valore in _VOCE.findall(html):
        voce = (testo_html(etichetta), " ".join((testo_html(valore) or "").split()))
        if voce[0] and voce[1] and voce[1] != _VUOTO and voce not in voci:
            voci.append(voce)
    descrizioni = [testo_html(d) for d in _DESCRIZIONE.findall(html)]
    return {"id": trovato.group(1), "voci": voci, "descrizione": "\n\n".join(d for d in descrizioni if d) or None}


def estrai_dettaglio(dati: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(dati["id"], id_atteso)
    voci = dict(reversed(dati["voci"]))  # a etichetta ripetuta vale la prima
    return Dettaglio(
        descrizione=dati["descrizione"],
        piano=voci.get("Piano"),
        classe_energetica=voci.get("Certificazione energetica"),
        caratteristiche=come_voci(dati["voci"]),
        letto_il=letto_il or adesso_utc(),
    )
