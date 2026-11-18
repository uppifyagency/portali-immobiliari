"""Legge la pagina di dettaglio degli annunci scelti e la aggiunge al file scritto da trova.

Si usa dopo che Claude ha fatto la prima selezione, solo sugli annunci scelti.
Da riga di comando si lancia con il dettagli.py alla radice del repo; il server MCP del plugin chiama dettagli().
"""

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType
from typing import Callable

from .clienti import PORTALI
from .dettaglio import lettore_di
from .portali.browser import ErroreBrowser
from .portali.modello import Annuncio, ErrorePortale, ErroreRichiesta, Esito, Risultato, adesso_utc
from .trasporto import Bloccato, apri, esito_del_blocco, pausa
from .trova import CODICI_USCITA, carica_json, scrivi, unisci

INTESTAZIONI_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def leggi_fixture(percorso: Path, lettore: ModuleType) -> dict:
    """Una pagina salvata: un .json è già estratto, qualsiasi altro file è l'HTML grezzo del portale."""
    if percorso.suffix == ".json":
        return carica_json(percorso)
    return lettore.dati_da_html(percorso.read_text(encoding="utf-8"))


def leggi_dettagli(da_leggere: list[Annuncio], lettore: ModuleType, tipo_trasporto: str,
                   leggi_pagina: Callable[[Annuncio], dict], pausa: Callable[[], None]) -> Risultato:
    """Una pagina che non è più quella dell'annuncio (rimosso: 404, struttura assente, altro id) si salta e va in
    non_letti. Un blocco o un guasto del browser fermano tutto: i dettagli già letti restano nel Risultato."""
    letti, non_letti = [], {}

    def fermo(stato: Esito, messaggio: str) -> Risultato:
        return Risultato(stato=stato, annunci=letti, pagine_lette=len(letti), messaggio=messaggio, non_letti=non_letti)

    for numero, annuncio in enumerate(da_leggere):
        if numero:
            pausa()
        try:
            dettaglio = lettore.estrai_dettaglio(leggi_pagina(annuncio), annuncio.id)
        except Bloccato as blocco:
            return fermo(*esito_del_blocco(annuncio.portale, tipo_trasporto, f"sull'annuncio {annuncio.id}", blocco))
        except ErroreBrowser as errore:
            return fermo(Esito.ERRORE, f"annuncio {annuncio.id}: {errore}")
        except ErrorePortale as errore:
            non_letti[annuncio.id] = str(errore)
            continue
        except Exception as errore:  # noqa: BLE001  il guasto di una pagina non deve perdere i dettagli già letti
            return fermo(Esito.ERRORE, f"annuncio {annuncio.id} ({annuncio.url}): {type(errore).__name__}: {errore}")
        letti.append(replace(annuncio, dettaglio=dettaglio))
    return fermo(Esito.FATTO, "")


def leggi_dal_portale(da_leggere: list[Annuncio], nome_portale: str, lettore: ModuleType) -> Risultato:
    """Apre il trasporto del client del portale come fa trova, con la stessa pausa tra una pagina e l'altra."""
    client = PORTALI[nome_portale]

    def leggi_pagina(annuncio: Annuncio) -> dict:
        risposta = trasporto.get(annuncio.url, intestazioni=INTESTAZIONI_HTML)
        if risposta.status != 200:
            raise ErrorePortale(f"HTTP {risposta.status} su {risposta.url}")
        return lettore.dati_da_html(risposta.testo)

    try:
        with apri(client, getattr(lettore, "ORIGINE", None)) as trasporto:
            return leggi_dettagli(da_leggere, lettore, client.TRASPORTO, leggi_pagina,
                                  pausa=lambda: pausa(client.TRASPORTO))
    except ErrorePortale as errore:
        return Risultato(stato=Esito.ERRORE, annunci=[], pagine_lette=0, messaggio=str(errore))


def leggi_output(percorso: Path) -> tuple[str, dict[str, Annuncio]]:
    contenuto = carica_json(percorso)
    try:
        annunci = [Annuncio.da_json(a) for a in contenuto["annunci"]]
        return contenuto["richiesta"]["portale"], {a.id: a for a in annunci}
    except (KeyError, TypeError) as errore:
        raise ErroreRichiesta(f"{percorso} non è un file di output di trova.py: {errore!r}") from errore


