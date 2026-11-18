#!/usr/bin/env python3
"""Server MCP locale del plugin portali-immobiliari.

Solo libreria standard: sul computer di chi lo usa non si installa nulla.
Parla MCP su stdio, un messaggio JSON per riga. Gli strumenti di ricerca chiamano il motore in motore/:
un client per portale in motore/clienti, la sua mappa dei filtri in motore/filtri.
"""
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motore.clienti import PORTALI  # noqa: E402
from motore.dettaglio import LETTORI  # noqa: E402
from motore.dettagli import dettagli as leggi_dettagli, leggi_output  # noqa: E402
from motore.portali.modello import ErrorePortale, ErroreRichiesta, Esito, Richiesta  # noqa: E402
from motore.trova import trova  # noqa: E402
from motore.unisci import unisci_output  # noqa: E402

VERSIONE_PROTOCOLLO = "2025-06-18"
VERSIONE = "0.3.0"
MAPPE = Path(__file__).resolve().parent.parent / "motore" / "filtri"

# I campi che servono alla prima scrematura (passo 4 del metodo); il resto resta nel file.
CAMPI_SCREMATURA = ("portale", "id", "url", "titolo", "prezzo", "mq", "locali", "piano", "ascensore", "indirizzo",
                    "zona", "condizione", "caratteristiche", "vista", "distanza_mare", "ribasso_pct", "asta")


class Fermo(Exception):
    """Lo strumento non può andare avanti: il testo dice all'umano cosa fare."""


def cartella_di_lavoro() -> Path:
    cartella = Path(os.environ.get("PORTALI_IMMOBILIARI_CARTELLA") or "~/portali-immobiliari/ricerche").expanduser()
    cartella.mkdir(parents=True, exist_ok=True)
    return cartella


def file_di_lavoro(nome: str) -> Path:
    percorso = Path(nome).expanduser()
    return percorso if percorso.is_absolute() else cartella_di_lavoro() / percorso


def riassunto(annuncio: dict) -> dict:
    """Solo i campi di scrematura che il portale ha valorizzato: un campo che manca è un dato assente."""
    return {campo: annuncio[campo] for campo in CAMPI_SCREMATURA if annuncio.get(campo) not in (None, [])}


def controlla_esito(risultato, percorso: Path) -> None:
    if risultato.stato is Esito.CAPTCHA:
        raise Fermo(
            f"Il portale ha mostrato un CAPTCHA ({risultato.messaggio}). Fermati e non riprovare da solo: "
            f"chiedi all'umano di risolverlo a mano nella scheda di Chrome rimasta aperta, poi, quando te lo dice, "
            f"rilancia la stessa richiesta. Quanto letto finora è salvato in {percorso}."
        )
    if risultato.stato is Esito.ERRORE:
        raise Fermo(
            f"Lettura non riuscita: {risultato.messaggio} Solo se l'errore nomina Chrome o osascript, chiedi "
            f"all'umano di aprire Google Chrome con una finestra del profilo in cui è attivo Visualizza > Opzioni "
            f"per sviluppatori > Consenti JavaScript da Eventi Apple, poi riprova. Altrimenti non riprovare su "
            f"questo portale e vai avanti con gli altri. Quanto letto finora è salvato in {percorso}."
        )


def dove_giro(_argomenti):
    """Dice su che sistema gira il server e se da qui si comanda il Mac."""
    esito = {"sistema": platform.system(), "versione": platform.release(), "macchina": platform.machine()}
    try:
        prova = subprocess.run(
            ["osascript", "-e", "return 1"], capture_output=True, text=True, timeout=10
        )
        esito["osascript"] = "funziona" if prova.stdout.strip() == "1" else f"errore: {prova.stderr.strip()}"
    except FileNotFoundError:
        esito["osascript"] = "assente"
    except subprocess.TimeoutExpired:
        esito["osascript"] = "nessuna risposta in 10 secondi"
    return esito


def mappa_di(nome) -> tuple[type, dict]:
    if nome not in PORTALI:
        raise ErroreRichiesta(f"portale {nome!r} sconosciuto; noti: {', '.join(PORTALI)}")
    return PORTALI[nome], json.loads((MAPPE / f"{nome}.json").read_text(encoding="utf-8"))


def elenco_portali(_argomenti):
    """Un portale che non si carica compare con il suo errore: gli altri restano usabili."""
    portali = []
    for nome in PORTALI:
        try:
            client, mappa = mappa_di(nome)
            portali.append({"portale": nome, "trasporto": client.TRASPORTO, "descrizione": mappa.get("descrizione"),
                            "filtri": len(client.FILTRI)})
        except Exception as errore:  # noqa: BLE001
            portali.append({"portale": nome, "errore": f"{type(errore).__name__}: {errore}"})
    return {"portali": portali}


