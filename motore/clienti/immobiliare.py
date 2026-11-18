"""Client di ricerca per immobiliare.it (solo lettura, solo ricerca annunci).

Il client non fa HTTP: riceve un `trasporto` e chiama solo `trasporto.get(percorso, intestazioni=...)`.
immobiliare.it risponde 403 a HTTP semplice, quindi TRASPORTO = "chrome" (fetch nella tab del Chrome vero).

I filtri sono quelli di filtri/immobiliare.json: chiave, parametro del portale, tipo e valori ammessi vengono da lì,
e ogni voce porta la prova dal vivo che il portale la applica. Il codice sotto tratta a parte solo le voci che non
sono "un parametro, un valore": categoria e tipologia, zona, ordinamento, raggio, area su mappa.

Flusso:
1. comune -> id geografici:
   - GET /vendita-case/<slug>/ (pagina SSR): da __NEXT_DATA__ la queryKey "real-estate-list" dà idComune.
   - GET /api-next/geography/geography-lists/?id=<idComune>&type=3: etichetta del comune e genitori.
   - zona: GET /vendita-case/<slug>/<zona>/, la stessa queryKey dà idMZona.
2. GET /api-next/search-list/listings/?path=/vendita-case/<slug>/&fkRegione=..&idProvincia=..&idComune=..
   &idNazione=IT&idContratto=1&idCategoria=1&<filtri>&__lang=it&pag=N  (25 annunci a pagina).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Dettaglio, Pagina, adesso_utc  # noqa: F401

PORTALE = "immobiliare"
MAPPA = json.loads((pathlib.Path(__file__).resolve().parents[1] / "filtri" / "immobiliare.json").read_text())
VOCI = {v["chiave"]: v for v in MAPPA["filtri"]}
# Chiavi sullo stesso parametro che il portale unisce in O: chi le passa insieme si aspetta E (misure in mappa).
IN_O = [g for g in MAPPA.get("combinazioni", []) if g["esito"] == "o"]
POSIZIONALI = {"comune", "contratto", "pagina"}

# contratto -> (idContratto, radice del path, categoria imposta). Le forme composte sono contratto + categoria.
CONTRATTI = {
    "vendita": ("1", "vendita-case", None),
    "affitto": ("2", "affitto-case", None),
    "asta": ("14", "aste-immobiliari", None),
    "affitto-stanze": ("2", "affitto-case", "4"),
    "vendita-nuove-costruzioni": ("1", "vendita-case", "6"),
    "vendita-garage": ("1", "vendita-case", "22"),
    "vendita-palazzi": ("1", "vendita-case", "20"),
    "vendita-negozi": ("1", "vendita-case", "26"),
    "vendita-terreni": ("1", "vendita-case", "24"),
}
assert set(CONTRATTI) == {x["valore"] for x in VOCI["contratto"]["valori"]}, "contratti del client != mappa"
CATEGORIA_CASE = "1"
STANZE, NUOVE_COSTRUZIONI, AFFITTO = "4", "6", "2"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NUMERO = r"-?\d+(?:\.\d+)?"
_PUNTO = re.compile(rf"{_NUMERO},{_NUMERO}")


@dataclass(frozen=True)
class Geo:
    slug: str
    etichetta: str
    id_comune: str
    id_provincia: str
    id_regione: str
    id_nazione: str


def slug_comune(comune: str) -> str:
    """"San Dorligo della Valle" -> "san-dorligo-della-valle" (forma usata negli URL del portale)."""
    s = unicodedata.normalize("NFKD", comune).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        raise ValueError(f"comune non valido: {comune!r}")
    return s


def _intero(testo) -> int | None:
    """"1.200 m²" -> 1200; "95 m²" -> 95."""
    if testo is None:
        return None
    if isinstance(testo, (int, float)):
        return int(testo)
    m = re.search(r"\d[\d.]*", str(testo))
    return int(m.group().replace(".", "")) if m else None


def _decimale(testo) -> float | None:
    if testo in (None, ""):
        return None
    try:
        return float(str(testo).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def leggi_next_data(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ non trovato nella pagina")
    return json.loads(m.group(1))


def query_lista(next_data: dict) -> dict | None:
    """La query react-query "real-estate-list" deidratata: {'queryKey': [...], 'state': {'data': {...}}}."""
    stato = next_data.get("props", {}).get("pageProps", {}).get("dehydratedState") or {}
    for q in stato.get("queries", []):
        chiave = q.get("queryKey") or []
        if chiave and chiave[0] == "real-estate-list":
            return q
    return None


def valida(chiave: str, valore) -> str | list[str] | bool:
    """Controlla un filtro contro la mappa e lo normalizza: lista di stringhe per multiplo, stringa altrimenti."""
    if chiave not in VOCI or chiave in POSIZIONALI:
        raise ValueError(f"filtro {chiave!r} sconosciuto: ammessi {sorted(set(VOCI) - POSIZIONALI)}")
    voce = VOCI[chiave]
    ammessi = [x["valore"] for x in voce["valori"]]
    tipo = voce["tipo"]
    if tipo == "booleano":
        if valore is not True:
            raise ValueError(f"filtro {chiave}: passa True oppure ometti il filtro, trovato {valore!r}")
        return True
    if tipo == "intero":
        if isinstance(valore, bool) or not isinstance(valore, int) or valore < 0:
            raise ValueError(f"filtro {chiave} deve essere un intero >= 0, trovato {valore!r}")
        if chiave == "bagni_min" and str(valore) not in ammessi:
            raise ValueError(f"filtro bagni_min: valore {valore!r} non ammesso, usa uno di {ammessi} (4 = 4 o più)")
        return str(valore)
    if tipo == "enum":
        if str(valore) not in ammessi:
            raise ValueError(f"filtro {chiave}: valore {valore!r} non ammesso, usa uno di {ammessi}")
        return str(valore)
    if tipo == "multiplo":
        lista = [valore] if isinstance(valore, (str, int)) else list(valore)
        fuori = [x for x in lista if str(x) not in ammessi]
        if not lista or fuori:
            raise ValueError(f"filtro {chiave}: valori {fuori or lista!r} non ammessi, usa uno o più di {ammessi}")
        return list(dict.fromkeys(str(x) for x in lista))
    if not isinstance(valore, str) or not valore.strip():
        raise ValueError(f"filtro {chiave} deve essere una stringa non vuota, trovato {valore!r}")
    valore = valore.strip()
    if chiave == "raggio" and not re.fullmatch(rf"{_NUMERO},{_NUMERO},\d+", valore):
        raise ValueError(f"filtro raggio: atteso 'lat,lng,metri', trovato {valore!r}")
    if chiave == "parola_chiave":
        parole = [x.strip() for x in valore.split(",")]
        if not all(parole):
            raise ValueError(f"filtro parola_chiave: parole separate da virgola, senza parole vuote, trovato {valore!r}")
    if chiave == "mappa_area":
        punti = valore.split(";")
        if len(punti) < 3 or not all(_PUNTO.fullmatch(p) for p in punti):
            raise ValueError(f"filtro mappa_area: attesi almeno 3 vertici 'lat,lng;lat,lng;...', trovato {valore!r}")
    return valore


def categoria_e_tipologie(contratto: str, filtri: dict) -> tuple[str, list[str]]:
    """Una sola categoria per ricerca: dal contratto composto, da categoria, nuova_costruzione, tipologia e dalle
    voci che `richiede`no una categoria o una tipologia. Due fonti in disaccordo: ValueError."""
    fonti: list[tuple[str, str]] = []
    if CONTRATTI[contratto][2]:
        fonti.append((f"contratto {contratto}", CONTRATTI[contratto][2]))
    if "categoria" in filtri:
        fonti.append(("categoria", filtri["categoria"]))
    if "nuova_costruzione" in filtri:
        fonti.append(("nuova_costruzione", NUOVE_COSTRUZIONI))
    scelte = filtri.get("tipologia", [])
    for t in scelte:
        fonti.append((f"tipologia {t}", t.split("_")[0]))
    tipologie = [t.split("_")[1] for t in scelte]
    for chiave in filtri:
        richiede = VOCI[chiave].get("richiede") or {}
        if "categoria" in richiede:
            fonti.append((chiave, richiede["categoria"]))
        if "tipologia" in richiede:
            cat, tip = richiede["tipologia"].split("_")
            fonti.append((chiave, cat))
            if scelte and tip not in tipologie:
                raise ValueError(f"filtro {chiave} vale solo con tipologia {richiede['tipologia']}, scelta {scelte}")
            tipologie = tipologie or [tip]
    categorie = {c for _, c in fonti}
    if len(categorie) > 1:
        raise ValueError(f"categorie incompatibili: {', '.join(f'{o} -> {c}' for o, c in fonti)}")
    categoria = categorie.pop() if categorie else CATEGORIA_CASE
    id_contratto = CONTRATTI[contratto][0]
    if categoria == STANZE and id_contratto != AFFITTO:
        raise ValueError(f"stanze e posti letto esistono solo in affitto, contratto {contratto!r}")
    if categoria == NUOVE_COSTRUZIONI and id_contratto == AFFITTO:
        raise ValueError(f"nuove costruzioni esistono solo in vendita, contratto {contratto!r}")
    return categoria, tipologie


def parametri_filtro(chiave: str, valore) -> list[tuple[str, str]]:
    """Le coppie query di un filtro già validato (categoria, tipologia e zona le mette `Client.richiesta`)."""
    voce = VOCI[chiave]
    if chiave == "ordinamento":
        return parse_qsl(valore)
    if chiave == "parola_chiave":  # la pagina manda keyword[0]=a&keyword[1]=b; keyword=a dà HTTP 422
        return [(f"keyword[{i}]", x.strip()) for i, x in enumerate(valore.split(","))]
    if chiave == "bagni_min":  # la pagina spunta anche i valori sopra (cascadeUp): bathrooms[]=N..4
        return [(voce["parametro"], x["valore"]) for x in voce["valori"] if int(x["valore"]) >= int(valore)]
    if chiave == "raggio":
        lat, lng, metri = valore.split(",")
        return [("raggio", metri), ("centro", f"{lat},{lng}")]
    if voce["tipo"] == "booleano":
        return [(voce["parametro"], voce["valori"][0]["valore"])]
    if voce["tipo"] == "multiplo":
        return [(voce["parametro"], x) for x in valore]
    return [(voce["parametro"], valore)]


def controlla_combinazioni(filtri: dict) -> None:
    for g in IN_O:
        insieme = [k for k in g["chiavi"] if k in filtri]
        if len(insieme) > 1:
            raise ValueError(f"filtri {' e '.join(insieme)} insieme: immobiliare.it li unisce in O (annunci con "
                             f"l'uno o l'altro), non in E ({g['misura']}). Chiedine uno per ricerca")


def _dentro(proprieta: dict, limiti: dict) -> bool:
    for campo, (minimo, massimo) in limiti.items():
        valore = proprieta.get(campo)
        x = _intero(valore.get("value") if isinstance(valore, dict) else valore)
        if x is not None and ((minimo is not None and x < minimo) or (massimo is not None and x > massimo)):
            return False
    return True


class Client:
    ORIGINE = "https://www.immobiliare.it"
    TRASPORTO = "chrome"
    FILTRI = frozenset(VOCI) - POSIZIONALI
    _GESTITI_A_PARTE = {"categoria", "nuova_costruzione", "tipologia", "zona"}

    def __init__(self, trasporto):
        self.t = trasporto
        self._geo: dict[str, Geo] = {}
        self._zone: dict[tuple[str, str], str] = {}

    # ---------------------------------------------------------------- geografia
    def _query_pagina(self, percorso: str, cosa: str) -> dict:
        r = self.t.get(percorso, intestazioni={"Accept": "text/html"})
        if r.status != 200:
            raise ValueError(f"{cosa} non trovato su immobiliare.it (HTTP {r.status} su {r.url})")
        q = query_lista(leggi_next_data(r.testo))
        if not q or len(q["queryKey"]) < 2:
            raise ValueError(f"{cosa}: la pagina {r.url} non è una ricerca")
        return q["queryKey"][1]

    def geo(self, comune: str) -> Geo:
        slug = slug_comune(comune)
        if slug in self._geo:
            return self._geo[slug]
        chiave = self._query_pagina(f"/vendita-case/{slug}/", f"comune {comune!r}")
        if not chiave.get("idComune"):
            raise ValueError(f"comune {comune!r}: /vendita-case/{slug}/ non è una ricerca per comune")
        id_comune = str(chiave["idComune"])
        g = self.t.get("/api-next/geography/geography-lists/?" + urlencode({"id": id_comune, "type": 3, "__lang": "it"}))
        if g.status != 200:
            raise RuntimeError(f"geography-lists HTTP {g.status} su {g.url}")
        dati = g.json()
        genitori = {p.get("type"): str(p.get("id")) for p in dati.get("parents", [])}
        geo = Geo(
            slug=slug,
            etichetta=dati.get("label") or comune,
            id_comune=str(dati.get("id") or id_comune),
            id_provincia=genitori.get(1) or str(chiave.get("idProvincia")),
            id_regione=genitori.get(0) or str(chiave.get("fkRegione")),
            id_nazione=genitori.get(-1) or str(chiave.get("idNazione") or "IT"),
        )
        if slug_comune(geo.etichetta) != slug:
            raise ValueError(f"comune {comune!r}: il portale ha risolto {geo.etichetta!r}")
        self._geo[slug] = geo
        return geo

    def zona(self, geo: Geo, zona: str) -> str:
        """slug della macrozona -> idMZona, letto dalla pagina /vendita-case/<comune>/<zona>/."""
        slug = slug_comune(zona)
        if (geo.slug, slug) not in self._zone:
            chiave = self._query_pagina(f"/vendita-case/{geo.slug}/{slug}/", f"zona {zona!r} di {geo.etichetta}")
            ids = chiave.get("idMZona") or []
            if len(ids) != 1 or str(chiave.get("idComune")) != geo.id_comune:
                raise ValueError(f"zona {zona!r}: non è una macrozona di {geo.etichetta} (queryKey {chiave})")
            self._zone[(geo.slug, slug)] = str(ids[0])
        return self._zone[(geo.slug, slug)]

    # ---------------------------------------------------------------- ricerca
    def richiesta(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        """Il percorso con query di /api-next/search-list/listings/ per questa ricerca."""
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r} non supportato: usa uno di {list(CONTRATTI)}")
        if isinstance(pagina, bool) or not isinstance(pagina, int) or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        filtri = {k: valida(k, v) for k, v in filtri.items()}
        controlla_combinazioni(filtri)
        categoria, tipologie = categoria_e_tipologie(contratto, filtri)
        id_contratto, radice, _ = CONTRATTI[contratto]
        geo = self.geo(comune)
        coppie = [("path", f"/{radice}/{geo.slug}/"), ("fkRegione", geo.id_regione),
                  ("idProvincia", geo.id_provincia), ("idComune", geo.id_comune), ("idNazione", geo.id_nazione)]
        if "zona" in filtri:
            coppie.append(("idMZona[]", self.zona(geo, filtri["zona"])))
        coppie += [("idContratto", id_contratto), ("idCategoria", categoria)]
        coppie += [("idTipologia[]", t) for t in tipologie]
        for chiave, valore in filtri.items():
            if chiave not in self._GESTITI_A_PARTE:
                coppie += parametri_filtro(chiave, valore)
        coppie += [("__lang", "it"), ("pag", str(pagina))]
        return "/api-next/search-list/listings/?" + urlencode(list(dict.fromkeys(coppie)), safe="[],;")

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        r = self.t.get(self.richiesta(comune, contratto, pagina, **filtri))
        if r.status == 422:  # il portale rifiuta i parametri: è un input non ammesso, non un guasto
            raise ValueError(f"il portale rifiuta la ricerca (HTTP 422) {r.url}: {r.testo[:300]}")
        if r.status != 200:
            raise RuntimeError(f"search-list/listings HTTP {r.status} su {r.url}: {r.testo[:300]}")
        limiti = {campo: (filtri.get(f"{nome}_min"), filtri.get(f"{nome}_max"))
                  for campo, nome in (("surface", "mq"), ("rooms", "locali"), ("bathrooms", "bagni"), ("price", "prezzo"))}
        return self.leggi_pagina(r.json(), limiti)

    # ---------------------------------------------------------------- parsing
    def leggi_pagina(self, dati: dict, limiti: dict | None = None) -> Pagina:
        """Stessa forma per la risposta JSON dell'API e per la query deidratata nel __NEXT_DATA__ SSR."""
        letto = adesso_utc()
        annunci = [a for a in (self._annuncio(x, letto, limiti or {}) for x in dati.get("results") or []) if a]
        totale = dati.get("totalAds", dati.get("count"))
        return Pagina(totale=_intero(totale), pagine=_intero(dati.get("maxPages")), annunci=annunci)

    def _annuncio(self, voce: dict, letto_il: str, limiti: dict) -> Annuncio | None:
        re_ = voce.get("realEstate") or {}
        if not re_.get("id"):
            return None
        proprieta = re_.get("properties") or [{}]
        principale = next((x for x in proprieta if x.get("isMain")), proprieta[0])
        # Un progetto (isProjectLike) ha più unità e il portale lo trova se una qualsiasi rispetta i limiti: si
        # descrive la prima unità che rispetta superficie, locali, bagni e prezzo, con il suo prezzo, altrimenti la
        # principale con il prezzo del progetto.
        p = next((x for x in [principale, *proprieta] if _dentro(x, limiti)), principale)
        loc = p.get("location") or {}
        prezzo = (p is not principale and p.get("price")) or re_.get("price") or p.get("price") or {}
        agenzia = ((re_.get("advertiser") or {}).get("agency") or {}).get("displayName")
        foto = (p.get("photo") or {}).get("urls") or {}
        piano = p.get("floor") or {}
        ribasso = prezzo.get("loweredPrice") or {}
        escluse = {"rooms", "surface", "bathrooms", "elevator", "floor"}
        caratt = [f["label"] for f in p.get("featureList") or [] if f.get("label") and f.get("type") not in escluse]
        ascensore = p.get("elevator")
        if ascensore is None:  # senza ascensore manca "elevator": lo dice solo featureList ("No Ascensore", "No")
            voce_ascensore = next((f for f in p.get("featureList") or [] if f.get("type") == "elevator"), None)
            ascensore = None if voce_ascensore is None else voce_ascensore.get("compactLabel") == "Sì"
        id_ = str(re_["id"])
        return Annuncio(
            portale=PORTALE,
            id=id_,
            url=(voce.get("seo") or {}).get("url") or f"{self.ORIGINE}/annunci/{id_}/",
            titolo=re_.get("title"),
            prezzo=_intero(prezzo.get("value")) if prezzo.get("visible", True) else None,
            mq=_intero(p.get("surface")),
            locali=p.get("rooms"),
            bagni=p.get("bathrooms"),
            piano=piano.get("value") if isinstance(piano, dict) else (str(piano) if piano else None),
            ascensore=ascensore,
            indirizzo=loc.get("address"),
            zona=loc.get("microzone") or loc.get("macrozone"),
            lat=loc.get("latitude"),
            lon=loc.get("longitude"),
            descrizione=p.get("description") or p.get("caption"),
            foto=foto.get("large") or foto.get("medium") or foto.get("small"),
            agenzia=agenzia,
            condizione=p.get("ga4Condition"),
            caratteristiche=caratt,
            ribasso_pct=_decimale(ribasso.get("discountPercentage")),
            letto_il=letto_il,
            vista=[v["name"] for v in p.get("views") or [] if v.get("name")],
            distanza_mare=p.get("seaDistanceValue"),
        )


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        c = Client(t)
        try:
            pag = c.cerca("Trieste", prezzo_max=300000, locali_min=3)
        except Bloccato as e:
            sys.exit(f"bloccato dall'antibot, serve una persona: {e}")
        print(f"totale={pag.totale} pagine={pag.pagine} in questa pagina={len(pag.annunci)}")
        for a in pag.annunci[:5]:
            print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.url)
