"""Client di ricerca per casadaprivato.it (solo annunci di privati, pagina renderizzata lato server).

casadaprivato.it (ASP.NET WebForms) non espone un'API JSON per la lista risultati: la pagina
`/annunci-<contratto>/<tipologia>/<luogo>[/pagina-N][?filtri]` contiene gli annunci come HTML
(`div#property-listing > div.item`), il totale in `div.total-properties h2 strong` e la
paginazione in `div.pagination`. Il client chiede quella pagina al trasporto e la legge con la
libreria standard (re + html.unescape). La forma dell'URL dei filtri è quella che restituisce
/handler/ricerca.ashx, il servizio che il bottone Cerca chiama per sapere dove andare (vedi README).

Nessun antibot visto: TRASPORTO = "http". Il client non fa HTTP da solo, usa solo
`trasporto.get(...)`; su blocco il trasporto solleva `Bloccato` e ci si ferma.
"""
from __future__ import annotations

import html
import math
import re
import unicodedata
from urllib.parse import urlencode

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "casadaprivato"
PER_PAGINA = 15  # annunci per pagina osservati nell'HAR (30 totali, 15 in pagina 1, link a pagina-2)
CONTRATTI = {"vendita": "annunci-vendita", "affitto": "annunci-affitto"}

# Tipologia: slug di percorso -> (id del form, etichetta, contratti). Gli id residenziali (sotto "Case", 999) si
# combinano: uno solo dà il suo slug, più d'uno dà /immobili/?tipologie=1,2 (risposte di /handler/ricerca.ashx).
TIPOLOGIE = {
    "appartamenti": (1, "Appartamenti", ("vendita", "affitto")),
    "attici-mansarde": (2, "Attici/Mansarde", ("vendita", "affitto")),
    "case-indipendenti": (4, "Case indipendenti", ("vendita", "affitto")),
    "loft-open-space": (5, "Loft/Open Space", ("vendita", "affitto")),
    "rustici-casali": (7, "Rustici/Casali", ("vendita", "affitto")),
    "ville-singole-e-a-schiera": (8, "Ville singole e a schiera", ("vendita", "affitto")),
    "altro-residenziale": (9, "Altro residenziale", ("vendita", "affitto")),
    "box-garage-posti-auto": (3, "Box/Garage/Posti auto", ("vendita", "affitto")),
    "palazzi-stabili": (6, "Palazzi/Stabili", ("vendita", "affitto")),
    "nuove-costruzioni": (14, "Nuove costruzioni", ("vendita", "affitto")),
    "immobili-attivita-commerciali": (10, "Immobili e attività commerciali", ("vendita", "affitto")),
    "terreni": (11, "Terreni", ("vendita", "affitto")),
    "case-vacanza": (12, "Case vacanza", ("affitto",)),
    "stanze-posti-letto": (13, "Stanze e posti letto", ("affitto",)),
}
RESIDENZIALI = frozenset(k for k, (n, _, _) in TIPOLOGIE.items() if n in (1, 2, 4, 5, 7, 8, 9))

# Soglie dei select (Ricerca.c in /js/ricerca e opzioni lette dal DOM): valore in euro o mq -> codice nell'URL.
SOGLIE_PREZZO = {
    "vendita": [20000, 50000, 100000, 130000, 150000, 170000, 190000, 220000, 250000, 290000, 350000, 450000,
                750000, 1000000, 3000000],
    "affitto": [100, 300, 500, 600, 700, 800, 900, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000],
}
SOGLIE_MQ = [20, 40, 60, 80, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 5000, 10000]
CODICI = {  # chiave -> (parametro, soglie in euro/mq oppure il massimo quando il codice è il numero stesso)
    "mq_min": ("mqmin", SOGLIE_MQ), "mq_max": ("mqmax", SOGLIE_MQ),
    "prezzo_min": ("prezzomin", "prezzo"), "prezzo_max": ("prezzomax", "prezzo"),
    "locali_min": ("localimin", 5), "locali_max": ("localimax", 5),
    "posti_letto_min": ("postilettomin", 10), "posti_letto_max": ("postilettomax", 10),
}
TIPI_STANZA = {"singola": 1, "doppia": 2, "tripla": 3, "posto-letto": 4}
ORDINAMENTI = {"data-desc": "Più recente", "data": "Meno recente", "prezzo-desc": "Più costoso",
               "prezzo": "Meno costoso", "piace-desc": "Piace di più", "piace": "Piace di meno"}
