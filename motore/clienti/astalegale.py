"""Client di ricerca per astalegale.net: aste giudiziarie di immobili, anche quelle del Portale Vendite Pubbliche.

La pagina `/Immobili?<query>` è renderizzata lato server da Nuxt e porta la risposta di `POST /webapi/Search`
nel payload `<script id="__NUXT_DATA__">` (formato devalue: un array piatto in cui gli oggetti puntano ad altri
indici). Il client chiede quella pagina al trasporto, decodifica il payload e legge `results`.
La query ha la forma che la pagina stessa costruisce (paramsToQuerystringSSR nel chunk Bb308-3p.js): chiavi in
ordine alfabetico, liste separate da virgola. Dal 16/11/2026 il sito risponde 403 a un client senza browser e 200 dal Chrome vero: TRASPORTO = "chrome".
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import unicodedata
from urllib.parse import urlencode

from ..portali.modello import Annuncio, Asta, Pagina, adesso_utc

PORTALE = "astalegale"
MAPPA = json.loads((pathlib.Path(__file__).resolve().parents[1] / "filtri" / f"{PORTALE}.json").read_text())
VALORI = {v["chiave"]: {x["valore"] for x in v["valori"]} for v in MAPPA["filtri"] if v["valori"]}

PROVINCE = {
    "Torino": "to", "Vercelli": "vc", "Novara": "no", "Cuneo": "cn", "Asti": "at", "Alessandria": "al", "Biella": "bi",
    "Verbano-Cusio-Ossola": "vb", "Aosta": "ao", "Varese": "va", "Como": "co", "Sondrio": "so", "Milano": "mi",
    "Bergamo": "bg", "Brescia": "bs", "Pavia": "pv", "Cremona": "cr", "Mantova": "mn", "Lecco": "lc", "Lodi": "lo",
    "Monza e della Brianza": "mb", "Bolzano": "bz", "Trento": "tn", "Verona": "vr", "Vicenza": "vi", "Belluno": "bl",
    "Treviso": "tv", "Venezia": "ve", "Padova": "pd", "Rovigo": "ro", "Udine": "ud", "Gorizia": "go", "Trieste": "ts",
    "Pordenone": "pn", "Imperia": "im", "Savona": "sv", "Genova": "ge", "La Spezia": "sp", "Piacenza": "pc",
    "Parma": "pr", "Reggio nell'Emilia": "re", "Modena": "mo", "Bologna": "bo", "Ferrara": "fe", "Ravenna": "ra",
    "Forlì-Cesena": "fc", "Rimini": "rn", "Massa-Carrara": "ms", "Lucca": "lu", "Pistoia": "pt", "Firenze": "fi",
    "Livorno": "li", "Pisa": "pi", "Arezzo": "ar", "Siena": "si", "Grosseto": "gr", "Prato": "po", "Perugia": "pg",
    "Terni": "tr", "Pesaro e Urbino": "pu", "Ancona": "an", "Macerata": "mc", "Ascoli Piceno": "ap", "Fermo": "fm",
    "Viterbo": "vt", "Rieti": "ri", "Roma": "rm", "Latina": "lt", "Frosinone": "fr", "L'Aquila": "aq", "Teramo": "te",
    "Pescara": "pe", "Chieti": "ch", "Campobasso": "cb", "Isernia": "is", "Caserta": "ce", "Benevento": "bn",
    "Napoli": "na", "Avellino": "av", "Salerno": "sa", "Foggia": "fg", "Bari": "ba", "Taranto": "ta", "Brindisi": "br",
    "Lecce": "le", "Barletta-Andria-Trani": "bt", "Potenza": "pz", "Matera": "mt", "Cosenza": "cs", "Catanzaro": "cz",
    "Reggio di Calabria": "rc", "Crotone": "kr", "Vibo Valentia": "vv", "Trapani": "tp", "Palermo": "pa",
    "Messina": "me", "Agrigento": "ag", "Caltanissetta": "cl", "Enna": "en", "Catania": "ct", "Ragusa": "rg",
    "Siracusa": "sr", "Sassari": "ss", "Nuoro": "nu", "Cagliari": "ca", "Oristano": "or", "Sud Sardegna": "su",
}
REGIONI = ("Piemonte", "Valle d'Aosta", "Lombardia", "Trentino Alto Adige", "Veneto", "Friuli Venezia Giulia", "Liguria",
           "Emilia Romagna", "Toscana", "Umbria", "Marche", "Lazio", "Abruzzo", "Molise", "Campania", "Puglia",
           "Basilicata", "Calabria", "Sicilia", "Sardegna")

ALLEGATI = {"con_foto": "foto", "con_planimetria": "planimetria", "con_video": "video",
            "con_virtual_tour": "virtualtour", "con_perizia": "perizia"}
SEMPLICI = {"prezzo_min": "prezzo_da", "prezzo_max": "prezzo_a", "tribunale": "tribunale",
            "tipo_procedura": "tipo_procedura", "esito": "esito", "fascia_data": "data", "data_asta_da": "asta_da",
            "data_asta_a": "asta_a", "parola_chiave": "q", "ordinamento": "sort",
            "risultati_per_pagina": "limit"}
SITO_ESTERNO = {"astalegale": "false", "altri_siti": "true"}
INTERI = {"prezzo_min", "prezzo_max", "risultati_per_pagina"}
DATE = {"data_asta_da", "data_asta_a"}

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
_PAYLOAD = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_DATA_ASTA = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s*-\s*(\d{2}):(\d{2}))?")
_COORDINATA = r"-?\d+(?:\.\d+)?"
_PUNTO = re.compile(rf"{_COORDINATA},{_COORDINATA}")


class ErroreStruttura(RuntimeError):
    """La pagina non contiene la ricerca attesa (struttura del sito cambiata o redirezione)."""


def slug(testo: str) -> str:
    """Come gli slug del sito: 'Sant'Angelo dei Lombardi' -> santangelo-dei-lombardi."""
    t = unicodedata.normalize("NFKD", testo.strip().lower()).encode("ascii", "ignore").decode().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def luogo(comune: str) -> tuple[str, str]:
    """-> (parametro, slug). 'Trieste' -> comuni; 'Provincia di Trieste' -> province=ts; 'Regione Lombardia' -> regioni."""
    c = comune.strip()
    minuscolo = c.lower()
    if minuscolo.startswith("provincia di "):
        nome = c[len("provincia di "):].strip().lower()
        sigla = next((s for n, s in PROVINCE.items() if nome in (n.lower(), s)), None)
        if sigla is None:
            raise ValueError(f"provincia {nome!r} non tra le {len(PROVINCE)} di astalegale")
        return "province", sigla
    if minuscolo.startswith("regione "):
        nome = c[len("regione "):].strip().lower()
        if nome not in {r.lower() for r in REGIONI}:
            raise ValueError(f"regione {nome!r}: ammesse {REGIONI}")
        return "regioni", slug(nome)
    if not slug(c):
        raise ValueError(f"comune {comune!r} vuoto")
    return "comuni", slug(c)


