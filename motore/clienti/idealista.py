"""Client di ricerca per idealista.it.

Gli annunci sono nell'HTML renderizzato lato server (SSR): ogni annuncio e' un
<article class="item ..." data-element-id="ID">. Non esiste un'API JSON della lista
visibile nell'HAR, quindi il client chiede la pagina di ricerca al trasporto e la analizza.

Percorsi e filtri: vedi README.md e filtri/idealista.json. Tutti i filtri a casella e a soglia vanno nel
segmento con-<token>,<token>/ che il modulo filtri costruisce in JavaScript; i token sono quelli letti dopo un clic.

Il portale e' dietro DataDome: TRASPORTO = "chrome".
"""
from __future__ import annotations

import html
import math
import re
import unicodedata
from urllib.parse import urlsplit

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Dettaglio, Pagina, adesso_utc  # noqa: F401

SENZA_RISULTATI = "Non ci sono annunci"  # inizio dell'h1 di una ricerca a zero (HTTP 404 o 200)
ACCETTA_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
ANNUNCI_PER_PAGINA = 30  # 30 annunci per pagina in entrambe le pagine dell'HAR


class ErroreStruttura(RuntimeError):
    """La pagina ricevuta non ha la struttura attesa (nessun elenco di annunci riconoscibile)."""


def _slug(testo: str) -> str:
    t = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if not t:
        raise ValueError(f"nome non valido: {testo!r}")
    return t


def _testo(frammento: str | None) -> str | None:
    if frammento is None:
        return None
    t = html.unescape(re.sub(r"<[^>]+>", " ", frammento))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _intero(testo: str | None) -> int | None:
    if not testo:
        return None
    cifre = re.sub(r"[^\d]", "", testo)
    return int(cifre) if cifre else None


def _primo(modello: str, testo: str, flags: int = re.S) -> str | None:
    m = re.search(modello, testo, flags)
    return m.group(1) if m else None


def _blocchi_articoli(pagina: str) -> list[tuple[str, str]]:
    """Restituisce (id, html) per ogni <article ... data-element-id="..."> (salta pubblicita' e moduli)."""
    blocchi = []
    for m in re.finditer(r'<article\b[^>]*\bdata-element-id="(\d+)"[^>]*>', pagina):
        fine = pagina.find("</article>", m.end())
        blocchi.append((m.group(1), pagina[m.start(): fine if fine != -1 else len(pagina)]))
    return blocchi


def _dividi_titolo(titolo: str | None) -> tuple[str | None, str | None]:
    """'Bilocale in Strada del Pilone, 2, Cologna - Università, Trieste' -> (indirizzo, zona)."""
    if not titolo or " in " not in titolo:
        return None, None
    luogo = titolo.split(" in ", 1)[1]
    parti = [p.strip() for p in luogo.split(",") if p.strip()]
    if len(parti) >= 3:
        return ", ".join(parti[:-2]), parti[-2]
    if len(parti) == 2:
        return None, parti[0]
    return None, None


