"""Client di ricerca per gate-away.com (portale per acquirenti esteri, pagine in inglese).

gate-away.com non usa un'API JSON same-origin per la lista risultati: la pagina SSR
`/properties/<regione>/<provincia>/<comune>[?pag=N]` contiene lo stato della ricerca in
`<script>var preloadedData = {...}</script>` (chiavi `data.searchResults.data` e `count`) e,
in piu', un blocco JSON-LD `ItemList` con titolo, URL e condizione di ogni annuncio.
Il client chiede quella pagina HTML al trasporto e unisce le due fonti per id.

Il sito e' dietro Cloudflare, quindi TRASPORTO = "chrome". Il client non fa HTTP da solo:
usa solo `trasporto.get(...)`. Su blocco il trasporto solleva `Bloccato` e ci si ferma.
"""
from __future__ import annotations

import json
import math
import re
import sys
from urllib.parse import quote, urlencode

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "gate-away"
URL_FOTO = "https://images.gate-away.com/properties2/{utente}/{id}/main/800/{file}"  # come nello srcset 2x delle card

# Annunci per pagina osservati nell'HAR (pagina 1: 12, pagina 2: 9, count 21).
# preloadedData non riporta il numero di pagine: lo si ricava da count / 12.
ANNUNCI_PER_PAGINA = 12

# Grammatica dell'URL (scoperta al clic del 03/11/2026 e getUrlBreadcrumbFromParams in gateaway-common.js, vedi
# risultati/scoperta/gateaway/appunti.md): /<categoria>[/<luogo>]?<filtri>. Una sola tipologia va nel percorso
# (/apartments/...), piu' tipologie in /properties con cat[]=; un solo luogo (regione, provincia, comune o area)
# va nel percorso; cerchio e poligono disegnati sulla mappa vanno in query (circle[]=, polygon[]=) senza luogo.
# Il percorso SSR del comune richiede regione e provincia: qui i comuni della provincia di Trieste e alcune
# citta' grandi (nomi italiani e slug inglesi di UrlDictionary.en.js). Per gli altri si passa
# "regione/provincia/comune" letto dall'URL di una ricerca fatta su gate-away.com.
PERCORSI_COMUNI = {
    "trieste": "friuli-venezia-giulia/trieste/trieste",
    "muggia": "friuli-venezia-giulia/trieste/muggia",
    "duino aurisina": "friuli-venezia-giulia/trieste/duino-aurisina",
    "san dorligo della valle": "friuli-venezia-giulia/trieste/san-dorligo-della-valle",
    "sgonico": "friuli-venezia-giulia/trieste/sgonico",
    "monrupino": "friuli-venezia-giulia/trieste/monrupino",
    "udine": "friuli-venezia-giulia/udine/udine",
    "gorizia": "friuli-venezia-giulia/gorizia/gorizia",
    "roma": "lazio/rome/rome", "rome": "lazio/rome/rome",
    "milano": "lombardy/milan/milan", "milan": "lombardy/milan/milan",
    "venezia": "veneto/venice/venice", "venice": "veneto/venice/venice",
    "firenze": "tuscany/florence/florence", "florence": "tuscany/florence/florence",
    "napoli": "campania/naples/naples", "naples": "campania/naples/naples",
    "torino": "piedmont/turin/turin", "turin": "piedmont/turin/turin",
}
TUTTA_ITALIA = "*"  # comune senza luogo: tutta Italia, base per regione, provincia, area, raggio e mappa_area
_PERCORSO = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+$")

