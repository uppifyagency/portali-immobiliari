"""Client di ricerca per cercacasa.it (pagina di ricerca renderizzata lato server, Angular SSR).

La pagina `/<vendita|affitto>/<luogo>[/<tipologia>]?<filtri>` contiene i risultati nel blocco
`<script id="serverApp-state" type="application/json">` (TransferState di Angular). Lì dentro
c'è la risposta, già scaricata dal server, di `https://api.cercacasa.it/api/listings/search`
con `total`, `page` (da 0), `limit` (15) e `listings`. L'API sta su un'altra origine, quindi il client
chiede la pagina HTML al trasporto e legge quel blocco. La forma dell'URL dei filtri viene dal bundle
Angular (generateSearchURL, generateQueryParams, setParamsForVenditaAndAffitto), vedi README.

Nessun antibot visto: TRASPORTO = "http". Il client non fa HTTP da solo: usa solo
`trasporto.get(...)`. Su blocco il trasporto solleva `Bloccato` e ci si ferma.
"""
from __future__ import annotations

import html as _html
import json
import math
import re
import sys
from urllib.parse import urlencode

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "cercacasa"
URL_API_RICERCA = "https://api.cercacasa.it/api/listings/search"

def rotta_scheda(x: dict) -> str | None:
    """La rotta della scheda come la sceglie il bundle (generateRouteByContractAndStatus): `/<rotta>/<slug>`.
    Con l'id al posto dello slug il sito risponde 200 con la pagina vuota (provato il 17/11/2026)."""
    if x.get("contract_type") == "buy":
        return "venduto" if x.get("status") == "sold" else "in-asta" if x.get("is_auction") else "in-vendita"
    if x.get("contract_type") == "rent":
        return "affittato" if x.get("status") == "rented" else "in-affitto"
    return None

# Province elencate nel footer della pagina dell'HAR (slug usati nei percorsi /vendita/provincia-<slug>/).
PROVINCE = frozenset("""
agrigento alessandria ancona aosta arezzo ascoli-piceno asti avellino bari barletta-andria-trani belluno
benevento bergamo biella bologna bolzano brescia brindisi cagliari caltanissetta campobasso caserta catania
catanzaro chieti como cosenza cremona crotone cuneo enna fermo ferrara firenze foggia forli-cesena frosinone
genova gorizia grosseto imperia isernia l'aquila la-spezia latina lecce lecco livorno lodi lucca macerata
mantova massa matera messina milano modena monza-e-della-brianza napoli novara nuoro oristano padova palermo
parma pavia perugia pesaro-urbino pescara piacenza pisa pistoia pordenone potenza prato ragusa ravenna
reggio-di-calabria reggio-nell'emilia rieti rimini roma rovigo salerno sassari savona siena siracusa sondrio
sud-sardegna taranto teramo terni torino trapani trento treviso trieste udine varese venezia verbania
vercelli verona vibo-valentia vicenza viterbo
""".split())

