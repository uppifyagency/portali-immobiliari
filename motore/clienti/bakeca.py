"""Client di ricerca per bakeca.it, sezione case in vendita (pagina elenco renderizzata lato server).

bakeca.it non usa un'API JSON per la lista: la pagina
`https://<citta>.bakeca.it/annunci/vendita-case/[<filtro>/<valore>/...][page/N/]` contiene gli annunci
come card HTML `<div data-id="..." class="... annuncio-in-elenco">`, ognuna con il proprio blocco
JSON-LD (schema.org Product + SingleFamilyResidence). Il client chiede quella pagina al trasporto e legge:
- dal JSON-LD: id, url, titolo, prezzo, descrizione, foto, agenzia, lat/lon;
- dall'HTML della card: "93 mq . Trilocale" (mq e tipologia), comune, etichetta Premium;
- dal form dei filtri: il totale ("23 annunci").

I filtri sono segmenti del percorso. Ogni provincia e' un sottodominio (trieste.bakeca.it, milano.bakeca.it):
il client cerca sull'origine del trasporto che riceve, e `luogo/<comune>/` restringe a un comune di quella provincia.
Chi chiama sceglie la provincia aprendo il trasporto sull'origine giusta, es.
`ChromeTrasporto("https://milano.bakeca.it")`; ORIGINE (Trieste) e' solo il valore predefinito.

Il sito e' dietro Cloudflare, quindi TRASPORTO = "chrome". Il client non fa HTTP da solo:
usa solo `trasporto.get(...)`. Su blocco il trasporto solleva `Bloccato` e ci si ferma.
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
from urllib.parse import urlsplit

from ..trasporto import Bloccato, Risposta  # noqa: F401
from ..portali.modello import Annuncio, Pagina, adesso_utc

PORTALE = "bakeca"

# Valore speciale di `comune`: tutta la provincia del sottodominio, senza segmento luogo/.
TUTTA_AREA = "*"

# Grammatica dell'URL, vista al clic il 03/11/2026 (risultati/scoperta/bakeca/appunti.md):
# /annunci/<categoria>/<nome>/<valore>/... con i segmenti in ordine alfabetico di nome, poi sorting/<cod>/nope/true/,
# poi page/N/, poi ?keyword=. Un solo valore di checkbox va come slug (tipoimmobile/bilocale/), piu' valori come
# id numerici separati da virgola (tipoimmobile/52,54/). Gli intervalli sono <min>-<max> con 0 = senza limite.
CONTRATTI = {"vendita": "vendita-case", "affitto": "offro-casa"}
CATEGORIE = ("case", "cerco-camera", "cerco-casa", "offro-camera", "terreni-rustici", "capannoni-magazzini",
             "locali-attivita-commerciali", "box-garage", "case-vacanza", "case-vacanza-estero")
NUOVE_COSTRUZIONI = "nuove-costruzioni"
# Nome del segmento degli intervalli per categoria (select range_<nome>[] del form #filterForm).
INTERVALLI = {"vendita-case": {"prezzo": "prezzoimmobile", "mq": "mqcaseinvendita"},
              "offro-casa": {"prezzo": "affittoimmobile", "mq": "mqoffrocasa"}}
CON_CHECKBOX = set(INTERVALLI)  # categorie in cui il form ha inserzionista, tipologia e arredato
# valore del client -> (slug nel percorso, id numerico per la scelta multipla), dai value "<slug>_<id>" del form.
TIPI = {"monolocale": ("monolocale", 50), "bilocale": ("bilocale", 52), "trilocale": ("trilocale", 54),
        "quadrilocale": ("quadrilocale", 56), "cinque-o-piu-locali": ("cinque-o-piu-locali", 62),
        "attico": ("attico", 64), "mansarda": ("mansarda", 66), "villa": ("villa", 68), "altro": ("20", 70)}
ARREDATO = {"nonarredato": ("nonarredato", 26940), "parzialmentearredato": ("parzialmentearredato", 26942),
            "completamentearredato": ("completamentearredato", 26944)}
INSERZIONISTI = {"privato": ("privato", 2182), "agenzia": ("agenzia", 2183)}
ORDINAMENTI = ("SBPRICEA", "SBPRICED", "SBMQA", "SBMQD")
# "In evidenza" sta nel menu ordinamento ma toglie gli annunci fuori vetrina (prova 03/11: 23 -> 11): e' un filtro.
IN_EVIDENZA = "SBVET"
# Comuni noti per sottodominio, con il nome che usa bakeca (lws/comune/suggestion, vhost "trieste"): per questi il
# client controlla il comune senza rete e accetta anche il nome breve. Per gli altri sottodomini lo slug segue la
# stessa regola (minuscole, spazi -> "+") e il controllo e' sulla risposta: un comune di un'altra provincia rimanda a un
# altro sottodominio o alla pagina senza luogo/ (trieste.bakeca.it/.../luogo/milano/ -> milano.bakeca.it/...).
COMUNI = {"trieste.bakeca.it": ("Trieste", "Muggia", "Duino Aurisina - Devin Nabrezina",
                                "San Dorligo della Valle - Dolina", "Sgonico - Zgonik", "Monrupino - Repentabor")}

FILTRI = frozenset({"categoria_annuncio", "nuova_costruzione", "pubblicato_da", "da_privato", "tipologia",
                    "arredato", "prezzo_min", "prezzo_max", "mq_min", "mq_max", "parola_chiave", "ordinamento",
                    "solo_in_evidenza"})

# Tipologia mostrata nella card -> numero di locali.
_LOCALI = {"monolocale": "1", "bilocale": "2", "trilocale": "3", "quadrilocale": "4", "cinque e più locali": "5+"}

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# Card normale <div ...> o card con foto grande <section ...> (vista negli affitti).
_CARD = re.compile(r'<(?:div|section) data-id="([A-Za-z0-9]+)"\s+class="[^"]*\bannuncio-in-elenco\b')
# Una ricerca senza risultati risponde 404 con "Nessun risultato disponibile" e annunci suggeriti (non pertinenti).
NESSUN_RISULTATO = "Nessun risultato disponibile"
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_PREZZO_HTML = re.compile(r'<strong class="text--section[^"]*">\s*([^<]*?)\s*</strong>')
# "93 mq . Trilocale": in uno span nella card normale, testo libero dopo il comune nella card con foto grande.
_MQ_TIPO = re.compile(r'<span class="text-sm font-semibold">([^<]*)</span>|</strong></span>\s*([^<]*?\bmq\b[^<]*?)\s*<')
_ZONA = re.compile(r'<span class="text-sm text-slate-700 truncate block px-3">\s*<strong>([^<]*)</strong>')
_TOTALE = re.compile(r"FacetSearchComponent\(\{\s*insertions:\s*'([^']*)'")


class ErroreStruttura(RuntimeError):
    """La pagina non ha la struttura attesa (sito cambiato o risposta non valida)."""


def _int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    cifre = re.sub(r"[^\d]", "", str(v))
    return int(cifre) if cifre else None


def _testo(v) -> str | None:
    if v is None:
        return None
    t = re.sub(r"\s+", " ", _html.unescape(str(v))).strip()
    return t or None


def slug_comune(comune: str, host: str = "trieste.bakeca.it") -> str:
    """'Duino Aurisina' o 'Duino Aurisina - Devin Nabrezina' -> 'duino+aurisina+-+devin+nabrezina' (link luogo/)."""
    c = re.sub(r"\s+", " ", comune.strip().lower())
    if not c or not re.fullmatch(r"[\w' -]+", c):
        raise ValueError(f"comune {comune!r} non valido: un nome di comune o {TUTTA_AREA!r} per tutta la provincia")
    noti = COMUNI.get(host)
    if noti is None:
        return c.replace(" ", "+")
    for nome in noti:
        if {c, c.replace("-", " ")} & {nome.lower(), nome.lower().split(" - ")[0]}:
            return nome.lower().replace(" ", "+")
    raise ValueError(f"comune {comune!r} fuori dalla provincia di https://{host}: ammessi {', '.join(noti)} "
                     f"o {TUTTA_AREA!r} per tutta la provincia; per un'altra provincia apri il trasporto sul suo "
                     f"sottodominio (es. https://milano.bakeca.it)")


def _card(blocco: str, letto_il: str) -> Annuncio | None:
    m = _LD.search(blocco)
    if not m:
        return None
    try:
        ld = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    pid = ld.get("productID") or (ld.get("identifier") or {}).get("value")
    if not pid or not ld.get("url"):
        return None
    offerta = ld.get("offers") or {}
    venditore = offerta.get("seller") or {}
    geo = ld.get("geo") or {}

    prezzo = _int(offerta.get("price"))
    if prezzo is None:  # "Trattativa riservata" -> None; altrimenti "131.000 €"
        mp = _PREZZO_HTML.search(blocco)
        prezzo = _int(mp.group(1)) if mp and "€" in mp.group(1) else None

    mq = tipo = None
    mt = _MQ_TIPO.search(blocco)
    if mt:
        for parte in (p.strip() for p in (_testo(mt.group(1) or mt.group(2)) or "").split(" . ")):
            if re.fullmatch(r"[\d.]+ mq", parte):
                mq = _int(parte)
            elif parte:
                tipo = parte
    zona = _ZONA.search(blocco)
    caratteristiche = [tipo] if tipo else []
    if re.search(r'<span class="uppercase[^"]*">Premium</span>', blocco):
        caratteristiche.append("premium")

    return Annuncio(
        portale=PORTALE,
        id=str(pid),
        url=ld["url"],
        titolo=_testo(ld.get("name")),
        prezzo=prezzo,
        mq=mq,
        locali=_LOCALI.get((tipo or "").lower()),
        bagni=None,
        piano=None,
        ascensore=None,
        indirizzo=None,  # la lista non riporta l'indirizzo, solo il comune
        zona=_testo(zona.group(1)) if zona else _testo((ld.get("address") or {}).get("addressLocality")),
        lat=geo.get("latitude"),
        lon=geo.get("longitude"),
        descrizione=_testo(ld.get("description")),  # troncata dal portale con "…"
        foto=ld.get("image"),
        agenzia=_testo(venditore.get("name")) if venditore.get("@type") == "RealEstateAgent" else None,
        condizione=None,
        caratteristiche=caratteristiche,
        ribasso_pct=None,
        letto_il=letto_il,
    )


def leggi_pagina(testo: str, pagina: int = 1) -> Pagina:
    """Estrae totale e annunci dall'HTML della pagina elenco."""
    if 'id="filterForm"' not in testo:
        raise ErroreStruttura("la risposta non e' una pagina elenco di bakeca (manca #filterForm)")
    letto_il = adesso_utc()
    pezzi = [m.start() for m in _CARD.finditer(testo)] + [len(testo)]
    annunci, visti = [], set()
    for inizio, fine in zip(pezzi, pezzi[1:]):
        a = _card(testo[inizio:fine], letto_il)
        if a and a.id not in visti:
            visti.add(a.id)
            annunci.append(a)
    mt = _TOTALE.search(testo)
    if mt is None:
        raise ErroreStruttura("contatore risultati (FacetSearchComponent insertions) assente")
    # "23 annunci", "1 annuncio", "1.545 annunci"; senza risultati il contatore dice "Non ci sono risultati".
    totale = _int(mt.group(1)) if re.match(r"[\d.]+ annunc", mt.group(1)) else 0
    # La pagina non mostra la paginazione ne' la dimensione di pagina: il numero di pagine e' noto
    # solo quando la prima pagina contiene gia' tutti i risultati.
    pagine = 1 if pagina == 1 and totale <= len(annunci) else None
    return Pagina(totale=totale, pagine=pagine, annunci=annunci)