def _analizza_articolo(id_: str, a: str, origine: str, letto_il: str) -> Annuncio:
    link = re.search(r'<a\s+href="([^"]+)"[^>]*class="item-link[^"]*"[^>]*title="([^"]*)"', a)
    if link:
        href, titolo = link.group(1), html.unescape(link.group(2)).strip() or None
    else:
        href = _primo(r'href="(/immobile/\d+/)"', a)
        titolo = _testo(_primo(r'class="item-link[^"]*"[^>]*>(.*?)</a>', a))
    url = origine + (href or f"/immobile/{id_}/")

    prezzo = _intero(_primo(r'<span class="item-price[^"]*">(.*?)</span>', a))

    mq = locali = piano = None
    ascensore = None
    for d in (_testo(x) for x in re.findall(r'<span class="item-detail">(.*?)</span>', a, re.S)):
        if not d:
            continue
        if re.search(r"\bm²|\bm2\b", d):
            mq = _intero(d)
        elif re.search(r"\blocal[ei]\b", d):
            locali = _primo(r"(\d+\+?)", d, 0)
        elif "ascensore" in d or "piano" in d.lower():
            if "con ascensore" in d:
                ascensore = True
            elif "senza ascensore" in d:
                ascensore = False
            p = re.sub(r"\s*(con|senza) ascensore\s*", "", d).strip()
            piano = p or None

    descrizione = _testo(_primo(r'<p class="ellipsis">(.*?)</p>', a))
    frase = _testo(_primo(r'<p class="highlight-phrase"[^>]*>(.*?)</p>', a))
    if frase and descrizione:
        descrizione = f"{frase}. {descrizione}"
    elif frase:
        descrizione = frase

    foto = _primo(r'<img[^>]+src="(https://img\d*\.idealista\.it/[^"]+)"', a)
    agenzia = _primo(r'<picture class="logo-branding">\s*<a[^>]*title="([^"]+)"', a)
    agenzia = html.unescape(agenzia) if agenzia else None

    caratteristiche = [t for t in (_testo(x) for x in
                       re.findall(r'<span class="listing-tags[^"]*">(.*?)</span>', a, re.S)) if t]
    if "item-info-container--auction" in a and not any(c.lower().startswith("asta") for c in caratteristiche):
        caratteristiche.append("Asta")

    ribasso = _primo(r'class="pricedown_icon[^"]*">\s*([\d.,]+)\s*%', a)
    ribasso_pct = float(ribasso.replace(",", ".")) if ribasso else None

    condizione = next((c for c in caratteristiche if re.search(
        r"ristrutturat|nuov|da ristrutturare|buono stato|ottimo stato", c, re.I)), None)

    indirizzo, zona = _dividi_titolo(titolo)

    return Annuncio(
        portale="idealista", id=id_, url=url, titolo=titolo, prezzo=prezzo, mq=mq, locali=locali,
        bagni=None, piano=piano, ascensore=ascensore, indirizzo=indirizzo, zona=zona, lat=None, lon=None,
        descrizione=descrizione, foto=foto, agenzia=agenzia, condizione=condizione,
        caratteristiche=caratteristiche, ribasso_pct=ribasso_pct, letto_il=letto_il,
    )


def analizza_pagina(testo: str, origine: str = "https://www.idealista.it") -> Pagina:
    """Analizza l'HTML di una pagina di risultati idealista."""
    letto_il = adesso_utc()
    blocchi = _blocchi_articoli(testo)

    h1 = _testo(_primo(r'<span id="h1-container__text">(.*?)</span>', testo))
    if (h1 or "").startswith(SENZA_RISULTATI):  # gli article di questa pagina sono suggerimenti, non risultati
        return Pagina(totale=0, pagine=0, annunci=[])
    totale = _intero(_primo(r"^([\d.]+)", h1 or "", 0))
    if totale is None:
        meta = _primo(r'<meta name="description" content="([\d.]+)\s', testo)
        totale = _intero(meta)

    if not blocchi and totale not in (0, None):
        raise ErroreStruttura("nessun article[data-element-id] nella pagina di risultati")
    if not blocchi and totale is None and 'id="h1-container' not in testo:
        raise ErroreStruttura("la pagina non sembra una lista di risultati idealista")

    numeri = [int(n) for n in re.findall(r'/lista-(\d+)\.htm"', testo)]
    selezionata = _intero(_primo(r'<li class="selected"><span>(\d+)</span>', testo))
    if totale is not None:
        pagine = max(1, math.ceil(totale / ANNUNCI_PER_PAGINA)) if totale else 0
    elif numeri or selezionata:
        pagine = max(numeri + [selezionata or 1])
    else:
        pagine = 1 if blocchi else None

    annunci = [_analizza_articolo(i, a, origine, letto_il) for i, a in blocchi]
    return Pagina(totale=totale, pagine=pagine, annunci=annunci)