# UrlDictionary.en.js (category, region, zone) e gateaway-common.js (provincia -> regione), letti il 03/11/2026.
PROVINCE = {"agrigento": "sicily", "alessandria": "piedmont", "ancona": "marche", "aosta": "valle-d-aosta", "arezzo": "tuscany", "ascoli-piceno": "marche", "asti": "piedmont", "avellino": "campania", "bari": "puglia", "barletta-andria-trani": "puglia", "belluno": "veneto", "benevento": "campania", "bergamo": "lombardy", "biella": "piedmont", "bologna": "emilia-romagna", "bolzano": "trentino-south-tyrol", "brescia": "lombardy", "brindisi": "puglia", "cagliari": "sardinia", "caltanissetta": "sicily", "campobasso": "molise", "caserta": "campania", "catania": "sicily", "catanzaro": "calabria", "chieti": "abruzzo", "como": "lombardy", "cosenza": "calabria", "cremona": "lombardy", "crotone": "calabria", "cuneo": "piedmont", "enna": "sicily", "fermo": "marche", "ferrara": "emilia-romagna", "florence": "tuscany", "foggia": "puglia", "forli-cesena": "emilia-romagna", "frosinone": "lazio", "gallura-north-east-sardinia": "sardinia", "genoa": "liguria", "gorizia": "friuli-venezia-giulia", "grosseto": "tuscany", "imperia": "liguria", "isernia": "molise", "l-aquila": "abruzzo", "la-spezia": "liguria", "latina": "lazio", "lecce": "puglia", "lecco": "lombardy", "livorno": "tuscany", "lodi": "lombardy", "lucca": "tuscany", "macerata": "marche", "mantua": "lombardy", "massa-carrara": "tuscany", "matera": "basilicata", "medio-campidano": "sardinia", "messina": "sicily", "milan": "lombardy", "modena": "emilia-romagna", "monza-and-brianza": "lombardy", "naples": "campania", "novara": "piedmont", "nuoro": "sardinia", "ogliastra": "sardinia", "oristano": "sardinia", "padua": "veneto", "palermo": "sicily", "parma": "emilia-romagna", "pavia": "lombardy", "perugia": "umbria", "pesaro-and-urbino": "marche", "pescara": "abruzzo", "piacenza": "emilia-romagna", "pisa": "tuscany", "pistoia": "tuscany", "pordenone": "friuli-venezia-giulia", "potenza": "basilicata", "prato": "tuscany", "ragusa": "sicily", "ravenna": "emilia-romagna", "reggio-calabria": "calabria", "reggio-emilia": "emilia-romagna", "republic-of-san-marino": "emilia-romagna", "rieti": "lazio", "rimini": "emilia-romagna", "rome": "lazio", "rovigo": "veneto", "salerno": "campania", "sassari": "sardinia", "savona": "liguria", "siena": "tuscany", "sondrio": "lombardy", "sulcis-iglesiente": "sardinia", "syracuse": "sicily", "taranto": "puglia", "teramo": "abruzzo", "terni": "umbria", "trapani": "sicily", "trento": "trentino-south-tyrol", "treviso": "veneto", "trieste": "friuli-venezia-giulia", "turin": "piedmont", "udine": "friuli-venezia-giulia", "varese": "lombardy", "venice": "veneto", "verbano-cusio-ossola": "piedmont", "vercelli": "piedmont", "verona": "veneto", "vibo-valentia": "calabria", "vicenza": "veneto", "viterbo": "lazio"}
ZONE = ["abruzzo-national-park", "aeolian-islands", "amalfi-coast", "canavese", "chianti", "cilento", "cinque-terre", "elba-island", "garfagnana", "gargano", "idro-lake", "itria-valley", "lago-trasimeno", "lake-bolsena", "lake-bracciano", "lake-como", "lake-garda", "lake-iseo", "lake-maggiore", "lake-orta", "langhe", "lunigiana", "maremma", "medio-campidano", "montferrat", "ogliastra", "sabina", "salento", "tigullio-gulf", "tuscia", "valpolicella", "versilia"]
REGIONI = ["abruzzo", "basilicata", "calabria", "campania", "emilia-romagna", "friuli-venezia-giulia", "lazio", "liguria", "lombardy", "marche", "molise", "piedmont", "puglia", "sardinia", "sicily", "trentino-south-tyrol", "tuscany", "umbria", "valle-d-aosta", "veneto"]
TIPOLOGIE = {"houses": 1, "farmhouses": 9, "apartments": 11, "loft-penthouses": 12, "commercial-properties": 2,
             "lands": 6, "unusual-properties": 3}  # checkbox property-type_2 del pulsante Property Type
