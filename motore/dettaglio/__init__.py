"""La lettura della pagina dell'annuncio, un modulo per portale: il nome del file è il nome del portale.

Ogni modulo espone dati_da_html(html) -> dict ed estrai_dettaglio(dati, id_atteso, letto_il) -> Dettaglio.
La pagina si apre all'url dell'annuncio, con il trasporto del client dello stesso portale; un modulo che legge
su un'altra origine la dichiara in ORIGINE.
"""
import html
import importlib
import pkgutil
import re
from types import ModuleType

from ..portali.modello import ErrorePortale, ErroreRichiesta

# Portale senza lettore -> il perché, detto all'agente.
SENZA_LETTORE = {
    "subito": "la lista di subito porta già la descrizione intera e le stesse caratteristiche della pagina "
              "dell'annuncio: lavora con i campi di cerca",
    "astalegale": "il dettaglio su astalegale non è ancora disponibile: usa il link dell'annuncio",
    "gateaway": "il dettaglio su gateaway non è ancora disponibile: la pagina non è stata provata, usa il link "
                "dell'annuncio",
    "properstar": "il dettaglio su properstar non è ancora disponibile: la pagina non è stata provata, usa il link "
                  "dell'annuncio",
}

LETTORI = frozenset(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))


def lettore_di(portale: str) -> ModuleType:
    if portale not in LETTORI:
        raise ErroreRichiesta(SENZA_LETTORE.get(portale, f"il dettaglio su {portale} non è disponibile; "
                                                          f"usa il link dell'annuncio"))
    return importlib.import_module(f".{portale}", __name__)


def testo_html(frammento: str | None) -> str | None:
    """Un pezzo di HTML ridotto a testo: gli a capo restano dove la pagina va a capo, gli spazi si stringono."""
    if frammento is None:
        return None
    frammento = re.sub(r"<br\s*/?>|</p>", "\n", frammento, flags=re.I)
    righe = (" ".join(html.unescape(re.sub(r"<[^>]+>", " ", r)).split()) for r in frammento.split("\n"))
    return "\n".join(r for r in righe if r) or None


def come_voci(coppie) -> list[str]:
    """Le coppie (etichetta, valore) della scheda come "etichetta: valore"; una coppia senza valore non entra."""
    return [f"{etichetta}: {valore}" for etichetta, valore in coppie if valore]


def controlla_id(trovato, atteso: str | None) -> None:
    if atteso is not None and str(trovato) != atteso:
        raise ErrorePortale(f"la pagina è dell'annuncio {trovato}, non di {atteso}")
