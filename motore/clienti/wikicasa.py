"""Client di ricerca per wikicasa.it.

Wikicasa è un'app Nuxt 3 dietro Cloudflare. La pagina di ricerca /vendita-case/<comune>/ è renderizzata lato server
e porta tutti i dati nel payload `<script id="__NUXT_DATA__">` (formato "devalue": un array piatto in cui oggetti e
liste contengono indici verso altri elementi dell'array). Gli annunci stanno nello store Pinia
`realEstateListingStoreId.realEstateList` (25 per pagina), il totale in `realEstateListingStoreId.listingCount`.

Il client non fa HTTP da sé: chiama solo `trasporto.get(...)`. Con un blocco antibot il trasporto solleva
`trasporto.Bloccato` e il client si ferma (nessun nuovo tentativo).
"""
from __future__ import annotations

import html as _html
import json
import math
import re
import unicodedata
from dataclasses import dataclass

from ..trasporto import Bloccato, Risposta  # noqa: F401  (Bloccato è riesportato per chi usa il client)
from ..portali.modello import Annuncio, Dettaglio, ErrorePortale, Pagina, adesso_utc  # noqa: F401

PER_PAGINA = 25
_ACCEPT_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# Forma dell'URL presa dal pannello "Filtri" e dai menu della pagina il 03/11/2026 (risultati/scoperta/wikicasa/clic.tsv).
CONTRATTI = ("vendita", "affitto", "asta")
TIPOLOGIE = ("case", "appartamenti", "ville", "ville-a-schiera", "ville-bifamiliari", "attici", "case-indipendenti",
             "rustici", "loft", "monolocali", "bilocali", "trilocali", "quadrilocali", "nuove-costruzioni", "box",
             "palazzi", "commerciale", "uffici", "negozi", "terreni")
ZONE_TRIESTE = ("zona-campanelle-altura-cattinara", "zona-centro-storico-citta-vecchia-rive-san-giusto",
                "zona-gretta-barcola-roiano", "zona-opicina-altipiano-trebiciano-basovizza",
                "zona-rozzol-chiadino-san-luigi-melara", "zona-san-giacomo-ippodromo-fiera",
                "zona-san-vito-sant-andrea-campi-elisi", "zona-santa-croce-miramare-grignano-costiera",
                "zona-scorcola-guardiella-cologna-san-giovanni", "zona-valmaura-chiarbola-ponziana-maddalena")


@dataclass(frozen=True)
class Filtro:
    """Chiave del client -> parametro della query del pannello. `fisso` = valore scritto per un booleano."""
    param: str
    tipo: str                    # "intero" | "booleano" | "enum" | "testo"
    valori: tuple = ()
    fisso: str = "true"