# Filtri del segmento con-<token1>,<token2>/: ogni token è quello che l'URL di idealista mostra dopo il clic sulla
# casella o sulla voce del menu (risultati/scoperta/idealista/piano*.esiti.json, 03/11/2026).
CASELLE = {
    "ascensore": "ascensori", "vista_mare": "vista-mare", "aria_condizionata": "ariacondizionata",
    "cantina": "ripostiglio", "nuova_costruzione": "nuova-costruzione", "nuda_proprieta": "nuda-proprieta",
    "libero": "senza-inquilini", "a_reddito": "inquilino", "piscina": "piscina", "lusso": "lusso",
    "con_virtual_tour": "visita-virtuale", "casa_accessibile": "case-accessibili", "armadi_a_muro": "armadi-muro",
    "con_planimetria": "planimetria", "balcone": "balcone", "terrazzo": "terrazza",
    "balcone_o_terrazzo": "terrazza-e-balcone", "animali_ammessi": "animali", "affitto_lungo_termine": "affitto-lungo-termine",
    "affitto_transitorio": "affitto-transitorio",
}
# Scelta multipla: il valore passato è il token stesso.
MULTIPLI = {
    "tipo_casa": ("appartamenti", "solo-appartamenti", "attici", "appartamenti-due-livelli", "ville",
                  "case-indipendenti", "villette-bifamiliari", "villette-a-schiera", "ville-indipendenti", "rustici",
                  "mansarde", "loft-open-space"),
    "stato_immobile": ("nuova-costruzione", "buono-stato", "ristrutturare"),
    "piano": ("ultimo-piano", "piani-intermedi", "piano-terra"),
    "classe_energetica": ("alta-efficienza", "media-efficienza", "bassa-efficienza"),
    "box_auto": ("garage", "box-singolo", "box-doppio"),
    "posto_auto": ("posto-auto-coperto", "posto-auto-scoperto"),
    "giardino": ("giardino", "giardino-privato", "giardino-comune"),
}
# Scelta singola: valore passato -> token.
SCELTE = {
    "asta": {"escludi": "aste_no", "solo": "aste_giudiziarie"},
    "pubblicato_entro": {"48-ore": "pubblicato_ultime-48-ore", "settimana": "pubblicato_ultima-settimana",
                         "mese": "pubblicato_ultimo-mese"},
    "arredato": {"ammobiliato": "arredamento_ammobiliato", "solo-cucina": "arredamento_solo-cucina-arredata"},
}
# Soglie: token con il numero in coda (il menu offre soglie fisse più il campo "Altro" libero).
SOGLIE = {"prezzo_max": "prezzo_", "prezzo_min": "prezzo-min_", "mq_min": "dimensione_", "mq_max": "dimensione-max_"}
LOCALI = ("monolocali-1", "bilocali-2", "trilocali-3", "quadrilocali-4", "5-locali-o-piu")
BAGNI = ("bagno-1", "bagno-2", "bagno-3")
ORDINAMENTI = ("prezzi-asc", "prezzi-desc", "pubblicazione-desc", "pubblicazione-asc", "da-privati-asc",
               "da-privati-desc", "queda-desc", "prezzoMedio-asc", "prezzoMedio-desc", "area-desc", "area-asc",
               "piano-desc", "piano-asc")
# Radice del percorso per tipologia; senza tipologia è <contratto>-case.
TIPOLOGIE = {"nuove_costruzioni": "{c}-nuove_costruzioni", "uffici": "{c}-uffici", "negozi": "{c}-negozi",
             "attivita": "cessione-attivita", "garage": "{c}-garage", "terreni": "{c}-terreni",
             "cantine": "{c}-cantine", "edifici": "{c}-edifici"}
ZONE = {"trieste": ("altipiano-est-opicina", "altipiano-ovest-barcola", "barriera-vecchia-san-giacomo",
                    "centro-san-vito", "san-giovanni-rozzol", "scorcola-roiano", "servola-borgo-san-sergio",
                    "triestino-nord", "triestino-sud")}
SOLO_CONTRATTO = {"asta": "vendita", "nuda_proprieta": "vendita", "a_reddito": "vendita", "libero": "vendita",
                  "arredato": "affitto", "animali_ammessi": "affitto", "affitto_lungo_termine": "affitto",
                  "affitto_transitorio": "affitto"}
