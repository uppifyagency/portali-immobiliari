"""L'unico modulo che parla con Chrome: AppleScript via osascript, niente CDP.

Il CDP accende navigator.webdriver e DataDome risponde col CAPTCHA (prove/2026-10-28-sonda-immobiliare.md).
"""

import subprocess
import time

from .modello import ErrorePortale

ATTESA_MASSIMA_SECONDI = 45
ASSESTAMENTO_SECONDI = 1.5
INTERVALLO_CONTROLLO_SECONDI = 0.5

APRI = """
on run argv
  tell application "Google Chrome"
    set w to missing value
    repeat with candidata in windows
      try
        execute active tab of candidata javascript "1"
        set w to contents of candidata
        exit repeat
      end try
    end repeat
    if w is missing value then error "nessuna finestra Chrome accetta JavaScript da Apple Events: apri una finestra del profilo in cui l'hai spuntato"
    set t to make new tab at end of tabs of w with properties {URL:"about:blank"}
    return (id of w as text) & " " & (id of t as text)
  end tell
end run
"""

# Il segno sulla pagina vecchia distingue il documento nuovo da quello precedente, che è già "complete".
VAI = """
on run argv
  tell application "Google Chrome"
    set t to tab id (item 2 of argv as integer) of window id (item 1 of argv as integer)
    execute t javascript "window.__portaliVecchia = true"
    set URL of t to item 3 of argv
  end tell
end run
"""

ESEGUI = """
on run argv
  tell application "Google Chrome"
    set t to tab id (item 2 of argv as integer) of window id (item 1 of argv as integer)
    return execute t javascript (item 3 of argv)
  end tell
end run
"""

CHIUDI = """
on run argv
  tell application "Google Chrome"
    close tab id (item 2 of argv as integer) of window id (item 1 of argv as integer)
  end tell
end run
"""

STATO_CARICAMENTO = "window.__portaliVecchia ? 'vecchia' : document.readyState"


class ErroreBrowser(ErrorePortale):
    pass


def _osascript(script: str, *argomenti: str) -> str:
    esito = subprocess.run(["osascript", "-", *argomenti], input=script, capture_output=True, text=True)
    if esito.returncode != 0:
        # L'ultimo argomento dice su quale passo Chrome non ha risposto: il timeout AppleEvent (-1712) è
        # intermittente e senza questo non si capisce se si ferma sul caricamento o sulla fetch.
        passo = f" durante {argomenti[-1][:80]!r}" if argomenti else ""
        raise ErroreBrowser(
            f"osascript fallito{passo}: {esito.stderr.strip()}. Controlla che Chrome sia aperto e che "
            "Visualizza > Opzioni per sviluppatori > Consenti JavaScript da Eventi Apple sia attivo."
        )
    return esito.stdout.removesuffix("\n")


class Scheda:
    """Una tab nel Chrome vero dell'utente, aperta all'ingresso e chiusa all'uscita.

    Dopo un CAPTCHA la tab resta aperta, perché l'umano lo risolve proprio lì.
    """

    def __init__(self):
        self.captcha = False
        self._finestra = ""
        self._tab = ""

    def __enter__(self) -> "Scheda":
        self._finestra, self._tab = _osascript(APRI).split()
        return self

    def __exit__(self, *_) -> None:
        if self.captcha:
            return
        try:
            _osascript(CHIUDI, self._finestra, self._tab)
        except ErroreBrowser:
            pass  # una tab non chiusa non deve far perdere gli annunci già letti

    def esegui(self, js: str) -> str:
        return _osascript(ESEGUI, self._finestra, self._tab, js)

    def vai(self, url: str) -> None:
        _osascript(VAI, self._finestra, self._tab, url)
        scadenza = time.monotonic() + ATTESA_MASSIMA_SECONDI
        while self.esegui(STATO_CARICAMENTO) != "complete":
            if time.monotonic() > scadenza:
                raise ErroreBrowser(f"pagina non caricata entro {ATTESA_MASSIMA_SECONDI} secondi: {url}")
            time.sleep(INTERVALLO_CONTROLLO_SECONDI)
        time.sleep(ASSESTAMENTO_SECONDI)