TIPOLOGIE = {  # id categoria -> (etichetta, [(valore, etichetta)]): P.LIST_OF_TYPOLOGY del bundle
    "case": ("Case", [("appartamenti", "Appartamenti"), ("attici", "Attici"), ("baite", "Baite"), ("bifamiliari", "Bifamiliare"), ("case-semi-indipendenti", "Case Semi-indipendenti"), ("case-indipendenti", "Case Indipendenti"), ("tenuta-complesso", "Tenuta Complesso"), ("terratetti", "Terratetti"), ("castelli", "Castelli"), ("case-coloniche", "Case Coloniche"), ("loft", "Loft"), ("mansarde", "Mansarde"), ("multiproprieta", "Multiproprieta"), ("palazzi", "Palazzi"), ("residence", "Residence"), ("rustici-casali", "Rustici Casali"), ("ville", "Ville"), ("ville-a-schiera", "Ville a Schiera"), ("masserie", "Masserie"), ("case-singole", "Case Singole"), ("porzione-di-casa", "Porzione di casa"), ("abitazione-tipica", "Abitazione tipica"), ("trulli", "Trulli"), ("vacanza-trulli", "Vacanza Trulli"), ("porzione-di-villa", "Porzione di villa")]),
    "nuove": ("Nuove Costruzioni", [("nuove-costruzioni", "Nuove Costruzioni")]),
    "garage": ("Garage", [("box", "Box"), ("posto-auto", "Posto Auto"), ("garage", "Garage")]),
    "commerciali": ("Attività commerciali", [("albergo", "Albergo"), ("uffici", "Uffici"), ("hotel", "Hotel"), ("laboratori", "Laboratori"), ("locali-commerciali", "Locali Commerciali"), ("negozi", "Negozi"), ("pizzerie-pub", "Pizzerie / Pub"), ("ristorante-pizzeria-pub", "Ristorante / Pizzerie / Pub"), ("attivita-commerciali", "Attività Commerciali"), ("aziende-agricole", "Aziende Agricole"), ("bar", "Bar"), ("ristoranti", "Ristoranti"), ("stabili", "Stabili"), ("stabilimento-balneare", "Stabilimento Balneare"), ("agriturismi", "Agriturismi"), ("capannoni-industriali", "Capannoni Industriali"), ("forni", "Forni"), ("immobile-storico", "Immobile Storico"), ("tabaccheria", "Tabaccheria"), ("sala-giochi-scommesse", "Sala e Giochi Scommesse"), ("alimentari", "Negozio di alimentari"), ("abbigliamento", "Negozio di abbigliamento"), ("pizza-al-taglio", "Pizza al taglio"), ("parrucchiere", "Parrucchiere"), ("palestra", "Palestra"), ("lavanderia-tintoria", "Lavanderia / Tintoria"), ("gelateria", "Gelateria"), ("fabbricato-misto", "Fabbricato Misto"), ("edicola", "Edicola"), ("discoteca", "Discoteca"), ("centro-estetico", "Centro Estetico"), ("bed-breakfast", "Bed and Breakfast")]),
    "magazzini": ("Magazzini", [("magazzini", "Magazzini")]),
    "terreni": ("Terreni", [("terreno-industriale", "Terreno Industriale"), ("annesso-agricolo", "Annesso Agricolo"), ("terreni", "Terreni"), ("terreno-agricolo", "Terreno Agricolo"), ("terreno-edificabile", "Terreno Edificabile"), ("terreno-edificabile-commerciale", "Terreno Edificabile Commerciale"), ("terreno-edificabile-produttivo", "Terreno Edificabile Produttivo")]),
    "stanze": ("Stanze", [("stanza-camera", "Stanza Camera"), ("stanza-/-camera", "Stanza / Camera")]),
    "altro": ("Altro", [("posto-barca", "Posto Barca"), ("qualsiasi-tipo", "Qualsiasi tipo"), ("altro", "Altro"), ("bungalow-piazzola", "Bungalow Piazzola"), ("pertinenze", "Pertinenze"), ("non-definito", "Non definito")]),
    "vacanze": ("Vacanze", [("vacanza-stanza-camera", "Camera"), ("vacanza-casa-singola", "Casa Singola"), ("vacanza-masseria", "Vacanza Masseria"), ("vacanza-villa", "Vacanza Villa"), ("vacanza-villa-a-schiera", "Vacanza Villa a schiera"), ("vacanza-villino", "Vacanza Villini"), ("vacanza-colonica", "Vacanza Colonica"), ("vacanza-loft", "Vacanza Loft"), ("vacanza-mansarda", "Vacanza Mansarda"), ("vacanza-pertinenze", "Vacanza Pertinenze"), ("vacanza-palazzo", "Vacanza Palazzo"), ("vacanza-residence", "Vacanza Residence"), ("vacanza-rustico-casale", "Vacanza Rustico Casale"), ("vacanza-appartamento", "Vacanza Appartamento"), ("vacanza-appartamenti-indipendente", "Vacanza Appartamenti Indirpendenti"), ("vacanza-attico", "Vacanza Attico"), ("vacanza-bifamiliare", "Vacanza Bifamiliare"), ("vacanza-casa-semi-indipendente", "Vacanza Casa Semi-indipendente"), ("vacanza-tenuta-complesso", "Vacanza Tenuta-Complesso"), ("vacanza-terratetto", "Vacanza Terratetto"), ("vacanza-albergo", "Vacanza Albergo"), ("vacanza-agriturismo", "Vacanza Agriturismo"), ("vacanza-hotel", "Vacanza Hotel")]),
}

