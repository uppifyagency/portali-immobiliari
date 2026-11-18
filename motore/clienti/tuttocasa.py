"""Client di ricerca per tuttocasa.it (lista risultati renderizzata lato server, HTML).

tuttocasa.it non usa un'API JSON per la ricerca: la pagina
`/?act=annunci&key=<comune>&contratto=..&tipologia=..&prezzoda=..&prezzoa=..&mqda=..&mqa=..
&vanida=..&vania=..&agenzia=..&privati=..[&page=N]` contiene gia' le schede degli annunci
(10 per pagina). Ogni filtro non impostato vale la stringa "no". Il client chiede quella
pagina al trasporto e la legge con espressioni regolari (solo libreria standard).

Nessun antibot osservato nell'HAR, quindi TRASPORTO = "http". Il client non fa HTTP da solo:
usa solo `trasporto.get(...)`. Su blocco il trasporto solleva `Bloccato` e ci si ferma.
"""
from __future__ import annotations

import html as _html
import re
import sys

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Dettaglio, Pagina, adesso_utc  # noqa: F401

PORTALE = "tuttocasa"
PERCORSO_RICERCA = "/"
NON_IMPOSTATO = "no"

# Valori della <select name="contratto"> del form di ricerca.
CONTRATTI = {"vendita": "1", "affitto": "2", "affitto-vacanze": "3"}

# <select name="tipologia">: id -> "gruppo: etichetta". Alcune etichette si ripetono in gruppi diversi
# (Azienda agricola 48 e 38, Altro 53, 54, 55, 56), per questo la chiave è l'id. Mancano le 9 voci del form che non
# si possono provare dal vivo: l'elenco e il motivo stanno nella nota della voce tipologia di filtri/tuttocasa.json.
TIPOLOGIE = {
    "1": "Residenziali: Appartamento",
    "2": "Residenziali: Loft/open space",
    "3": "Residenziali: Attico",
    "4": "Residenziali: Mansarda",
    "5": "Residenziali: Camere",
    "6": "Residenziali: Casa indipendente",
    "7": "Residenziali: Casa semi-indipendente",
    "8": "Residenziali: Villetta",
    "9": "Residenziali: Villetta a schiera",
    "11": "Residenziali: Viareggina",
    "12": "Residenziali: Villa",
    "13": "Residenziali: Terratetto",
    "14": "Residenziali: Palazzo",
    "15": "Residenziali: Rustico",
    "16": "Residenziali: Colonica",
    "17": "Residenziali: Fienile",
    "18": "Residenziali: Cascinale",
    "19": "Residenziali: Residence",
    "22": "Residenziali: Garage/box/posto auto",
    "23": "Residenziali: Castello/torre",
    "24": "Residenziali: Seminterrato",
    "25": "Residenziali: Multiproprietà",
    "26": "Residenziali: Duplex",
    "27": "Residenziali: Porzione di casa",
    "48": "Residenziali: Azienda agricola",
    "53": "Residenziali: Altro",
    "29": "Uffici-Fondi commerciali: Ufficio",
    "30": "Uffici-Fondi commerciali: Capannone",
    "31": "Uffici-Fondi commerciali: Laboratorio",
    "33": "Uffici-Fondi commerciali: Fondo commerciale",
    "34": "Uffici-Fondi commerciali: Fondo artigianale",
    "35": "Uffici-Fondi commerciali: Magazzino",
    "36": "Uffici-Fondi commerciali: Negozio",
    "37": "Uffici-Fondi commerciali: Show room",
    "54": "Uffici-Fondi commerciali: Altro",
    "38": "Attivita': Azienda agricola",
    "39": "Attivita': Albergo/hotel",
    "40": "Attivita': Ristorazione",
    "41": "Attivita': Attività commerciale",
    "42": "Attivita': Azienda agrituristica",
    "46": "Attivita': Bar",
    "55": "Attivita': Altro",
    "49": "Terreni: Terreno agricolo",
    "50": "Terreni: Terreno edificabile",
    "51": "Terreni: Terreno industriale",
    "52": "Terreni: Annesso agricolo",
    "56": "Terreni: Altro",
}

# Campi numerici del form: chiave -> (parametro, minimo ammesso).
INTERI = {
    "prezzo_min": ("prezzoda", 0), "prezzo_max": ("prezzoa", 0),
    "mq_min": ("mqda", 0), "mq_max": ("mqa", 0),
    "locali_min": ("vanida", 0), "locali_max": ("vania", 0),  # il portale conta i vani
    "agenzia": ("agenzia", 1),  # id dell'agenzia, dai link "annunci dell'agenzia" delle schede
}

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

