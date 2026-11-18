"""Client di ricerca per properstar.it (pagina dei risultati renderizzata lato server).

properstar.it non espone nell'HAR un'API JSON same-origin per la lista: la pagina
`/italia/<area>/<in-vendita|affitare>/appartamento-casa[/<filtro>][?p=N]` contiene tutto lo stato
della ricerca in `window.__INITIAL_STATE__ = {...}` (JSON puro, non escapato). Il client chiede
quella pagina HTML al trasporto e ne legge `search.results` (listings, total, params) ed
`entities` (agenzie, luoghi).

Il sito e' dietro Azure WAF, quindi TRASPORTO = "chrome". Il client non fa HTTP da solo:
usa `trasporto.get(...)` e, solo per mappa_area, `trasporto.post(...)` verso /auth/token e search-api.listglobally.com,
le stesse chiamate che la pagina fa da sola. Su blocco il trasporto (o questo client) solleva `Bloccato`
e ci si ferma, senza riprovare.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sys
import unicodedata
from urllib.parse import urlencode

from dataclasses import dataclass

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Dettaglio, Pagina, adesso_utc  # noqa: F401

PORTALE = "properstar"

# Slug del luogo nel percorso /italia/<slug>/... . `comune` vuol dire il comune, come sugli altri portali; un'area piu'
# grande si chiede solo per nome esplicito (AREE). Lo slug "trieste" il sito lo risolve negli Stati Uniti: il comune e'
# trieste-loc (entities.place type locality, link "Trieste" nella pagina), uti-giuliana e' la provincia
# (administrative_area_level_2, con Muggia, Duino-Aurisina e gli altri comuni). Gli altri comuni usano lo slug del nome.
COMUNI = {"trieste": "trieste-loc"}
AREE = {
    "provincia di trieste": "uti-giuliana",
    "uti giuliana": "uti-giuliana",
    "friuli venezia giulia": "friuli-venezia-giulia",
    "italia": "",  # tutta Italia: /italia/<contratto>/<tipo>, come il link "Italia" dell'interfaccia
}
TIPI_COMUNE = ("locality", "administrative_area_level_3")  # tipi Google di un comune italiano

SEGMENTO_CONTRATTO = {"vendita": "in-vendita", "affitto": "affitare"}

_STATO = "window.__INITIAL_STATE__ ="
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Pagina di blocco Azure WAF / Front Door (non presente nell'HAR: euristica prudente).
_SEGNI_WAF = ("azure web application firewall", "the request is blocked", "x-azure-ref", "azurewaf")

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# Ricerca su un'area della mappa: la pagina del server non la esegue (containers-SearchPage Ze.get salta la ricerca
# con neLat/.../zoom in query), la fa la SPA con POST search-api.listglobally.com/api/v2/searches e
# Authorization: Bearer <token di POST /auth/token>. Scoperta in risultati/scoperta/properstar/mappa_area.md.
SEARCH_API = "https://search-api.listglobally.com"
PORTALE_ID = 10262


class ErroreStruttura(RuntimeError):
    """La pagina non contiene lo stato atteso, o il portale non ha applicato i filtri richiesti."""


def luogo(comune: str) -> tuple[str, bool]:
    """Nome del comune (o di un'area di AREE) -> (slug del percorso, e' un comune). Accetta anche uno slug pronto."""
    norm = unicodedata.normalize("NFKD", comune.strip().lower()).encode("ascii", "ignore").decode()
    nome = re.sub(r"[^a-z0-9]+", " ", norm).strip()
    if not nome:
        raise ValueError("comune vuoto")
    if nome in AREE:
        return AREE[nome], False
    slug = COMUNI.get(nome) or nome.replace(" ", "-")
    if not _SLUG.match(slug):
        raise ValueError(f"comune {comune!r}: impossibile ricavare uno slug properstar")
    # Dopo la risposta _verifica_luogo controlla che il sito abbia risolto lo slug su un comune italiano.
    return slug, True


def estrai_stato(html: str) -> dict:
    """Legge window.__INITIAL_STATE__ = {...} (JSON letterale)."""
    i = html.find(_STATO)
    if i < 0:
        basso = html[:20000].lower()
        if any(s in basso for s in _SEGNI_WAF):
            raise Bloccato("pagina di blocco Azure WAF")
        raise ErroreStruttura("window.__INITIAL_STATE__ non trovato nella pagina")
    try:
        stato, _ = json.JSONDecoder().raw_decode(html[i + len(_STATO):].lstrip())
    except json.JSONDecodeError as e:
        raise ErroreStruttura(f"__INITIAL_STATE__ non e' JSON valido: {e}") from e
    if not isinstance(stato, dict):
        raise ErroreStruttura("__INITIAL_STATE__ non e' un oggetto")
    return stato


def _int(v) -> int | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    cifre = re.sub(r"[^\d]", "", str(v))
    return int(cifre) if cifre else None


def _str(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _testo_localizzato(voci, lingua: str = "it") -> str | None:
    """[{language, text}] -> testo nella lingua preferita, altrimenti il primo."""
    if not isinstance(voci, list) or not voci:
        return None
    for v in voci:
        if v.get("language") == lingua and v.get("text"):
            return v["text"]
    return voci[0].get("text")


def _nome_luogo(luogo: dict | None) -> str | None:
    if not luogo:
        return None
    loc = luogo.get("localizations") or []
    for l in loc:
        if l.get("language") == "it":
            return l.get("name") or l.get("locality")
    return (loc[0].get("name") or loc[0].get("locality")) if loc else None


def _prezzo(p: dict) -> int | None:
    if not p or p.get("show") is False:
        return None
    valori = p.get("values") or []
    for v in valori:
        if v.get("type") == "Original" and v.get("currencyId") == "EUR":
            return _int(v.get("value"))
    for v in valori:
        if v.get("currencyId") == "EUR":
            return _int(v.get("value"))
    return None


def _mq(area: dict) -> int | None:
    for v in (area or {}).get("values") or []:
        if (v.get("unit") or {}).get("id") == "SquareMeter":
            return _int(v.get("living"))
    if ((area or {}).get("unit") or {}).get("id") == "SquareMeter":
        return _int(area.get("living"))
    return None


def _ribasso_pct(p: dict, prezzo: int | None) -> float | None:
    """price.lastChange = {type: "Drop", amount: [{value, currencyId}]} -> % rispetto al prezzo precedente."""
    lc = (p or {}).get("lastChange") or {}
    if lc.get("type") != "Drop" or not prezzo:
        return None
    calo = next((_int(a.get("value")) for a in lc.get("amount") or [] if a.get("currencyId") == "EUR"), None)
    if not calo:
        return None
    return round(calo / (prezzo + calo) * 100, 2)


def _agenzia(x: dict, entita: dict) -> str | None:
    conti = entita.get("account") or {}
    ids = [x.get("contactAccountId")] + [r.get("id") for r in x.get("relatedAccounts") or [] if r.get("type") == "Office"]
    for i in ids:
        nome = _testo_localizzato((conti.get(str(i)) or {}).get("name"))
        if nome:
            return nome
    utenti = entita.get("user") or {}
    for c in x.get("contactUsers") or []:
        ag = (utenti.get(str(c.get("userId"))) or {}).get("agency") or {}
        if ag.get("name"):
            return ag["name"]
    return None


def annuncio_da_lista(x: dict, entita: dict, origine: str, letto_il: str) -> Annuncio:
    prezzo = _prezzo(x.get("price") or {})
    num = x.get("numberOf") or {}
    loc = x.get("location") or {}
    foto = sorted(((x.get("resources") or {}).get("pictures") or {}).get("items") or [],
                  key=lambda f: f.get("displayOrder") or 0)
    tipo = (x.get("subType") or {}).get("name") or (x.get("type") or {}).get("name")
    caratteristiche = [c for c in (
        tipo,
        f"{num['bedrooms']} camere da letto" if num.get("bedrooms") is not None else None,
        f"anno di costruzione {x['constructionYear']}" if x.get("constructionYear") else None,
        "progetto/nuova costruzione" if x.get("class") == "UnitListing" else None,
    ) if c]
    return Annuncio(
        portale=PORTALE,
        id=str(x["id"]),
        url=f"{origine}/immobili/{x['id']}",
        titolo=_str(x.get("automaticTitle")) or _testo_localizzato(x.get("title")),
        prezzo=prezzo,
        mq=_mq(x.get("area") or {}),
        locali=_str(num.get("rooms")),
        bagni=_str(num.get("bathrooms")),
        piano=None,  # non presente nei dati della lista
        ascensore=None,  # non presente nei dati della lista
        indirizzo=_str(loc.get("address1")) if loc.get("showAddress") else None,
        zona=_str(loc.get("city")),
        lat=loc.get("latitude"),
        lon=loc.get("longitude"),
        descrizione=None,  # la lista non contiene la descrizione
        foto=foto[0].get("url") if foto else None,
        agenzia=_agenzia(x, entita),
        condizione=None,  # non presente nei dati della lista
        caratteristiche=caratteristiche,
        ribasso_pct=_ribasso_pct(x.get("price") or {}, prezzo),
        letto_il=letto_il,
    )


def _verifica_luogo(stato: dict, filtri: dict, comune: bool) -> None:
    """Lo slug deve essere stato risolto su un luogo italiano (es. "trieste" porta negli USA) e, per un comune, su un
    comune: uno slug che il sito risolve su una provincia o una regione allargherebbe la ricerca in silenzio."""
    pid = ((filtri.get("location") or {}).get("placeId"))
    ent = stato.get("entities") or {}
    posto = (ent.get("place") or {}).get(pid) or (ent.get("placeLocalization") or {}).get(pid)
    if not pid or not posto:
        raise ErroreStruttura("luogo della ricerca non presente nello stato")
    if posto.get("countryISO") != "IT":
        raise ValueError(f"lo slug richiesto e' stato risolto su {_nome_luogo(posto)!r} "
                         f"({posto.get('countryISO')}), non in Italia: usa lo slug corretto (es. 'trieste-loc')")
    if comune and posto.get("type") not in TIPI_COMUNE:
        raise ValueError(f"lo slug richiesto e' stato risolto su {_nome_luogo(posto)!r} di tipo {posto.get('type')!r}, "
                         f"non un comune; per un'area piu' grande usa {', '.join(sorted(AREE))}")


# Filtri come li scrive l'interfaccia di properstar.it (generateSearchUrl in /assets/app.*.js, 03/11/2026):
# i filtri annidati diventano chiavi piatte con il punto (price.min, area.living.min, numberOf.rooms.min) e le liste
# chiavi ripetute (qs, arrayFormat "repeat"). Il server legge la stessa query (router.location.query ->
# search.results.params.filters), quindi dopo ogni risposta si controlla che il filtro sia stato applicato.

TIPOLOGIE = {  # slug del percorso -> filters.types applicato
    "proprieta": None, "appartamento": ["Apartment"], "casa": ["House"], "edificio": ["Building"],
    "terreno": ["PlotOfLand"], "parcheggio": ["ParkingPlace"], "locale-commerciale": ["CommercialGround"],
}
# camera (Room) e barca (Boat) hanno uno slug ma 0 annunci in tutta Italia (03/11/2026): una ricerca vuota per
# costruzione non si distingue da un filtro rotto, quindi il client non li offre.
TIPOLOGIA_BASE = "appartamento-casa"  # "Case e appartamenti", la scelta iniziale dell'interfaccia

AMENITA = ("AirConditioning", "Alarm", "Concierge", "DiningRoom", "Dishwasher", "ElectricVehicleCharging",
           "FitnessCenter", "Furnished", "Garage", "Garden", "HotTub", "Lift", "Sauna", "Security",
           "SteamRoom", "SwimmingPool", "Tennis", "Terrace", "WashingMachine", "WineCellar", "YogaStudio")
# amenities=Balcony e amenities=Parking contano piu' annunci di quelli che le faccette della stessa ricerca danno con
# quella dotazione (Trieste provincia 03/11: Balcony 46 contro 22, Parking 34 contro 22): il portale non offre un filtro
# stretto. Le chiavi canoniche e i valori di dotazioni rimandano alle chiavi oneste che mandano lo stesso parametro.
LARGHE = {"Balcony": ("balcone", "balcone_o_affini"), "Parking": ("posto_auto", "posto_auto_o_affini")}
_NON_STRETTO = ("properstar non offre un filtro stretto: amenities={0} restituisce piu' annunci di quelli che le "
                "faccette del sito contano con quella dotazione; usa {1}=True (stesso parametro, significato onesto)")
SOTTOTIPI = ("Apartment", "TerracedApartment", "Attic", "Room", "Duplex", "Loft", "StudioApartment", "Penthouse",
             "House", "Bungalow", "Chalet", "Castle", "Farmhouse", "MansionHouse", "TerracedHouse", "Cottage", "Villa",
             "AdjacentHouse", "DetachedHouse", "TwinHouse", "Semi-detachedHouse", "Townhouse", "Plot", "Garage",
             "Office", "Farm", "PlotOfLand")
PAESAGGI = ("Mountains", "SkiSlopes", "Greenery", "Sky", "SwimmingPool", "City", "Countryside", "Hills", "Garden",
            "Forest", "Village", "Street", "Courtyard", "Sea", "Harbor", "Lake", "Monument", "Park")
TIPI_VISTA = ("Unobstructed", "Panoramic", "Obstructed", "Slight", "Outstanding")
ESPOSIZIONI = ("West", "North", "South", "SouthWest", "NorthEast", "East", "NorthWest", "SouthEast")
PIANI = ("Basement", "GardenLevel", "GroundFloor", "SingleStorey", "SplitLevel", "TopFloor",
         *(str(n) for n in range(-10, 164)))
VICINANZE = ("Highway", "SkiSlope", "Airport", "Station", "DayCare", "Golf", "PublicPool", "PublicParking", "Tennis",
             "University", "SportCenter", "Movies", "PrimarySchool", "Sea", "TGVStation", "TownCentre", "MiddleSchool",
             "SeaPort", "Supermarket", "Taxi", "Lake", "Doctor", "PublicTransport", "Park", "Beach",
             "ConventionCenter", "HospitalClinic", "Shops")
CONDIZIONI = ("New", "Renovated", "GoodCondition", "ToRenovate")
ANNI = {"2027Plus": (2027, None), "2020s": (2020, 2029), "2010s": (2010, 2019), "2000s": (2000, 2009),
        "1990s": (1990, 1999), "1980s": (1980, 1989), "1970s": (1970, 1979), "1960s": (1960, 1969),
        "1950s": (1950, 1959), "Pre1950s": (None, 1949)}
ORDINAMENTI = {"updateDate": "-updateDate", "priceAsc": "price", "priceDesc": "-price",
               "biggestPriceDrops": "-priceLastDropAmount", "biggestPriceIncreases": "-priceLastIncreaseAmount",
               "livingArea": "-areaLiving", "numberOfRooms": "-numberOfRooms"}


@dataclass(frozen=True)
class Spec:
    tipo: str                     # intero | lista | bool | valore | anni | data | segmento
    param: str                    # nome nella query
    valori: tuple = ()            # ammessi per lista/valore
    fisso: str | None = None      # per i booleani che sono un elemento di una lista (ascensore -> amenities=Lift)


SPEC: dict[str, Spec] = {
    "prezzo_min": Spec("intero", "price.min"), "prezzo_max": Spec("intero", "price.max"),
    "mq_min": Spec("intero", "area.living.min"), "mq_max": Spec("intero", "area.living.max"),
    "locali_min": Spec("intero", "numberOf.rooms.min"), "locali_max": Spec("intero", "numberOf.rooms.max"),
    "camere_letto_min": Spec("intero", "numberOf.bedrooms.min"),
    "camere_letto_max": Spec("intero", "numberOf.bedrooms.max"),
    "bagni_min": Spec("intero", "numberOf.bathrooms.min"), "bagni_max": Spec("intero", "numberOf.bathrooms.max"),
    "sottotipologia": Spec("lista", "st", SOTTOTIPI),
    "dotazioni": Spec("lista", "amenities", AMENITA),
    "ascensore": Spec("bool", "amenities", fisso="Lift"),
    "balcone_o_affini": Spec("bool", "amenities", fisso="Balcony"),
    "terrazzo": Spec("bool", "amenities", fisso="Terrace"), "giardino": Spec("bool", "amenities", fisso="Garden"),
    "box_auto": Spec("bool", "amenities", fisso="Garage"),
    "posto_auto_o_affini": Spec("bool", "amenities", fisso="Parking"),
    "arredato": Spec("bool", "amenities", fisso="Furnished"),
    "aria_condizionata": Spec("bool", "amenities", fisso="AirConditioning"),
    "vista_mare": Spec("bool", "viewLandscapes", fisso="Sea"),
    "vista_paesaggio": Spec("lista", "viewLandscapes", PAESAGGI),
    "tipo_vista": Spec("lista", "viewTypes", TIPI_VISTA),
    "esposizione": Spec("lista", "orientations", ESPOSIZIONI),
    "piano": Spec("lista", "floors", PIANI),
    "vicinanza": Spec("lista", "proximities", VICINANZE),
    "stato_immobile": Spec("lista", "conditionId", CONDIZIONI),
    "anno_costruzione": Spec("anni", "constructionYear", tuple(ANNI)),
    "disponibile_entro": Spec("data", "availableFrom.max"),
    "lusso": Spec("segmento", "lusso"),
    "monolocale": Spec("segmento", "studio"),
    "nuova_costruzione": Spec("bool", "projects", fisso="true"),
    "includi_dintorni": Spec("dintorni", "exactLocation"),
    "ordinamento": Spec("valore", "sort", tuple(ORDINAMENTI)),
    "mappa_area": Spec("area", "location.polygons"),
}
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _lista(chiave: str, v, ammessi: tuple) -> list[str]:
    voci = [v] if isinstance(v, (str, int)) and not isinstance(v, bool) else v
    if not isinstance(voci, (list, tuple)) or not voci:
        raise ValueError(f"{chiave}: atteso un valore o una lista non vuota di {ammessi[:6]}..., non {v!r}")
    voci = [str(x) for x in voci]
    fuori = [x for x in voci if x not in ammessi]
    if fuori:
        raise ValueError(f"{chiave}: valori non ammessi {fuori}; ammessi {', '.join(ammessi[:30])}")
    return voci


def coppie_query(contratto: str, filtri: dict) -> list[tuple[str, str]]:
    """**filtri -> coppie (param, valore) della query, nell'ordine e nella forma dell'interfaccia."""
    liste: dict[str, list[str]] = {}
    semplici: list[tuple[str, str]] = []
    for chiave, v in filtri.items():
        s = SPEC.get(chiave)
        larga = next((a for a, (canonica, _) in LARGHE.items() if canonica == chiave), None)
        if larga:
            raise ValueError(f"{chiave}: " + _NON_STRETTO.format(larga, LARGHE[larga][1]))
        if chiave == "dotazioni":
            for a in [v] if isinstance(v, str) else v if isinstance(v, (list, tuple)) else []:
                if a in LARGHE:
                    raise ValueError(f"dotazioni {a!r}: " + _NON_STRETTO.format(a, LARGHE[a][1]))
        if s is None:
            raise ValueError(f"filtro {chiave!r} sconosciuto; ammessi: {', '.join(sorted(SPEC))}")
        if s.tipo == "intero":
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{chiave}: atteso un intero >= 0, non {v!r}")
            semplici.append((s.param, str(v)))
        elif s.tipo == "bool":
            if v is not True:
                raise ValueError(f"{chiave}: atteso True, non {v!r}")
            if s.param in ("amenities", "viewLandscapes"):
                liste.setdefault(s.param, []).append(s.fisso)
            else:
                semplici.append((s.param, s.fisso))
        elif s.tipo == "lista":
            if chiave == "stato_immobile" and contratto == "affitto":
                raise ValueError("stato_immobile: l'interfaccia non lo applica agli affitti (conditionId solo in vendita)")
            liste.setdefault(s.param, []).extend(_lista(chiave, v, s.valori))
        elif s.tipo in ("segmento", "dintorni"):
            if v is not True:
                raise ValueError(f"{chiave}: atteso True, non {v!r}")
        elif s.tipo == "valore":
            if v not in s.valori:
                raise ValueError(f"{chiave}: valore {v!r} non ammesso; ammessi {', '.join(s.valori)}")
            semplici.append((s.param, ORDINAMENTI[v]))
        elif s.tipo == "anni":
            for i, pill in enumerate(_lista(chiave, v, s.valori)):
                lo, hi = ANNI[pill]
                semplici += [(f"constructionYear.{i}.{k}", str(x)) for k, x in (("min", lo), ("max", hi)) if x is not None]
        elif s.tipo == "area":
            area(v)  # va nel corpo della POST a search-api, non nella query
        elif s.tipo == "data":
            if not isinstance(v, str) or not _DATA.match(v):
                raise ValueError(f"{chiave}: attesa una data AAAA-MM-GG, non {v!r}")
            semplici.append((s.param, v))
    for param, voci in liste.items():
        semplici += [(param, x) for x in dict.fromkeys(voci)]
    return semplici


def area(v) -> tuple[float, float, float, float]:
    """mappa_area "sud,ovest,nord,est" (gradi decimali) -> (sud, ovest, nord, est)."""
    try:
        sud, ovest, nord, est = (float(x) for x in str(v).split(","))
    except ValueError:
        raise ValueError(f"mappa_area: attesa \"sud,ovest,nord,est\" in gradi decimali, non {v!r}") from None
    if isinstance(v, bool) or not (-90 <= sud < nord <= 90 and -180 <= ovest < est <= 180):
        raise ValueError(f"mappa_area: rettangolo non valido {v!r} (serve sud < nord, ovest < est)")
    return sud, ovest, nord, est


def anello(sud: float, ovest: float, nord: float, est: float) -> str:
    """Rettangolo -> anello chiuso "lng lat,..." come oe.Y0(bounds) nel bundle (NE, NO, SO, SE, NE)."""
    return ",".join(f"{lng} {lat}" for lng, lat in ((est, nord), (ovest, nord), (ovest, sud), (est, sud), (est, nord)))


def _applicato(chiave: str, v, filtri: dict, par: dict) -> bool:
    """Il filtro richiesto compare nello stato che il server ha usato per la ricerca?"""
    s = SPEC[chiave]
    if s.tipo == "intero":
        nodo = filtri
        for parte in s.param.split("."):
            nodo = (nodo or {}).get(parte)
        return _int(nodo) == v
    if s.param in ("amenities", "viewLandscapes", "viewTypes", "orientations", "floors", "proximities", "conditionId"):
        attesi = [s.fisso] if s.tipo == "bool" else _lista(chiave, v, s.valori)
        return set(attesi) <= {str(x) for x in filtri.get(s.param) or []}
    if chiave == "sottotipologia":
        return set(_lista(chiave, v, s.valori)) <= set(filtri.get("subTypes") or [])
    if chiave == "lusso":
        return filtri.get("luxury") is True
    if chiave == "monolocale":
        return filtri.get("studios") is True
    if chiave == "nuova_costruzione":
        return filtri.get("classes") == ["ProjectListing"]
    if chiave == "includi_dintorni":
        return (filtri.get("location") or {}).get("exact") is False
    if chiave == "ordinamento":
        return par.get("sort") == ORDINAMENTI[v]
    if chiave == "anno_costruzione":
        return bool(filtri.get("constructionYear"))
    if chiave == "mappa_area":
        return True  # applicata nella POST a search-api, controllata da _cerca_area
    if chiave == "disponibile_entro":
        return str((filtri.get("availableFrom") or {}).get("max") or "").startswith(v)
    raise AssertionError(chiave)


class Client:
    ORIGINE = "https://www.properstar.it"
    TRASPORTO = "chrome"  # Azure WAF
    ALTRE_ORIGINI = (SEARCH_API,)  # la pagina la chiama da sola (mappa_area.md)
    FILTRI = frozenset(SPEC) | {"tipologia"}

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def percorso(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        """Percorso con query, come lo costruisce l'interfaccia: /italia/<luogo>/<contratto>/<tipo>?<filtri>&p=N."""
        if contratto not in SEGMENTO_CONTRATTO:
            raise ValueError(f"contratto {contratto!r}: ammessi {', '.join(SEGMENTO_CONTRATTO)}")
        if isinstance(pagina, bool) or not isinstance(pagina, int) or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, non {pagina!r}")
        tipo = filtri.pop("tipologia", None)
        if tipo is not None and tipo not in TIPOLOGIE:
            raise ValueError(f"tipologia {tipo!r}: ammesse {', '.join(TIPOLOGIE)}")
        slug, e_comune = luogo(comune)
        if filtri.get("includi_dintorni") and not e_comune:
            raise ValueError(f"includi_dintorni vale solo per un comune, non per l'area {comune!r}")
        # Per un comune il sito per costruzione include i dintorni (Muggia 03/11: 28 annunci, 16 dei primi 20 a
        # Trieste, nessuno segnato isNearby); exactLocation=true lo restringe al comune (3). L'interfaccia lo scrive
        # dopo sort e p (generateSearchUrl). Le aree amministrative sono gia' esatte senza parametro.
        esatto = [("exactLocation", "true")] if e_comune and not filtri.get("includi_dintorni") else []
        coppie = coppie_query(contratto, filtri) + ([("p", str(pagina))] if pagina > 1 else []) + esatto
        # luxury=true e studios=true in query il server li ignora: l'interfaccia li scrive solo come segmenti,
        # nell'ordine di pe() in app.js (.../<tipo>/lusso/studio)
        extra = [SPEC[k].param for k in ("lusso", "monolocale") if filtri.get(k)]
        segmenti = ("italia", slug, SEGMENTO_CONTRATTO[contratto], tipo or TIPOLOGIA_BASE, *extra)
        p = "".join(f"/{x}" for x in segmenti if x)
        return p + ("?" + urlencode(coppie) if coppie else "")

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso = self.percorso(comune, contratto, pagina, **filtri)
        # con mappa_area la pagina del server serve solo per i filtri; la pagina dei risultati la sceglie skip
        pagina_html = 1 if "mappa_area" in filtri else pagina
        if pagina_html != pagina:
            percorso = self.percorso(comune, contratto, pagina_html, **filtri)
        r = self.trasporto.get(percorso, intestazioni=INTESTAZIONI_HTML)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        stato = estrai_stato(r.testo)
        ris = (stato.get("search") or {}).get("results")
        if not isinstance(ris, dict) or not isinstance(ris.get("listings"), list):
            raise ErroreStruttura("stato senza search.results.listings")
        richiesta = ris.get("params") or {}
        applicati = richiesta.get("filters") or {}
        par = richiesta.get("params") or {}
        e_comune = luogo(comune)[1]
        _verifica_luogo(stato, applicati, e_comune)
        if e_comune and not filtri.get("includi_dintorni") and (applicati.get("location") or {}).get("exact") is not True:
            raise ErroreStruttura(f"il portale non ha ristretto la ricerca al comune {comune!r} (location.exact non vero)")
        self._verifica_filtri(applicati, par, contratto, pagina_html, filtri)
        if "mappa_area" in filtri:
            ris, entita = self._cerca_area(applicati, par, pagina, area(filtri["mappa_area"])), {}
        else:
            entita = stato.get("entities") or {}

        letto_il = adesso_utc()
        annunci = [annuncio_da_lista(x, entita, self.ORIGINE, letto_il)
                   for x in ris["listings"] if x.get("id") is not None and not x.get("isNearby")]
        totale = _int(ris.get("total"))
        recuperabili = _int(ris.get("totalRetrievable"))
        dim = _int(par.get("pageSize")) or 20
        noti = [x for x in (totale, recuperabili) if x is not None]
        base = min(noti) if noti else None
        pagine = math.ceil(base / dim) if base is not None else None
        return Pagina(totale=totale, pagine=pagine, annunci=annunci)

    def _cerca_area(self, applicati: dict, par: dict, pagina: int, rett: tuple) -> dict:
        """La stessa ricerca, ristretta al rettangolo, come la fa la SPA: i filtri sono quelli che il server ha
        ricavato dalla query (search.results.params.filters), con location.polygons = [rettangolo]."""
        t = self.trasporto.post("/auth/token", {"forceRefresh": False})
        token = t.json().get("token") if t.status == 200 else None
        if not token:
            raise ErroreStruttura(f"POST /auth/token: HTTP {t.status}, nessun token")
        ring = anello(*rett)
        # liste vuote come null, come nel corpo che la pagina manda (cattura_search_api.txt)
        filtri = {k: (None if v == [] else v) for k, v in copy.deepcopy(applicati).items()}
        filtri["location"] = {**filtri.get("location", {}), "polygons": [ring], "excludedPolygons": [],
                              "polygon": ring, "viewport": ring}
        dim = _int(par.get("pageSize")) or 20
        query = {"count": dim, "saveInDb": "false", "mode": "Light", "sort": par.get("sort"),
                 "skip": (pagina - 1) * dim if pagina > 1 else None}
        r = self.trasporto.post(
            f"{SEARCH_API}/api/v2/searches?" + urlencode({k: v for k, v in query.items() if v is not None}),
            {"search": {"filters": {"listings": filtri}, "currencyId": "EUR", "portalId": PORTALE_ID}},
            intestazioni={"Accept": "application/json, text/plain, */*", "Authorization": f"Bearer {token}"})
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        ris = r.json()
        loc = (((ris.get("search") or {}).get("filters") or {}).get("listings") or {}).get("location") or {}
        if len(loc.get("polygons") or []) != 1:
            raise ErroreStruttura(f"search-api non ha applicato mappa_area (location: {json.dumps(loc)[:300]})")
        if not isinstance(ris.get("listings"), list):
            raise ErroreStruttura("risposta di search-api senza listings")
        sud, ovest, nord, est = rett
        fuori = [x["id"] for x in ris["listings"] if not x.get("isNearby")
                 and (la := (x.get("location") or {}).get("latitude")) is not None
                 and (lo := x["location"].get("longitude")) is not None
                 and not (sud <= la <= nord and ovest <= lo <= est)]
        if fuori:
            raise ErroreStruttura(f"mappa_area: annunci fuori dal rettangolo {rett}: {fuori[:5]}")
        return ris

    @staticmethod
    def _verifica_filtri(applicati: dict, par: dict, contratto: str, pagina: int, filtri: dict) -> None:
        """Il sito ignora in silenzio i parametri che non riconosce: controllo che li abbia applicati."""
        tx = applicati.get("transactionType")
        if tx != {"vendita": "Sell", "affitto": "Rent"}[contratto]:
            raise ErroreStruttura(f"contratto applicato {tx!r}, richiesto {contratto!r}")
        if _int(par.get("page")) not in (pagina, None):
            raise ErroreStruttura(f"pagina applicata {par.get('page')!r}, attesa {pagina}")
        tipo = filtri.get("tipologia")
        if tipo is not None and TIPOLOGIE[tipo] is not None and applicati.get("types") != TIPOLOGIE[tipo]:
            raise ErroreStruttura(f"tipologia {tipo!r} non applicata (types={applicati.get('types')!r})")
        for chiave, v in filtri.items():
            if chiave != "tipologia" and not _applicato(chiave, v, applicati, par):
                raise ErroreStruttura(f"il portale non ha applicato {chiave}={v!r} (stato: {json.dumps(applicati)[:300]})")


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        try:
            p = Client(t).cerca("Trieste", "vendita")
        except Bloccato as e:
            sys.exit(f"Bloccato da Azure WAF: {e}. Risolvi la verifica nella tab di Chrome e rilancia.")
    print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.titolo, a.url)
