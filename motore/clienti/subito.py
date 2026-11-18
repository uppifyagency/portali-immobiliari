"""Client di ricerca per Subito.it, categoria 7 (Appartamenti).

La lista annunci non arriva da un'API JSON: la pagina di ricerca è renderizzata lato server (Next.js) e
contiene tutto nello <script id="__NEXT_DATA__">, in props.pageProps.initialState.items.
Il client chiede quel percorso al trasporto e legge il JSON incorporato. Nessuna richiesta HTTP diretta.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..trasporto import Bloccato, Risposta  # noqa: F401  (Bloccato è sollevato dal trasporto e lasciato salire)
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "subito"
HADES = "https://hades.subito.it"   # valori di riferimento dei filtri (/v1/values/*)

# comune (minuscolo) -> (regione, provincia, comune) come slug nell'URL, e centro (lat, lon) per `raggio`.
# Per altri comuni si passa "regione/provincia/comune"; `raggio` vuole allora un comune di questa tabella.
COMUNI = {
    "trieste": ("friuli-venezia-giulia", "trieste", "trieste", (45.6504, 13.7676)),
    "muggia": ("friuli-venezia-giulia", "trieste", "muggia", (45.6026, 13.7669)),
    "duino aurisina": ("friuli-venezia-giulia", "trieste", "duino-aurisina", None),  # link nel HAR, centro non letto
    "milano": ("lombardia", "milano", "milano", (45.4642, 9.1900)),
    "roma": ("lazio", "roma", "roma", (41.8933, 12.4829)),
    "venezia": ("veneto", "venezia", "venezia", (45.4380, 12.3358)),
}

# contratto -> segmento del percorso (l'interfaccia trasforma il radio "Tipo di annuncio" s/k/u in questo segmento).
CONTRATTI = {"vendita": "vendita", "affitto": "affitto", "cerco": "cerco"}
TIPOLOGIE = ("appartamenti", "ville-singole-e-a-schiera", "loft-mansarde", "terreni-e-rustici", "garage-e-box",
             "uffici-locali-commerciali", "immobili", "camere-posti-letto")

_N = lambda *v: tuple(str(x) for x in v)  # noqa: E731
PREZZI = {
    "vendita": _N(20000, 50000, 100000, 130000, 150000, 170000, 190000, 220000, 250000, 290000, 350000, 450000,
                  700000, 1000000),
    "affitto": _N(100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 2000, 2500,
                  3000, 3500, 4000, 4500, 5000),
}
MQ = _N(40, 60, 80, 100, 150, 300, 500, 1000, 1500, 2000, 2500, 3000)
LOCALI = _N(*range(1, 12))          # 11 = "Più di 10"
PIANI = _N(*range(1, 13))           # codici: 1 interrato, 2 seminterrato, 3 rialzato, 4 = piano 0 ... 12 oltre il 7°
BAGNI = _N(1, 2, 3, 4)              # 4 = "Più di 3"
CLASSI = _N(*range(1500, 0, -100))  # 1500 non classificabile ... 1200 G ... 600 A ... 100 A4
ORDINI = ("datedesc", "relevance", "priceasc", "pricedesc")
RAGGIO_M = (500, 10000)             # cursore "Seleziona il raggio" (aria-valuemin/max)


@dataclass(frozen=True)
class Filtro:
    """Una chiave del client -> un parametro della query. `fisso` = valore scritto per un booleano che è un
    valore di un altro filtro (da_privato -> advt=0,2)."""
    param: str
    tipo: str                     # "enum" | "multiplo" | "booleano" | "testo"
    valori: tuple = ()
    fisso: str = "true"
    solo: tuple = ()              # (argomento, valori ammessi): fuori da lì Subito ignora il parametro in silenzio


VOCI = {
    "parola_chiave": Filtro("q", "testo"),
    "parola_nel_titolo": Filtro("q", "testo"),   # q + qso=true: "Ricerca solo nel titolo"
    "urgente": Filtro("urg", "booleano"),
    "ordinamento": Filtro("order", "enum", ORDINI),
    "zona": Filtro("z", "testo"),
    "pubblicato_da": Filtro("advt", "enum", ("0,2", "1")),
    "da_privato": Filtro("advt", "booleano", fisso="0,2"),
    "stato_immobile": Filtro("bc", "multiplo", _N(10, 20, 30, 40)),
    "nuova_costruzione": Filtro("bc", "booleano", fisso="10"),
    "prezzo_min": Filtro("ps", "enum", PREZZI["vendita"] + PREZZI["affitto"]),
    "prezzo_max": Filtro("pe", "enum", PREZZI["vendita"] + PREZZI["affitto"]),
    "mq_min": Filtro("szs", "enum", MQ),
    "mq_max": Filtro("sze", "enum", MQ),
    "locali_min": Filtro("rs", "enum", LOCALI),
    "locali_max": Filtro("re", "enum", LOCALI),
    "piano": Filtro("fls", "enum", PIANI),
    "piano_max": Filtro("fle", "enum", PIANI),
    "bagni_min": Filtro("btrs", "enum", BAGNI),
    "bagni_max": Filtro("btre", "enum", BAGNI),
    "arredato": Filtro("fr", "booleano", solo=("contratto", ("affitto",))),
    "libero": Filtro("an", "booleano"),
    "balcone": Filtro("blc", "booleano"),
    "ascensore": Filtro("el", "booleano"),
    "ultimo_piano": Filtro("lflr", "booleano"),
    "giardino": Filtro("grd", "booleano", solo=("tipologia", ("ville-singole-e-a-schiera",))),
    "posto_auto": Filtro("prk", "multiplo", _N(1, 2, 3, 4, 5)),
    "box_auto": Filtro("prk", "booleano", fisso="1"),
    "riscaldamento": Filtro("htg", "enum", _N(1, 2, 3, 4)),
    "aria_condizionata": Filtro("ac", "booleano"),
    "classe_energetica": Filtro("ecs", "enum", CLASSI),
    "classe_energetica_max": Filtro("ece", "enum", CLASSI),
}
# Ordine dei parametri come li scrive l'interfaccia (td() nel bundle: q, qso, urg, rad/lat/lon, order, o, z, filtri).
ORDINE_QUERY = ("q", "qso", "urg", "rad", "lat", "lon", "order", "o", "z", "advt", "bc", "ps", "pe", "szs", "sze",
                "rs", "re", "fls", "fle", "btrs", "btre", "fr", "an", "blc", "el", "lflr", "grd", "prk", "htg", "ac",
                "ecs", "ece")
# Riscrittura SEO della categoria appartamenti (mappe `to`/`te` nel bundle): parametro=valore -> segmento.
SEO_APPARTAMENTI = (("bc", "10", "nuove-costruzioni"), ("advt", "0,2", "da-privati"), ("blc", "true", "con-terrazzo"))

_RE_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_RE_ZONA = re.compile(r"\d{6}-\d+")


def _intero(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _decimale(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _valore(features: dict, uri: str, campo: str = "value"):
    vals = (features.get(uri) or {}).get("values") or []
    return vals[0].get(campo) if vals else None


def _comune(comune: str):
    chiave = comune.strip().lower()
    if chiave.replace("-", " ") in COMUNI:
        return COMUNI[chiave.replace("-", " ")]
    if chiave.count("/") == 2:
        return (*(p.strip() for p in chiave.split("/")), None)
    raise ValueError(f"comune {comune!r} non mappato: aggiungerlo a COMUNI oppure passare "
                     f"'regione/provincia/comune' come slug Subito (es. friuli-venezia-giulia/trieste/muggia)")


class Client:
    ORIGINE = "https://www.subito.it"
    TRASPORTO = "chrome"
    FILTRI = frozenset(VOCI) | {"tipologia", "raggio"}

    def __init__(self, trasporto):
        self.t = trasporto

    # ---------------------------------------------------------------- ricerca
    @staticmethod
    def richiesta(comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> tuple[str, dict]:
        """-> (percorso, query) come li costruisce l'interfaccia di Subito. ValueError su ogni input non ammesso."""
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r} non ammesso: {', '.join(CONTRATTI)}")
        if not isinstance(pagina, int) or pagina < 1:
            raise ValueError(f"pagina {pagina!r}: serve un intero >= 1")
        ignote = sorted(set(filtri) - Client.FILTRI)
        if ignote:
            raise ValueError(f"filtri sconosciuti per subito: {ignote}; ammessi: {sorted(Client.FILTRI)}")
        regione, provincia, paese, centro = _comune(comune)
        tipologia = filtri.pop("tipologia", "appartamenti")
        if tipologia not in TIPOLOGIE:
            raise ValueError(f"tipologia {tipologia!r} non ammessa: {', '.join(TIPOLOGIE)}")
        q: dict[str, str] = {}
        raggio = filtri.pop("raggio", None)
        if raggio is not None:
            if type(raggio) is not int or not RAGGIO_M[0] <= raggio <= RAGGIO_M[1]:
                raise ValueError(f"raggio {raggio!r}: serve un intero di metri tra {RAGGIO_M[0]} e {RAGGIO_M[1]}")
            if centro is None:
                raise ValueError(f"raggio: centro di {comune!r} sconosciuto, aggiungere il comune a COMUNI")
            q.update(rad=str(raggio), lat=str(centro[0]), lon=str(centro[1]))
            percorso = f"/annunci-locali/{CONTRATTI[contratto]}/{tipologia}/"
        else:
            percorso = f"/annunci-{regione}/{CONTRATTI[contratto]}/{tipologia}/{provincia}/{paese}/"

        multi: dict[str, list[str]] = {}
        for chiave, v in filtri.items():
            f = VOCI[chiave]
            if f.solo:
                arg, ammessi = f.solo
                if {"contratto": contratto, "tipologia": tipologia}[arg] not in ammessi:
                    raise ValueError(f"{chiave}: Subito lo applica solo con {arg} {', '.join(ammessi)}, "
                                     f"altrimenti lo ignora")
            if f.tipo == "booleano":
                if v is not True:
                    raise ValueError(f"{chiave}={v!r}: booleano, passare True (False = non filtrare, omettere)")
                val = [f.fisso]
            elif f.tipo == "testo":
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"{chiave}={v!r}: serve un testo non vuoto")
                if chiave == "zona" and not _RE_ZONA.fullmatch(v):
                    raise ValueError(f"zona={v!r}: serve l'id Subito '<istat comune>-<n>' (es. 015146-1)")
                val = [v.strip()]
                if chiave == "parola_nel_titolo":
                    q["qso"] = "true"
            else:
                val = [str(x) for x in (v if f.tipo == "multiplo" and isinstance(v, (list, tuple)) else [v])]
                ammessi = PREZZI.get(contratto, f.valori) if f.param in ("ps", "pe") else f.valori
                fuori = [x for x in val if x not in ammessi]
                if fuori or not val:
                    raise ValueError(f"{chiave}={v!r}: valori ammessi {', '.join(ammessi)}")
            if f.tipo == "multiplo" or f.param in ("prk", "bc"):
                multi.setdefault(f.param, [])
                multi[f.param] += [x for x in val if x not in multi[f.param]]
            elif f.param in q and q[f.param] != val[0]:
                raise ValueError(f"{chiave}: {f.param}={val[0]} in conflitto con {f.param}={q[f.param]}")
            else:
                q[f.param] = val[0]
        for param, vals in multi.items():
            q[param] = ",".join(vals)
        if q.get("order") == "datedesc":
            del q["order"]          # ordine predefinito: l'interfaccia non lo scrive
        if pagina > 1:
            q["o"] = str(pagina)
        if tipologia == "appartamenti":
            segmenti = []
            for param, valore, segmento in SEO_APPARTAMENTI:
                if q.get(param) == valore:
                    del q[param]
                    segmenti.append(segmento)
            percorso += "".join(s + "/" for s in segmenti)
        return percorso, {k: q[k] for k in ORDINE_QUERY if k in q}

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso, query = self.richiesta(comune, contratto, pagina, **filtri)
        r = self.t.get(percorso, params=query or None,
                       intestazioni={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        if r.status != 200:
            raise RuntimeError(f"Subito ha risposto {r.status} su {r.url}")
        return self.leggi_pagina(r.testo)

    # ---------------------------------------------------------------- parsing
    @staticmethod
    def next_data(testo: str) -> dict:
        m = _RE_NEXT.search(testo)
        if not m:
            raise RuntimeError("__NEXT_DATA__ assente: la pagina di ricerca ha cambiato struttura")
        return json.loads(m.group(1))

    @classmethod
    def leggi_pagina(cls, testo: str) -> Pagina:
        stato = cls.next_data(testo)["props"]["pageProps"].get("initialState") or {}
        items = stato.get("items") or {}
        if items.get("hasError"):
            raise RuntimeError("Subito segnala un errore nella lista risultati (items.hasError)")
        letto = adesso_utc()
        annunci = [cls._annuncio(x, letto) for x in items.get("originalList") or []
                   if x.get("kind") == "AdItem"]
        return Pagina(totale=_intero(items.get("total")), pagine=_intero(items.get("totalPages")),
                      annunci=annunci)

    @staticmethod
    def _annuncio(x: dict, letto_il: str) -> Annuncio:
        f = x.get("features") or {}
        geo = x.get("geo") or {}
        mappa = geo.get("map") or {}
        adv = x.get("advertiser") or {}
        urn = x.get("urn") or ""
        ident = urn.rsplit(":", 1)[-1] if ":list:" in urn else urn

        caratteristiche: list[str] = []
        for uri, feat in f.items():
            vals = feat.get("values") or []
            if not vals or uri in ("/price", "/size", "/room", "/bathrooms", "/floor",
                                   "/elevator", "/building_condition"):
                continue
            if feat.get("type") == "bool":
                if vals[0].get("key") == "1":
                    caratteristiche.append(feat.get("label") or uri.strip("/"))
            else:
                caratteristiche.append(f"{feat.get('label')}: {', '.join(str(v.get('value')) for v in vals)}")

        asc = _valore(f, "/elevator", "key")
        immagini = x.get("images") or []
        foto = immagini[0].get("cdnBaseUrl") if immagini else None
        zona = (geo.get("zone") or {}).get("value") if isinstance(geo.get("zone"), dict) else None

        return Annuncio(
            portale=PORTALE,
            id=ident,
            url=(x.get("urls") or {}).get("default"),
            titolo=x.get("subject"),
            prezzo=_intero(_valore(f, "/price", "key")),
            mq=_intero(_valore(f, "/size", "key")),
            locali=_valore(f, "/room"),
            bagni=_valore(f, "/bathrooms"),
            piano=_valore(f, "/floor"),
            ascensore=None if asc is None else asc == "1",
            indirizzo=mappa.get("address"),
            zona=zona or (geo.get("town") or {}).get("value"),
            lat=_decimale(mappa.get("latitude")),
            lon=_decimale(mappa.get("longitude")),
            descrizione=x.get("body"),
            foto=f"{foto}?rule=gallery-desktop-2x-auto" if foto else None,
            agenzia=(adv.get("shopName") or None) if adv.get("company") else None,
            condizione=_valore(f, "/building_condition"),
            caratteristiche=caratteristiche,
            ribasso_pct=None,
            letto_il=letto_il,
        )

    # ------------------------------------------------------ valori di riferimento
    def valori(self, percorso: str) -> list[dict]:
        """Legge una lista di valori dei filtri, es. '/v1/values/rooms' o '/v1/values/categories/7/sizes'.

        Sta su hades.subito.it, quindi funziona con ChromeTrasporto; ChromeTrasporto (solo stessa origine) lo rifiuta.
        """
        r = self.t.get(HADES + percorso, intestazioni={"Accept": "application/json"})
        if r.status != 200:
            raise RuntimeError(f"hades ha risposto {r.status} su {r.url}")
        return r.json().get("values") or []


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        p = Client(t).cerca("trieste", "vendita")
    print(f"totale={p.totale} pagine={p.pagine} in pagina={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.locali, a.titolo, a.url)