ZONE_TRIESTE = {  # slug di percorso -> etichetta: figli 'Zona' del comune Trieste in api/zones/search
    "altura-trieste": "Altura",
    "baiamonti-valmaura-trieste": "Baiamonti - Valmaura",
    "barcola-trieste": "Barcola",
    "basovizza-trebiciano-trieste": "Basovizza - Trebiciano",
    "borgo-san-sergio-trieste": "Borgo San Sergio",
    "borgo-teresiano-trieste": "Borgo Teresiano",
    "campanelle-costalunga-trieste": "Campanelle - Costalunga",
    "chiarbola-ponziana-trieste": "Chiarbola - Ponziana",
    "citta-vecchia-trieste": "Città Vecchia",
    "cologna-universita-trieste": "Cologna - Università",
    "conconello-trieste": "Conconello",
    "costiera-grignano-trieste": "Costiera - Grignano",
    "giardino-pubblico-trieste": "Giardino Pubblico",
    "gretta-trieste": "Gretta",
    "largo-barriera-ospedale-maggiore-trieste": "Largo Barriera - Ospedale Maggiore",
    "longera-trieste": "Longera",
    "opicina-trieste": "Opicina",
    "prosecco-trieste": "Prosecco",
    "roiano-trieste": "Roiano",
    "san-giacomo-trieste": "San Giacomo",
    "san-giovanni-trieste": "San Giovanni",
    "san-luigi-rozzol-trieste": "San Luigi - Rozzol",
    "san-vito-campi-elisi-trieste": "San Vito - Campi Elisi",
    "santa-croce-trieste": "Santa Croce",
    "sant'anna-trieste": "Sant'Anna",
    "scorcola-trieste": "Scorcola",
    "settefontane-trieste": "Settefontane",
}

# Valori di percorso che coincidono con l'id di una categoria: da soli il sito li legge come la categoria intera.
SOTTOTIPI_AMBIGUI = frozenset(v for c in TIPOLOGIE.values() for v, _ in c[1]) & frozenset(TIPOLOGIE)
# "stanza-/-camera" contiene una barra e romperebbe il percorso: resta fuori, "stanza-camera" copre le stanze.
SOTTOTIPI = {v: f"{c[0]}: {e}" for c in TIPOLOGIE.values() for v, e in c[1] if "/" not in v}

INTERI = {  # chiave -> parametro query (main.filtersList -> generateQueryParams)
    "prezzo_min": "con_prezzo_min", "prezzo_max": "con_prezzo_max",
    "mq_min": "con_superficie_min", "mq_max": "con_superficie_max",
    "locali_min": "con_locali_min", "locali_max": "con_locali_max",
    "bagni_min": "con_bagni",
    # Il sito scambia i due parametri della rata (setParamsForVenditaAndAffitto: finanziamentoMin ->
    # maxFinanziamento). Il client sceglie quello che dà l'effetto del nome: provato, finanziamentoMax=500
    # dà prezzi da 225000 in su, finanziamentoMin=500 prezzi fino a 220000.
    "rata_min": "finanziamentoMax", "rata_max": "finanziamentoMin",
}
MULTIPLI = {  # chiave -> (parametro query ripetuto, {valore: etichetta})
    "stato_immobile": ("condizioni", {"abitabile": "Abitabile", "buone-condizioni": "Buone Condizioni",
                       "da-ristrutturare": "Da Ristrutturare", "nuova-costruzione": "Nuova Costruzione",
                       "ottime-condizioni": "Ottime Condizioni", "ristrutturato": "Ristrutturato",
                       "seminuovo": "Seminuovo"}),
    "contesto": ("contesto", {"ultra-popolare": "Ultra popolare", "popolare": "Popolare", "economico": "Economico",
                 "civile": "Civile", "medio-signorile": "Medio Signorile", "signorile": "Signorile"}),
    "piano": ("piano", {"interrato": "Piano Interrato", "terra": "Piano Terra", "rialzato": "Piano Rialzato"}),
    "tipo_garage": ("garage", {"singolo": "Garage Singolo", "doppio": "Garage doppio"}),
    "tipo_giardino": ("giardino", {"privato": "Giardino Privato", "comune": "Giardino Comune"}),
    "riscaldamento": ("riscaldamento", {"a-pavimento": "A Pavimento", "autonomo": "Autonomo",
                      "centralizzato": "Centralizzato", "inesistente": "Inesistente"}),
    "cucina": ("con_cucina", {"abitabile": "Abitabile", "angolo-cottura": "Angolo Cottura", "a-vista": "A Vista",
               "cucinotto": "Cucinotto", "semi-abitabile": "Semi abitabile"}),
}
# Chiave booleana -> valore del parametro ripetuto `extra` (FILTER_EXTRA). Più chiavi insieme restringono (E):
# misura 03/11 a Trieste, balcone 25, terrazzo 7, entrambi 2 (risultati/scoperta/cercacasa/combinazioni.py).
EXTRA = {
    "balcone": "balcone", "terrazzo": "terrazzo", "ascensore": "ascensore", "arredato": "arredamento",
    "cantina": "cantina", "taverna": "taverna", "box_auto": "garage", "giardino": "garden",
}
BOOLEANI = {"asta": ("asta_immobiliare", "asta"), "in_esclusiva": ("in_esclusiva", "true")}
# Chiavi canoniche che il sito offre come valore del segmento tipologia: mandano quel valore.
SEGMENTO_BOOLEANI = {"nuova_costruzione": "nuove", "posto_auto": "posto-auto"}
TUTTA_ITALIA = "*"  # comune senza luogo: /vendita/, obbligatorio con raggio e mappa_area
ORDINAMENTI = ("Più Recenti", "Meno Recenti", "Meno Costosi", "Più Costosi", "Meno Grandi", "Più Grandi", "Più Salvati")
ORDINE_BASE = "Più Recenti"
SPECIALI = frozenset({"zona", "tipologia", "sottotipologia", "ordinamento", "raggio", "mappa_area"})


