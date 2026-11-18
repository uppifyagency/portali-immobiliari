"""Lettura della pagina di dettaglio di idealista.it. La ricerca la fa motore/clienti/idealista.py."""
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from ..portali.modello import Dettaglio, ErrorePortale, adesso_utc


class ErroreEstrazione(ErrorePortale):
    pass


_VUOTI = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class _Nodo:
    tag: str
    attributi: dict[str, str]
    figli: list["_Nodo | str"] = field(default_factory=list)

    def ha_classe(self, classe: str) -> bool:
        return classe in self.attributi.get("class", "").split()


class _Albero(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.radice = _Nodo("radice", {})
        self._pila = [self.radice]

    def handle_starttag(self, tag, attrs):
        nodo = _Nodo(tag, {nome: valore or "" for nome, valore in attrs})
        self._pila[-1].figli.append(nodo)
        if tag not in _VUOTI:
            self._pila.append(nodo)

    def handle_endtag(self, tag):
        for livello in range(len(self._pila) - 1, 0, -1):
            if self._pila[livello].tag == tag:
                del self._pila[livello:]
                return

    def handle_data(self, data):
        if self._pila[-1].tag not in ("script", "style"):
            self._pila[-1].figli.append(data)


def _nodi(nodo: _Nodo):
    for figlio in nodo.figli:
        if isinstance(figlio, _Nodo):
            yield figlio
            yield from _nodi(figlio)


def _tutti(nodo: _Nodo | None, tag: str | None = None, classe: str | None = None) -> list[_Nodo]:
    if nodo is None:
        return []
    return [n for n in _nodi(nodo) if (tag is None or n.tag == tag) and (classe is None or n.ha_classe(classe))]


def _primo(nodo: _Nodo | None, tag: str | None = None, classe: str | None = None) -> _Nodo | None:
    if nodo is None:
        return None
    return next((n for n in _nodi(nodo) if (tag is None or n.tag == tag) and (classe is None or n.ha_classe(classe))), None)


def _testo(nodo: _Nodo | None) -> str | None:
    if nodo is None:
        return None
    pezzi = []

    def raccogli(n: _Nodo) -> None:
        for figlio in n.figli:
            if isinstance(figlio, str):
                pezzi.append(figlio)
            else:
                pezzi.append(" ")
                raccogli(figlio)
                pezzi.append(" ")

    raccogli(nodo)
    return " ".join("".join(pezzi).split()) or None


_CLASSE_ENERGETICA = re.compile(r"icon-energy-([a-g]\d?)\b")


def _caratteristiche_del_dettaglio(radice: _Nodo) -> list[str]:
    voci = []
    for blocco in _tutti(radice, "div", "details-property_features"):
        for voce in _tutti(blocco, "li"):
            # La lettera della classe energetica c'è solo come classe dell'icona (icon-energy-c-12), non come testo.
            for icona in _tutti(voce, "span"):
                trovato = _CLASSE_ENERGETICA.search(icona.attributi.get("class", ""))
                if trovato:
                    icona.figli.append(trovato.group(1).upper())
            if testo := _testo(voce):
                voci.append(testo)
    return voci


def _voci_prezzo(radice: _Nodo) -> dict[str, str]:
    voci = {}
    for riga in _tutti(_primo(radice, "section", "price-features__container"), "p", "flex-feature"):
        parti = [_testo(n) for n in _tutti(riga, classe="flex-feature-details")]
        if len(parti) == 2 and parti[0] and parti[1]:
            voci[parti[0].rstrip(":").strip()] = parti[1]
    return voci


_URL_IMMOBILE = re.compile(r"/immobile/(\d+)/")


def dati_da_html(html: str) -> dict:
    """Riduce la pagina di dettaglio di idealista a un dizionario semplice, salvabile come JSON."""
    albero = _Albero()
    albero.feed(html)
    albero.close()
    radice = albero.radice

    canonico = next((n.attributi.get("href", "") for n in _tutti(radice, "link")
                     if n.attributi.get("rel") == "canonical"), "")
    trovato = _URL_IMMOBILE.search(canonico)
    if not trovato:
        raise ErroreEstrazione("la pagina non è un dettaglio di idealista.it: struttura cambiata o pagina sbagliata")
    paragrafi = [_testo(p) for p in _tutti(_primo(radice, "div", "comment"), "p")]
    agenzia = _primo(radice, "a", "about-advertiser-name")
    return {
        "pagina": "dettaglio",
        "id": trovato.group(1),
        "descrizione": "\n\n".join(p for p in paragrafi if p) or None,
        "caratteristiche": _caratteristiche_del_dettaglio(radice),
        "voci_prezzo": _voci_prezzo(radice),
        "agenzia": _testo(agenzia),
    }


def _pagina(dati: dict, attesa: str) -> dict:
    if not isinstance(dati, dict) or dati.get("pagina") != attesa:
        trovata = dati.get("pagina") if isinstance(dati, dict) else type(dati).__name__
        raise ErroreEstrazione(f"attesa una pagina di {attesa} di idealista, trovata {trovata!r}")
    return dati


def _cerca_voce(voci: list[str], schema: str) -> str | None:
    return next((v for v in voci if re.search(schema, v, re.IGNORECASE)), None)


def estrai_dettaglio(dati: dict, id_atteso: str | None = None, letto_il: str | None = None) -> Dettaglio:
    dettaglio = _pagina(dati, "dettaglio")
    if id_atteso is not None and dettaglio["id"] != id_atteso:
        raise ErroreEstrazione(f"la pagina è dell'annuncio {dettaglio['id']}, non di {id_atteso}")
    voci = dettaglio["caratteristiche"]
    anno = re.search(r"Costruito nel (\d{4})", _cerca_voce(voci, r"^Costruito nel ") or "")
    energia = _cerca_voce(voci, r"^Classe energetica:")
    classe = energia.split(":", 1)[1].split(" (")[0].strip() if energia else None
    return Dettaglio(
        descrizione=dettaglio["descrizione"],
        piano=_cerca_voce(voci, r"^(\d+º piano|piano |attico|seminterrato|interrato|ammezzato)"),
        spese_condominio=dettaglio["voci_prezzo"].get("Spese condominiali"),
        classe_energetica=classe or None,
        anno_costruzione=anno.group(1) if anno else None,
        stato=_cerca_voce(voci, r"^(buono stato|ottimo stato|ristrutturato|da ristrutturare|nuova costruzione)$"),
        caratteristiche=voci,
        letto_il=letto_il or adesso_utc(),
    )
