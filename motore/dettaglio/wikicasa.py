"""Lettura della pagina dell'annuncio di wikicasa.it: i dati stanno nel payload __NUXT_DATA__, sotto
data["re-detail-<id>--it"].realEstate. Lo stato dell'immobile è un codice numerico senza tabella nota: non si legge,
la lista porta già la condizione."""
from ..clienti.wikicasa import decodifica_nuxt_data
from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import controlla_id

_DOTAZIONI = (("hasTerraces", "terrazzo"), ("cellar", "cantina"), ("hasParking", "parcheggio"))


def dati_da_html(html: str) -> dict:
    dati = decodifica_nuxt_data(html).get("data") or {}
    immobile = next((v["realEstate"] for k, v in dati.items() if k.startswith("re-detail-") and v.get("realEstate")),
                    None)
    if not immobile:
        raise ErrorePortale("la pagina non è un annuncio di wikicasa.it: manca re-detail nel payload __NUXT_DATA__")
    return immobile


def estrai_dettaglio(immobile: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(immobile["id"], id_atteso)
    anno = immobile.get("buildingYear")
    return Dettaglio(
        descrizione=immobile.get("description"),
        piano=str(immobile["floor"]) if immobile.get("floor") not in (None, "") else None,
        classe_energetica=immobile.get("energyClass2015") or None,
        anno_costruzione=str(anno) if anno else None,
        caratteristiche=[nome for chiave, nome in _DOTAZIONI if immobile.get(chiave) is True],
        letto_il=letto_il or adesso_utc(),
    )
