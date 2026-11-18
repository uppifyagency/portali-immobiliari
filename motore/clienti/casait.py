"""Client di ricerca per casa.it (pagina SRP renderizzata lato server, mappa per raggio e area disegnata).

casa.it non espone un'API JSON same-origin per la lista risultati: la pagina `/srp?...` contiene tutto lo stato
della ricerca in `window.__INITIAL_STATE__ = JSON.parse("...")`. Il client chiede quella pagina HTML al trasporto e
ne legge `search.list`, `search.total`, `search.paginator`.

I parametri dell'URL sono quelli che il sito costruisce: a ogni clic su un filtro la pagina manda il suo stato a
`/portal-srp/api/v1/apireq` e riceve l'uri `/srp?...` (risultati/scoperta/casait/apireq/*.json). L'ordine dei
parametri e la forma dei valori qui sotto vengono da quegli uri.

Raggio e area disegnata esistono solo sulla mappa (/srp/map): lì la pagina chiede l'apireq con
POST /portal-srp/api/v1/apireq (pageType searchMap) e gli annunci con POST /portal-srp/api/v1/search-map. Il client
fa le stesse due POST (risultati/scoperta/casait/mappa/searchMap.js, cerchio-*.json, poligono-*.json).

Il sito è dietro DataDome, quindi TRASPORTO = "chrome".
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "casa"
URL_FOTO = "https://images-1.casa.it/800x600"
_STATO = re.compile(r"window\.__INITIAL_STATE__\s*=\s*JSON\.parse\(")
_HKEY = re.compile(r"^[0-9a-f]{8}$")
INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


@dataclass(frozen=True)
class Luogo:
    nome: str
    hkey: str
    centro: tuple[float, float] | None = None   # lat, lon: il centro del cerchio per `raggio`


# Trieste è nota dall'HAR e da html/srp-trieste.html (resolvedLocations): nessuna richiesta per risolverla.
TRIESTE = Luogo("Trieste", "967b818a", (45.6639954690306, 13.7985718962732))

# Zone di Trieste: search.breadcrumbData.dropdown.items della SRP di Trieste (risultati/scoperta/casait/html/srp-trieste.html).
ZONE_TRIESTE = {
    "altura": "ba4f4ca1", "baiamonti-valmura": "cda765ec", "barcola": "d4c9ddd1", "basovizza": "4eaa78e6",
    "borgo-san-sergio": "78fe2ef0", "borgo-teresiano-giardino-pubblico": "42339a99",
    "campanelle-sant-anna-via-costalunga": "713c3979", "chiarbola-san-giacomo": "8b5223a5",
    "citta-vecchia-san-vito": "bcd789b4", "cologna-universita": "838097b6", "gretta-roiano-scorcola": "29c22bdb",
    "grignano-miramare": "7bd43d2e", "largo-barriera-settefontane": "d390a42d", "opicina-banne": "5c7c34c8",
    "prosecco-contovello": "77133658", "rozzol-san-luigi": "b8a7776f", "san-giovanni": "d9fd61d7",
    "santa-croce": "e723b706", "trebiciano": "e5403055",
}

CONTRATTI = ("vendita", "affitto", "affitto-breve")
TIPOLOGIE_CASA = ("appartamento", "attico-mansarda", "baita-bungalow-chalet-trullo", "casa-bi-trifamiliare",
                  "casa-indipendente", "loft", "rustico-casale", "villa", "villetta-a-schiera")
GRUPPO_DI_TIPOLOGIA = {
    **{t: "casa" for t in TIPOLOGIE_CASA},
    "negozio-locale-commerciale": "commerciale", "attivita-licenza-commerciale": "commerciale",
    "terreno-agricolo": "terreno", "terreno-edificabile": "terreno",
}
# I gruppi residenziali condividono le tipologie della casa (conf_filtri.json propertyTypes.values).
GRUPPI_CON_TIPOLOGIE_CASA = ("casa", "nuova-costruzione", "case-vacanza")


@dataclass(frozen=True)
class Filtro:
    """Una chiave della mappa. `valori` vuoto vuol dire intero libero."""
    parametro: str
    tipo: str                       # intero | booleano | enum | multiplo | testo
    valori: dict | None = None      # valore del client -> codice URL (enum, multiplo, booleano a valore fisso)
    estremo: int | None = None      # 0 = minimo, 1 = massimo di un intervallo MIN-MAX
    soglie: tuple = ()              # interi ammessi quando l'interfaccia offre solo bottoni


# Il server di casa.it applica balconyTerrace solo come lista di due valori: con un valore solo il filtro non entra
# nella query. Provato il 03/11 con =balcone, =balcone, (virgola finale), balcony=true, due volte la chiave,
# balconyTerrace[]=balcone: sempre 726 come senza filtro (risultati/scoperta/casait/html/diag-*.html). Per questo
# balcone e terrazzo sono in `assenti` nella mappa e qui sollevano ValueError invece di essere ignorati.
ASSENTI = {
    "balcone": "casa.it non applica il balcone da solo (balconyTerrace=balcone resta senza effetto); usa balcone_o_terrazzo=True",
    "terrazzo": "casa.it non applica il terrazzo da solo (balconyTerrace=terrazzo resta senza effetto); usa balcone_o_terrazzo=True",
}
GEO = ("raggio", "mappa_area")


def _rng(p, i):
    return Filtro(p, "intero", estremo=i)


def _flag(p, codice):
    return Filtro(p, "booleano", {True: codice})


FILTRI_DEF: dict[str, Filtro] = {
    "tipologia_gruppo": Filtro("propertyTypeGroup", "enum", {g: g for g in (
        "casa", "stanza", "box-posto-auto", "cantina-solaio", "edificio", "ufficio", "commerciale", "capannone",
        "terreno")}),
    "nuova_costruzione": _flag("propertyTypeGroup", "nuova-costruzione"),
    "tipologia": Filtro("propertyTypes", "multiplo", {t: t for t in GRUPPO_DI_TIPOLOGIA}),
    "zona": Filtro("q", "multiplo", {z: h for z, h in ZONE_TRIESTE.items()}),
    "prezzo_min": _rng("price", 0), "prezzo_max": _rng("price", 1),
    "prezzo_mq_min": _rng("mqPrice", 0), "prezzo_mq_max": _rng("mqPrice", 1),
    "rata_mutuo_min": _rng("payment", 0), "rata_mutuo_max": _rng("payment", 1),
    "mq_min": _rng("mq", 0), "mq_max": _rng("mq", 1),
    "locali_min": _rng("rooms", 0), "locali_max": _rng("rooms", 1),
    "bagni_min": Filtro("bathrooms", "intero", estremo=0, soglie=(1, 2, 3, 4, 5)),
    "box_auto": Filtro("parkingSpaces", "intero", estremo=0, soglie=(1, 2, 3)),
    "anno_costruzione_min": _rng("constructionYear", 0), "anno_costruzione_max": _rng("constructionYear", 1),
    "stato_immobile": Filtro("condition", "multiplo", {
        "nuovo-ristrutturato": "nuovo,ristrutturato", "abitabile": "abitabile", "da-ristrutturare": "da-ristrutturare"}),
    "riscaldamento": Filtro("heating", "multiplo", {"centralizzato": "centralizzato", "autonomo": "autonomo"}),
    "giardino": Filtro("garden", "multiplo", {"comune": "comune", "privato": "privato"}),
    "balcone_o_terrazzo": _flag("balconyTerrace", "balcone,terrazzo"),
    "arredato": Filtro("furniture", "multiplo", {"no": "no", "parzialmente": "parzialmente", "completamente": "completamente"}),
    "data_pubblicazione": Filtro("publicationDt", "enum", {"2d": "2d", "7d": "7d", "30d": "30d"}),
    "pubblicato_da": Filtro("publisher", "enum", {"agenzie": "agenzie", "privati": "privati"}),
    "da_privato": _flag("publisher", "privati"),
    "tipo_affitto": Filtro("rentType", "enum", {"residential": "residential", "seasonalRental": "seasonalRental"}),
    "occupato": _flag("availability", "occupato"),
    "libero": _flag("availability", "libero"),
    "nuda_proprieta": _flag("availability", "nuda-proprieta"),
    "a_reddito": _flag("availability", "affittato"),
    "classe_energetica": Filtro("energyClass", "enum", {c: c for c in "GFEDCBA"}),
    "piano": Filtro("level", "enum", {"terra": "0", "intermedio": "999", "ultimo": "1000",
                                      **{str(n): str(n) for n in range(1, 6)}}),
    "ascensore": Filtro("lift", "enum", {"si": "true", "no": "false", **{str(n): str(n) for n in range(1, 6)}}),
    "con_foto": _flag("hasPhoto", "true"),
    "aria_condizionata": _flag("hasAC", "true"),
    "piscina": _flag("hasSwimmingPool", "true"),
    "portineria": _flag("hasReception", "true"),
    "con_virtual_tour": _flag("hasVirtualTour", "true"),
    "immobili_di_prestigio": _flag("isLux", "true"),
    "escludi_in_costruzione": _flag("isUnderConstruction", "false"),
    "solo_trattative_riservate": _flag("isPrivateNegotiation", "true"),
    "escludi_trattative_riservate": _flag("isPrivateNegotiation", "false"),
    "asta": Filtro("isAuction", "enum", {"solo": "true", "escludi": "false"}),
    "raggio": Filtro("geocircle", "intero"),       # metri dal centro del comune
    "mappa_area": Filtro("geopolygon", "testo"),   # "lat,lon;lat,lon;lat,lon[;...]"
    "ordinamento": Filtro("sortType", "enum", {s: s for s in (
        "relevance", "date-desc", "date-asc", "price-asc", "price-desc", "surface-asc", "surface-desc")}),
}

# Ordine dei parametri negli uri restituiti da apireq (apireq/A.json, B.json, C.json).
ORDINE = ["tr", "propertyTypeGroup", "propertyTypes", "q", "price", "mqPrice", "payment", "mq", "rooms", "bathrooms",
          "parkingSpaces", "constructionYear", "condition", "heating", "garden", "balconyTerrace", "furniture",
          "publicationDt", "publisher", "rentType", "availability", "energyClass", "level", "liftIfAtLeast", "lift",
          "hasPhoto", "hasAC", "hasSwimmingPool", "hasReception", "hasVirtualTour", "isLux", "isUnderConstruction",
          "isPrivateNegotiation", "isAuction", "sortType", "page"]
LISTE = {"propertyTypes", "q", "condition", "heating", "garden", "balconyTerrace", "furniture"}
# Nel qObject (il corpo di apireq) publisher è una lista, level e liftIfAtLeast numeri, i flag booleani
# (risultati/scoperta/casait/apireq/A.json..D.json). sortType e page vanno a parte.
LISTE_Q = LISTE | {"publisher"}
NUMERI_Q = {"level", "liftIfAtLeast"}
ESAPI = {"esapiSite": "it_casa", "searchResultWithChild": True, "applyFilterToChild": True,
         "searchResultwithCharacteristic": True}


class ErroreStruttura(RuntimeError):
    """La pagina non contiene lo stato atteso (struttura del sito cambiata)."""


def estrai_stato(html: str) -> dict:
    """Legge window.__INITIAL_STATE__ = JSON.parse("<stringa JSON escapata>")."""
    m = _STATO.search(html)
    if not m:
        raise ErroreStruttura("window.__INITIAL_STATE__ non trovato nella pagina")
    testo = html[m.end():]
    try:
        stringa, _ = json.JSONDecoder().raw_decode(testo)
    except json.JSONDecodeError:
        # Escape solo-JS (\' e \xNN) non validi in JSON: li normalizzo e riprovo.
        fine = testo.find('");')
        grezzo = testo[: fine + 1].replace("\\'", "'")
        grezzo = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", grezzo)
        stringa = json.loads(grezzo)
    return json.loads(stringa) if isinstance(stringa, str) else stringa


def slug(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    cifre = re.sub(r"[^\d]", "", str(v))
    return int(cifre) if cifre else None


def _str(v) -> str | None:
    return None if v is None or v == "" else str(v)


def annuncio_da_lista(x: dict, origine: str, letto_il: str) -> Annuncio:
    f = x.get("features") or {}
    prezzo = f.get("price") or {}
    prezzo_val = None
    if prezzo.get("show", True):
        prezzo_val = _int((prezzo.get("marker") or {}).get("originalPrice")) or _int(prezzo.get("value"))
    geo = x.get("geoInfos") or {}
    titolo = x.get("title") or {}
    media = (x.get("media") or {}).get("items") or []
    publisher = x.get("publisher") or {}
    uri = x.get("uri") or f"/immobili/{x.get('id')}/"
    etichette = [str(e) for e in (x.get("labels") or [])]
    caratteristiche = list(etichette)
    if f.get("energyClass"):
        caratteristiche.append(f"classe energetica {f['energyClass']}")
    if f.get("availability"):
        caratteristiche.append(str(f["availability"]))
    if x.get("isAuction"):
        caratteristiche.append("asta")
    return Annuncio(
        portale=PORTALE,
        id=str(x["id"]),
        url=origine + uri,
        titolo=_str(titolo.get("main")) or _str(x.get("highlight")),
        prezzo=prezzo_val,
        mq=_int(f.get("mq")),
        locali=_str(f.get("rooms")),
        bagni=_str(f.get("bathrooms")),
        piano=_str(f.get("level")),
        ascensore=None,  # non presente nei dati della lista
        indirizzo=_str(geo.get("street")) if geo.get("show_address") else None,
        zona=_str(geo.get("district_name")),
        lat=geo.get("lat"),
        lon=geo.get("lon"),
        descrizione=_str(x.get("description")),
        foto=URL_FOTO + media[0]["uri"] if media and media[0].get("uri") else None,
        agenzia=_str(publisher.get("publisherName")),
        condizione=next((e for e in etichette if e.upper() in ("RISTRUTTURATO", "NUOVO", "DA RISTRUTTURARE")), None),
        caratteristiche=caratteristiche,
        ribasso_pct=None,  # la lista non riporta il prezzo precedente
        letto_il=letto_il,
    )


def _codici(chiave: str, f: Filtro, valore) -> list[str]:
    """Valida il valore del client e restituisce i codici URL che porta (più di uno solo per i multipli)."""
    if f.tipo == "booleano":
        if valore is not True:
            raise ValueError(f"filtro {chiave}: valore {valore!r} non ammesso, usa True oppure ometti il filtro")
        return [f.valori[True]]
    if f.tipo == "intero":
        if isinstance(valore, bool) or not isinstance(valore, int) or valore < 0:
            raise ValueError(f"filtro {chiave}: valore {valore!r} non ammesso, serve un intero >= 0")
        if f.soglie and valore not in f.soglie:
            raise ValueError(f"filtro {chiave}: valore {valore!r} non ammesso, l'interfaccia offre {list(f.soglie)}")
        return [str(valore)]
    if f.tipo == "testo":
        if not isinstance(valore, str) or not valore.strip():
            raise ValueError(f"filtro {chiave}: serve una stringa")
        return [valore]
    voci = valore if f.tipo == "multiplo" and isinstance(valore, (list, tuple)) else [valore]
    if not voci:
        raise ValueError(f"filtro {chiave}: lista vuota")
    fuori = [v for v in voci if not isinstance(v, str) or v not in f.valori]
    if fuori:
        raise ValueError(f"filtro {chiave}: valore {fuori[0]!r} non ammesso, ammessi {sorted(f.valori)}")
    return [f.valori[v] for v in voci]


def _q_object(p: dict[str, list[str]]) -> dict:
    """Dai codici dell'uri al qObject che la pagina manda ad apireq (stessa forma di apireq/A.json..D.json)."""
    q = {}
    for k, codici in p.items():
        if k in ("sortType", "page"):
            continue
        if k in LISTE_Q:
            q[k] = [x for c in codici for x in c.split(",")]
        elif k in NUMERI_Q:
            q[k] = int(codici[0])
        elif codici[0] in ("true", "false"):
            q[k] = codici[0] == "true"
        else:
            q[k] = codici[0]
    return q