def elenco_filtri(argomenti):
    """La mappa del portale ridotta a quello che serve per scrivere i filtri di cerca."""
    nome = argomenti.get("portale")
    client, mappa = mappa_di(nome)
    filtri = []
    for voce in mappa["filtri"]:
        if voce["chiave"] not in client.FILTRI:
            continue
        compatta = {"chiave": voce["chiave"], "etichetta": voce["etichetta"], "tipo": voce["tipo"]}
        if voce["valori"]:
            compatta["valori"] = [{"valore": v["valore"], "etichetta": v["etichetta"]} for v in voce["valori"]]
        if voce.get("unita"):
            compatta["unita"] = voce["unita"]
        filtri.append(compatta)
    esito = {"portale": nome, "filtri": filtri, "assenti": [a["chiave"] for a in mappa.get("assenti", [])]}
    for chiave in ("combinazioni", "note"):
        if mappa.get(chiave):
            esito[chiave] = mappa[chiave]
    return esito


def cerca(argomenti):
    filtri = argomenti.get("filtri", {})
    impronta = hashlib.sha1(json.dumps(filtri, sort_keys=True).encode()).hexdigest()[:6]
    comune = re.sub(r"[^a-z0-9]+", "-", str(argomenti.get("comune", "")).lower()).strip("-")
    richiesta = Richiesta.da_json({
        # Stessa ricerca nello stesso giorno, stesso file: rilanciarla dopo un CAPTCHA aggiunge, non duplica.
        "id": f"{date.today()}-{argomenti.get('portale')}-{comune}-{argomenti.get('contratto')}-{impronta}",
        "portale": argomenti.get("portale"),
        "contratto": argomenti.get("contratto"),
        "comune": argomenti.get("comune"),
        "provincia": argomenti.get("provincia"),
        "filtri": filtri,
        "preferenze": argomenti.get("preferenze", []),
        "max_pagine": argomenti.get("max_pagine", 2),
    })
    risultato, percorso, nel_file = trova(richiesta, cartella_di_lavoro())
    controlla_esito(risultato, percorso)
    esito = {
        "file": str(percorso),
        "totale_sul_portale": risultato.totale,
        "pagine_lette": risultato.pagine_lette,
        "annunci_letti": len(risultato.annunci),
        "annunci_nel_file": nel_file,
    }
    if risultato.fuori_comune:
        esito["scartati_fuori_comune"] = risultato.fuori_comune
    return {**esito, "annunci": [riassunto(asdict(a)) for a in risultato.annunci]}


def dettagli(argomenti):
    percorso = file_di_lavoro(str(argomenti.get("file", "")))
    ids = [str(i) for i in argomenti.get("id", [])]
    risultato, gia_letti = leggi_dettagli(percorso, ids)
    controlla_esito(risultato, percorso)
    _portale, annunci = leggi_output(percorso)
    esito = {
        "file": str(percorso),
        "dettagli_letti_ora": risultato.pagine_lette,
        "gia_letti_prima": gia_letti,
    }
    if risultato.non_letti:
        esito["non_letti"] = risultato.non_letti
    return {
        **esito,
        "annunci": [
            {"id": i, "titolo": annunci[i].titolo, "url": annunci[i].url, **asdict(annunci[i].dettaglio)}
            for i in dict.fromkeys(ids) if annunci[i].dettaglio is not None
        ],
    }


def unisci(argomenti):
    fonti = [file_di_lavoro(str(f)) for f in argomenti.get("file", [])]
    if len(fonti) < 2:
        raise ErroreRichiesta("servono almeno due file di ricerca da unire")
    impronta = hashlib.sha1("|".join(sorted(map(str, fonti))).encode()).hexdigest()[:6]
    uscita = cartella_di_lavoro() / f"{date.today()}-unione-{impronta}.json"
    risultato = unisci_output(fonti, uscita)
    return {
        "file": str(uscita),
        "annunci_in_ingresso": risultato["annunci"],
        "immobili": risultato["immobili"],
        "su_piu_portali": risultato["su_piu_portali"],
        "gruppi": [
            {"etichetta": g["etichetta"], "link": g["link"], "motivo": g["motivo"],
             "annunci": [riassunto(a) for a in g["annunci"]]}
            for g in risultato["gruppi"]
        ],
    }


SCHEMA_PORTALE = {"type": "string", "description": "Il nome di un portale come lo restituisce elenco_portali."}

