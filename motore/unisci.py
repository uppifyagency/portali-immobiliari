"""Unisce gli output di più portali: ogni immobile una volta sola, con i link di tutti i portali che lo pubblicano.

Da riga di comando si lancia con l'unisci.py alla radice del repo; il server MCP del plugin chiama unisci_output().
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .portali.modello import Annuncio
from .portali.unione import Immobile, indirizzo_di, raggruppa
from .trova import carica_json, scrivi, unisci


def euro(valore: int) -> str:
    return f"{valore:,} €".replace(",", ".")


def etichetta(immobile: Immobile) -> str:
    """Il titolo dell'annuncio con il civico, se uno ce l'ha, e il prezzo, o la forbice se i portali non concordano."""
    def senza_civico(a: Annuncio) -> bool:
        indirizzo = indirizzo_di(a)
        return indirizzo is None or indirizzo.civico is None

    migliore = min(immobile.annunci, key=lambda a: (senza_civico(a), a.portale, a.id))
    prezzi = sorted({a.prezzo for a in immobile.annunci if a.prezzo is not None})
    if not prezzi:
        prezzo = "prezzo assente"
    elif len(prezzi) == 1:
        prezzo = euro(prezzi[0])
    else:
        prezzo = f"{euro(prezzi[0])} - {euro(prezzi[-1])}"
    return f"{migliore.titolo or migliore.indirizzo or migliore.id}, {prezzo}"


def contenuto(immobili: list[Immobile], fonti: list[Path], annunci_in: int) -> dict:
    return {
        "fonti": [percorso.name for percorso in fonti],
        "annunci": annunci_in,
        "immobili": len(immobili),
        "su_piu_portali": sum(len(i.annunci) > 1 for i in immobili),
        "gruppi": [{
            "etichetta": etichetta(i),
            "portali": list(i.portali),
            "link": [a.url for a in i.annunci],
            "motivo": i.motivo,
            "annunci": [asdict(a) for a in i.annunci],
        } for i in immobili],
    }


def unisci_output(fonti: list[Path], uscita: Path) -> dict:
    """Raggruppa per immobile gli annunci dei file di ricerca in fonti e scrive il risultato in uscita."""
    annunci: list[dict] = []
    for percorso in fonti:
        annunci = unisci(annunci, carica_json(percorso)["annunci"])
    risultato = contenuto(raggruppa([Annuncio.da_json(a) for a in annunci]), fonti, len(annunci))
    scrivi(uscita, risultato)
    return risultato


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unisce gli output di trova.py e dettagli.py di più portali per immobile.")
    parser.add_argument("output", type=Path, nargs="+")
    parser.add_argument("--uscita", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        risultato = unisci_output(args.output, args.uscita)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as errore:
        print(f"errore: {errore!r}", file=sys.stderr)
        return 3

    print(f"{risultato['annunci']} annunci in ingresso, {risultato['immobili']} immobili, "
          f"{risultato['su_piu_portali']} su più portali: {args.uscita}")
    return 0