# Filtri che l'interfaccia mostra solo con una tipologia: il client la sceglie se manca, altrimenti la controlla.
TIPOLOGIA_OBBLIGATA = {"posti_letto_min": "case-vacanza", "posti_letto_max": "case-vacanza",
                       "tipo_stanza": "stanze-posti-letto"}
SENZA_LOCALI = frozenset({"box-garage-posti-auto", "terreni", "case-vacanza", "stanze-posti-letto"})
SENZA_PREZZO_MQ = frozenset({"case-vacanza", "stanze-posti-letto"})

# Province (select#cmbProvincia): per un capoluogo basta il nome ("Trieste" -> trieste-trieste).
PROVINCE = frozenset([
    "Agrigento", "Alessandria", "Ancona", "Aosta", "Arezzo", "Ascoli Piceno", "Asti", "Avellino", "Bari", "Barletta-Andria-Trani", "Belluno", "Benevento", "Bergamo", "Biella", "Bologna", "Bolzano/Bozen", "Brescia", "Brindisi", "Cagliari", "Caltanissetta", "Campobasso", "Caserta", "Catania", "Catanzaro", "Chieti", "Como", "Cosenza", "Cremona", "Crotone", "Cuneo", "Enna", "Fermo", "Ferrara", "Firenze", "Foggia", "Forlì-Cesena", "Frosinone", "Genova", "Gorizia", "Grosseto", "Imperia", "Isernia", "L'Aquila", "La Spezia", "Latina", "Lecce", "Lecco", "Livorno", "Lodi", "Lucca", "Macerata", "Mantova", "Massa-Carrara", "Matera", "Messina", "Milano", "Modena", "Monza e Brianza", "Napoli", "Novara", "Nuoro", "Oristano", "Padova", "Palermo", "Parma", "Pavia", "Perugia", "Pesaro e Urbino", "Pescara", "Piacenza", "Pisa", "Pistoia", "Pordenone", "Potenza", "Prato", "Ragusa", "Ravenna", "Reggio Calabria", "Reggio Emilia", "Rieti", "Rimini", "Roma", "Rovigo", "Salerno", "Sassari", "Savona", "Siena", "Siracusa", "Sondrio", "Sud Sardegna", "Taranto", "Teramo", "Terni", "Torino", "Trapani", "Trento", "Treviso", "Trieste", "Udine", "Varese", "Venezia", "Verbano-Cusio-Ossola", "Vercelli", "Verona", "Vibo Valentia", "Vicenza", "Viterbo"])

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

_ITEM = re.compile(r'<div class="item col-md-12">')
# La lista finisce dove cominciano gli annunci "simili nelle vicinanze", che sono di altri luoghi.
_FINE_LISTA = re.compile(r'<!-- END PROPERTY LISTING -->|<!-- BEGIN PAGINATION -->|<h2 class="similar-properties')
_HREF = re.compile(r'<h3>\s*<a href="(/[^"]+?-(\d+))"[^>]*>(.*?)</a>', re.S)
_FOTO = re.compile(r'<img class="lazyload"[^>]*data-src="([^"]*)"')
_PREZZO = re.compile(r'<div class="price">(.*?)</div>', re.S)
_SOTTOTITOLO = re.compile(r'<small class="subtitle">(.*?)</small>', re.S)
_DESCR = re.compile(r'</small>\s*<p>(.*?)</p>', re.S)
_AMENITY = re.compile(r'<li><i class="icon-(rooms|area|bathrooms)"></i>(.*?)</li>', re.S)
_TAGS = re.compile(r'<div class="tags">(.*?)</div>', re.S)
_TOTALE = re.compile(r'class="total-properties[^"]*">\s*<h2>\s*<strong>([\d.]+)</strong>', re.S)
_PAGINAZIONE = re.compile(r'<div class="pagination">(.*?)</div>', re.S)
_NUM_PAGINA = re.compile(r"/pagina-(\d+)\"|<li class=\"active\">(\d+)</li>")
_TAG_HTML = re.compile(r"<[^>]+>")
_SIMILI = re.compile(r'class="similar-properties')
_PIANO = re.compile(r"\b(piano terra|seminterrato|piano rialzato|ultimo piano|attico|\w+ piano|piano su più livelli)\b", re.I)
_CONDIZIONI = ("Nuovo/In costruzione", "Ristrutturato", "Da ristrutturare", "Buono stato", "Ottimo stato", "Abitabile")