def _poligono(valore) -> list[list[float]]:
    """"lat,lon;lat,lon;lat,lon" -> [[lat, lon], ...], come il disegno sulla mappa (geopolygon.polygon)."""
    errore = ValueError(f"filtro mappa_area: valore {valore!r} non ammesso, serve 'lat,lon;lat,lon;lat,lon' (almeno 3 punti)")
    if not isinstance(valore, str):
        raise errore
    try:
        punti = [[float(n) for n in coppia.split(",")] for coppia in valore.split(";") if coppia.strip()]
    except ValueError:
        raise errore from None
    if len(punti) < 3 or any(len(x) != 2 or not (-90 <= x[0] <= 90 and -180 <= x[1] <= 180) for x in punti):
        raise errore
    return punti


class Client:
    ORIGINE = "https://www.casa.it"
    TRASPORTO = "chrome"
    FILTRI = frozenset(FILTRI_DEF)
    PAUSA_RISOLUZIONE = 15  # secondi dopo la pagina SEO del comune, prima della ricerca: il ritmo di DataDome

    def __init__(self, trasporto):
        self.trasporto = trasporto
        self._luoghi: dict[str, Luogo] = {"trieste": TRIESTE}

    def luogo(self, comune: str) -> Luogo:
        """Comune -> hkey. Trieste è nota; gli altri si leggono dalla pagina SEO /vendita/residenziale/<slug>/."""
        if _HKEY.match(comune.strip().lower()):
            return Luogo(comune, comune.strip().lower())
        s = slug(comune)
        if not s:
            raise ValueError(f"comune {comune!r} vuoto")
        if s not in self._luoghi:
            r = self.trasporto.get(f"/vendita/residenziale/{s}/", intestazioni=INTESTAZIONI_HTML)
            if r.status == 404:
                raise ValueError(f"comune {comune!r}: casa.it non ha la pagina /vendita/residenziale/{s}/")
            if r.status != 200:
                raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
            luoghi = (estrai_stato(r.testo).get("search") or {}).get("resolvedLocations") or []
            città = [x for x in luoghi if x.get("level") == 9 and slug(x.get("name", "")) == s]
            if not città:
                trovati = [x.get("name") for x in luoghi]
                raise ValueError(f"comune {comune!r}: casa.it risolve {s!r} in {trovati}, non in un comune con quel nome")
            x = città[0]
            self._luoghi[s] = Luogo(x["name"], x["hkey"], (x["lat"], x["lon"]) if "lat" in x and "lon" in x else None)
            time.sleep(self.PAUSA_RISOLUZIONE)
        return self._luoghi[s]

    def _parametri(self, comune: str, contratto: str, pagina: int, filtri: dict) -> dict[str, list[str]]:
        """Parametro -> codici, nell'ordine degli uri del sito. Raggio e area disegnata restano fuori: vanno nel qObject."""
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r}: ammessi {list(CONTRATTI)}")
        if isinstance(pagina, bool) or not isinstance(pagina, int) or pagina < 1:
            raise ValueError("pagina deve essere un intero >= 1")
        for chiave in filtri:
            if chiave in ASSENTI:
                raise ValueError(f"filtro {chiave}: {ASSENTI[chiave]}")
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtro {ignoti[0]!r} sconosciuto per casa.it; ammessi: {sorted(self.FILTRI)}")
        if "tipo_affitto" in filtri and contratto != "affitto":
            raise ValueError("filtro tipo_affitto: esiste solo con contratto 'affitto'")
        geo = [k for k in GEO if k in filtri]
        if len(geo) > 1:
            raise ValueError("filtri raggio e mappa_area: la mappa ne applica uno solo, scegline uno")
        if geo and "zona" in filtri:
            raise ValueError(f"filtri zona e {geo[0]}: la mappa sostituisce la zona con la forma, scegline uno")

        p: dict[str, list[str]] = {"tr": [contratto]}
        da: dict[str, str] = {}
        intervalli: dict[str, list[str]] = {}
        for chiave, valore in filtri.items():
            if chiave in GEO:
                continue
            f = FILTRI_DEF[chiave]
            codici = _codici(chiave, f, valore)
            if f.estremo is not None:
                intervalli.setdefault(f.parametro, ["", ""])[f.estremo] = codici[0]
                continue
            if f.parametro in p and f.parametro not in LISTE and p[f.parametro] != codici:
                raise ValueError(f"filtri {da[f.parametro]} e {chiave} usano lo stesso parametro "
                                 f"{f.parametro} con valori diversi: scegline uno")
            p[f.parametro] = [*p.get(f.parametro, []), *(c for c in codici if c not in p.get(f.parametro, []))]
            da.setdefault(f.parametro, chiave)
        for par, (mn, mx) in intervalli.items():
            p[par] = [f"{mn}-{mx}"]

        if contratto == "affitto-breve":
            if p.get("propertyTypeGroup", ["casa"]) != ["casa"]:
                raise ValueError("contratto affitto-breve: l'interfaccia lo offre solo per le case (gruppo case-vacanza)")
            p["propertyTypeGroup"] = ["case-vacanza"]
        tipologie = p.get("propertyTypes", [])
        gruppi = {GRUPPO_DI_TIPOLOGIA[t] for t in tipologie}
        if len(gruppi) > 1:
            raise ValueError(f"filtro tipologia: {tipologie} appartengono a categorie diverse {sorted(gruppi)}")
        if gruppi:
            (g,) = gruppi
            scelto = p.setdefault("propertyTypeGroup", [g])[0]
            if scelto != g and not (g == "casa" and scelto in GRUPPI_CON_TIPOLOGIE_CASA):
                raise ValueError(f"filtro tipologia {tipologie}: non esiste nella categoria {scelto!r}")
        p.setdefault("propertyTypeGroup", ["casa"])

        livello = p.pop("lift", None)
        if livello and livello[0] not in ("true", "false"):
            p["liftIfAtLeast"] = livello
        elif livello:
            p["lift"] = livello

        if "zona" in filtri:
            if slug(comune) != "trieste":
                raise ValueError(f"filtro zona: i valori ammessi sono le zone di Trieste, non di {comune!r}")
        elif not geo:
            p["q"] = [self.luogo(comune).hkey]
        if pagina > 1:
            p["page"] = [str(pagina)]
        return {k: p[k] for k in ORDINE if k in p}

    def parametri(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> list[tuple[str, str]]:
        """Coppie (parametro, valore) nell'ordine degli uri del sito."""
        geo = [k for k in GEO if k in filtri]
        if geo:
            raise ValueError(f"filtro {geo[0]}: non ha un URL /srp (lì risponde 503), la ricerca passa da corpo_mappa()")
        return [(k, ",".join(v)) for k, v in self._parametri(comune, contratto, pagina, filtri).items()]

    def url(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        coppie = self.parametri(comune, contratto, pagina, **filtri)
        return "/srp?" + "&".join(f"{k}={quote(v, safe=',-')}" for k, v in coppie)

    def corpo_mappa(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> dict:
        """Il corpo di POST /portal-srp/api/v1/apireq che la pagina /srp/map manda per un cerchio o un'area disegnata.

        Come la mappa, la forma sostituisce il luogo (q). Il cerchio ha centro nel centro del comune."""
        p = self._parametri(comune, contratto, pagina, filtri)
        q = _q_object(p)
        if "raggio" in filtri:
            r = filtri["raggio"]
            if isinstance(r, bool) or not isinstance(r, int) or r < 1:
                raise ValueError(f"filtro raggio: valore {r!r} non ammesso, servono i metri come intero >= 1")
            centro = self.luogo(comune).centro
            if centro is None:
                raise ValueError(f"filtro raggio: il comune {comune!r} è un hkey senza centro, passa il nome del comune")
            q["geocircle"] = [{"circle": {"center": list(centro), "radius": r}}]
        elif "mappa_area" in filtri:
            q["geopolygon"] = [{"polygon": _poligono(filtri["mappa_area"])}]
        else:
            raise ValueError("corpo_mappa serve solo con raggio o mappa_area")
        return {"qObject": q, "parameters": {"sortType": p["sortType"][0]} if "sortType" in p else {},
                "apireqParams": {"pathname": "/srp/map", "domain": "casa.it", "locale": "it", "esapi": ESAPI,
                                 "publisherPage": False, "size": 20},
                "pageType": "searchMap"}

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        if any(k in filtri for k in GEO):
            return self._cerca_mappa(comune, contratto, pagina, filtri)
        r = self.trasporto.get(self.url(comune, contratto, pagina, **filtri), intestazioni=INTESTAZIONI_HTML)
        # Senza risultati casa.it risponde 404 con la SRP completa e search.noResults404 vero.
        if r.status not in (200, 404):
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        try:
            s = estrai_stato(r.testo).get("search")
        except ErroreStruttura:
            raise ErroreStruttura(f"HTTP {r.status} senza stato su {r.url}") from None
        if isinstance(s, dict) and r.status == 404 and not s.get("noResults404"):
            raise ErroreStruttura(f"HTTP 404 senza noResults404 su {r.url}")
        return self._pagina(s)

    def _cerca_mappa(self, comune: str, contratto: str, pagina: int, filtri: dict) -> Pagina:
        """Le due POST della mappa: apireq traduce il qObject nella query, search-map restituisce gli annunci."""
        a = self.trasporto.post("/portal-srp/api/v1/apireq", self.corpo_mappa(comune, contratto, pagina, **filtri))
        if a.status != 200:
            raise ErroreStruttura(f"HTTP {a.status} su {a.url}")
        j = a.json()
        corpo = {**j["apireq"], "uri": j.get("uri"), "queryParameters": j.get("queryParameters")}
        if pagina > 1:
            corpo["apireqList"] = {**corpo["apireqList"], "page": pagina}
        r = self.trasporto.post("/portal-srp/api/v1/search-map", corpo)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        return self._pagina(r.json())

    def _pagina(self, s) -> Pagina:
        """Lo stato di una ricerca (search della SRP o risposta di search-map): list, total, paginator."""
        if not isinstance(s, dict) or "list" not in s:
            raise ErroreStruttura("stato senza list")
        letto_il = adesso_utc()
        annunci = [annuncio_da_lista(x, self.ORIGINE, letto_il) for x in s.get("list") or [] if x.get("id")]
        pag = s.get("paginator") or {}
        return Pagina(totale=_int(s.get("total")), pagine=_int(pag.get("totalPages")), annunci=annunci)


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        try:
            p = Client(t).cerca("Trieste", "vendita", prezzo_max=300000)
        except Bloccato as e:
            sys.exit(f"Bloccato da DataDome: {e}. Risolvi la verifica nella tab di Chrome e rilancia.")
    print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.locali, a.titolo, a.url)