def slug(nome: str) -> str:
    """La funzione V del bundle: spazi in trattini, minuscolo."""
    return nome.strip().replace(" ", "-").lower()


def _intero(chiave: str, v) -> int:
    if type(v) is not int or v < 1:
        raise ValueError(f"filtro {chiave}: serve un intero >= 1, non {v!r}")
    return v


def _lista(chiave: str, v, ammessi) -> list[str]:
    valori = [v] if isinstance(v, str) else v
    if not isinstance(valori, (list, tuple)) or not valori:
        raise ValueError(f"filtro {chiave}: serve un valore o una lista di valori, non {v!r}")
    fuori = [x for x in valori if x not in ammessi]
    if fuori:
        raise ValueError(f"filtro {chiave}: valori non ammessi {fuori}; ammessi: {sorted(ammessi)}")
    return list(dict.fromkeys(valori))


def _punti(chiave: str, testo, minimo: int, extra: int = 0) -> list[float]:
    """'lat,lon' (o 'lat,lon,metri' per raggio) separati da ';' -> numeri. Errore esplicito se malformato."""
    try:
        gruppi = [[float(x) for x in g.split(",")] for g in str(testo).split(";")]
    except ValueError:
        gruppi = []
    if len(gruppi) < minimo or any(len(g) != 2 + extra for g in gruppi):
        forma = "'lat,lon,metri'" if extra else "'lat,lon;lat,lon;lat,lon[;...]'"
        raise ValueError(f"filtro {chiave}: serve {forma}, non {testo!r}")
    return gruppi


REGIONI = frozenset("""abruzzo basilicata calabria campania emilia-romagna friuli-venezia-giulia lazio liguria lombardia
marche molise piemonte puglia sardegna sicilia toscana trentino-alto-adige umbria valle-d'aosta veneto""".split())


def luogo(comune: str) -> str:
    """'Trieste' -> 'comune-trieste'; 'Provincia di Trieste' -> 'provincia-trieste'; 'Regione Lazio' -> 'regione-lazio'."""
    c = comune.strip()
    if not c:
        raise ValueError("comune vuoto")
    if c.lower().startswith("regione "):
        r = slug(c[len("regione "):])
        if r not in REGIONI:
            raise ValueError(f"regione {c!r}: ammesse {sorted(REGIONI)}")
        return f"regione-{r}"
    if c.lower().startswith("provincia di "):
        p = slug(c[len("provincia di "):])
        if p not in PROVINCE:
            raise ValueError(f"provincia {c!r} non tra le 107 note (PROVINCE)")
        return f"provincia-{p}"
    return f"comune-{slug(c)}"


