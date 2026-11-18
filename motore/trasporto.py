"""Trasporti per i client dei portali.

ChromeTrasporto esegue fetch() dentro una tab del Chrome vero (AppleScript, niente CDP): stessi cookie, stessa
impronta TLS del browser dell'utente. HttpTrasporto usa urllib della libreria standard, per i portali senza antibot,
e si presenta con un User-Agent fisso che dice chi è. Nessuno dei due falsifica impronte o ruota identità.
"""
from __future__ import annotations

import json
import random
import ssl
import subprocess
import time
import uuid
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from .portali.browser import ErroreBrowser, Scheda
from .portali.modello import Esito


@dataclass
class Risposta:
    status: int
    testo: str
    url: str

    def json(self):
        return json.loads(self.testo)


class Bloccato(Exception):
    """Il portale ha risposto con una pagina antibot. Non si riprova: lo risolve una persona."""


SEGNI_BLOCCO = ("captcha-delivery.com", "challenges.cloudflare.com/turnstile", "<title>Just a moment")


def _controlla(r: Risposta) -> Risposta:
    if r.status in (401, 403, 429) or any(s in r.testo[:5000] for s in SEGNI_BLOCCO):
        raise Bloccato(f"{r.status} su {r.url}")
    return r


class ChromeTrasporto:
    """Una tab del Chrome vero aperta su `origine`; ogni get() è una fetch() della pagina stessa."""

    def __init__(self, origine: str, pausa_secondi: float = 2.0, altre_origini: tuple[str, ...] = ()):
        self.origine = origine.rstrip("/")
        self.ammessi = {urlsplit(o).netloc for o in (origine, *altre_origini)}
        self.pausa = pausa_secondi
        self._scheda = Scheda()

    def __enter__(self):
        self._scheda.__enter__()
        try:
            self._scheda.vai(self.origine + "/")
        except ErroreBrowser:
            self._scheda.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        self._scheda.__exit__(*exc)

    def get(self, percorso: str, params: dict | None = None, intestazioni: dict | None = None) -> Risposta:
        return self._fetch("GET", percorso, params, None, intestazioni)

    def post(self, percorso: str, corpo: dict | str, intestazioni: dict | None = None) -> Risposta:
        """La stessa POST che la pagina del portale fa da sola. Un dict va come JSON."""
        return self._fetch("POST", percorso, None, corpo, intestazioni)

    def _fetch(self, metodo, percorso, params, corpo, intestazioni) -> Risposta:
        url = percorso if percorso.startswith("http") else self.origine + percorso
        if params:
            url += ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        host = urlsplit(url).netloc
        if host not in self.ammessi:
            raise ValueError(f"{url}: host non dichiarato per la tab {self.origine}")
        testa = intestazioni or {"Accept": "application/json, text/plain, */*"}
        if isinstance(corpo, dict):
            corpo, testa = json.dumps(corpo), {"Content-Type": "application/json", **testa}
        opzioni = {"method": metodo, "headers": testa,
                   "credentials": "include" if host == urlsplit(self.origine).netloc else "same-origin"}
        if corpo is not None:
            opzioni["body"] = corpo
        chiave = "__f" + uuid.uuid4().hex[:8]
        js = (
            f"window.{chiave}=null;fetch({json.dumps(url)},{json.dumps(opzioni)})"
            f".then(async r=>{{window.{chiave}=JSON.stringify({{s:r.status,t:await r.text(),u:r.url}})}})"
            f".catch(e=>{{window.{chiave}=JSON.stringify({{s:0,t:String(e),u:{json.dumps(url)}}})}});'ok'"
        )
        self._scheda.esegui(js)
        for _ in range(60):
            time.sleep(0.5)
            v = self._scheda.esegui(f"window.{chiave} || ''")
            if v:
                d = json.loads(v)
                self._scheda.esegui(f"delete window.{chiave}")
                time.sleep(self.pausa)
                try:
                    return _controlla(Risposta(d["s"], d["t"], d["u"]))
                except Bloccato:
                    self._mostra_blocco(metodo, url)
                    raise
        raise TimeoutError(f"fetch senza risposta in 30 s: {url}")

    def _mostra_blocco(self, metodo: str, url: str) -> None:
        """La tab resta aperta sull'indirizzo rifiutato: il rifiuto è arrivato a una fetch, e senza caricare
        quell'indirizzo l'umano troverebbe la pagina d'ingresso, senza nessuna verifica da risolvere."""
        self._scheda.captcha = True
        if metodo != "GET":
            return
        try:
            self._scheda.vai(url)
        except ErroreBrowser:
            pass


