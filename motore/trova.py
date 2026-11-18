"""Cerca gli annunci di una Richiesta con il client del suo portale e li salva in <cartella>/<id>.json.

Da riga di comando si lancia con il trova.py alla radice del repo; il server MCP del plugin chiama trova().
"""

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .clienti import PORTALI
from .normalizza import nel_comune, normalizza
from .portali.modello import ErrorePortale, ErroreRichiesta, Esito, Richiesta, Risultato, adesso_utc
from .trasporto import Bloccato, apri, esito_del_blocco, pausa

CODICI_USCITA = {Esito.FATTO: 0, Esito.CAPTCHA: 2, Esito.ERRORE: 3}


def carica_json(percorso: Path):
    return json.loads(Path(percorso).read_text(encoding="utf-8"))


def client_di(richiesta: Richiesta) -> type:
    """La classe Client del portale, dopo aver controllato che offra ogni filtro chiesto."""
    if richiesta.portale not in PORTALI:
        raise ErroreRichiesta(f"portale {richiesta.portale!r} sconosciuto; noti: {', '.join(PORTALI)}")
    client = PORTALI[richiesta.portale]
    sconosciuti = sorted(set(richiesta.filtri) - client.FILTRI)
    if sconosciuti:
        raise ErroreRichiesta(f"filtri non offerti da {richiesta.portale}: {', '.join(sconosciuti)}. "
                              f"Ammessi: {', '.join(sorted(client.FILTRI))}")
    return client


def cerca(client, richiesta: Richiesta, tipo_trasporto: str) -> Risultato:
    """Legge le pagine fino a max_pagine o all'ultima del portale. Un ValueError del client è una richiesta sbagliata.
    Gli annunci fuori dal comune chiesto (normalizza.nel_comune) si scartano e si contano."""
    annunci, totale, lette, fuori = [], None, 0, 0

    def fermo(stato: Esito, messaggio: str) -> Risultato:
        return Risultato(stato=stato, annunci=annunci, pagine_lette=lette, totale=totale, messaggio=messaggio,
                         fuori_comune=fuori)

    for pagina in range(1, richiesta.max_pagine + 1):
        if pagina > 1:
            pausa(tipo_trasporto)
        try:
            letta = client.cerca(richiesta.comune, richiesta.contratto, pagina=pagina, **richiesta.filtri)
        except Bloccato as blocco:
            return fermo(*esito_del_blocco(richiesta.portale, tipo_trasporto, f"alla pagina {pagina}", blocco))
        except (json.JSONDecodeError, UnicodeError) as errore:
            return fermo(Esito.ERRORE, f"pagina {pagina}: risposta illeggibile ({errore})")
        except ValueError as errore:
            # I client rifiutano filtri e valori prima della prima richiesta. Più avanti un ValueError è un guasto
            # nella lettura, e le pagine già lette vanno tenute.
            if pagina == 1:
                raise ErroreRichiesta(str(errore)) from errore
            return fermo(Esito.ERRORE, f"pagina {pagina}: {errore}")
        except Exception as errore:  # noqa: BLE001  il guasto di una pagina non deve perdere le pagine già lette
            return fermo(Esito.ERRORE, f"pagina {pagina}: {type(errore).__name__}: {errore}")
        # Il nome è quello del registro: alcuni client ne scrivono un altro (casait scrive "casa").
        tenuti = [a for a in letta.annunci if nel_comune(a, richiesta.comune)]
        fuori += len(letta.annunci) - len(tenuti)
        annunci.extend(replace(normalizza(a), portale=richiesta.portale) for a in tenuti)
        lette = pagina
        totale = letta.totale if totale is None else totale
        if not letta.annunci or (letta.pagine is not None and pagina >= letta.pagine):
            break
    scartati = f", {fuori} letti e scartati perché fuori da {richiesta.comune}" if fuori else ""
    return fermo(Esito.FATTO, f"{totale if totale is not None else 'numero non dichiarato di'} annunci in totale sul "
                              f"portale{scartati}")


def unisci(precedenti: list[dict], nuovi: list[dict]) -> list[dict]:
    """Il nuovo annuncio sostituisce il vecchio, ma un dettaglio già letto resta: la lista di ricerca non lo porta."""
    per_chiave = {(a["portale"], a["id"]): a for a in precedenti}
    for annuncio in nuovi:
        chiave = (annuncio["portale"], annuncio["id"])
        dettaglio = annuncio.get("dettaglio") or per_chiave.get(chiave, {}).get("dettaglio")
        per_chiave[chiave] = {**annuncio, "dettaglio": dettaglio}
    return list(per_chiave.values())


def scrivi(percorso: Path, contenuto: dict) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = percorso.with_name(percorso.name + ".tmp")
    provvisorio.write_text(json.dumps(contenuto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(provvisorio, percorso)


def salva(percorso: Path, richiesta: Richiesta, risultato: Risultato) -> int:
    precedenti = carica_json(percorso)["annunci"] if percorso.exists() else []
    annunci = unisci(precedenti, [asdict(a) for a in risultato.annunci])
    scrivi(percorso, {
        "richiesta": asdict(richiesta),
        "aggiornato_il": adesso_utc(),
        "esito": risultato.stato.value,
        "annunci": annunci,
    })
    return len(annunci)


def trova(richiesta: Richiesta, cartella: Path) -> tuple[Risultato, Path, int]:
    """Cerca sul portale della richiesta e salva in cartella/<id>.json.

    Portale o filtri sconosciuti sollevano ErroreRichiesta prima di aprire il trasporto e senza scrivere nulla.
    Blocchi ed errori del portale tornano nello stato del Risultato, con gli annunci già letti salvati.
    """
    client = client_di(richiesta)
    try:
        with apri(client) as trasporto:
            argomenti = {"provincia": richiesta.provincia} if getattr(client, "CHIEDE_PROVINCIA", False) else {}
            risultato = cerca(client(trasporto, **argomenti), richiesta, client.TRASPORTO)
    except ErrorePortale as errore:
        risultato = Risultato(stato=Esito.ERRORE, annunci=[], pagine_lette=0, messaggio=str(errore))
    percorso = cartella / f"{richiesta.id}.json"
    return risultato, percorso, salva(percorso, richiesta, risultato)


def main(cartella_output: Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cerca gli annunci di una Richiesta sul portale indicato.")
    parser.add_argument("richiesta", type=Path)
    parser.add_argument("--cartella-output", type=Path, default=cartella_output)
    args = parser.parse_args(argv)

    try:
        richiesta = Richiesta.da_json(carica_json(args.richiesta))
        risultato, percorso, nel_file = trova(richiesta, args.cartella_output)
    except (OSError, json.JSONDecodeError, ErroreRichiesta) as errore:
        print(f"errore: {errore}", file=sys.stderr)
        return CODICI_USCITA[Esito.ERRORE]

    print(f"{risultato.stato.value}: {len(risultato.annunci)} annunci letti, pagine lette {risultato.pagine_lette}, "
          f"{nel_file} annunci in {percorso}. {risultato.messaggio}")
    if risultato.stato is Esito.CAPTCHA:
        print(f"CAPTCHA su {richiesta.portale}: risolvilo a mano nella tab di Chrome rimasta aperta, "
              f"poi rilancia lo stesso comando. Gli annunci già letti restano salvati.", file=sys.stderr)
    return CODICI_USCITA[risultato.stato]
