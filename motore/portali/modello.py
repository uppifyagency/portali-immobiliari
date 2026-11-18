from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum

CONTRATTI = ("vendita", "affitto")


def adesso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ErroreRichiesta(ValueError):
    """Input sbagliato: richiesta o filtri. Si corregge il file, non si riprova."""


class ErrorePortale(RuntimeError):
    """La lettura del portale e' fallita: browser o struttura della pagina."""


def _controlla(condizione: bool, campo: str, atteso: str, valore) -> None:
    if not condizione:
        raise ErroreRichiesta(f"campo {campo!r}: atteso {atteso}, trovato {valore!r}")


@dataclass(frozen=True, kw_only=True)
class Richiesta:
    id: str
    portale: str = "immobiliare"
    contratto: str
    comune: str
    provincia: str | None = None
    filtri: dict[str, str | int | bool | list[str | int]] = field(default_factory=dict)
    preferenze: list[str] = field(default_factory=list)
    max_pagine: int = 2

    @classmethod
    def da_json(cls, dati: dict) -> "Richiesta":
        if not isinstance(dati, dict):
            raise ErroreRichiesta(f"la richiesta deve essere un oggetto JSON, trovato {type(dati).__name__}")
        noti = {f.name for f in fields(cls)}
        sconosciuti = sorted(set(dati) - noti)
        if sconosciuti:
            raise ErroreRichiesta(f"campi sconosciuti: {', '.join(sconosciuti)}; ammessi: {', '.join(sorted(noti))}")
        for obbligatorio in ("id", "contratto", "comune"):
            if obbligatorio not in dati:
                raise ErroreRichiesta(f"campo {obbligatorio!r} mancante")

        for nome in ("id", "portale", "comune"):
            if nome in dati:
                valore = dati[nome]
                _controlla(isinstance(valore, str) and valore.strip() != "", nome, "un testo non vuoto", valore)
        provincia = dati.get("provincia")
        _controlla(provincia is None or isinstance(provincia, str) and provincia.strip() != "", "provincia",
                   "un testo non vuoto o niente", provincia)
        _controlla(dati["contratto"] in CONTRATTI, "contratto", " o ".join(CONTRATTI), dati["contratto"])

        filtri = dati.get("filtri", {})
        _controlla(isinstance(filtri, dict), "filtri", "un oggetto", filtri)
        for nome, valore in filtri.items():
            semplice = isinstance(valore, (str, int, bool))
            lista = isinstance(valore, list) and valore and all(isinstance(v, (str, int)) and type(v) is not bool for v in valore)
            _controlla(semplice or lista, f"filtri.{nome}",
                       "testo, numero intero, vero/falso o una lista non vuota di testi o interi", valore)

        preferenze = dati.get("preferenze", [])
        _controlla(isinstance(preferenze, list) and all(isinstance(p, str) for p in preferenze),
                   "preferenze", "una lista di testi", preferenze)

        max_pagine = dati.get("max_pagine", 2)
        _controlla(type(max_pagine) is int and max_pagine >= 1, "max_pagine", "un intero da 1 in su", max_pagine)

        return cls(**{**dati, "filtri": {k: list(v) if isinstance(v, list) else v for k, v in filtri.items()},
                      "preferenze": list(preferenze)})


@dataclass(frozen=True, kw_only=True)
class Dettaglio:
    descrizione: str | None = None
    piano: str | None = None
    spese_condominio: str | None = None
    classe_energetica: str | None = None
    anno_costruzione: str | None = None
    stato: str | None = None
    caratteristiche: list[str]
    letto_il: str
    indirizzo: str | None = None


@dataclass(frozen=True, kw_only=True)
class Asta:
    """I dati di una vendita giudiziaria. Il prezzo dell'Annuncio è il prezzo base."""
    data: str | None
    offerta_minima: int | None
    tribunale: str | None
    procedura: str | None
    lotto: str | None
    modalita: str | None


@dataclass(frozen=True, kw_only=True)
class Annuncio:
    portale: str
    id: str
    url: str
    titolo: str | None
    prezzo: int | None
    mq: int | None
    locali: str | None
    bagni: str | None
    piano: str | None
    ascensore: bool | None
    indirizzo: str | None
    zona: str | None
    lat: float | None
    lon: float | None
    descrizione: str | None
    foto: str | None
    agenzia: str | None
    condizione: str | None
    caratteristiche: list[str]
    ribasso_pct: float | None
    letto_il: str
    vista: list[str] = field(default_factory=list)
    distanza_mare: str | None = None
    dettaglio: Dettaglio | None = None
    asta: Asta | None = None
    comune: str | None = None  # il comune che l'annuncio dichiara, dove il client lo sa leggere

    @classmethod
    def da_json(cls, dati: dict) -> "Annuncio":
        """Legge un annuncio da un file di output, anche scritto prima che esistessero i campi con un valore predefinito."""
        dettaglio, asta = dati.get("dettaglio"), dati.get("asta")
        return cls(**{**dati, "dettaglio": Dettaglio(**dettaglio) if dettaglio else None,
                      "asta": Asta(**asta) if asta else None})


@dataclass
class Pagina:
    totale: int | None
    pagine: int | None
    annunci: list[Annuncio]


class Esito(Enum):
    FATTO = "fatto"
    CAPTCHA = "captcha"
    ERRORE = "errore"


@dataclass(kw_only=True)
class Risultato:
    stato: Esito
    annunci: list[Annuncio]
    pagine_lette: int
    totale: int | None = None
    messaggio: str = ""
    fuori_comune: int = 0  # annunci letti e scartati perché dichiarano un altro comune
    non_letti: dict[str, str] = field(default_factory=dict)  # id -> perché il suo dettaglio non si è letto