_SI = lambda p: Filtro(p, "booleano")  # noqa: E731
VOCI = {
    "prezzo_min": Filtro("PrezzoMinimo", "intero"),
    "prezzo_max": Filtro("PrezzoMassimo", "intero"),
    "mq_min": Filtro("SuperficieMinima", "intero"),
    "mq_max": Filtro("SuperficieMassima", "intero"),
    "locali_min": Filtro("NumeroMinimoLocali", "intero"),
    "locali_max": Filtro("NumeroMassimoLocali", "intero"),
    "bagni_min": Filtro("NumeroMinimoBagni", "intero"),
    "bagni_max": Filtro("NumeroMassimoBagni", "intero"),
    "escludi_aste": _SI("EscludiAste"),
    "nuda_proprieta": _SI("SoloNudaProprieta"),
    "escludi_nuda_proprieta": _SI("EscludiNudaProprieta"),
    "parola_chiave": Filtro("ParoleChiave", "testo"),
    "con_video": Filtro("Media", "booleano", fisso="VIDEO"),
    "con_virtual_tour": Filtro("Media", "booleano", fisso="VIRTUAL_TOUR"),
    "tour_matterport": Filtro("Media", "booleano", fisso="MATTERPORT_TOUR"),
    "stato_immobile": Filtro("StatoImmobile", "enum", ("5", "1", "3", "2")),
    "nuova_costruzione": Filtro("StatoImmobile", "booleano", fisso="5"),
    "classe_energetica": Filtro("ClasseEnergetica", "enum", ("1", "2", "3")),
    "piano": Filtro("Piano", "enum", ("1", "2", "3")),
    "ascensore": _SI("Ascensore"),
    "giardino": _SI("GiardinoPrivato"),
    "giardino_condominiale": _SI("GiardinoPubblico"),
    "aria_condizionata": _SI("AriaCondizionata"),
    "balcone": _SI("Balcone"),
    "cantina": _SI("Cantina"),
    "box_auto": _SI("Box"),
    "arredato": _SI("Arredato"),
    "terrazzo": _SI("Terrazzo"),
    "posto_auto": _SI("PostoAuto"),
    "piscina_condominiale": _SI("PiscinaCondominiale"),
    "piscina": _SI("PiscinaPrivata"),
    "ribassato": _SI("Sconto"),
    "lusso": _SI("Lusso"),
    "vicino_scuola": _SI("Istruzione"),
    "vicino_supermercato": _SI("Supermercati"),
    "vicino_ricarica": _SI("Ricarica"),
    "vicino_farmacia": _SI("Farmacie"),
    "vicino_ospedale": _SI("Ospedali"),
    "vicino_ufficio_postale": _SI("UfficioPostale"),
    "vicino_banca": _SI("Banche"),
    "vicino_trasporti": _SI("Trasporti"),
    "ordinamento": Filtro("Ordinamento", "enum", tuple(str(i) for i in range(0, 9))),
}
_RE_POLIGONO = re.compile(r"-?\d+(\.\d+)?,-?\d+(\.\d+)?(;-?\d+(\.\d+)?,-?\d+(\.\d+)?){3,}")

_CONDIZIONI = {
    "NEW": "nuovo",
    "REFURBISHED": "ristrutturato",
    "GOOD_CONDITION": "buono stato",
    "TO_REFURBISH": "da ristrutturare",
}


# --------------------------------------------------------------------------------------------- devalue (Nuxt 3)

_TIPI_WRAPPER = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "skipHydrate", "NuxtError", "Island"}
_SPECIALI = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: -0.0}