def decodifica_nuxt(payload: list):
    """Ricostruisce l'oggetto dal formato devalue di Nuxt: ogni intero in un contenitore è l'indice di un altro valore."""
    fatti: dict[int, object] = {}

    def valore(i):
        if not isinstance(i, int) or i < 0:
            return None
        if i in fatti:
            return fatti[i]
        v = payload[i]
        if isinstance(v, list) and v and isinstance(v[0], str):
            tipo = v[0]
            if tipo in ("Date", "RegExp", "BigInt"):
                fatti[i] = v[1]
                return v[1]
            if tipo in ("ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "NuxtError"):
                fatti[i] = valore(v[1]) if len(v) > 1 else None
                return fatti[i]
            if tipo in ("Set", "Map"):
                fatti[i] = [valore(x) for x in v[1:]]
                return fatti[i]
        if isinstance(v, list):
            fatti[i] = out = []
            out.extend(valore(x) for x in v)
            return out
        if isinstance(v, dict):
            fatti[i] = out = {}
            for k, x in v.items():
                out[k] = valore(x)
            return out
        fatti[i] = v
        return v

    return valore(0)


def _euro(testo) -> int | None:
    """'€ 139.500,00' o '62.400,00' -> intero in euro."""
    if testo is None:
        return None
    cifre = re.sub(r"[^\d]", "", str(testo).split(",")[0])
    return int(cifre) if cifre else None


def _data_iso(testo: str | None) -> str | None:
    m = _DATA_ASTA.search(testo or "")
    if not m:
        return None
    g, me, a, h, mi = m.groups()
    return f"{a}-{me}-{g}" + (f"T{h}:{mi}" if h else "")


def annuncio(r: dict, origine: str, letto_il: str) -> Annuncio:
    posizione = r.get("posizione") or {}
    tipologia, via, comune = r.get("tipologia"), r.get("titolo"), r.get("comune")
    via = None if via in ("-", "") else via  # i posti auto senza indirizzo hanno titolo "-"
    caratteristiche = [x for x in (tipologia, r.get("tipoProceduraEsteso"),
                                   r.get("provenienza") and f"Fonte: {r['provenienza']}") if x]
    if r.get("hasVirtualTour"):
        caratteristiche.append("Virtual tour")
    prezzo = r.get("prezzoNum")
    return Annuncio(
        portale=PORTALE,
        id=r["id"],
        url=f"{origine}/Aste/Detail/{r.get('friendlyId') or r['id']}",
        titolo=", ".join(x for x in (tipologia, via, comune) if x) or None,
        prezzo=round(prezzo) if isinstance(prezzo, (int, float)) else _euro(r.get("prezzo")),
        mq=None,
        locali=None,
        bagni=None,
        piano=None,
        ascensore=None,
        indirizzo=", ".join(x for x in (via, comune) if x) or None,
        zona=None,
        lat=posizione.get("lat"),
        lon=posizione.get("lng"),
        descrizione=r.get("descrizione"),
        foto=r.get("urlImmaginePrincipale"),
        agenzia=None,
        condizione=None,
        caratteristiche=caratteristiche,
        ribasso_pct=None,
        letto_il=letto_il,
        asta=Asta(
            data=_data_iso(r.get("dataAsta")),
            offerta_minima=_euro(r.get("offertaMinima")),
            tribunale=r.get("tribunale"),
            procedura=r.get("proceduraNumeroAnno"),
            lotto=r.get("codiceLotto"),
            modalita=r.get("modalitaVendita"),
        ),
    )


def ricerca_da_html(testo: str) -> dict:
    """Il risultato di /webapi/Search dentro il payload Nuxt: {tags, facets, results, title}."""
    m = _PAYLOAD.search(testo)
    if not m:
        raise ErroreStruttura("payload __NUXT_DATA__ non trovato nella pagina")
    dati = decodifica_nuxt(json.loads(m.group(1)))
    ricerca = next((v for k, v in (dati.get("data") or {}).items() if k.startswith("/webapi/Search")), None)
    if not isinstance(ricerca, dict) or not isinstance(ricerca.get("results"), dict):
        raise ErroreStruttura(f"nessuna ricerca nel payload (pagina {dati.get('path')!r})")
    return ricerca


def leggi_pagina(ricerca: dict, origine: str) -> Pagina:
    res = ricerca["results"]
    totale, per_pagina = res.get("totalResults"), res.get("pageSize") or 12
    letto_il = adesso_utc()
    annunci = [annuncio(r, origine, letto_il) for r in res.get("currentPage") or []]
    pagine = math.ceil(totale / per_pagina) if isinstance(totale, int) else None
    return Pagina(totale=totale, pagine=pagine, annunci=annunci)


def _intero(chiave: str, v) -> int:
    if type(v) is not int or v < 0:
        raise ValueError(f"filtro {chiave}: serve un intero >= 0, non {v!r}")
    return v


def _ammesso(chiave: str, v) -> str:
    if v not in VALORI[chiave]:
        raise ValueError(f"filtro {chiave}: valore {v!r} non ammesso, vedi i valori in filtri/{PORTALE}.json")
    return v


def _lista(chiave: str, v) -> list[str]:
    valori = [v] if isinstance(v, str) else list(v) if isinstance(v, (list, tuple)) else None
    if not valori:
        raise ValueError(f"filtro {chiave}: serve un valore o una lista di valori, non {v!r}")
    return [_ammesso(chiave, x) for x in valori]


def _punti(chiave: str, v) -> list[str]:
    punti = v.split(";") if isinstance(v, str) else []
    if not punti or not all(_PUNTO.fullmatch(p.strip()) for p in punti):
        raise ValueError(f"filtro {chiave}: servono punti 'lat,lng' separati da ';', non {v!r}")
    return [p.strip() for p in punti]


class Client:
    ORIGINE = "https://www.astalegale.net"
    TRASPORTO = "chrome"
    FILTRI = frozenset(SEMPLICI) | frozenset(ALLEGATI) | frozenset(
        {"tipologia", "sottocategoria", "procedura", "includi_aste_passate", "sito_provenienza", "raggio", "mappa_area"})

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def query(self, comune: str, pagina: int = 1, **filtri) -> dict[str, str]:
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtri sconosciuti per {PORTALE}: {ignoti}")
        f = {k: v for k, v in filtri.items() if v is not None}
        q: dict[str, str] = {}

        # Un'area disegnata prende il posto del luogo: con wkt il sito ignora comuni (guarda/comuni_trieste_radius_*).
        if "raggio" in f and "mappa_area" in f:
            raise ValueError("raggio e mappa_area insieme: il sito accetta una sola area")
        if "raggio" in f:
            *centro, metri = (f["raggio"].split(",") if isinstance(f["raggio"], str) else [])
            if len(centro) != 2 or not metri.strip().isdigit() or not _PUNTO.fullmatch(",".join(centro)):
                raise ValueError(f"filtro raggio: serve 'lat,lng,metri', non {f['raggio']!r}")
            q["wkt"], q["radius"] = ",".join(centro), metri.strip()
        elif "mappa_area" in f:
            punti = _punti("mappa_area", f["mappa_area"])
            if len(punti) < 3:
                raise ValueError("filtro mappa_area: un'area chiede almeno 3 punti")
            q["wkt"] = ";".join(punti)
        else:
            parametro, valore = luogo(comune)
            q[parametro] = valore

        categorie = _lista("tipologia", f["tipologia"]) if "tipologia" in f else []
        if "sottocategoria" in f:
            categorie += _lista("sottocategoria", f["sottocategoria"])
        if categorie:
            q["categories"] = ",".join(categorie)
        for chiave, parametro in SEMPLICI.items():
            if chiave not in f:
                continue
            v = f[chiave]
            if chiave in INTERI:
                v = _intero(chiave, v)
                if chiave in VALORI and v not in VALORI[chiave]:
                    raise ValueError(f"filtro {chiave}: ammessi {sorted(VALORI[chiave])}, non {v}")
            elif chiave in DATE:
                if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    raise ValueError(f"filtro {chiave}: serve una data AAAA-MM-GG, non {v!r}")
            elif chiave == "parola_chiave":
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"filtro parola_chiave: serve un testo, non {v!r}")
                v = v.strip()
            else:
                v = _ammesso(chiave, v)
            q[parametro] = str(v)
        if "procedura" in f:
            m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d{4})\s*", f["procedura"]) if isinstance(f["procedura"], str) else None
            if not m:
                raise ValueError(f"filtro procedura: serve 'numero/anno' come '35/2026', non {f['procedura']!r}")
            q["proc_numero"], q["proc_anno"] = m.groups()
        if "includi_aste_passate" in f:
            if f["includi_aste_passate"] is not True:
                raise ValueError("filtro includi_aste_passate: l'unico valore è True")
            q["aste_passate"] = "true"
        allegati = []
        for chiave, valore in ALLEGATI.items():
            if chiave in f:
                if f[chiave] is not True:
                    raise ValueError(f"filtro {chiave}: l'unico valore è True")
                allegati.append(valore)
        if allegati:
            q["tipo_allegati"] = ",".join(allegati)
        if "sito_provenienza" in f:
            if f["sito_provenienza"] not in SITO_ESTERNO:
                raise ValueError(f"filtro sito_provenienza: ammessi {list(SITO_ESTERNO)}, non {f['sito_provenienza']!r}")
            q["sitoEsterno"] = SITO_ESTERNO[f["sito_provenienza"]]
        if pagina > 1:
            q["page"] = str(pagina)
        return dict(sorted(q.items()))

    def percorso(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        if contratto != "vendita":
            raise ValueError(f"contratto {contratto!r}: su {PORTALE} ci sono solo vendite all'asta")
        return "/Immobili?" + urlencode(self.query(comune, pagina, **filtri), safe=",;")

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso = self.percorso(comune, contratto, pagina, **filtri)
        r = self.trasporto.get(percorso, intestazioni=INTESTAZIONI_HTML)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        ricerca = ricerca_da_html(r.testo)
        if "raggio" not in filtri and "mappa_area" not in filtri:
            parametro, valore = luogo(comune)
            luoghi = {t.get("id") for t in ricerca.get("tags") or [] if t.get("type") == "Luogo"}
            if valore not in luoghi:
                raise ValueError(f"luogo {comune!r} ({parametro}={valore}) non riconosciuto da {PORTALE}: tag {sorted(luoghi)}")
        return leggi_pagina(ricerca, self.ORIGINE)


if __name__ == "__main__":
    from ..trasporto import HttpTrasporto

    with HttpTrasporto(Client.ORIGINE, pausa_secondi=8) as t:
        p = Client(t).cerca("Trieste")
        print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
        for a in p.annunci:
            print(a.id, a.prezzo, a.asta, a.url)