def _scelta(chiave: str, valore, ammessi: dict, multiplo: bool) -> str:
    """Slug per un valore, id separati da virgola per piu' valori (come li scrive il form)."""
    valori = valore if multiplo and isinstance(valore, list) else [valore]
    if not valori or len(set(valori)) != len(valori):
        raise ValueError(f"{chiave}: lista vuota o con doppioni {valore!r}")
    for v in valori:
        if v not in ammessi:
            raise ValueError(f"{chiave} {v!r} non ammesso: ammessi {', '.join(ammessi)}")
    if len(valori) == 1:
        return ammessi[valori[0]][0]
    return ",".join(str(i) for i in sorted(ammessi[v][1] for v in valori))


def _intero(chiave: str, v) -> int:
    if type(v) is not int or v < 1:
        raise ValueError(f"{chiave} deve essere un intero >= 1, non {v!r}")
    return v


class Client:
    ORIGINE = "https://trieste.bakeca.it"
    TRASPORTO = "chrome"
    FILTRI = FILTRI

    def __init__(self, trasporto):
        self.trasporto = trasporto
        self.origine = getattr(trasporto, "origine", self.ORIGINE).rstrip("/")

    def richiesta(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> tuple[str, dict | None]:
        """Percorso e query della pagina elenco, nella forma che costruisce il form del sito."""
        ignote = sorted(set(filtri) - FILTRI)
        if ignote:
            raise ValueError(f"filtri sconosciuti per bakeca: {', '.join(ignote)}; ammessi: {', '.join(sorted(FILTRI))}")
        f = {k: v for k, v in filtri.items() if v is not None}
        if contratto not in CONTRATTI:
            raise ValueError(f"contratto {contratto!r}: ammessi {', '.join(CONTRATTI)}")
        if type(pagina) is not int or pagina < 1:
            raise ValueError("pagina deve essere un intero >= 1")

        categoria = CONTRATTI[contratto]
        for chiave, cat in (("categoria_annuncio", f.get("categoria_annuncio")),
                            ("nuova_costruzione", NUOVE_COSTRUZIONI if "nuova_costruzione" in f else None)):
            if cat is None:
                continue
            if chiave == "categoria_annuncio" and cat not in CATEGORIE:
                raise ValueError(f"categoria_annuncio {cat!r}: ammesse {', '.join(CATEGORIE)}")
            if chiave == "nuova_costruzione" and f["nuova_costruzione"] is not True:
                raise ValueError("nuova_costruzione accetta solo True")
            if categoria != CONTRATTI["vendita"] or contratto != "vendita":
                raise ValueError(f"{chiave} sostituisce la categoria del contratto: non combinarlo con "
                                 f"contratto={contratto!r} ne' con un'altra categoria")
            categoria = cat

        segmenti: dict[str, str] = {}
        if "da_privato" in f:
            if f["da_privato"] is not True:
                raise ValueError("da_privato accetta solo True")
            if f.get("pubblicato_da", "privato") != "privato":
                raise ValueError("da_privato=True e pubblicato_da diverso da 'privato' si contraddicono")
            f.setdefault("pubblicato_da", "privato")
        for chiave, nome, ammessi, multiplo in (("pubblicato_da", "inserzionistacase", INSERZIONISTI, False),
                                                ("tipologia", "tipoimmobile", TIPI, True),
                                                ("arredato", "arredato", ARREDATO, True)):
            if chiave in f:
                if categoria not in CON_CHECKBOX:
                    raise ValueError(f"{chiave} non esiste nel form della categoria {categoria!r}")
                segmenti[nome] = _scelta(chiave, f[chiave], ammessi, multiplo)
        for gruppo in ("prezzo", "mq"):
            lo, hi = f.get(f"{gruppo}_min"), f.get(f"{gruppo}_max")
            if lo is None and hi is None:
                continue
            if categoria not in INTERVALLI:
                raise ValueError(f"{gruppo}_min/{gruppo}_max non esistono nel form della categoria {categoria!r}")
            lo = 0 if lo is None else _intero(f"{gruppo}_min", lo)
            hi = 0 if hi is None else _intero(f"{gruppo}_max", hi)
            if hi and lo > hi:
                raise ValueError(f"{gruppo}_min {lo} maggiore di {gruppo}_max {hi}")
            segmenti[INTERVALLI[categoria][gruppo]] = f"{lo}-{hi}"
        if comune.strip() != TUTTA_AREA:
            segmenti["luogo"] = slug_comune(comune, urlsplit(self.origine).netloc)

        coda = [f"{n}/{segmenti[n]}/" for n in sorted(segmenti)]
        if "solo_in_evidenza" in f:
            if f["solo_in_evidenza"] is not True:
                raise ValueError("solo_in_evidenza accetta solo True")
            if "ordinamento" in f:
                raise ValueError("solo_in_evidenza e ordinamento occupano lo stesso segmento sorting/: il sito ne "
                                 "applica uno solo, usane uno")
            coda.append(f"sorting/{IN_EVIDENZA}/nope/true/")
        if "ordinamento" in f:
            if f["ordinamento"] not in ORDINAMENTI:
                hint = " (solo gli annunci in vetrina: usa solo_in_evidenza=True)" if f["ordinamento"] == IN_EVIDENZA else ""
                raise ValueError(f"ordinamento {f['ordinamento']!r}: ammessi {', '.join(ORDINAMENTI)}{hint}")
            coda.append(f"sorting/{f['ordinamento']}/nope/true/")
        if pagina > 1:
            coda.append(f"page/{pagina}/")
        params = None
        if "parola_chiave" in f:
            if not isinstance(f["parola_chiave"], str) or not f["parola_chiave"].strip():
                raise ValueError(f"parola_chiave deve essere un testo non vuoto, non {f['parola_chiave']!r}")
            params = {"keyword": f["parola_chiave"].strip()}
        return f"/annunci/{categoria}/" + "".join(coda), params

    def cerca(self, comune: str, contratto: str = "vendita", pagina: int = 1, **filtri) -> Pagina:
        """`comune` = comune della provincia del sottodominio del trasporto (Trieste, Muggia, ...) o "*" per tutta la
        provincia."""
        percorso, params = self.richiesta(comune, contratto, pagina, **filtri)
        r = self.trasporto.get(percorso, params=params, intestazioni=INTESTAZIONI_HTML)
        arrivo = urlsplit(r.url)
        if arrivo.netloc != urlsplit(self.origine).netloc or ("/luogo/" in percorso and arrivo.path != percorso):
            raise ValueError(f"comune {comune!r}: {self.origine}{percorso} rimanda a {r.url}, il comune non e' nella "
                             f"provincia di {self.origine}")
        if r.status == 404 and NESSUN_RISULTATO in r.testo:
            return Pagina(totale=0, pagine=0, annunci=[])
        if r.status != 200:
            raise ErroreStruttura(f"HTTP {r.status} su {r.url}")
        return leggi_pagina(r.testo, pagina)


if __name__ == "__main__":
    from ..trasporto import ChromeTrasporto

    with ChromeTrasporto(Client.ORIGINE) as t:
        try:
            p = Client(t).cerca("Trieste")
        except Bloccato as e:
            sys.exit(f"Bloccato da Cloudflare: {e}. Risolvi la verifica nella tab di Chrome e rilancia.")
    print(f"totale={p.totale} pagine={p.pagine} annunci={len(p.annunci)}")
    for a in p.annunci[:5]:
        print(a.id, a.prezzo, a.mq, a.locali, a.zona, a.titolo, a.url)