_STATO = re.compile(r'<script id="serverApp-state" type="application/json">(.*?)</script>', re.S)
INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class ErroreStruttura(RuntimeError):
    """La pagina non contiene lo stato atteso (struttura del sito cambiata)."""


def estrai_stato(html: str) -> dict:
    """Legge il TransferState Angular (<script id="serverApp-state">)."""
    m = _STATO.search(html)
    if not m:
        raise ErroreStruttura("<script id=serverApp-state> non trovato nella pagina")
    testo = m.group(1)
    try:
        return json.loads(testo)
    except json.JSONDecodeError:
        # Versioni Angular più vecchie escapano il JSON: &q; &a; &l; &g; &s;
        for a, b in (("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")):
            testo = testo.replace(a, b)
        return json.loads(_html.unescape(testo))


def risultati_ricerca(stato: dict) -> dict:
    """La risposta di /api/listings/search nella cache SSR.

    Se la ricerca dà zero annunci la pagina riprova togliendo filtri (retrySearchAnnunci) e mostra quei
    "suggerimenti": nel TransferState restano più risposte, e quella vuota è la ricerca vera. In quel caso il
    client restituisce la risposta vuota, mai i suggerimenti (provato: /vendita/comune-trieste/nuove ha una
    voce con total 0 e una con total 286).
    """
    trovate = []
    for k, v in stato.items():
        if k.startswith("__") or not isinstance(v, dict):
            continue
        if str(v.get("u", "")).split("?")[0] == URL_API_RICERCA:
            if v.get("s") not in (None, 200):
                raise ErroreStruttura(f"l'API di ricerca ha risposto {v.get('s')} durante il rendering SSR")
            b = v.get("b")
            if isinstance(b, dict) and "listings" in b:
                trovate.append(b)
    if not trovate:
        raise ErroreStruttura(f"nessuna risposta di {URL_API_RICERCA} nel TransferState")
    vuote = [b for b in trovate if not b["listings"]]
    if len(trovate) > 1 and not vuote:
        raise ErroreStruttura(f"{len(trovate)} risposte di ricerca e nessuna vuota: non so quale sia quella vera")
    return vuote[0] if vuote else trovate[0]


def _int(v) -> int | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    cifre = re.sub(r"[^\d]", "", str(v))
    return int(cifre) if cifre else None


