"""Riconosce lo stesso immobile pubblicato su portali diversi e lo raggruppa in un Immobile.

Regola (`motivo_coppia`): due annunci di portali diversi sono lo stesso immobile in due casi.

Per via (`per_via`), quando entrambi hanno una via leggibile (e allora decide solo questa regola):
  1. hanno lo stesso tipo di strada (via, salita, strada...) e lo stesso nome, oppure un nome
     è la coda dell'altro ("de Jenner" e "Luigi de Jenner");
  2. il civico coincide, oppure manca su uno dei due;
  3. il prezzo differisce al massimo del 2%, contando anche il prezzo prima del ribasso;
  4. i mq differiscono al massimo del 10%.

Per descrizione (`per_descrizione`), quando almeno uno dei due non ha la via (bakeca, subito,
tuttocasa e gate-away quasi mai la danno):
  1. almeno il 70% delle terne di parole consecutive della descrizione più corta compare anche
     nell'altra, e la più corta ha almeno 10 terne;
  2. il testo non è un testo modello: nessun altro annuncio dello stesso portale, nello stesso
     gruppo di annunci, ha la stessa descrizione copiata (`testi_modello`);
  3. il prezzo è uguale, contando anche il prezzo prima del ribasso;
  4. i mq differiscono al massimo del 10%, se li hanno entrambi (i portali delle aste non li danno);
  5. il piano è lo stesso, se entrambi ne danno uno solo.

Da dove vengono le soglie della via, sui due output reali del 28/10/2026 (fixtures/output-*-2026-10-28.json):
- La zona non entra: la stessa via è "Settefontane" su immobiliare e "Barriera Vecchia" su idealista.
- Prezzo e mq da soli sbagliano: Via delle Linfe (215.000, 82 mq) non è via Monte Peralba 15
  (215.000, 81 mq). Serve la via.
- Il nome si scrive in modi diversi: "Via del Monte Peralba" e "via Monte Peralba", "De Rittmeyer"
  e "de Rittmeyer", "D'Assisi" e "d'Assisi". Per questo articoli e preposizioni non contano.
  Il nome di battesimo a volte manca ("via de Jenner 4" e "Via Luigi de Jenner, 4"), per questo
  basta la coda; il tipo di strada resta obbligatorio perché "dell'Istria" è la coda anche di
  "Strada Vecchia dell'Istria", un'altra strada.
- Il civico ha forme diverse ("7/1" e "7", "12 d", "45 /a"): conta solo il numero iniziale.
  Un civico diverso esclude: via Monte Peralba 15 non è Via del Monte Peralba, 21.
- I mq variano di qualche unità (commerciali o calpestabili): 70 e 68, 117 e 113.
- Il prezzo di solito coincide. Via Fabio Severo 97 costa 205.000 su immobiliare con un ribasso
  del 9,3% e 226.000 su idealista: 205.000 / (1 - 0,093) = 226.000, l'altro portale ha il prezzo vecchio.
- I locali non entrano: via Francesco Cappello 7/1 è un quadrilocale su immobiliare e un trilocale
  su idealista, con stesso prezzo, mq, piano e la stessa descrizione parola per parola.

Da dove vengono le condizioni della descrizione: 1.092 coppie candidate tra portali diversi, con almeno
un annuncio senza via, dagli output salvati fino al 17/11/2026 (Trieste, Muggia, Duino Aurisina),
giudicate a mano una per una, più le coppie dello stesso portale, che sono case diverse per costruzione
(prove/giro7/unione/, rigenerate da strumenti/coppie_senza_via.py).
- La stessa agenzia copia lo stesso testo su più portali, anche quando uno lo tronca (tuttocasa
  si ferma a un centinaio di caratteri): per questo si misura sul testo più corto. Le copie
  stanno tra 0,78 e 1; due testi diversi della stessa asta arrivano a 0,55 e restano divisi.
- Il modello di testo di un'agenzia porta due case diverse a 0,48 (le aste Aste Florio di
  via Vergerio 3 e di viale dei Campi Elisi 20): la soglia sta a 0,7, sopra.
- Sotto le 10 terne, una dozzina di parole, una frase fatta come "appartamento in vendita a
  Trieste" basterebbe da sola.
- Il testo da solo non basta: a Portopiccolo il costruttore usa lo stesso testo per tutte le unità.
  Il monolocale da 61 mq a 412.000 di wikicasa e il bilocale da 64 mq a 416.000 di idealista hanno
  la descrizione uguale al 100% e stanno nelle vecchie tolleranze (2% sul prezzo, 10% sui mq).
  Il prezzo uguale li separa: le 118 copie giudicate STESSA che le vecchie tolleranze univano hanno
  tutte lo stesso prezzo, mentre due unità dello stesso listino differiscono anche di poco.
- Il prezzo uguale non basta: idealista pubblica due volte lo stesso bilocale di Portopiccolo
  (36228709 e 32217843: 416.000, 64 mq, sesto piano, Savills, stesso testo) e nessun campo li
  distingue. Se il portale stesso ha due annunci con quel testo, il testo descrive un listino e
  non una casa: la regola per descrizione non vale per quegli annunci, e resta un doppione.
  Su tutti gli annunci salvati il testo modello non toglie nessuna delle 118 copie giudicate STESSA.
- Il piano separa due appartamenti uguali dello stesso stabile: N E X T Immobiliare pubblica su
  subito due bilocali da 52 mq a 250.000 a Muggia con lo stesso testo, uno al primo e uno al secondo
  piano. Tra le coppie unite che danno entrambe un piano solo (22 per descrizione, 44 per via) il
  piano coincide sempre. Le forme a più piani ("T, 4") non contano.
- I mq restano al 10%: la stessa villetta di Domus a Zaule ha 120 mq su cercacasa e 124 su idealista.
- Le coordinate non entrano: immobiliare mette il centro della via quando nasconde il civico
  (tre case diverse di via de Amicis allo stesso punto, tre di via del Boveto 5 a prezzi diversi)
  e un posto auto e un terreno d'asta cadono sullo stesso punto di ripiego.
- Il prezzo deve coincidere anche quando il testo è uguale: via Giulia 48 è a 83.200 (prezzo base)
  su un portale e a 62.400 (offerta minima) su un altro, e una casa con il testo uguale a 199.000
  e 215.000 resta divisa. Meglio un doppione che una casa nascosta.
"""