CONDIZIONI = {"restaurato": 1, "nuovo": 2, "parzialmente-restaurato": 3, "da-restaurare": 4, "non-indicato": 5}
ORDINAMENTI = ("latest", "priceasc", "pricedesc", "city")  # menu Sort by; "Most Relevant" = nessun orderby
DISTANZE = {"distanza_mare_max": ("coastline", (1, 5, 10, 20, 30, 50)),
            "distanza_sci_max": ("skiresort", (1, 5, 10, 20, 30, 50)),
            "distanza_aeroporto_max": ("airport", (10, 20, 30, 50, 100))}
CARATTERISTICHE = {"giardino": "garden", "con_terreno": "land", "box_auto": "garage", "cantina": "cellar",
                   "piscina": "pool", "terrazzo": "terrace"}  # More filters > Main Features
INTERI = {"prezzo_min": "min_price", "prezzo_max": "max_price", "camere_min": "bedrooms", "bagni_min": "bathrooms",
          "mq_min": "sqm_min", "mq_max": "sqm_max", "terreno_mq_min": "land_sqm"}
LIMITI_INTERI = {"camere_min": range(1, 6), "bagni_min": range(1, 4)}
LUOGHI = ("regione", "provincia", "area_turistica", "raggio", "mappa_area")
FILTRI = frozenset({"tipologia", "stato_immobile", "ordinamento", *DISTANZE, *CARATTERISTICHE, *INTERI, *LUOGHI})

_PRELOADED = re.compile(r"var\s+preloadedData\s*=\s*")
_LDJSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TITOLO_CODA = re.compile(r"\s*\[\d+\]\s*\|\s*Gate-away.*$")

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class ErroreStruttura(RuntimeError):
    """La pagina non contiene lo stato atteso (struttura del sito cambiata)."""


def percorso_comune(comune: str) -> str:
    c = comune.strip().strip("/").lower()
    if c.startswith("properties/"):
        c = c[len("properties/"):]
    if _PERCORSO.match(c):
        return c
    if c.replace("-", " ") in PERCORSI_COMUNI:
        return PERCORSI_COMUNI[c.replace("-", " ")]
    raise ValueError(
        f"comune {comune!r}: percorso gate-away sconosciuto. Comuni noti: {', '.join(sorted(PERCORSI_COMUNI))}. "
        "Passa 'regione/provincia/comune' (es. 'friuli-venezia-giulia/trieste/trieste', dall'URL "
        "/properties/... di una ricerca su gate-away.com) oppure aggiungilo a PERCORSI_COMUNI."
    )


def estrai_preloaded(html: str) -> dict:
    m = _PRELOADED.search(html)
    if not m:
        raise ErroreStruttura("var preloadedData non trovato nella pagina")
    try:
        dati, _ = json.JSONDecoder().raw_decode(html[m.end():])
    except json.JSONDecodeError as e:
        raise ErroreStruttura(f"preloadedData non e' JSON valido: {e}") from e
    if dati.get("status") != "ok":
        raise ErroreStruttura(f"preloadedData.status = {dati.get('status')!r}")
    return dati["data"]