def _str(v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return s or None


def _testo_slug(v) -> str | None:
    s = _str(v)
    return s.replace("-", " ") if s else None


_EXTRA = (("garage", "garage"), ("parking_space", "posto auto"), ("terrace", "terrazzo"),
          ("balcony", "balcone"), ("garden", "giardino"), ("basement", "cantina"), ("tavern", "taverna"),
          ("sottotetto", "sottotetto"), ("soppalco", "soppalco"), ("furnished", "arredato"),
          ("alarm", "allarme"))


def annuncio_da_lista(x: dict, url: str, letto_il: str) -> Annuncio:
    descr = next((d.get("string") for d in x.get("descriptions") or [] if d.get("language") == "it"), None)
    coord = (x.get("location") or {}).get("coordinates") or []
    lat = x.get("lat") if x.get("lat") is not None else (coord[1] if len(coord) == 2 else None)
    lon = x.get("lng") if x.get("lng") is not None else (coord[0] if len(coord) == 2 else None)
    if x.get("map_visibility") is False:
        lat = lon = None
    citta, prov = _str(x.get("city")), _str(x.get("state"))
    caratteristiche = []
    if x.get("typology"):
        caratteristiche.append(_testo_slug(x["typology"]))
    caratteristiche += [nome for chiave, nome in _EXTRA if x.get(chiave) is True]
    if x.get("energy_class"):
        caratteristiche.append(f"classe energetica {x['energy_class']}")
    if x.get("construction_year"):
        caratteristiche.append(f"anno {x['construction_year']}")
    if x.get("condominium_fees"):
        caratteristiche.append(f"spese condominio {x['condominium_fees']} €")
    if x.get("is_auction"):
        caratteristiche.append("asta")
    if x.get("private_negotiation"):
        caratteristiche.append("trattativa riservata")
    immagini = x.get("images") or []
    return Annuncio(
        portale=PORTALE,
        id=str(x["id"]),
        url=url,
        titolo=_str(x.get("title")),
        # Con trattativa riservata la card non mostra il prezzo (il JSON lo contiene comunque).
        prezzo=None if x.get("private_negotiation") else _int(x.get("price")),
        mq=_int(x.get("surface")) or _int(x.get("mq_commercial")),
        locali=_str(x.get("total_rooms")),
        bagni=_str(x.get("bathrooms")),
        piano=_str(x.get("floors")),
        ascensore=x.get("elevator") if isinstance(x.get("elevator"), bool) else None,
        indirizzo=_str(x.get("address")) if x.get("address_visible") else None,
        # `city` a volte è il comune ("Trieste"), a volte il quartiere ("Centro citta'", "San Pelagio").
        zona=citta if citta and (not prov or citta.lower() != prov.lower()) else None,
        lat=lat,
        lon=lon,
        descrizione=_str(descr),
        foto=_str(immagini[0].get("url")) if immagini else None,
        agenzia=_str((x.get("agency") or {}).get("name")),
        condizione=_testo_slug(x.get("conditions")),
        caratteristiche=[c for c in caratteristiche if c],
        ribasso_pct=None,  # la lista non riporta un prezzo precedente
        letto_il=letto_il,
    )


class Client:
    ORIGINE = "https://cercacasa.it"
    TRASPORTO = "http"
    FILTRI = frozenset(INTERI) | frozenset(MULTIPLI) | frozenset(EXTRA) | frozenset(BOOLEANI) | frozenset(SEGMENTO_BOOLEANI) | SPECIALI

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def percorso(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> tuple[str, list]:
        """Percorso e query (lista di coppie, i multipli ripetono il parametro) come li costruisce l'interfaccia."""
        if contratto not in ("vendita", "affitto"):
            raise ValueError(f"contratto {contratto!r}: ammessi 'vendita' e 'affitto'")
        if type(pagina) is not int or pagina < 1:
            raise ValueError("pagina deve essere un intero >= 1")
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtri sconosciuti per cercacasa: {ignoti}")
        f = {k: v for k, v in filtri.items() if v is not None}
        q: list[tuple[str, object]] = []

        aree = [k for k in ("zona", "raggio", "mappa_area") if k in f]
        if len(aree) > 1:
            raise ValueError(f"{aree}: il sito accetta un solo modo di scegliere il luogo")
        italia = comune.strip() == TUTTA_ITALIA
        cerchio_o_area = [k for k in ("raggio", "mappa_area") if k in f]
        if cerchio_o_area and not italia:
            # Con il cerchio o l'area il sito non usa il comune (provato: comune-muggia e comune-trieste con lo
            # stesso cerchio danno lo stesso totale): il luogo è il disegno, come nell'interfaccia.
            raise ValueError(f"{cerchio_o_area[0]} sostituisce il comune {comune!r}: il sito cerca solo nel disegno. "
                             f"Passa comune={TUTTA_ITALIA!r}")
        if "raggio" in f:
            (lat, lon, metri), = _punti("raggio", f["raggio"], 1, extra=1)
            if metri <= 0:
                raise ValueError("filtro raggio: metri > 0")
            dove = ""
            q += [("metodo", 2), ("coords", lon), ("coords", lat), ("raggio", int(metri))]
        elif "mappa_area" in f:
            punti = _punti("mappa_area", f["mappa_area"], 3)
            if punti[0] != punti[-1]:
                punti.append(punti[0])
            dove = ""
            q += [("metodo", 4), ("coords", ",".join(f"{lon},{lat}" for lat, lon in punti))]
        elif "zona" in f:
            if slug(comune) != "trieste":
                raise ValueError(f"filtro zona: elenco zone noto solo per Trieste, non per {comune!r}")
            dove = "/zona-" + ",".join(_lista("zona", f["zona"], ZONE_TRIESTE))
        elif italia:
            dove = ""
        else:
            dove = "/" + luogo(comune)

        segmenti = {}  # chiave -> valore del segmento tipologia; più chiavi devono chiedere lo stesso valore
        if "tipologia" in f:
            if f["tipologia"] not in TIPOLOGIE:
                raise ValueError(f"tipologia {f['tipologia']!r}: ammesse {sorted(TIPOLOGIE)}")
            segmenti["tipologia"] = f["tipologia"]
        if "sottotipologia" in f:
            scelti = _lista("sottotipologia", f["sottotipologia"], SOTTOTIPI)
            if len(scelti) == 1 and scelti[0] in SOTTOTIPI_AMBIGUI:
                raise ValueError(f"sottotipologia {scelti[0]!r} da sola coincide con la categoria omonima: "
                                 f"usa tipologia={scelti[0]!r} o aggiungi un altro sottotipo")
            intera = next((cid for cid, (_, vs) in TIPOLOGIE.items() if set(scelti) == {v for v, _ in vs}), None)
            segmenti["sottotipologia"] = intera or ",".join(scelti)
        for k, val in SEGMENTO_BOOLEANI.items():
            if k in f:
                if f[k] is not True:
                    raise ValueError(f"filtro {k}: booleano, passa True (o ometti), non {f[k]!r}")
                segmenti[k] = val
        if len(set(segmenti.values())) > 1:
            raise ValueError(f"{sorted(segmenti)} occupano lo stesso segmento del percorso con valori diversi "
                             f"{sorted(set(segmenti.values()))}: il sito ne applica uno solo, usane uno")
        tipo = "/" + next(iter(segmenti.values())) if segmenti else ""

        for k, par in INTERI.items():
            if k in f:
                if k.startswith("rata_") and contratto != "vendita":
                    raise ValueError(f"filtro {k}: la rata del mutuo esiste solo in vendita")
                q.append((par, _intero(k, f[k])))
        for k, (par, ammessi) in MULTIPLI.items():
            if k in f:
                q += [(par, x) for x in _lista(k, f[k], ammessi)]
        for k, val in EXTRA.items():
            if k in f:
                if f[k] is not True:
                    raise ValueError(f"filtro {k}: booleano, passa True (o ometti), non {f[k]!r}")
                q.append(("extra", val))
        for k, (par, val) in BOOLEANI.items():
            if k in f:
                if f[k] is not True:
                    raise ValueError(f"filtro {k}: booleano, passa True (o ometti), non {f[k]!r}")
                q.append((par, val))
        # Senza ordinamento il sito ordina in modo instabile e lo stesso annuncio esce in pagina 1 e in pagina 2
        # (provato su comune-trieste); con "Più Recenti" le pagine 1-3 sono disgiunte. Il client lo usa come base.
        ordine = f.get("ordinamento", ORDINE_BASE)
        if ordine not in ORDINAMENTI:
            raise ValueError(f"ordinamento {ordine!r}: ammessi {list(ORDINAMENTI)}")
        q.append(("ordine", ordine))
        if pagina > 1:
            q.append(("pagina", pagina))
        # Senza luogo né tipologia generateSearchURL dà "/vendita/": la barra finale resta, come nella sonda.
        return f"/{contratto}{dove}{tipo}" if dove or tipo else f"/{contratto}/", q

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso, query = self.percorso(comune, contratto, pagina, **filtri)
        if query:
            percorso += "?" + urlencode(query)
        r = self.trasporto.get(percorso, intestazioni=INTESTAZIONI_HTML)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        b = risultati_ricerca(estrai_stato(r.testo))
        letto_il = adesso_utc()
        pagina_url = self.ORIGINE + percorso
        annunci = []
        for x in b.get("listings") or []:
            if not x.get("id"):
                continue
            rotta = rotta_scheda(x)
            if rotta and x.get("slug"):
                url = f"{self.ORIGINE}/{rotta}/{x['slug']}"
            else:
                url = f"{pagina_url}#{x['id']}"
            annunci.append(annuncio_da_lista(x, url, letto_il))
        totale, limite = _int(b.get("total")), _int(b.get("limit"))
        pagine = math.ceil(totale / limite) if totale is not None and limite else None
        return Pagina(totale=totale, pagine=pagine, annunci=annunci)


if __name__ == "__main__":
    from ..trasporto import HttpTrasporto

    with HttpTrasporto(Client.ORIGINE) as t:
        try:
            p = Client(t).cerca("Trieste")
        except Bloccato as e:
            sys.exit(f"Bloccato: {e}. Non si riprova.")
    print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.locali, a.titolo, a.url)