import re
import unicodedata
from collections.abc import Iterable, Set
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from itertools import combinations

from .modello import Annuncio

TOLLERANZA_PREZZO = 0.02
TOLLERANZA_MQ = 0.10
DESCRIZIONE_COPIATA = 0.7
TERNE_MINIME = 10

TIPI_STRADA = frozenset({
    "via", "viale", "strada", "piazza", "piazzale", "piazzetta", "largo", "salita", "scala",
    "vicolo", "riva", "corso", "androna", "passo", "galleria", "borgo", "contrada", "localita",
    "calle", "campo", "rotonda", "lungomare", "molo",
})
PARTICELLE = frozenset({
    "di", "de", "del", "della", "dello", "dei", "degli", "delle", "dell", "d",
    "da", "dal", "dalla", "dallo", "dai", "dagli", "dalle", "dall",
    "il", "lo", "la", "i", "gli", "le", "l",
})
PIANO_TERRA = frozenset({"t", "pt", "terra", "piano terra"})
SENZA_CIVICO = re.compile(r"[,\s]+s\.?\s*n\.?\s*c\.?\s*$", re.IGNORECASE)
CIVICO_FINALE = re.compile(r"^(?P<strada>.*?)[,\s]\s*(?P<numero>\d+)(?:\s*/\s*\w+|\s+[a-z])?\s*$", re.IGNORECASE)


class Regola(IntEnum):
    """Le regole che uniscono una coppia, dalla più forte: `raggruppa` unisce prima le coppie più forti."""
    VIA_E_CIVICO = 0
    VIA = 1
    DESCRIZIONE = 2


@dataclass(frozen=True)
class Motivo:
    regola: Regola
    testo: str


@dataclass(frozen=True)
class Indirizzo:
    tipo: str
    nome: tuple[str, ...]
    civico: int | None

    def stessa_strada(self, altro: "Indirizzo") -> bool:
        corto, lungo = sorted((self.nome, altro.nome), key=len)
        return self.tipo == altro.tipo and bool(corto) and lungo[len(lungo) - len(corto):] == corto


@dataclass(frozen=True)
class Immobile:
    """Un immobile reale: al massimo un annuncio per portale, `motivo` è None se l'annuncio è solo."""
    annunci: tuple[Annuncio, ...]
    motivo: str | None

    @property
    def portali(self) -> tuple[str, ...]:
        return tuple(a.portale for a in self.annunci)


def chiave(annuncio: Annuncio) -> tuple[str, str]:
    return annuncio.portale, annuncio.id