def estrai_ldjson(html: str) -> dict[str, dict]:
    """id -> item RealEstateListing dal JSON-LD ItemList (titolo, url, condizione)."""
    voci: dict[str, dict] = {}
    for blocco in _LDJSON.findall(html):
        try:
            d = json.loads(blocco)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("@type") != "ItemList":
            continue
        for el in d.get("itemListElement") or []:
            item = el.get("item") or {}
            ident = str(item.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
            if ident.isdigit():
                voci[ident] = item
    return voci


def _int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    cifre = re.sub(r"[^\d]", "", str(v))
    return int(cifre) if cifre else None


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _str(v) -> str | None:
    return None if v is None or str(v).strip() == "" else str(v).strip()


_CONDIZIONI = {"NewCondition": "nuovo", "UsedCondition": "usato", "RefurbishedCondition": "ristrutturato"}


def annuncio_da_lista(x: dict, ld: dict | None, origine: str, letto_il: str) -> Annuncio:
    ident = str(x["IDProperty"])
    ld = ld or {}
    offerta = ld.get("offers") or {}
    offerto = offerta.get("itemOffered") or {}
    indirizzo = offerto.get("address") or {}

    prezzo = _int(x.get("Price")) or None  # 0 = prezzo su richiesta
    # PositionType 'F': coordinate generiche (centro del comune, uguali a additionalCenterCoords), non le uso.
    esatta = x.get("PositionType") == "R"
    immagine = x.get("Main_image")
    utente = (x.get("user") or {})

    caratteristiche: list[str] = []
    if _int(x.get("Rooms_number")):
        caratteristiche.append(f"{x['Rooms_number']} camere da letto")
    for chiave, nome in (("Garden", "giardino"), ("Terrace", "terrazzo"), ("Pool", "piscina")):
        if x.get(chiave):
            mq = _int(x.get(f"{chiave}_sqm"))
            caratteristiche.append(f"{nome} {mq} m²" if mq else nome)
    if _int(x.get("Land_sqm")):
        caratteristiche.append(f"terreno {x['Land_sqm']} m²")
    if x.get("Costruttore"):
        caratteristiche.append("costruttore")
    if _str(x.get("Youtube")):
        caratteristiche.append("video")
    if _str(x.get("VirtualTour")):
        caratteristiche.append("virtual tour")
    if _str(x.get("Price_qualifier")):
        caratteristiche.append(f"prezzo: {x['Price_qualifier']}")

    costa = _float(x.get("Coastline_distance"))
    titolo = _TITOLO_CODA.sub("", ld.get("name") or "") or None
    return Annuncio(
        portale=PORTALE,
        id=ident,
        url=_str(ld.get("url")) or f"{origine}/id/{ident}",  # url canonico del JSON-LD
        titolo=titolo,
        prezzo=prezzo,
        mq=_int(x.get("Sqm")) or None,
        locali=None,  # gate-away riporta le camere da letto (Rooms_number), non i locali: vedi caratteristiche
        bagni=_str(x.get("Bathrooms")) if _int(x.get("Bathrooms")) else None,
        piano=None,
        ascensore=None,
        indirizzo=None,  # la lista non riporta la via
        zona=_str(indirizzo.get("addressLocality")),
        lat=_float(x.get("Latitude")) if esatta else None,
        lon=_float(x.get("Longitude")) if esatta else None,
        descrizione=None,  # la lista non riporta la descrizione
        foto=URL_FOTO.format(utente=x.get("IDUser"), id=ident, file=immagine) if immagine and x.get("IDUser") else None,
        agenzia=_str(utente.get("Label")),
        condizione=_CONDIZIONI.get(str(offerta.get("itemCondition") or "").rsplit("/", 1)[-1]),
        caratteristiche=caratteristiche,
        ribasso_pct=None,  # la lista non riporta il prezzo precedente
        letto_il=letto_il,
        distanza_mare=f"{costa:g} km" if costa is not None else None,
    )


def _numeri(testo: str, n: int, chiave: str) -> list[float]:
    try:
        v = [float(x) for x in re.split(r"[,\s]+", testo.strip())]
    except ValueError:
        v = []
    if len(v) != n:
        raise ValueError(f"{chiave}: atteso {n} numeri, non {testo!r}")
    return v


def _luogo(chiave: str, v) -> tuple[str | None, list[tuple[str, str]]]:
    """(segmento di percorso, coppie di query) per regione, provincia, area turistica, cerchio o poligono."""
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{chiave} deve essere un testo, non {v!r}")
    if chiave == "regione":
        if v not in REGIONI:
            raise ValueError(f"regione {v!r}: ammesse {', '.join(REGIONI)}")
        return v, []
    if chiave == "provincia":
        if v not in PROVINCE:
            raise ValueError(f"provincia {v!r} non ammessa: usa lo slug inglese di gate-away (es. trieste, rome, milan)")
        return f"{PROVINCE[v]}/{v}", []
    if chiave == "area_turistica":
        if v not in ZONE:
            raise ValueError(f"area_turistica {v!r}: ammesse {', '.join(ZONE)}")
        return v, []
    if chiave == "raggio":  # "lat,lon,metri": il cerchio disegnato con "Draw a circle"
        lat, lon, metri = _numeri(v, 3, chiave)
        if metri <= 0:
            raise ValueError("raggio: i metri devono essere > 0")
        # Il server ignora un raggio intero (prova del 03/11/2026: 1000, 5000 e 5000.0 -> nessun filtro; 3000.5 e
        # 1131.58 -> applicato). Il cerchio disegnato a mano ha sempre decimali: mezzo metro in piu' non cambia l'area.
        if metri.is_integer():
            metri += 0.5
        cerchio = {"radius": metri, "center_lat": lat, "center_lon": lon}
        return None, [("circle[]", json.dumps(cerchio, separators=(",", ":")))]
    # mappa_area: "lat lon;lat lon;..." almeno 3 vertici, il poligono di "Draw a polygon" (chiuso sul primo punto).
    punti = [p for p in v.split(";") if p.strip()]
    if len(punti) < 3:
        raise ValueError("mappa_area: servono almeno 3 vertici 'lat lon' separati da ';'")
    vertici = [" ".join(f"{x:g}" for x in _numeri(p, 2, chiave)) for p in punti]
    if vertici[0] != vertici[-1]:
        vertici.append(vertici[0])
    return None, [("polygon[]", json.dumps(vertici, separators=(",", ":")))]


class Client:
    ORIGINE = "https://www.gate-away.com"
    TRASPORTO = "chrome"
    FILTRI = FILTRI

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def richiesta(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        """Percorso con query della pagina SSR, nella forma che la barra degli indirizzi mostra dopo una ricerca."""
        ignote = sorted(set(filtri) - FILTRI)
        if ignote:
            raise ValueError(f"filtri sconosciuti per gate-away: {', '.join(ignote)}; ammessi: {', '.join(sorted(FILTRI))}")
        f = {k: v for k, v in filtri.items() if v is not None}
        if contratto != "vendita":
            raise ValueError(f"contratto {contratto!r}: gate-away.com pubblica solo vendite (ammesso 'vendita')")
        if type(pagina) is not int or pagina < 1:
            raise ValueError("pagina deve essere un intero >= 1")

        luoghi = [k for k in LUOGHI if k in f]
        if len(luoghi) > 1 or (luoghi and comune.strip() != TUTTA_ITALIA):
            raise ValueError(f"un solo luogo per ricerca: {', '.join(luoghi)} richiedono comune={TUTTA_ITALIA!r}"
                             if luoghi else "")
        segmento, query = (_luogo(luoghi[0], f[luoghi[0]]) if luoghi
                           else (None if comune.strip() == TUTTA_ITALIA else percorso_comune(comune), []))

        categoria = "properties"
        if "tipologia" in f:
            tipi = f["tipologia"] if isinstance(f["tipologia"], list) else [f["tipologia"]]
            if not tipi or len(set(tipi)) != len(tipi) or any(t not in TIPOLOGIE for t in tipi):
                raise ValueError(f"tipologia {f['tipologia']!r}: ammesse {', '.join(TIPOLOGIE)} (anche in lista)")
            if len(tipi) == 1:
                categoria = tipi[0]
            else:
                query += [("cat[]", str(TIPOLOGIE[t])) for t in tipi]
        filtro = []
        for chiave, nome in INTERI.items():
            if chiave in f:
                v = f[chiave]
                if type(v) is not int or v < 1 or (chiave in LIMITI_INTERI and v not in LIMITI_INTERI[chiave]):
                    ammessi = LIMITI_INTERI.get(chiave)
                    raise ValueError(f"{chiave} deve essere un intero " +
                                     (f"tra {ammessi.start} e {ammessi.stop - 1}" if ammessi else ">= 1") + f", non {v!r}")
                filtro.append((nome, str(v)))
        for gruppo in ("prezzo", "mq"):
            lo, hi = f.get(f"{gruppo}_min"), f.get(f"{gruppo}_max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{gruppo}_min {lo} maggiore di {gruppo}_max {hi}")
        if "stato_immobile" in f:
            stati = f["stato_immobile"] if isinstance(f["stato_immobile"], list) else [f["stato_immobile"]]
            if not stati or len(set(stati)) != len(stati) or any(x not in CONDIZIONI for x in stati):
                raise ValueError(f"stato_immobile {f['stato_immobile']!r}: ammessi {', '.join(CONDIZIONI)} (anche in lista)")
            filtro += [("conditions[]", str(CONDIZIONI[x])) for x in stati]
        # "Land / Garden (min area)": il sito applica land_sqm solo con Garden o Land spuntati (prova del 03/11/2026:
        # land_sqm=500 da solo non cambia il totale; land=1 9841 -> con land_sqm=5000 5906; garden=1 23631 -> 1991).
        # Senza giardino ne' con_terreno il client spunta Land, come farebbe una persona per usare quel controllo.
        if "terreno_mq_min" in f and not (f.get("giardino") or f.get("con_terreno")):
            f["con_terreno"] = True
        for chiave, nome in CARATTERISTICHE.items():
            if chiave in f:
                if f[chiave] is not True:
                    raise ValueError(f"{chiave} accetta solo True")
                filtro.append((nome, "1"))
        vicini = [k for k in DISTANZE if k in f]
        if len(vicini) > 1:
            raise ValueError(f"Near to e' una scelta sola: {', '.join(vicini)} insieme non si possono usare")
        for chiave in vicini:
            nome, km = DISTANZE[chiave]
            if f[chiave] not in km:
                raise ValueError(f"{chiave} {f[chiave]!r}: ammessi {', '.join(map(str, km))} (km)")
            filtro.append((nome, str(f[chiave])))
        if filtro:
            filtro.append(("currency", "EUR"))  # l'interfaccia lo aggiunge a ogni ricerca filtrata
        query += filtro
        if "ordinamento" in f:
            if f["ordinamento"] not in ORDINAMENTI:
                raise ValueError(f"ordinamento {f['ordinamento']!r}: ammessi {', '.join(ORDINAMENTI)}")
            query.append(("orderby", f["ordinamento"]))
        if pagina > 1:
            query.append(("pag", str(pagina)))
        percorso = "/" + categoria + (f"/{segmento}" if segmento else "")
        return percorso + ("?" + urlencode(query, quote_via=quote, safe="[]") if query else "")

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        """`comune`: nome noto (PERCORSI_COMUNI), "regione/provincia/comune", o "*" per tutta Italia."""
        percorso = self.richiesta(comune, contratto, pagina, **filtri)
        r = self.trasporto.get(percorso, intestazioni=INTESTAZIONI_HTML)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        dati = estrai_preloaded(r.testo)
        risultati = dati.get("searchResults")
        if not isinstance(risultati, dict) or not isinstance(risultati.get("data"), list):
            raise ErroreStruttura("preloadedData senza searchResults.data")
        ld = estrai_ldjson(r.testo)
        letto_il = adesso_utc()
        # Solo searchResults.data: "featured" e "additionalResults" sono annunci di altri comuni.
        annunci = [annuncio_da_lista(x, ld.get(str(x["IDProperty"])), self.ORIGINE, letto_il)
                   for x in risultati["data"] if x.get("IDProperty")]
        totale = _int(risultati.get("count"))
        pagine = math.ceil(totale / ANNUNCI_PER_PAGINA) if totale is not None else None
        return Pagina(totale=totale, pagine=pagine, annunci=annunci)


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        try:
            p = Client(t).cerca("Trieste")
        except Bloccato as e:
            sys.exit(f"Bloccato da Cloudflare: {e}. Risolvi la verifica nella tab di Chrome e rilancia.")
    print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.titolo, a.url)