USER_AGENT = "portali-immobiliari-plugin/0.3"
# I caratteri che requests lascia com'erano quando ripulisce un URL (requote_uri): il resto si codifica.
SICURI_URL = "!#$%&'()*+,/:;=?@[]~"
CERTIFICATI_MACOS = Path("/etc/ssl/cert.pem")
RADICI_MACOS = Path("/System/Library/Keychains/SystemRootCertificates.keychain")


def _contesto_tls() -> ssl.SSLContext:
    """Il Python di python.org su macOS parte senza autorità di certificazione: si caricano quelle del sistema.

    /etc/ssl/cert.pem resta indietro rispetto al portachiavi: il 17/11/2026 non aveva la radice Sectigo R46 del
    certificato nuovo di astalegale.net, che il portachiavi ha. Si caricano tutti e due."""
    contesto = ssl.create_default_context()
    if not contesto.cert_store_stats()["x509_ca"]:
        if CERTIFICATI_MACOS.exists():
            contesto.load_verify_locations(CERTIFICATI_MACOS)
        if RADICI_MACOS.exists():
            radici = subprocess.run(["security", "find-certificate", "-a", "-p", str(RADICI_MACOS)],
                                    capture_output=True, text=True)
            if radici.returncode == 0 and radici.stdout:
                contesto.load_verify_locations(cadata=radici.stdout)
    return contesto


class HttpTrasporto:
    def __init__(self, origine: str, pausa_secondi: float = 2.0, altre_origini: tuple[str, ...] = ()):
        self.origine = origine.rstrip("/")
        self.pausa = pausa_secondi
        self._apri = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=_contesto_tls())).open
        self._testa = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "it-IT,it;q=0.9"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def get(self, percorso: str, params: dict | None = None, intestazioni: dict | None = None) -> Risposta:
        url = self._url(percorso)
        if params:
            url += ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        return self._invia(Request(url, headers={**self._testa, **(intestazioni or {})}, method="GET"))

    def post(self, percorso: str, corpo: dict | str, intestazioni: dict | None = None) -> Risposta:
        testa = {**self._testa, **(intestazioni or {})}
        if isinstance(corpo, dict):
            corpo, testa = json.dumps(corpo), {"Content-Type": "application/json", **testa}
        return self._invia(Request(self._url(percorso), data=corpo.encode(), headers=testa, method="POST"))

    def _url(self, percorso: str) -> str:
        url = percorso if percorso.startswith("http") else self.origine + percorso
        if urlsplit(url).netloc != urlsplit(self.origine).netloc:
            raise ValueError(f"{url} non è sull'origine {self.origine}")
        return quote(url, safe=SICURI_URL)

    def _invia(self, richiesta: Request) -> Risposta:
        try:
            with self._apri(richiesta, timeout=30) as r:
                risposta = Risposta(r.status, _testo(r.read(), r.headers.get_content_charset()), r.url)
        except HTTPError as e:
            risposta = Risposta(e.code, _testo(e.read(), e.headers.get_content_charset()), e.url)
        time.sleep(self.pausa)
        return _controlla(risposta)


def _testo(corpo: bytes, charset: str | None) -> str:
    return corpo.decode(charset or "utf-8", errors="replace")


TRASPORTI = {"chrome": ChromeTrasporto, "http": HttpTrasporto}
# Secondi tra due pagine: sui portali con antibot il passo di una persona, sugli altri solo cortesia.
PAUSE = {"chrome": (8, 20), "http": (3, 6)}
dormi = time.sleep


def apri(client: type, origine: str | None = None) -> ChromeTrasporto | HttpTrasporto:
    """Il trasporto del client, sulla sua origine o su `origine`, con le altre origini che il client dichiara."""
    return TRASPORTI[client.TRASPORTO](origine or client.ORIGINE, altre_origini=tuple(getattr(client, "ALTRE_ORIGINI", ())))


def pausa(tipo_trasporto: str) -> None:
    dormi(random.uniform(*PAUSE[tipo_trasporto]))


def esito_del_blocco(portale: str, tipo_trasporto: str, dove: str, blocco: Bloccato) -> tuple[Esito, str]:
    """Su chrome il blocco è una verifica che una persona risolve nella tab; su http il portale chiede di rallentare."""
    if tipo_trasporto == "chrome":
        return Esito.CAPTCHA, f"verifica antibot {dove} ({blocco})"
    return Esito.ERRORE, (f"{portale} ha rifiutato la lettura {dove} ({blocco}). "
                          f"Non riprovare subito: il portale ha chiesto di rallentare.")