def piega(testo: str) -> str:
    """Minuscolo, senza accenti, ogni segno di punteggiatura diventa spazio."""
    senza_accenti = "".join(c for c in unicodedata.normalize("NFKD", testo) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", senza_accenti.lower()).split())


def numero_civico(testo: str) -> int | None:
    """Il numero iniziale del civico: "7/1" -> 7, "12 d" -> 12, "45 /a" -> 45."""
    trovato = re.match(r"\s*(\d+)", testo)
    return int(trovato.group(1)) if trovato else None


def numero_piano(testo: str | None) -> int | None:
    """Il piano se l'annuncio ne dà uno solo: "3" -> 3, "T" -> 0; None per "T, 4", "R" o un piano assente."""
    if testo is None:
        return None
    piegato = piega(testo)
    if piegato in PIANO_TERRA:
        return 0
    return int(piegato) if piegato.isdigit() else None


def chiave_strada(strada: str) -> tuple[str, tuple[str, ...]]:
    parole = piega(strada).split()
    tipo = parole.pop(0) if parole and parole[0] in TIPI_STRADA else ""
    return tipo, tuple(p for p in parole if p not in PARTICELLE)


def separa_civico(indirizzo: str) -> tuple[str, int | None]:
    """"Via della Tesa, 21" -> ("Via della Tesa", 21). Il numero conta come civico solo in fondo,
    così "Via 24 Maggio" resta una strada senza civico."""
    testo = SENZA_CIVICO.sub("", indirizzo.strip())
    trovato = CIVICO_FINALE.match(testo)
    if not trovato:
        return testo, None
    return trovato.group("strada").rstrip(" ,"), int(trovato.group("numero"))


def indirizzo_di(annuncio: Annuncio) -> Indirizzo | None:
    """La strada dal campo indirizzo; il civico, se lì manca, dal titolo subito dopo la strada.

    immobiliare mette il civico solo nel titolo ("Trilocale via della Tesa 61, Settefontane, Trieste"
    con indirizzo "Via della Tesa"); idealista lo mette nell'indirizzo ("Via della Tesa, 21")."""
    if not annuncio.indirizzo:
        return None
    strada, civico = separa_civico(annuncio.indirizzo)
    if civico is None and annuncio.titolo:
        titolo, cercata = piega(annuncio.titolo), piega(strada)
        posizione = f" {titolo} ".find(f" {cercata} ")
        if cercata and posizione >= 0:
            civico = numero_civico(titolo[posizione + len(cercata):])
    tipo, nome = chiave_strada(strada)
    return Indirizzo(tipo, nome, civico) if nome else None


def prezzi_possibili(annuncio: Annuncio) -> tuple[int, ...]:
    if annuncio.prezzo is None:
        return ()
    if not annuncio.ribasso_pct:
        return (annuncio.prezzo,)
    return annuncio.prezzo, round(annuncio.prezzo / (1 - annuncio.ribasso_pct / 100))


def vicini(a: int, b: int, tolleranza: float) -> bool:
    return abs(a - b) <= tolleranza * max(a, b)


def mq_vicini(a: Annuncio, b: Annuncio) -> bool:
    return a.mq is not None and b.mq is not None and vicini(a.mq, b.mq, TOLLERANZA_MQ)


def testo_prezzo(a: Annuncio, b: Annuncio, tolleranza: float) -> str | None:
    """Come si confrontano i prezzi, o None se nessuna coppia di prezzi possibili sta nella tolleranza."""
    coppia = next(((pa, pb) for pa in prezzi_possibili(a) for pb in prezzi_possibili(b)
                   if vicini(pa, pb, tolleranza)), None)
    if coppia is None:
        return None
    ribasso = " (uguali prima del ribasso)" if coppia != (a.prezzo, b.prezzo) else ""
    return f"prezzo {a.prezzo} e {b.prezzo}{ribasso}"


@lru_cache(maxsize=4096)
def terne(testo: str | None) -> frozenset[tuple[str, ...]]:
    parole = piega(testo or "").split()
    return frozenset(tuple(parole[i:i + 3]) for i in range(len(parole) - 2))


def descrizione_copiata(a: Annuncio, b: Annuncio) -> float | None:
    """La parte delle terne di parole della descrizione più corta che compare anche nell'altra,
    o None se la più corta ha meno di TERNE_MINIME terne."""
    corta, lunga = sorted((terne(a.descrizione), terne(b.descrizione)), key=len)
    return len(corta & lunga) / len(corta) if len(corta) >= TERNE_MINIME else None


def copiata(a: Annuncio, b: Annuncio) -> bool:
    quanto = descrizione_copiata(a, b)
    return quanto is not None and quanto >= DESCRIZIONE_COPIATA


def testi_modello(annunci: Iterable[Annuncio]) -> frozenset[tuple[str, str]]:
    """Le chiavi degli annunci la cui descrizione è copiata in un altro annuncio dello stesso portale:
    un testo che il portale usa per due case descrive un listino, non una casa."""
    per_portale: dict[str, list[Annuncio]] = {}
    for annuncio in annunci:
        per_portale.setdefault(annuncio.portale, []).append(annuncio)
    return frozenset(chiave(x) for gruppo in per_portale.values() for a, b in combinations(gruppo, 2)
                     if copiata(a, b) for x in (a, b))


def per_via(a: Annuncio, b: Annuncio, ia: Indirizzo, ib: Indirizzo) -> Motivo | None:
    prezzo = testo_prezzo(a, b, TOLLERANZA_PREZZO)
    if prezzo is None or not mq_vicini(a, b) or not ia.stessa_strada(ib):
        return None
    if ia.civico is not None and ib.civico is not None and ia.civico != ib.civico:
        return None
    strada = " ".join(filter(None, (ia.tipo, *max(ia.nome, ib.nome, key=len))))
    if ia.civico is not None and ib.civico is not None:
        regola, civico = Regola.VIA_E_CIVICO, f"civico {ia.civico}"
    elif ia.civico == ib.civico:
        regola, civico = Regola.VIA, "civico assente su entrambi"
    else:
        regola, civico = Regola.VIA, f"civico {ia.civico or ib.civico} su un solo portale"
    return Motivo(regola, f"{a.portale} {a.id} e {b.portale} {b.id}: {strada}, {civico}, {prezzo}, mq {a.mq} e {b.mq}")


def per_descrizione(a: Annuncio, b: Annuncio) -> Motivo | None:
    """La regola per descrizione senza la condizione sul testo modello, che dipende dagli altri annunci."""
    prezzo = testo_prezzo(a, b, 0)
    if prezzo is None or (a.mq is not None and b.mq is not None and not mq_vicini(a, b)):
        return None
    piano_a, piano_b = numero_piano(a.piano), numero_piano(b.piano)
    if piano_a is not None and piano_b is not None and piano_a != piano_b:
        return None
    quanto = descrizione_copiata(a, b)
    if quanto is None or quanto < DESCRIZIONE_COPIATA:
        return None
    senza_via = " e ".join(x.portale for x in (a, b) if indirizzo_di(x) is None)
    return Motivo(Regola.DESCRIZIONE, f"{a.portale} {a.id} e {b.portale} {b.id}: via assente su {senza_via}, "
                                      f"descrizione uguale al {round(quanto * 100)}%, {prezzo}, mq {a.mq} e {b.mq}")


def motivo_coppia(a: Annuncio, b: Annuncio, modello: Set[tuple[str, str]] = frozenset()) -> Motivo | None:
    """Perché `a` e `b` sono lo stesso immobile, o None. `modello` sono le chiavi che `testi_modello`
    trova tra gli annunci raggruppati insieme: per quegli annunci la descrizione non vale."""
    if a.portale == b.portale:
        return None
    ia, ib = indirizzo_di(a), indirizzo_di(b)
    if ia is not None and ib is not None:
        return per_via(a, b, ia, ib)
    if chiave(a) in modello or chiave(b) in modello:
        return None
    return per_descrizione(a, b)


def raggruppa(annunci: list[Annuncio]) -> list[Immobile]:
    """Raggruppa per immobile. Due gruppi si fondono solo se non condividono un portale e ogni
    coppia di annunci tra i due soddisfa la regola, così la fusione non è mai transitiva.
    Le coppie si provano dalla regola più forte, così un gruppo che la via forma non cambia per
    una coppia unita per descrizione; a parità, prima prezzo e mq più vicini."""
    ordinati = sorted(annunci, key=chiave)
    modello = testi_modello(ordinati)
    coppie = [(a, b, motivo) for a, b in combinations(ordinati, 2) if (motivo := motivo_coppia(a, b, modello))]
    coppie.sort(key=lambda t: (t[2].regola, abs(t[0].prezzo - t[1].prezzo), abs((t[0].mq or 0) - (t[1].mq or 0)),
                               chiave(t[0]), chiave(t[1])))
    gruppo_di = {chiave(a): {"annunci": [a], "motivi": []} for a in ordinati}
    for a, b, motivo in coppie:
        ga, gb = gruppo_di[chiave(a)], gruppo_di[chiave(b)]
        if ga is gb or {x.portale for x in ga["annunci"]} & {x.portale for x in gb["annunci"]}:
            continue
        if not all(motivo_coppia(x, y, modello) for x in ga["annunci"] for y in gb["annunci"]):
            continue
        unito = {"annunci": sorted(ga["annunci"] + gb["annunci"], key=chiave),
                 "motivi": ga["motivi"] + gb["motivi"] + [motivo.testo]}
        for x in unito["annunci"]:
            gruppo_di[chiave(x)] = unito

    unici = {id(g): g for g in gruppo_di.values()}.values()
    immobili = [Immobile(tuple(g["annunci"]), "; ".join(g["motivi"]) or None) for g in unici]
    return sorted(immobili, key=lambda i: (min(a.prezzo or 0 for a in i.annunci), chiave(i.annunci[0])))