STRUMENTI = {
    "dove_giro": {
        "funzione": dove_giro,
        "descrizione": "Dice su che sistema gira questo server (macOS o Linux) e se può comandare il Mac con osascript. Serve a controllare che il computer sia pronto.",
        "schema": {"type": "object", "properties": {}},
    },
    "elenco_portali": {
        "funzione": elenco_portali,
        "descrizione": (
            "Elenca i portali immobiliari su cui cerca può cercare: per ognuno il nome da passare agli altri "
            "strumenti, il trasporto (chrome: apre una scheda nel Google Chrome di questo Mac, con il rischio di "
            "CAPTCHA; http: legge senza browser), una frase che aiuta a scegliere dove cercare e quanti filtri "
            "accetta. Chiamalo per primo e scegli i portali adatti al cliente."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "elenco_filtri": {
        "funzione": elenco_filtri,
        "descrizione": (
            "Elenca i filtri che cerca accetta su un portale: per ognuno la chiave da usare, l'etichetta del sito, "
            "il tipo (intero, booleano, enum, multiplo, testo) e, dove ci sono, i valori ammessi con il loro "
            "significato e l'unità. Per un booleano passa true. Per un multiplo passa una lista di valori. Per un "
            "intero i valori sono le soglie del menu del sito: un numero diverso può essere rifiutato, e l'errore "
            "lo dice. La stessa chiave vuol dire la stessa cosa su ogni portale (prezzo_max, locali_min, ascensore), "
            "ma ogni portale ne offre alcune e non altre: `assenti` elenca quelle che il portale non ha. "
            "`combinazioni` dice quali filtri il portale unisce in O invece che in E. Chiamalo per ogni portale "
            "prima di scrivere i filtri di cerca. Un desiderio del cliente che non ha un filtro va nelle "
            "preferenze, non si inventa un filtro."
        ),
        "schema": {"type": "object", "properties": {"portale": SCHEMA_PORTALE}, "required": ["portale"]},
    },
    "cerca": {
        "funzione": cerca,
        "descrizione": (
            "Cerca annunci immobiliari su un portale di elenco_portali. Sui portali chrome apre una scheda nel "
            "Google Chrome di questo Mac e va al passo di una persona (8-20 secondi tra una pagina e l'altra), "
            "sugli altri legge senza browser con pause più brevi. Salva tutti i dati in un file JSON e "
            "restituisce il percorso del file, "
            "il totale degli annunci sul portale, quanti ne ha letti e, per ogni annuncio, i campi utili a una "
            "prima scrematura (prezzo, mq, locali, piano, ascensore, zona, condizione, caratteristiche, vista, "
            "distanza dal mare, ribasso). Un campo che manca vuol dire dato assente sul portale, non no. "
            "Alcuni portali cercano il comune come testo e restituiscono anche annunci di altri comuni: "
            "`scartati_fuori_comune`, quando c'è, dice quanti ne sono stati letti e tolti, per questo "
            "annunci_letti può essere minore del totale sul portale. "
            "Se torna un errore che parla di CAPTCHA, fermati: non rilanciare, chiedi all'umano di risolverlo "
            "in Chrome. Se dice che il portale ha rifiutato la lettura, non riprovare subito. Se dice che Chrome "
            "non accetta JavaScript, chiedi all'umano di sistemare Chrome."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "portale": SCHEMA_PORTALE,
                "comune": {"type": "string", "description": (
                    "Il nome italiano del comune, per esempio Trieste, Muggia, Duino Aurisina.")},
                "provincia": {"type": "string", "description": (
                    "La provincia del comune, per esempio Trieste. Passala sempre: idealista e casadaprivato non "
                    "trovano un comune che non è capoluogo senza la sua provincia.")},
                "contratto": {"type": "string", "enum": ["vendita", "affitto"]},
                "filtri": {"type": "object", "description": (
                    "Solo i vincoli duri del cliente, con le chiavi restituite da elenco_filtri per questo "
                    "portale, per esempio {\"prezzo_max\": 250000, \"locali_min\": 3, \"ascensore\": true}. "
                    "Una chiave che il portale non offre, o un valore non ammesso, fa fallire la ricerca con un "
                    "messaggio che lo nomina, prima di aprire il portale."),
                    "additionalProperties": {"anyOf": [
                        {"type": ["string", "integer", "boolean"]},
                        {"type": "array", "items": {"type": ["string", "integer"]}, "minItems": 1}]}},
                "preferenze": {"type": "array", "items": {"type": "string"}, "description": (
                    "I desideri del cliente che non sono filtri (per esempio vista mare, piano alto). "
                    "Non cambiano la ricerca: restano scritti nel file per la selezione.")},
                "max_pagine": {"type": "integer", "minimum": 1, "default": 2, "description": (
                    "Quante pagine di risultati leggere (da 10 a 30 annunci a pagina, secondo il portale). Alzala "
                    "solo se i filtri sono già stretti: più pagine vuol dire più rischio di CAPTCHA.")},
            },
            "required": ["portale", "comune", "contratto"],
        },
    },
    "dettagli": {
        "funzione": dettagli,
        "descrizione": (
            f"Funziona su {', '.join(sorted(LETTORI))}; sugli altri portali risponde con il motivo (per esempio "
            "che la lista è già completa). Apre la pagina di alcuni annunci già trovati con cerca, con lo stesso "
            "trasporto di cerca (sui portali chrome una scheda di Google Chrome), e aggiunge al file la descrizione "
            "completa e, dove la pagina li dà, piano, spese condominiali, classe energetica, anno, stato, indirizzo "
            "e le altre voci della scheda (per le aste offerta minima, disponibilità, metri quadri, termine delle "
            "offerte). Usalo solo sui candidati scelti dopo la prima scrematura, al massimo 8: ogni pagina aperta "
            "aumenta il rischio di CAPTCHA. Gli annunci già letti non vengono riaperti. Restituisce il dettaglio "
            "di ogni id richiesto. `non_letti`, quando c'è, elenca gli id la cui pagina non è più quella "
            "dell'annuncio (di solito rimosso dal portale) con il motivo: gli altri sono stati letti lo stesso, "
            "non rilanciarli. Dopo un errore di CAPTCHA fermati e chiedi all'umano, non rilanciare da solo. "
            "Se dice che il portale ha rifiutato la lettura, non riprovare subito."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Il percorso del file restituito da cerca (un portale solo)."},
                "id": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                       "description": "Gli id degli annunci di quel file da leggere."},
            },
            "required": ["file", "id"],
        },
    },
    "unisci": {
        "funzione": unisci,
        "descrizione": (
            "Unisce i file di ricerca di portali diversi: la stessa casa pubblicata su più portali diventa un "
            "gruppo solo, con i link di ogni portale e il motivo per cui gli annunci sono stati uniti. Non apre "
            "Chrome. Salva l'unione in un file e restituisce, per ogni casa, etichetta, link, motivo e i campi "
            "di scrematura di ogni annuncio (un campo che manca è un dato assente). Da qui in avanti si ragiona "
            "per casa, non per annuncio."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "file": {"type": "array", "items": {"type": "string"}, "minItems": 2,
                         "description": "I percorsi di due o più file restituiti da cerca."},
            },
            "required": ["file"],
        },
    },
}


