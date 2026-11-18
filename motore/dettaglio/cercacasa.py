"""Lettura della pagina dell'annuncio di cercacasa.it: nel TransferState Angular (<script id="serverApp-state">)
c'è la risposta di /api/listings/findOne, già scaricata dal server."""
from ..clienti.cercacasa import ErroreStruttura, estrai_stato
from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc
from . import controlla_id

URL_API_SCHEDA = "https://api.cercacasa.it/api/listings/findOne"
_DOTAZIONI = (("elevator", "ascensore"), ("garden", "giardino"), ("terrace", "terrazzo"), ("balcony", "balcone"),
              ("basement", "cantina"), ("garage", "box"), ("parking_space", "posto auto"), ("furnished", "arredato"))


def dati_da_html(html: str) -> dict:
    try:
        stato = estrai_stato(html)
    except ErroreStruttura as errore:
        raise ErrorePortale(f"la pagina non è un annuncio di cercacasa.it: {errore}") from errore
    scheda = next((v["b"] for k, v in stato.items()
                   if not k.startswith("__") and isinstance(v, dict) and str(v.get("u", "")).split("?")[0] == URL_API_SCHEDA
                   and isinstance(v.get("b"), dict)), None)
    if not scheda:
        raise ErrorePortale("la pagina non è la scheda di un annuncio di cercacasa.it: manca la risposta di findOne")
    return scheda


def estrai_dettaglio(scheda: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    controlla_id(scheda["id"], id_atteso)
    descrizione = next((d["string"] for d in scheda.get("descriptions") or [] if d.get("language") == "it"), None)
    caratteristiche = [f"{scheda['total_rooms']} locali"] if scheda.get("total_rooms") else []
    if scheda.get("kitchen"):
        caratteristiche.append(f"cucina {scheda['kitchen']}")
    if scheda.get("heating"):
        caratteristiche.append(f"riscaldamento {scheda['heating']}")
    caratteristiche += [nome for chiave, nome in _DOTAZIONI if scheda.get(chiave) is True]
    anno, spese = scheda.get("construction_year"), scheda.get("condominium_fees")
    return Dettaglio(
        descrizione=descrizione,
        piano=f"{scheda['floors']}°" if scheda.get("floors") else None,
        spese_condominio=f"{spese} € mensili" if spese else None,
        classe_energetica=scheda.get("energy_class") or None,
        anno_costruzione=str(anno) if anno else None,
        stato=scheda["conditions"].replace("-", " ") if scheda.get("conditions") else None,
        caratteristiche=caratteristiche,
        letto_il=letto_il or adesso_utc(),
        indirizzo=", ".join(x for x in (scheda.get("address"), scheda.get("city")) if x) or None,
    )