def decodifica_nuxt_data(testo_html: str):
    """Ricostruisce l'oggetto radice del payload __NUXT_DATA__ di una pagina Nuxt 3."""
    m = re.search(r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', testo_html, re.S)
    if not m:
        raise ErrorePortale("payload __NUXT_DATA__ assente nella pagina di ricerca")
    arr = json.loads(m.group(1))
    memo: dict[int, object] = {}

    def h(i: int):
        if i in _SPECIALI:
            return _SPECIALI[i]
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tipo = v[0]
                if tipo in _TIPI_WRAPPER:
                    memo[i] = r = h(v[1])
                elif tipo in ("EmptyRef", "EmptyShallowRef"):
                    memo[i] = r = None
                elif tipo in ("Date", "RegExp", "BigInt"):
                    memo[i] = r = v[1]
                elif tipo == "Set":
                    memo[i] = r = []
                    r.extend(h(x) for x in v[1:])
                elif tipo == "Map":
                    memo[i] = r = {}
                    for k in range(1, len(v), 2):
                        r[str(h(v[k]))] = h(v[k + 1])
                elif tipo == "null":
                    memo[i] = r = {}
                    for k in range(1, len(v), 2):
                        r[v[k]] = h(v[k + 1])
                else:
                    raise ErrorePortale(f"tipo devalue sconosciuto: {tipo!r}")
                return r
            memo[i] = r = []
            r.extend(h(x) for x in v)
            return r
        if isinstance(v, dict):
            memo[i] = r = {}
            for k, x in v.items():
                r[k] = h(x)
            return r
        memo[i] = v
        return v

    return h(0)


# --------------------------------------------------------------------------------------------- utilità

def slug_comune(comune: str) -> str:
    """'Trieste' -> 'trieste', 'San Dorligo della Valle' -> 'san-dorligo-della-valle'."""
    s = unicodedata.normalize("NFKD", comune).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        raise ValueError(f"comune non valido: {comune!r}")
    return s


def _testo(descrizione: str | None) -> str | None:
    if not descrizione:
        return None
    t = re.sub(r"<br\s*/?>", "\n", descrizione, flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return t.strip() or None


def _positivo(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def _foto(immagini: list | None) -> str | None:
    for img in immagini or []:
        formati = {f.get("format"): f.get("storagePath") for f in img.get("images") or []}
        url = formati.get(3) or formati.get(1) or next((u for u in formati.values() if u), None)
        if url:
            return url
    return None


# --------------------------------------------------------------------------------------------- client

class Client:
    ORIGINE = "https://www.wikicasa.it"
    TRASPORTO = "chrome"   # Cloudflare davanti al sito
    PORTALE = "wikicasa"

    def __init__(self, trasporto):
        self.t = trasporto

    FILTRI = frozenset(VOCI) | {"tipologia", "zona", "vista_mare", "mappa_area"}

    # ---- richiesta

    @staticmethod
    def richiesta(comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> tuple[str, dict]:
        """-> (percorso, query) come li costruisce l'interfaccia di Wikicasa. ValueError su ogni input non ammesso."""
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r} non ammesso: {', '.join(CONTRATTI)}")
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        ignote = sorted(set(filtri) - Client.FILTRI)
        if ignote:
            raise ValueError(f"filtri sconosciuti per wikicasa: {ignote}; ammessi: {sorted(Client.FILTRI)}")
        tipologia = filtri.pop("tipologia", "case")
        if tipologia not in TIPOLOGIE:
            raise ValueError(f"tipologia {tipologia!r} non ammessa: {', '.join(TIPOLOGIE)}")
        zona, vista_mare, poligono = filtri.pop("zona", None), filtri.pop("vista_mare", None), filtri.pop("mappa_area", None)
        if vista_mare not in (None, True):
            raise ValueError(f"vista_mare={vista_mare!r}: booleano, passare True")
        luogo = slug_comune(comune)
        if poligono is not None:
            if not isinstance(poligono, str) or not _RE_POLIGONO.fullmatch(poligono):
                raise ValueError(f"mappa_area={poligono!r}: serve 'lon,lat;lon,lat;...' con almeno 4 vertici")
            if zona or vista_mare:
                raise ValueError("mappa_area sostituisce il luogo: non si combina con zona o vista_mare")
            luogo = "ricerca"
        if zona is not None:
            if luogo != "trieste" or zona not in ZONE_TRIESTE:
                raise ValueError(f"zona {zona!r}: ammesse solo le zone di Trieste: {', '.join(ZONE_TRIESTE)}")
            if vista_mare:
                raise ValueError("zona e vista_mare insieme: percorso non visto nell'interfaccia")
        economiche = str(filtri.get("ordinamento")) == "2"
        if economiche:
            del filtri["ordinamento"]   # "Più economici": l'interfaccia apre la pagina /economiche/, non ?Ordinamento=2
            if zona or vista_mare or poligono:
                raise ValueError("ordinamento 2 (più economici) è la pagina /economiche/: non si combina con zona, "
                                 "vista_mare o mappa_area")
        percorso = (f"/{contratto}-{tipologia}/{luogo}/" + (f"{zona}/" if zona else "") + ("vista-mare/" if vista_mare else "")
                    + ("economiche/" if economiche else ""))

        q: dict[str, str] = {}
        for chiave, v in filtri.items():
            f = VOCI[chiave]
            if f.tipo == "booleano":
                if v is not True:
                    raise ValueError(f"{chiave}={v!r}: booleano, passare True (False = non filtrare, omettere)")
                val = f.fisso
            elif f.tipo == "intero":
                if type(v) is not int or v < 0:
                    raise ValueError(f"{chiave}={v!r}: serve un intero >= 0")
                val = str(v)
            elif f.tipo == "testo":
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"{chiave}={v!r}: serve un testo non vuoto")
                val = v.strip()
            else:
                val = str(v)
                if val not in f.valori:
                    raise ValueError(f"{chiave}={v!r}: valori ammessi {', '.join(f.valori)}")
            if f.param in q and q[f.param] != val:
                raise ValueError(f"{chiave}: {f.param}={val} in conflitto con {f.param}={q[f.param]}")
            q[f.param] = val
        if q.get("Ordinamento") == "0":
            del q["Ordinamento"]        # "Più rilevanti" è l'ordine predefinito, l'interfaccia non lo scrive
        if poligono is not None:
            q["Poligono"] = poligono
        if pagina > 1:
            q["p"] = str(pagina)
        return percorso, q

    # ---- ricerca

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso, query = self.richiesta(comune, contratto, pagina, **filtri)
        r = self.t.get(percorso, params=query or None, intestazioni=_ACCEPT_HTML)
        if r.status == 404:
            raise ValueError(f"pagina di ricerca inesistente su wikicasa ({r.url}): comune o tipologia sbagliati")
        if r.status != 200:
            raise ErrorePortale(f"HTTP {r.status} su {r.url}")
        return self.analizza(r.testo)

    def analizza(self, testo_html: str) -> Pagina:
        dati = decodifica_nuxt_data(testo_html)
        try:
            store = dati["pinia"]["realEstateListingStoreId"]
        except (KeyError, TypeError):
            raise ErrorePortale("store realEstateListingStoreId assente nel payload Nuxt") from None
        lista = store.get("realEstateList")
        if not isinstance(lista, list):
            raise ErrorePortale("realEstateList assente nel payload Nuxt")

        letto_il = adesso_utc()
        annunci = [self._annuncio(x, letto_il) for x in lista if isinstance(x, dict) and x.get("realEstateID")]

        totale = store.get("listingCount")
        totale = totale if isinstance(totale, int) else None
        pagine = self._pagine(testo_html, totale)
        return Pagina(totale=totale, pagine=pagine, annunci=annunci)

    @staticmethod
    def _pagine(testo_html: str, totale: int | None) -> int | None:
        if totale is not None:
            return max(1, math.ceil(totale / PER_PAGINA)) if totale else 0
        numeri = [int(n) for n in re.findall(r'href="[^"]*\?p=(\d+)"', testo_html)]
        return max(numeri) if numeri else None

    def _annuncio(self, x: dict, letto_il: str) -> Annuncio:
        rid = str(x["realEstateID"])
        url = x.get("url") or f"/annuncio/{rid}"
        prezzo = None if x.get("reservedPrice") else _positivo(x.get("price"))
        originale = _positivo(x.get("originalPrice"))
        ribasso = round((originale - prezzo) / originale * 100, 1) if prezzo and originale and originale > prezzo \
            else None

        luogo = x.get("placeDto") or {}
        lat = luogo.get("latitude") if x.get("publishMap") else None
        lon = luogo.get("longitude") if x.get("publishMap") else None

        agenzia = (x.get("agency") or {}).get("name") or None

        caratteristiche = []
        for chiave, etichetta in (("balconies", "balconi"), ("garden", "giardino privato"),
                                  ("gardenPublic", "giardino condominiale"), ("furnished", "arredato"),
                                  ("carSpot", "posto auto"), ("luxury", "lusso"), ("bareOwnership", "nuda proprietà"),
                                  ("shortRent", "affitto breve")):
            if x.get(chiave) is True:
                caratteristiche.append(etichetta)
        if x.get("energyClass2015"):
            caratteristiche.append(f"classe energetica {x['energyClass2015']}")
        if x.get("urlVirtualTour"):
            caratteristiche.append("virtual tour")

        cond = x.get("conditionType")
        return Annuncio(
            portale=self.PORTALE,
            id=rid,
            url=url if url.startswith("http") else self.ORIGINE + url,
            titolo=x.get("title") or None,
            prezzo=prezzo,
            mq=_positivo(x.get("sqm")),
            locali=str(x["rooms"]) if _positivo(x.get("rooms")) else None,
            bagni=str(x["bathrooms"]) if _positivo(x.get("bathrooms")) else None,
            piano=None,
            ascensore=None,
            indirizzo=x.get("address") or None,
            zona=x.get("zoneName") or None,
            lat=float(lat) if isinstance(lat, (int, float)) and lat else None,
            lon=float(lon) if isinstance(lon, (int, float)) and lon else None,
            descrizione=_testo(x.get("description")),
            foto=_foto(x.get("reImages")),
            agenzia=agenzia,
            condizione=_CONDIZIONI.get(cond, cond.lower().replace("_", " ")) if cond else None,
            caratteristiche=caratteristiche,
            ribasso_pct=ribasso,
            letto_il=letto_il,
        )


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        p = Client(t).cerca("Trieste")
        print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
        for a in p.annunci[:5]:
            print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.url)
