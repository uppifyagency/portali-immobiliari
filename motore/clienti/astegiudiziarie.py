"""Client di ricerca per astegiudiziarie.it: aste giudiziarie di immobili, tribunali di tutta Italia.

La pagina dei risultati non porta gli annunci: lo script ricerca.js manda i `searchParameters` del form a
`POST webapi.astegiudiziarie.it/api/search/map`, che restituisce tutti i lotti trovati (id, coordinate, prezzo base)
nell'ordine scelto, e poi chiede i dettagli a `POST /api/search/Data` 20 id per volta. Il client fa le stesse due
chiamate. Il corpo è quello che ricerca.js lascia al montaggio: `indirizzo` a null e `idPubblicazione` null vero,
non la stringa "null" con cui la pagina lo riceve (con la stringa search/map risponde [] anche quando ci sono lotti).
Nessun antibot: TRASPORTO = "http", origine l'API.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

from ..portali.modello import Annuncio, Asta, Pagina, adesso_utc

PORTALE = "astegiudiziarie"
SITO = "https://www.astegiudiziarie.it"
PER_PAGINA = 20  # itemsPerPage di ricerca.js
MAPPA = json.loads((pathlib.Path(__file__).resolve().parents[1] / "filtri" / f"{PORTALE}.json").read_text())
VOCI = {v["chiave"]: v for v in MAPPA["filtri"]}
VALORI = {k: {x["valore"] for x in v["valori"]} for k, v in VOCI.items() if v["valori"]}
ETICHETTE = {k: {x["valore"]: x["etichetta"] for x in v["valori"]} for k, v in VOCI.items() if v["valori"]}

# I searchParameters che /results scrive nella pagina per una ricerca di immobili senza filtri.
CORPO_VUOTO = {
    "tipoRicerca": 1, "indirizzo": None, "latitudine": None, "longitudine": None, "latitudineNW": None,
    "longitudineNW": None, "latitudineSE": None, "longitudineSE": None, "noGeo": False, "idEsperimentoVendita": None,
    "idTipologie": [], "tipologia": None, "idCategorie": [], "categoria": None, "descrizione": None, "comune": None,
    "provincia": None, "regione": None, "cap": None, "ricercaCap": None, "prezzoDa": None, "prezzoA": None,
    "priceMax": 0.0, "priceSteps": None, "tipologie": None, "idTribunale": None, "tribunale": None,
    "numeroProcedura": None, "annoProcedura": None, "ruolo": None, "idTipologiaProcedura": None, "giudice": None,
    "professionista": None, "idTipologiaVendita": None, "idModalitaVendita": None, "idPubblicazione": None,
    "dataVenditaDa": None, "dataVenditaA": None, "codiceAsta": None, "hasFoto": None, "hasPlanimetrie": None,
    "hasVirtualTour": None, "hasVideo": None, "bandita": None, "telematica": None, "inScadenza": None,
    "lottoUnico": None, "venditeAGI": None, "storica": False, "vetrina": False, "sezione": None, "searchOnMap": False,
    "tribunali": None, "tipologieProcedura": None, "tipologieVendita": None, "modalitaVendita": None,
    "numeroPubblicazioni": None, "listaIdLotto": None, "idProcedura": None, "orderBy": 6,
}
ID_SINGOLI = {"tribunale": "idTribunale", "tipo_procedura": "idTipologiaProcedura", "tipo_vendita": "idTipologiaVendita",
              "modalita_vendita": "idModalitaVendita", "ordinamento": "orderBy"}
# Il form ha anche il campo giudice: le schede non pubblicano il nome, quindi non si può provare e non si offre.
TESTI = {"professionista": "professionista"}
# Le caselle del form hanno true-value="true": la pagina manda la stringa.
CASELLE = {"con_foto": "hasFoto", "con_planimetria": "hasPlanimetrie", "con_video": "hasVideo",
           "con_virtual_tour": "hasVirtualTour", "escludi_aste_bandite": "bandita", "solo_telematiche": "telematica"}
INTESTAZIONI = {"Accept": "application/json, text/plain, */*", "X-Referer": SITO + "/"}
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_COORDINATE = re.compile(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")


class ErroreStruttura(RuntimeError):
    """L'API ha risposto qualcosa che non è la lista attesa."""