def chiama(strumento: dict, argomenti: dict) -> dict:
    """Ogni errore diventa un risultato con isError: il server resta in piedi e Claude legge cosa è successo."""
    try:
        esito = strumento["funzione"](argomenti)
    except Fermo as fermo:
        return {"content": [{"type": "text", "text": str(fermo)}], "isError": True}
    except (ErroreRichiesta, ErrorePortale, OSError, json.JSONDecodeError, KeyError, TypeError) as errore:
        return {"content": [{"type": "text", "text": f"Richiesta non valida o file illeggibile: {errore}"}],
                "isError": True}
    except Exception as errore:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"Errore inatteso nel server: {errore!r}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(esito, ensure_ascii=False)}]}


def rispondi(richiesta):
    metodo = richiesta.get("method")
    parametri = richiesta.get("params") or {}
    if metodo == "initialize":
        return {
            "protocolVersion": parametri.get("protocolVersion", VERSIONE_PROTOCOLLO),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "portali-immobiliari", "version": VERSIONE},
        }
    if metodo == "ping":
        return {}
    if metodo == "tools/list":
        return {
            "tools": [
                {"name": nome, "description": s["descrizione"], "inputSchema": s["schema"]}
                for nome, s in STRUMENTI.items()
            ]
        }
    if metodo == "tools/call":
        strumento = STRUMENTI.get(parametri.get("name"))
        if strumento is None:
            raise LookupError(f"strumento sconosciuto: {parametri.get('name')}")
        return chiama(strumento, parametri.get("arguments") or {})
    raise LookupError(f"metodo sconosciuto: {metodo}")


def main():
    for riga in sys.stdin:
        riga = riga.strip()
        if not riga:
            continue
        richiesta = json.loads(riga)
        if "id" not in richiesta:
            continue  # le notifiche non vogliono risposta
        try:
            messaggio = {"jsonrpc": "2.0", "id": richiesta["id"], "result": rispondi(richiesta)}
        except LookupError as errore:
            messaggio = {"jsonrpc": "2.0", "id": richiesta["id"], "error": {"code": -32601, "message": str(errore)}}
        sys.stdout.write(json.dumps(messaggio, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