SOLO_VENDITA_TIPO_CASA = {"mansarde", "loft-open-space"}

# Filtri dello stesso gruppo di token, misurati dal vivo il 03/11 (risultati/scoperta/idealista/misure.tsv insieme,
# risultati/prova_mappa/idealista.tsv da soli, Trieste vendita, base 718). In O il client rifiuta le chiavi insieme.
COMBINAZIONI = [
    {"chiavi": ["nuda_proprieta", "a_reddito", "libero"], "gruppo": "situazione", "esito": "o",
     "misura": "nuda_proprieta 24, a_reddito 25, insieme 49: sopra il maggiore, il portale unisce in O"},
    {"chiavi": ["box_auto", "posto_auto"], "gruppo": "parcheggio", "esito": "o",
     "misura": "box_auto garage 208, posto_auto posto-auto-coperto 138, insieme 208: in E non potrebbe superare 138; "
               "nella scoperta box-singolo 38 diventa 56 aggiungendo posto-auto-coperto"},
    {"chiavi": ["nuova_costruzione", "stato_immobile"], "gruppo": "stato", "esito": "o",
     "misura": "nuova_costruzione 91, stato_immobile ristrutturare 56, insieme 147: sopra il maggiore, il portale "
               "unisce in O"},
    {"chiavi": ["balcone", "terrazzo"], "gruppo": "esterni", "esito": "e",
     "misura": "balcone 215, terrazzo 258, insieme 92: sotto il minore, il portale interseca (E); balcone_o_terrazzo "
               "381 = 215 + 258 - 92, cioè l'unione"},
    {"chiavi": ["ascensore", "aria_condizionata"], "gruppo": "dotazioni", "esito": "e",
     "misura": "ascensore 431, aria_condizionata 357, insieme 250: sotto il minore, il portale interseca (E)"},
    {"chiavi": ["con_virtual_tour", "con_planimetria"], "gruppo": "multimedia", "esito": "e",
     "misura": "con_virtual_tour 54, con_planimetria 573, insieme 50: sotto il minore, il portale interseca (E)"},
]

# Ordine dei token come lo scrive il modulo di idealista negli URL osservati; un token fuori elenco va in coda.
ORDINE = ("prezzo_", "prezzo-min_", "dimensione_", "dimensione-max_", "appartamenti", "solo-appartamenti", "ville",
          "case-indipendenti", "villette-bifamiliari", "villette-a-schiera", "ville-indipendenti", "rustici",
          "appartamenti-due-livelli", "attici", "loft-open-space", "mansarde", *LOCALI, *BAGNI,
          "nuova-costruzione", "buono-stato", "ristrutturare", "garage", "box-singolo", "box-doppio",
          "posto-auto-coperto", "posto-auto-scoperto", "terrazza-e-balcone", "balcone", "terrazza", "giardino",
          "giardino-privato", "giardino-comune", "piscina", "lusso", "visita-virtuale", "case-accessibili",
          "armadi-muro", "planimetria")

FILTRI_SPECIALI = {"tipologia", "zona", "ordinamento", "mappa_area", "locali_min", "locali_max", "bagni_min"}


def _posto(token: str) -> tuple[int, str]:
    for i, t in enumerate(ORDINE):
        if token == t or (t.endswith("_") and token.startswith(t)):
            return i, token
    return len(ORDINE), token


def _intero_positivo(chiave: str, v) -> int:
    if type(v) is not int or v <= 0:
        raise ValueError(f"{chiave} deve essere un intero positivo, trovato {v!r}")
    return v


def _polilinea(punti: list[tuple[float, float]]) -> str:
    """Codifica Google Encoded Polyline (precisione 1e-5), quella dell'area disegnata di idealista."""
    out, plat, plon = [], 0, 0
    for lat, lon in punti:
        for v, prec in ((round(lat * 1e5), plat), (round(lon * 1e5), plon)):
            d = v - prec
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(lat * 1e5), round(lon * 1e5)
    return "".join(out)


