"""Lettura della pagina dell'annuncio di tuttocasa.it. La pagina non ha dati strutturati: si leggono le coppie
etichetta-valore del riquadro Dettagli, la descrizione e il nome dell'inserzionista."""
import re

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import come_voci, controlla_id, testo_html

_ID = re.compile(r'name="annuncio_id" value="(\d+)"')
_VOCE = re.compile(r'<div class="title">\s*<p>(.*?)</p>\s*</div>\s*<div class="title-content">(.*?)</div>', re.S)
_DESCRIZIONE = re.compile(r'<div class="property-description-wrapper">\s*<h4[^>]*>Descrizione</h4>(.*?)</div>', re.S)
_INSERZIONISTA = re.compile(r'Informazioni utente</h6>\s*<div class="side-bar-agent-detail">\s*<h6[^>]*>(.*?)</h6>',
                            re.S)


def dati_da_html(html: str) -> dict:
    trovato = _ID.search(html)
    if not trovato:
        raise ErrorePortale("la pagina non è un annuncio di tuttocasa.it: struttura cambiata o pagina sbagliata")
    html = html.replace("<sup>2</sup>", "²")
    descrizione, inserzionista = _DESCRIZIONE.search(html), _INSERZIONISTA.search(html)
    return {
        "id": trovato.group(1),
        "voci": {testo_html(e): testo_html(v) for e, v in _VOCE.findall(html) if testo_html(e) and testo_html(v)},
        "descrizione": testo_html(descrizione.group(1)) if descrizione else None,
        "inserzionista": testo_html(inserzionista.group(1)) if inserzionista else None,
    }


def estrai_dettaglio(dati: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(dati["id"], id_atteso)
    voci = dati["voci"]
    return Dettaglio(
        descrizione=dati["descrizione"],
        classe_energetica=voci.get("Classe energetica"),
        caratteristiche=come_voci([*voci.items(), ("Inserzionista", dati["inserzionista"])]),
        letto_il=letto_il or adesso_utc(),
    )
