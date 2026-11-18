"""Lettura della pagina di dettaglio di immobiliare.it. La ricerca la fa motore/clienti/immobiliare.py."""
import json
import re

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc


class ErroreEstrazione(ErrorePortale):
    pass


_NEXT_DATA = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


def dati_da_html(html: str) -> dict:
    trovato = _NEXT_DATA.search(html)
    if not trovato:
        raise ErroreEstrazione("la pagina non contiene __NEXT_DATA__: struttura di immobiliare.it cambiata o pagina sbagliata")
    try:
        return json.loads(trovato.group(1))
    except json.JSONDecodeError as errore:
        raise ErroreEstrazione(f"__NEXT_DATA__ non è JSON valido: {errore}") from errore


def _principale(immobile: dict) -> dict:
    proprieta = immobile["properties"]
    return next((p for p in proprieta if p.get("isMain")), proprieta[0])


def _piano_esteso(proprieta: dict) -> str | None:
    piano = (proprieta.get("floor") or {}).get("value")
    edificio = proprieta.get("floors")
    return f"{piano} (edificio di {edificio})" if piano and edificio else piano


def _caratteristiche(proprieta: dict) -> list[str]:
    nomi = [v["name"] for v in proprieta.get("primaryFeatures") or [] if v.get("isVisible") and v.get("name")]
    nomi += proprieta.get("features") or []
    visti = set()
    return [n for n in nomi if not (n.lower() in visti or visti.add(n.lower()))]


def estrai_dettaglio(next_data: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    try:
        immobile = next_data["props"]["pageProps"]["detailData"]["realEstate"]
        if id_atteso is not None and str(immobile["id"]) != id_atteso:
            raise ErroreEstrazione(f"la pagina è dell'annuncio {immobile['id']}, non di {id_atteso}")
        principale = _principale(immobile)
        anno = principale.get("buildingYear")
        return Dettaglio(
            descrizione=principale.get("description"),
            piano=_piano_esteso(principale),
            spese_condominio=(principale.get("costs") or {}).get("condominiumExpenses"),
            classe_energetica=((principale.get("energy") or {}).get("class") or {}).get("name"),
            anno_costruzione=str(anno) if anno else None,
            stato=principale.get("condition"),
            caratteristiche=_caratteristiche(principale),
            letto_il=letto_il or adesso_utc(),
        )
    except (KeyError, IndexError, TypeError, AttributeError) as errore:
        raise ErroreEstrazione(f"struttura del dettaglio inattesa (detailData.realEstate.properties): {errore!r}") from errore