def _area(testo) -> str:
    """'lat,lon;lat,lon;lat,lon' -> parametro shape '((<polilinea>))'."""
    if not isinstance(testo, str):
        raise ValueError(f"mappa_area deve essere una stringa 'lat,lon;lat,lon;...', trovato {testo!r}")
    try:
        punti = [tuple(float(x) for x in p.split(",")) for p in testo.split(";") if p.strip()]
    except ValueError:
        raise ValueError(f"mappa_area non leggibile: {testo!r}") from None
    if len(punti) < 3 or any(len(p) != 2 for p in punti):
        raise ValueError(f"mappa_area vuole almeno 3 punti 'lat,lon', trovato {testo!r}")
    if punti[0] != punti[-1]:
        punti.append(punti[0])
    return f"(({_polilinea(punti)}))"


class Client:
    ORIGINE = "https://www.idealista.it"
    TRASPORTO = "chrome"  # DataDome
    FILTRI = frozenset({*CASELLE, *MULTIPLI, *SCELTE, *SOGLIE, *FILTRI_SPECIALI})
    CHIEDE_PROVINCIA = True

    def __init__(self, trasporto, provincia: str | None = None):
        """`provincia`: provincia del comune (slug o nome), da Richiesta.provincia. Se assente si usa il comune
        stesso, giusto per i capoluoghi (es. trieste -> trieste-trieste)."""
        self.t = trasporto
        self.provincia = provincia

    def richiesta(self, comune: str, contratto: str = "vendita", pagina: int = 1,
                  **filtri) -> tuple[str, dict | None]:
        """-> (percorso, params) della ricerca, nella forma dell'URL che costruisce l'interfaccia."""
        if contratto not in ("vendita", "affitto"):
            raise ValueError(f"contratto {contratto!r} non ammesso: vendita | affitto")
        if type(pagina) is not int or pagina < 1:
            raise ValueError(f"pagina deve essere un intero >= 1, trovato {pagina!r}")
        sconosciute = set(filtri) - self.FILTRI
        if sconosciute:
            raise ValueError(f"filtri sconosciuti per idealista: {sorted(sconosciute)}")
        for k in filtri:
            if SOLO_CONTRATTO.get(k, contratto) != contratto:
                raise ValueError(f"il filtro {k} esiste solo per il contratto {SOLO_CONTRATTO[k]}")
        for g in COMBINAZIONI:
            insieme = [k for k in g["chiavi"] if k in filtri]
            if g["esito"] == "o" and len(insieme) > 1:
                raise ValueError(f"filtri {' e '.join(insieme)} insieme: idealista li unisce in O (annunci con l'uno "
                                 f"o l'altro), non in E ({g['misura']}). Chiedine uno per ricerca")

        tokens: set[str] = set()
        for k, v in filtri.items():
            if k in CASELLE:
                if v is not True:
                    raise ValueError(f"{k} è una casella: passa True, trovato {v!r}")
                tokens.add(CASELLE[k])
            elif k in MULTIPLI:
                scelti = [v] if isinstance(v, str) else list(v) if isinstance(v, (list, tuple)) else None
                if not scelti or any(x not in MULTIPLI[k] for x in scelti):
                    raise ValueError(f"{k}: valore {v!r} non ammesso, scegli tra {MULTIPLI[k]}")
                if k == "tipo_casa" and contratto == "affitto" and SOLO_VENDITA_TIPO_CASA & set(scelti):
                    raise ValueError(f"tipo_casa {sorted(SOLO_VENDITA_TIPO_CASA & set(scelti))} esiste solo in vendita")
                tokens.update(scelti)
            elif k in SCELTE:
                if v not in SCELTE[k]:
                    raise ValueError(f"{k}: valore {v!r} non ammesso, scegli tra {sorted(SCELTE[k])}")
                tokens.add(SCELTE[k][v])
            elif k in SOGLIE:
                tokens.add(f"{SOGLIE[k]}{_intero_positivo(k, v)}")
        if "locali_min" in filtri or "locali_max" in filtri:
            lo = filtri.get("locali_min", 1)
            hi = filtri.get("locali_max", len(LOCALI))
            for k in ("locali_min", "locali_max"):
                if k in filtri and (type(filtri[k]) is not int or not 1 <= filtri[k] <= len(LOCALI)):
                    raise ValueError(f"{k} deve essere un intero tra 1 e {len(LOCALI)} (5 = 5 o più), trovato {filtri[k]!r}")
            if lo > hi:
                raise ValueError(f"locali_min {lo} maggiore di locali_max {hi}")
            tokens.update(LOCALI[lo - 1:hi])
        if "bagni_min" in filtri:
            b = filtri["bagni_min"]
            if type(b) is not int or not 1 <= b <= len(BAGNI):
                raise ValueError(f"bagni_min deve essere un intero tra 1 e {len(BAGNI)} (3 = 3 o più), trovato {b!r}")
            tokens.update(BAGNI[b - 1:])

        tipologia = filtri.get("tipologia")
        if tipologia is None:
            radice = f"{contratto}-case"
        elif tipologia in TIPOLOGIE:
            if tipologia in ("attivita", "nuove_costruzioni") and contratto != "vendita":
                raise ValueError(f"tipologia {tipologia!r} esiste solo in vendita")
            radice = TIPOLOGIE[tipologia].format(c=contratto)
        else:
            raise ValueError(f"tipologia {tipologia!r} non ammessa, scegli tra {sorted(TIPOLOGIE)} (omessa = case)")

        params = None
        c = _slug(comune)
        if "mappa_area" in filtri:
            if "zona" in filtri:
                raise ValueError("mappa_area e zona si escludono: l'area disegnata sostituisce il luogo")
            percorso = f"/aree/{radice}/"
            params = {"shape": _area(filtri["mappa_area"])}
        elif "zona" in filtri:
            zona = filtri["zona"]
            if zona not in ZONE.get(c, ()):
                raise ValueError(f"zona {zona!r} non ammessa per {comune!r}: {ZONE.get(c, ())}")
            percorso = f"/{radice}/{c}/{zona}/"
        else:
            p = _slug(self.provincia) if self.provincia else c
            percorso = f"/{radice}/{c}-{p}/"
        if tokens:
            percorso += "con-" + ",".join(sorted(tokens, key=_posto)) + "/"
        if pagina > 1:
            percorso += f"lista-{pagina}.htm"
        if "ordinamento" in filtri:
            o = filtri["ordinamento"]
            if o not in ORDINAMENTI:
                raise ValueError(f"ordinamento {o!r} non ammesso, scegli tra {ORDINAMENTI} (omesso = rilevanza)")
            params = {**(params or {}), "ordine": o}
        return percorso, params

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        percorso, params = self.richiesta(comune, contratto, pagina, **filtri)
        r = self.t.get(percorso, params=params, intestazioni=ACCETTA_HTML)
        if urlsplit(r.url).path != percorso:
            raise ValueError(f"idealista ha reindirizzato {percorso} a {r.url}: non è la ricerca chiesta, controlla "
                             f"comune e provincia")
        if r.status == 404:  # idealista risponde 404 anche a una ricerca valida senza annunci
            zero = analizza_pagina(r.testo, self.ORIGINE) if SENZA_RISULTATI in r.testo else None
            if zero is None or zero.totale != 0:
                raise ValueError(f"ricerca inesistente su idealista (404): {percorso}; per un comune non capoluogo "
                                 f"passa la sua provincia (provincia di cerca)")
            return zero
        if r.status != 200:
            raise ErroreStruttura(f"risposta {r.status} su {r.url}")
        return analizza_pagina(r.testo, self.ORIGINE)


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        client = Client(t)
        try:
            p = client.cerca("trieste", prezzo_max=300000)
        except Bloccato as e:
            print(f"bloccato dall'antibot, risolvi a mano nella tab di Chrome: {e}")
            raise SystemExit(2)
        print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
        for a in p.annunci[:5]:
            print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.url)