_SCHEDA = re.compile(r'<div class="property-list">')
_TOTALE = re.compile(r"<h3[^>]*>\s*([\d.]+)\s+annunc[io]\s+trovat[io]\s*</h3>", re.I)
_ULTIMA = re.compile(r'aria-label="Ultima"\s+href="[^"]*?[?&](?:amp;)?page=(\d+)"')
_PAGINE = re.compile(r'href="[^"]*?act=annunci[^"]*?[?&](?:amp;)?page=(\d+)"')
_DETTAGLIO = re.compile(r'href="(/dettagli/(\d+))"\s+title="([^"]*)"')
_FOTO = re.compile(r'<img src="([^"]+)"[^>]*class="annunci_img"')
_CONTRATTO = re.compile(r'<span class="rent [^"]*">\s*([^<]+?)\s*</span>')
_TIPOLOGIA = re.compile(r'<h5>\s*<a [^>]*>([^<]*)</a>\s*<small>([^<]*)</small>', re.S)
_PREZZO = re.compile(r"<h6>\s*<strong>([^<]*)</strong>\s*</h6>")
_DESCRIZIONE = re.compile(r"</div>\s*<p>(.*?)</p>", re.S)
_VANI = re.compile(r"<li>\s*(\d+)\s+Vani\s*</li>", re.I)
_MQ = re.compile(r"<li>\s*([\d.]+)\s+M<sup>2</sup>\s*</li>", re.I)
_AGENZIA = re.compile(r'href="/\?act=annunci&amp;agenzia=(\d+)"[^>]*>\s*<span>([^<]*)</span>')
_INSERITO = re.compile(r'title="Inserito il ([\d/]+)"')
_FORM = re.compile(r'name="act"\s+value="annunci"')
_NESSUNO = re.compile(r"Nessun annuncio trovato")


class ErroreStruttura(RuntimeError):
    """La pagina non e' la lista risultati attesa (struttura del sito cambiata)."""


def _testo(s: str | None) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return t or None


def _intero(s: str | None) -> int | None:
    if not s:
        return None
    cifre = re.sub(r"[^\d]", "", s)
    return int(cifre) if cifre else None


def _primo(rx: re.Pattern, testo: str, gruppo: int = 1) -> str | None:
    m = rx.search(testo)
    return m.group(gruppo) if m else None


_COMUNE_IN_LOCALITA = re.compile(r"(?:.*,\s*)?(.+?)\s*\([A-Z]{2}\)")  # "[frazione, ]comune (PR)"


def annuncio_da_scheda(blocco: str, origine: str, letto_il: str) -> Annuncio | None:
    """Converte un blocco HTML `<div class="property-list">...` in un Annuncio."""
    m = _DETTAGLIO.search(blocco)
    if not m:
        return None
    percorso, id_, titolo = m.group(1), m.group(2), _testo(m.group(3))
    tip = _TIPOLOGIA.search(blocco)
    localita = _testo(tip.group(2)) if tip else None
    comune = _COMUNE_IN_LOCALITA.fullmatch(localita or "")
    contratto = _testo(_primo(_CONTRATTO, blocco))
    agenzia = _AGENZIA.search(blocco)
    inserito = _primo(_INSERITO, blocco)
    descr = _testo(_primo(_DESCRIZIONE, blocco))

    caratteristiche: list[str] = []
    if tip and _testo(tip.group(1)):
        caratteristiche.append(_testo(tip.group(1)))
    if contratto:
        caratteristiche.append(contratto)
    if inserito:
        caratteristiche.append(f"inserito il {inserito}")

    return Annuncio(
        portale=PORTALE,
        id=id_,
        url=origine + percorso,
        titolo=titolo,
        prezzo=_intero(_primo(_PREZZO, blocco)),
        mq=_intero(_primo(_MQ, blocco)),
        locali=_primo(_VANI, blocco),  # "vani" del portale
        bagni=None,
        piano=None,
        ascensore=None,
        indirizzo=None,
        zona=localita,  # es. "Trieste (TS)" o "Malchina, Duino Aurisina-devin Nabrežina (TS)"
        comune=comune.group(1) if comune else None,
        lat=None,
        lon=None,
        descrizione=descr,  # troncata dal portale ("...")
        foto=_html.unescape(_primo(_FOTO, blocco) or "") or None,
        agenzia=_testo(agenzia.group(2)) if agenzia else None,
        condizione=None,
        caratteristiche=caratteristiche,
        ribasso_pct=None,
        letto_il=letto_il,
    )