class ErroreStruttura(RuntimeError):
    """La pagina non contiene la lista attesa (struttura del sito cambiata)."""


def _testo(frammento: str | None) -> str | None:
    if frammento is None:
        return None
    t = re.sub(r"\s+", " ", html.unescape(_TAG_HTML.sub(" ", frammento))).strip()
    return t or None


def _int(v: str | None) -> int | None:
    if not v:
        return None
    cifre = re.sub(r"[^\d]", "", v)
    return int(cifre) if cifre else None


def slug(testo: str) -> str:
    """Minuscolo senza accenti, ogni corsa di caratteri non alfanumerici diventa '-'."""
    t = unicodedata.normalize("NFKD", testo.strip().lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _provincia_nota(nome: str) -> str:
    if nome.strip().lower() not in {p.lower() for p in PROVINCE}:
        raise ValueError(f"provincia {nome!r} non tra le {len(PROVINCE)} di casadaprivato")
    return slug(nome)


def slug_luogo(comune: str, provincia: str | None = None) -> str:
    """'Trieste' -> trieste-trieste (capoluogo); 'Muggia' in provincia 'Trieste' -> trieste-muggia;
    'Provincia di Trieste' -> trieste-provincia."""
    c = comune.strip()
    if c.lower().startswith("provincia di "):
        return f"{_provincia_nota(c[len('provincia di '):])}-provincia"
    if provincia:
        return f"{_provincia_nota(provincia)}-{slug(c)}"
    if c.lower() in {p.lower() for p in PROVINCE}:
        return f"{slug(c)}-{slug(c)}"
    raise ValueError(f"comune {comune!r}: non è un capoluogo di provincia. Passa la sua provincia (provincia di cerca) "
                     f"o 'Provincia di <nome>'.")


def annuncio_da_html(blocco: str, origine: str, letto_il: str) -> Annuncio | None:
    m = _HREF.search(blocco)
    if not m:
        return None
    percorso, id_, titolo_html = m.groups()
    titolo = _testo(titolo_html)

    # Titolo: "<Tipologia> da Privato a <Comune>, Frazione <Zona>, <Indirizzo>"
    zona = indirizzo = None
    if titolo:
        parti = [p.strip() for p in titolo.split(",")]
        resto = parti[1:]
        if resto and resto[0].startswith("Frazione "):
            zona = resto.pop(0)[len("Frazione "):] or None
        indirizzo = ", ".join(resto) or None

    prezzo_txt = _testo(_PREZZO.search(blocco).group(1)) if _PREZZO.search(blocco) else None
    prezzo = _int(prezzo_txt) if prezzo_txt and "€" in prezzo_txt else None  # "Trattativa Riservata" -> None

    locali = mq = bagni = None
    for tipo, valore in _AMENITY.findall(blocco):
        v = _testo(valore) or ""
        if tipo == "rooms":
            locali = v.split(" ")[0] or None  # "2 locali", "5+ locali"
        elif tipo == "area":
            mq = _int(v)
        elif tipo == "bathrooms":
            bagni = v.split(" ")[0] or None

    tags_txt = _testo(_TAGS.search(blocco).group(1)) if _TAGS.search(blocco) else None
    tags = [t.strip() for t in tags_txt.split("•")] if tags_txt else []
    tags = [t for t in tags if t]
    piano = next((t for t in tags if _PIANO.fullmatch(t)), None)
    condizione = next((t for t in tags if t in _CONDIZIONI), None)

    foto = _FOTO.search(blocco)
    sottotitolo = _SOTTOTITOLO.search(blocco)
    descr = _DESCR.search(blocco)
    descrizione = _testo(descr.group(1)) if descr else None
    if descrizione and sottotitolo and _testo(sottotitolo.group(1)):
        descrizione = f"{_testo(sottotitolo.group(1))}. {descrizione}"

    return Annuncio(
        portale=PORTALE,
        id=id_,
        url=origine + percorso,
        titolo=titolo,
        prezzo=prezzo,
        mq=mq,
        locali=locali,
        bagni=bagni,
        piano=piano,
        ascensore=True if "Ascensore" in tags else None,  # l'assenza del tag non implica "senza ascensore"
        indirizzo=indirizzo,
        zona=zona,
        lat=None,
        lon=None,
        descrizione=descrizione,
        foto=(foto.group(1) or None) if foto else None,
        agenzia=None,  # solo privati
        condizione=condizione,
        caratteristiche=tags,
        ribasso_pct=None,
        letto_il=letto_il,
    )


def leggi_pagina(testo: str, origine: str) -> Pagina:
    inizio = testo.find('id="property-listing"')
    tot = _TOTALE.search(testo)
    if inizio < 0 and not tot:
        raise ErroreStruttura("lista annunci (#property-listing) non trovata nella pagina")
    totale = int(tot.group(1).replace(".", "")) if tot else None
    if totale is None and _SIMILI.search(testo):
        # Zero risultati: il sito mostra annunci "simili nelle vicinanze" di un altro luogo. Non sono la ricerca.
        return Pagina(totale=0, pagine=0, annunci=[])

    annunci: list[Annuncio] = []
    if inizio >= 0:
        lista = testo[inizio:]
        fine = _FINE_LISTA.search(lista)
        if fine:
            lista = lista[: fine.start()]
        letto_il = adesso_utc()
        for blocco in _ITEM.split(lista)[1:]:
            a = annuncio_da_html(blocco, origine, letto_il)
            if a is not None:
                annunci.append(a)

    pagine = None
    pag = _PAGINAZIONE.search(testo)
    if pag:
        numeri = [int(x or y) for x, y in _NUM_PAGINA.findall(pag.group(1))]
        pagine = max(numeri) if numeri else None
    if totale is not None:
        stimate = max(1, math.ceil(totale / PER_PAGINA)) if totale else 0
        # la paginazione puo' mostrare solo una finestra di pagine: tengo il massimo
        pagine = max(pagine or 0, stimate)
    return Pagina(totale=totale, pagine=pagine, annunci=annunci)


def _codice(chiave: str, v, soglie, contratto: str) -> int:
    if type(v) is not int:
        raise ValueError(f"filtro {chiave}: serve un intero, non {v!r}")
    if soglie == "prezzo":
        soglie = SOGLIE_PREZZO[contratto]
    if isinstance(soglie, int):
        if not 1 <= v <= soglie:
            raise ValueError(f"filtro {chiave}: ammessi 1..{soglie}, non {v}")
        return v
    if v not in soglie:
        raise ValueError(f"filtro {chiave}: il sito offre solo le soglie {soglie}, non {v}")
    return soglie.index(v) + 1


class Client:
    ORIGINE = "https://www.casadaprivato.it"
    TRASPORTO = "http"
    FILTRI = frozenset(CODICI) | frozenset({"tipologia", "zona", "tipo_stanza", "ordinamento"})

    CHIEDE_PROVINCIA = True

    def __init__(self, trasporto, provincia: str | None = None):
        self.trasporto = trasporto
        self.provincia = provincia

    def percorso(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> str:
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r} non supportato: usa {' o '.join(CONTRATTI)}")
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtri sconosciuti per casadaprivato: {ignoti}")
        f = {k: v for k, v in filtri.items() if v is not None}

        tipi = f.get("tipologia")
        tipi = [] if tipi is None else [tipi] if isinstance(tipi, str) else list(tipi)
        fuori = [t for t in tipi if t not in TIPOLOGIE]
        if fuori or (tipi and len(set(tipi)) != len(tipi)):
            raise ValueError(f"tipologia {fuori or tipi}: ammesse {sorted(TIPOLOGIE)}")
        for k, obbligata in TIPOLOGIA_OBBLIGATA.items():
            if k in f:
                if tipi and tipi != [obbligata]:
                    raise ValueError(f"filtro {k}: esiste solo con tipologia {obbligata!r}, non {tipi}")
                tipi = [obbligata]
        if len(tipi) > 1 and not set(tipi) <= RESIDENZIALI:
            raise ValueError(f"tipologia: più valori insieme solo tra i residenziali {sorted(RESIDENZIALI)}")
        for t in tipi:
            if contratto not in TIPOLOGIE[t][2]:
                raise ValueError(f"tipologia {t!r}: solo in {TIPOLOGIE[t][2]}")
        q: list[tuple[str, object]] = []
        if len(tipi) > 1:
            segmento = "immobili"
            q.append(("tipologie", ",".join(str(TIPOLOGIE[t][0]) for t in sorted(tipi, key=lambda t: TIPOLOGIE[t][0]))))
        else:
            segmento = tipi[0] if tipi else "immobili"

        for k, (par, soglie) in CODICI.items():
            if k not in f:
                continue
            if segmento in SENZA_PREZZO_MQ and k.split("_")[0] in ("prezzo", "mq", "locali"):
                raise ValueError(f"filtro {k}: non esiste per {segmento}")
            if segmento in SENZA_LOCALI and k.startswith("locali"):
                raise ValueError(f"filtro {k}: non esiste per {segmento}")
            q.append((par, _codice(k, f[k], soglie, contratto)))
        if "tipo_stanza" in f:
            if f["tipo_stanza"] not in TIPI_STANZA:
                raise ValueError(f"tipo_stanza {f['tipo_stanza']!r}: ammessi {list(TIPI_STANZA)}")
            q.append(("tipostanza", TIPI_STANZA[f["tipo_stanza"]]))

        luogo = slug_luogo(comune, self.provincia)
        if "zona" in f:
            # /handler/ricerca.ashx: Milano + Centro Storico -> /milano-zona-centro_storico (senza la provincia).
            if luogo.endswith("-provincia") or not isinstance(f["zona"], str) or not f["zona"].strip():
                raise ValueError(f"filtro zona: serve il nome di una zona di un comune, non {f['zona']!r} su {comune!r}")
            luogo = f"{slug(comune)}-zona-{slug(f['zona']).replace('-', '_')}"
        if "ordinamento" in f:
            if f["ordinamento"] not in ORDINAMENTI:
                raise ValueError(f"ordinamento {f['ordinamento']!r}: ammessi {list(ORDINAMENTI)}")
            q.append(("sort", f["ordinamento"]))
        p = f"/{CONTRATTI[contratto]}/{segmento}/{luogo}" + ("" if pagina == 1 else f"/pagina-{pagina}")
        return p + ("?" + urlencode(q) if q else "")

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso = self.percorso(comune, contratto, pagina, **filtri)
        r = self.trasporto.get(percorso, intestazioni=INTESTAZIONI_HTML)
        if r.status == 404:
            raise ValueError(f"luogo inesistente su casadaprivato: {percorso}")
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        return leggi_pagina(r.testo, self.ORIGINE)


if __name__ == "__main__":
    from ..trasporto import HttpTrasporto

    with HttpTrasporto(Client.ORIGINE) as t:
        p = Client(t).cerca("Trieste")
        print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
        for a in p.annunci[:5]:
            print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.url)