def luogo(comune: str) -> dict:
    """'Trieste' -> comune; 'Provincia di Trieste' -> provincia; 'Regione Friuli Venezia Giulia' -> regione."""
    c = comune.strip()
    m = re.fullmatch(r"(provincia di|regione)\b\s*(.*)", c, re.I)
    if m:
        campo = "provincia" if m.group(1).lower().startswith("provincia") else "regione"
        if not m.group(2):
            raise ValueError(f"{campo} vuota in {comune!r}")
        return {campo: m.group(2)}
    if not c:
        raise ValueError("comune vuoto")
    return {"comune": c}


def _intero(chiave: str, v) -> int:
    if type(v) is not int or v < 0:
        raise ValueError(f"filtro {chiave}: serve un intero >= 0, non {v!r}")
    return v


def _ammesso(chiave: str, v) -> str:
    if not isinstance(v, str) or v not in VALORI[chiave]:
        raise ValueError(f"filtro {chiave}: valore {v!r} non ammesso, vedi i valori in filtri/{PORTALE}.json")
    return v


def _ora(testo: str | None) -> str | None:
    return testo[:16] if testo else None


def annuncio(d: dict, marcatore: dict, letto_il: str) -> Annuncio:
    categoria = (d.get("categoria") or "").capitalize() or None
    prezzo = d.get("prezzoBase")
    esito = (d.get("esito") or {}).get("Descrizione")
    caratteristiche = [x for x in (
        categoria, ETICHETTE["tipo_vendita"].get(str(d.get("idTipologiaVendita"))),
        "Vendita telematica" if d.get("venditaTelematica") else None,
        f"Esito: {esito}" if esito and esito != "Sconosciuto" else None,
        "Planimetria" if d.get("hasPlanimetrie") else None, "Video" if d.get("hasMovie") else None,
        "Virtual tour" if d.get("hasVirtualTour") else None) if x]
    numero, anno = d.get("numeroProcedura"), d.get("annoProcedura")
    return Annuncio(
        portale=PORTALE,
        id=str(d["idLotto"]),
        url=SITO + d["urlSchedaDettagliata"] if d.get("urlSchedaDettagliata") else f"{SITO}/?idLotto={d['idLotto']}",
        titolo=", ".join(x for x in (categoria, d.get("indirizzo")) if x) or None,
        prezzo=round(prezzo) if isinstance(prezzo, (int, float)) else None,
        mq=None,
        locali=None,
        bagni=None,
        piano=None,
        ascensore=None,
        indirizzo=d.get("indirizzo"),
        zona=None,
        lat=d.get("latitudine") or marcatore.get("latitudine"),
        lon=d.get("longitudine") or marcatore.get("longitudine"),
        descrizione=d.get("descrizione"),
        foto=SITO + d["urlPhoto"] if d.get("urlPhoto") else None,
        agenzia=None,
        condizione=None,
        caratteristiche=caratteristiche,
        ribasso_pct=None,
        letto_il=letto_il,
        asta=Asta(
            data=_ora(d.get("dataUdienza") or d.get("dataInizioGara") or d.get("dataVendita")),
            offerta_minima=None,
            tribunale=d.get("tribunale"),
            procedura=f"{numero}/{anno}" if numero and anno else d.get("ruolo"),
            lotto=d.get("numeroLotto"),
            modalita=ETICHETTE["modalita_vendita"].get(str(d.get("idModalitaVendita"))),
        ),
    )