def leggi_pagina(html: str, origine: str, letto_il: str | None = None) -> Pagina:
    if not _FORM.search(html):
        raise ErroreStruttura("la risposta non contiene il form di ricerca annunci di tuttocasa.it")
    letto_il = letto_il or adesso_utc()
    parti = _SCHEDA.split(html)[1:]
    annunci: list[Annuncio] = []
    visti: set[str] = set()
    for blocco in parti:
        a = annuncio_da_scheda(blocco, origine, letto_il)
        if a and a.id not in visti:
            visti.add(a.id)
            annunci.append(a)

    totale = _intero(_primo(_TOTALE, html))
    if totale is None and not annunci and _NESSUNO.search(html):
        return Pagina(totale=0, pagine=0, annunci=[])
    ultima = _primo(_ULTIMA, html)
    if ultima:
        pagine = int(ultima)
    else:
        numeri = [int(n) for n in _PAGINE.findall(html)]
        pagine = max(numeri) if numeri else (1 if annunci else (0 if totale == 0 else None))
    if totale is not None and pagine is None:
        pagine = -(-totale // 10) if totale else 0
    return Pagina(totale=totale, pagine=pagine, annunci=annunci)


class Client:
    ORIGINE = "https://tuttocasa.it"
    TRASPORTO = "http"
    FILTRI = frozenset(INTERI) | frozenset({"tipologia", "da_privato"})

    def __init__(self, trasporto):
        self.trasporto = trasporto

    def parametri(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> dict[str, str]:
        # key è una ricerca testuale: "duino-aurisina" non trova nulla, "duino aurisina" sì (provato il 17/11/2026).
        chiave = (comune or "").strip().replace("-", " ")
        if len(chiave) < 3:
            raise ValueError(f"comune {comune!r}: tuttocasa.it richiede almeno 3 caratteri (campo key)")
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r} non supportato: ammessi {', '.join(CONTRATTI)}")
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        ignoti = sorted(set(filtri) - self.FILTRI)
        if ignoti:
            raise ValueError(f"filtri sconosciuti per tuttocasa: {ignoti}; ammessi {sorted(self.FILTRI)}")
        f = {k: v for k, v in filtri.items() if v is not None}

        # Ordine e nomi come nei link di paginazione del portale; "no" = filtro non impostato.
        p = {"act": "annunci", "key": chiave.lower(), "contratto": CONTRATTI[contratto], "tipologia": NON_IMPOSTATO}
        if "tipologia" in f:
            if f["tipologia"] not in TIPOLOGIE:
                raise ValueError(f"tipologia {f['tipologia']!r} non ammessa: usa l'id, tra {list(TIPOLOGIE)}")
            p["tipologia"] = f["tipologia"]
        for k, (par, minimo) in INTERI.items():
            v = f.get(k)
            if v is not None and (type(v) is not int or v < minimo):
                raise ValueError(f"filtro {k}: serve un intero >= {minimo}, non {v!r}")
            p[par] = NON_IMPOSTATO if v is None else str(v)
        privati = f.get("da_privato", False)
        if type(privati) is not bool:
            raise ValueError(f"filtro da_privato: serve True o False, non {privati!r}")
        p["privati"] = "1" if privati else NON_IMPOSTATO
        if pagina > 1:
            p["page"] = str(pagina)
        return p

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        params = self.parametri(comune, contratto, pagina, **filtri)
        r = self.trasporto.get(PERCORSO_RICERCA, params=params, intestazioni=INTESTAZIONI_HTML)
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        return leggi_pagina(r.testo, self.ORIGINE)


if __name__ == "__main__":
    from ..trasporto import HttpTrasporto

    with HttpTrasporto(Client.ORIGINE) as t:
        c = Client(t)
        try:
            pag = c.cerca("trieste", "vendita", prezzo_max=300000, locali_min=2)
        except Bloccato as e:
            sys.exit(f"Bloccato: {e}")
        print(f"totale={pag.totale} pagine={pag.pagine} in pagina={len(pag.annunci)}")
        for a in pag.annunci:
            print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.agenzia, a.url)