def fixture_per_id(voci: list[str]) -> dict[str, Path]:
    per_id = {}
    for voce in voci:
        id_, uguale, percorso = voce.partition("=")
        if not (uguale and id_ and percorso):
            raise ErroreRichiesta(f"--da-fixture {voce!r}: atteso ID=PATH")
        per_id[id_] = Path(percorso)
    return per_id


def dettagli(percorso: Path, ids: list[str], fixture: dict[str, Path] | None = None) -> tuple[Risultato, list[str]]:
    """Legge il dettaglio degli annunci ids che non lo hanno ancora e lo salva nello stesso file.

    Restituisce il risultato e gli id saltati perché già letti. Un input sbagliato solleva ErroreRichiesta
    prima di aprire il portale; blocchi ed errori del portale tornano nello stato del Risultato.
    """
    fixture = fixture or {}
    nome_portale, annunci = leggi_output(percorso)
    ids = list(dict.fromkeys(i.strip() for i in ids if i.strip()))
    if not ids:
        raise ErroreRichiesta("--id vuoto: indica almeno un annuncio")
    sconosciuti = [i for i in ids if i not in annunci]
    if sconosciuti:
        raise ErroreRichiesta(f"annunci non presenti in {percorso}: {', '.join(sconosciuti)}")
    lettore = lettore_di(nome_portale)
    gia_letti = [i for i in ids if annunci[i].dettaglio is not None]
    da_leggere = [annunci[i] for i in ids if annunci[i].dettaglio is None]
    senza_fixture = [a.id for a in da_leggere if fixture and a.id not in fixture]
    if senza_fixture:
        raise ErroreRichiesta(f"manca --da-fixture per: {', '.join(senza_fixture)}")
    pagine = {id_: leggi_fixture(file, lettore) for id_, file in fixture.items()}

    if not da_leggere:
        return Risultato(stato=Esito.FATTO, annunci=[], pagine_lette=0), gia_letti
    if pagine:
        risultato = leggi_dettagli(da_leggere, lettore, PORTALI[nome_portale].TRASPORTO,
                                   leggi_pagina=lambda annuncio: pagine[annuncio.id], pausa=lambda: None)
    else:
        risultato = leggi_dal_portale(da_leggere, nome_portale, lettore)

    if risultato.annunci:
        contenuto = carica_json(percorso)
        contenuto["annunci"] = unisci(contenuto["annunci"], [asdict(a) for a in risultato.annunci])
        contenuto["aggiornato_il"] = adesso_utc()
        scrivi(percorso, contenuto)
    return risultato, gia_letti


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legge il dettaglio degli annunci scelti e lo salva nel file di output.")
    parser.add_argument("output", type=Path, help="il file output/<id>.json scritto da trova.py")
    parser.add_argument("--id", required=True, help="id degli annunci da leggere, separati da virgola")
    parser.add_argument("--da-fixture", action="append", default=[], metavar="ID=PATH",
                        help="legge il dettaglio da file (JSON già estratto o HTML salvato) invece che dal portale")
    args = parser.parse_args(argv)

    try:
        risultato, gia_letti = dettagli(args.output, args.id.split(","), fixture_per_id(args.da_fixture))
    except (OSError, json.JSONDecodeError, ErroreRichiesta, ErrorePortale) as errore:
        print(f"errore: {errore}", file=sys.stderr)
        return CODICI_USCITA[Esito.ERRORE]

    if gia_letti:
        print(f"già letti, salto: {', '.join(gia_letti)}")
    chiesti = len({i.strip() for i in args.id.split(",") if i.strip()}) - len(gia_letti)
    if not chiesti:
        print(f"fatto: niente da leggere in {args.output}")
        return CODICI_USCITA[Esito.FATTO]
    salvati = f", salvati in {args.output}" if risultato.annunci else ""
    print(f"{risultato.stato.value}: {risultato.pagine_lette} dettagli letti su {chiesti}{salvati}. "
          f"{risultato.messaggio}".rstrip())
    for id_, motivo in risultato.non_letti.items():
        print(f"non letto {id_}: {motivo}")
    if risultato.stato is Esito.CAPTCHA:
        print(f"CAPTCHA: risolvilo a mano nella tab di Chrome rimasta aperta, poi rilancia "
              f"lo stesso comando. I dettagli già letti restano salvati e non verranno riletti.", file=sys.stderr)
    return CODICI_USCITA[risultato.stato]