class Client:
    ORIGINE = "https://webapi.astegiudiziarie.it"
    TRASPORTO = "http"
    FILTRI = frozenset(ID_SINGOLI) | frozenset(TESTI) | frozenset(CASELLE) | frozenset(
        {"tipologia", "sottocategoria", "prezzo_min", "prezzo_max", "procedura", "numero_pubblicazione",
         "codice_asta", "data_asta_da", "data_asta_a", "mappa_area"})

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def corpo(self, comune: str, **filtri) -> dict:
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtri sconosciuti per {PORTALE}: {ignoti}")
        f = {k: v for k, v in filtri.items() if v is not None}
        c = {**CORPO_VUOTO, "idTipologie": [], "idCategorie": []}
        if "mappa_area" in f:
            punti = [_COORDINATE.fullmatch(p.strip()) for p in f["mappa_area"].split(";")] \
                if isinstance(f["mappa_area"], str) else []
            if len(punti) != 2 or not all(punti):
                raise ValueError(f"filtro mappa_area: serve un rettangolo 'lat,lng;lat,lng', non {f['mappa_area']!r}")
            (a1, o1), (a2, o2) = ((float(p.group(1)), float(p.group(2))) for p in punti)
            c.update(latitudineNW=max(a1, a2), longitudineNW=min(o1, o2), latitudineSE=min(a1, a2),
                     longitudineSE=max(o1, o2))
        else:
            # Con il rettangolo la pagina cerca solo per coordinate: il luogo resta vuoto.
            c.update(luogo(comune))
        if "tipologia" in f:
            c["idTipologie"] = [int(_ammesso("tipologia", f["tipologia"]))]
        if "sottocategoria" in f:
            v = f["sottocategoria"]
            scelte = [v] if isinstance(v, str) else list(v) if isinstance(v, (list, tuple)) else []
            if not scelte:
                raise ValueError(f"filtro sottocategoria: serve un id o una lista di id, non {v!r}")
            c["idCategorie"] = [int(_ammesso("sottocategoria", x)) for x in scelte]
        for chiave, campo in (("prezzo_min", "prezzoDa"), ("prezzo_max", "prezzoA")):
            if chiave in f:
                c[campo] = _intero(chiave, f[chiave])
        for chiave, campo in ID_SINGOLI.items():
            if chiave in f:
                c[campo] = int(_ammesso(chiave, f[chiave]))
        if "numero_pubblicazione" in f:
            c["idPubblicazione"] = _ammesso("numero_pubblicazione", f["numero_pubblicazione"])
        for chiave, campo in TESTI.items():
            if chiave in f:
                if not isinstance(f[chiave], str) or not f[chiave].strip():
                    raise ValueError(f"filtro {chiave}: serve un testo, non {f[chiave]!r}")
                c[campo] = f[chiave].strip()
        if "codice_asta" in f:
            c["codiceAsta"] = _intero("codice_asta", f["codice_asta"])
        if "procedura" in f:
            m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d{4})\s*", f["procedura"]) if isinstance(f["procedura"], str) else None
            if not m:
                raise ValueError(f"filtro procedura: serve 'numero/anno' come '35/2026', non {f['procedura']!r}")
            c["numeroProcedura"], c["annoProcedura"] = m.group(1), int(m.group(2))
        for chiave, campo in (("data_asta_da", "dataVenditaDa"), ("data_asta_a", "dataVenditaA")):
            if chiave in f:
                if not isinstance(f[chiave], str) or not _DATA.fullmatch(f[chiave]):
                    raise ValueError(f"filtro {chiave}: serve una data AAAA-MM-GG, non {f[chiave]!r}")
                c[campo] = f[chiave]
        for chiave, campo in CASELLE.items():
            if chiave in f:
                if f[chiave] is not True:
                    raise ValueError(f"filtro {chiave}: l'unico valore è True")
                c[campo] = "true"
        return c

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        if contratto != "vendita":
            raise ValueError(f"contratto {contratto!r}: su {PORTALE} ci sono solo vendite all'asta")
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        r = self.trasporto.post("/api/search/map", self.corpo(comune, **filtri), intestazioni=INTESTAZIONI)
        marcatori = r.json() if r.status == 200 else None
        if not isinstance(marcatori, list):
            raise ErroreStruttura(f"search/map: HTTP {r.status}, attesa una lista")
        totale = len(marcatori)
        scelti = marcatori[(pagina - 1) * PER_PAGINA: pagina * PER_PAGINA]
        pagine = math.ceil(totale / PER_PAGINA)
        if not scelti:
            return Pagina(totale=totale, pagine=pagine, annunci=[])
        ids = json.dumps([m["idLotto"] for m in scelti])
        r = self.trasporto.post("/api/search/Data", ids, intestazioni={**INTESTAZIONI, "Content-Type": "application/json"})
        dettagli = r.json() if r.status == 200 else None
        if not isinstance(dettagli, list):
            raise ErroreStruttura(f"search/Data: HTTP {r.status}, attesa una lista")
        per_id = {d["idLotto"]: d for d in dettagli}
        letto_il = adesso_utc()
        annunci = [annuncio(per_id[m["idLotto"]], m, letto_il) for m in scelti if m["idLotto"] in per_id]
        return Pagina(totale=totale, pagine=pagine, annunci=annunci)


if __name__ == "__main__":
    from ..trasporto import HttpTrasporto

    with HttpTrasporto(Client.ORIGINE, pausa_secondi=8) as t:
        p = Client(t).cerca("Trieste")
        print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
        for a in p.annunci:
            print(a.id, a.prezzo, a.asta, a.url)
