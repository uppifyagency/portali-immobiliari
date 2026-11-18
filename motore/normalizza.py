"""Dà la stessa forma allo stesso fatto su tutti i portali, così scrematura e unione li confrontano.

Le forme vengono dal motore del 28/10/2026 (fixtures/output-*-2026-10-28.json): piano "4", "T", "R",
"T, 4", "4 - 5"; vista come lista di nomi minuscoli ("mare", "panoramica").
"""

import re
import unicodedata
from dataclasses import replace

from .portali.modello import Annuncio

ORDINALI = {"primo": "1", "secondo": "2", "terzo": "3", "quarto": "4", "quinto": "5",
            "sesto": "6", "settimo": "7", "ottavo": "8", "nono": "9", "decimo": "10"}
PIANI_NOMINATI = {"terra": "T", "piano terra": "T", "0": "T", "rialzato": "R", "piano rialzato": "R",
                  "seminterrato": "S", "interrato": "S"}
FORMA_BREVE = re.compile(r"^(\d+|T|R|S\d*)$")
NUMERO = re.compile(r"^(\d+)\s*[°º]?(?:\s+piano)?$")
ORDINALE = re.compile(r"^(\w+)\s+piano$")
INTERVALLO = re.compile(r"^(?:\d+ piani:\s*)?da\s+(.+?)\s+a\s+(.+)$")
ASCENSORE_NEL_PIANO = re.compile(r",?\s*(con|senza) ascensore$", re.IGNORECASE)
ASCENSORE_IN_CARATTERISTICHE = {"ascensore": True, "no ascensore": False, "senza ascensore": False}
VISTA = re.compile(r"^vista\s+(.+)$", re.IGNORECASE)
AREE = ("provincia di ", "regione ")
SIGLA_PROVINCIA = re.compile(r"\s*\([A-Z]{2}\)\s*$")


def _piano_singolo(testo: str) -> str | None:
    """Un piano solo in forma breve, o None se non si legge."""
    minuscolo = testo.lower()
    if minuscolo in PIANI_NOMINATI:
        return PIANI_NOMINATI[minuscolo]
    if FORMA_BREVE.match(testo):
        return testo
    if trovato := NUMERO.match(minuscolo):
        return trovato.group(1)
    if (trovato := ORDINALE.match(minuscolo)) and trovato.group(1) in ORDINALI:
        return ORDINALI[trovato.group(1)]
    return None


def piano_breve(testo: str) -> str:
    """"3°, con ascensore" -> "3", "Piano terra" -> "T", "2 piani: da 2° a 3°" -> "2 - 3".
    Un testo che non si legge torna intero."""
    resto = ASCENSORE_NEL_PIANO.sub("", testo.strip()).strip()
    if trovato := INTERVALLO.match(resto.lower()):
        parti, separatore = [trovato.group(1), trovato.group(2)], " - "
    elif " - " in resto:
        parti, separatore = resto.split(" - "), " - "
    else:
        parti, separatore = resto.split(", "), ", "
    brevi = [_piano_singolo(p.strip()) for p in parti]
    return separatore.join(brevi) if all(brevi) else testo


def ascensore_da(piano: str | None, caratteristiche: list[str]) -> bool | None:
    if piano and (trovato := ASCENSORE_NEL_PIANO.search(piano.strip())):
        return trovato.group(1).lower() == "con"
    voci = [ASCENSORE_IN_CARATTERISTICHE.get(c.strip().lower()) for c in caratteristiche]
    return next((v for v in voci if v is not None), None)


def vista_da(caratteristiche: list[str]) -> list[str]:
    """"Vista mare" (idealista) -> "mare", "VISTA PANORAMICA" (casait) -> "panoramica"."""
    viste = (trovato.group(1).strip().lower() for c in caratteristiche if (trovato := VISTA.match(c.strip())))
    return list(dict.fromkeys(viste))


def _slug(nome: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()).strip("-")


def stesso_comune(dichiarato: str, chiesto: str) -> bool:
    """Il nome bilingue vale anche per la sua prima parte: "Duino Aurisina-devin Nabrežina" è "Duino-Aurisina"."""
    a, b = _slug(dichiarato), _slug(chiesto)
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def nel_comune(annuncio: Annuncio, chiesto: str) -> bool:
    """Se l'annuncio sta nel comune chiesto. Alcuni portali cercano il nome come testo (tuttocasa: Torino dà anche
    Moncalieri); la loro località è "[frazione, ]comune (PR)", e chi cerca la frazione ("Sistiana") la vuole.
    Un annuncio che non dichiara il comune, o una richiesta per area ("Provincia di ...", "Regione ..."), resta."""
    if annuncio.comune is None or chiesto.strip().lower().startswith(AREE):
        return True
    if stesso_comune(annuncio.comune, chiesto):
        return True
    localita = SIGLA_PROVINCIA.sub("", annuncio.zona or "")
    return _slug(chiesto) in {_slug(parte) for parte in localita.split(",")}


def normalizza(annuncio: Annuncio) -> Annuncio:
    """Riempie o accorcia piano, ascensore e vista. Un ascensore o una vista già dati dal client restano."""
    ascensore = annuncio.ascensore
    if ascensore is None:
        ascensore = ascensore_da(annuncio.piano, annuncio.caratteristiche)
    return replace(
        annuncio,
        piano=piano_breve(annuncio.piano) if annuncio.piano else annuncio.piano,
        ascensore=ascensore,
        vista=annuncio.vista or vista_da(annuncio.caratteristiche),
    )
