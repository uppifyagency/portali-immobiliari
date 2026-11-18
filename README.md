# portali-immobiliari

Plugin per Claude Code che cerca annunci immobiliari su 13 portali italiani e li restituisce in una forma unica.
Un agente descrive la casa che cerca un cliente, Claude sceglie i portali, applica i filtri, unisce i doppioni
e propone poche case con i link.

Il plugin legge soltanto. Non pubblica annunci, non entra con nessun account e non contatta gli inserzionisti.

## Requisiti

- macOS. I portali con protezione antibot si leggono da una scheda di Google Chrome, comandata con `osascript`.
- Google Chrome, con la voce **Visualizza > Opzioni per sviluppatori > Consenti JavaScript da Eventi Apple** attiva.
- Python 3. Il plugin usa solo la libreria standard e non installa nulla. È provato con Python 3.12.

Su Linux manca `osascript`, quindi restano solo i portali con trasporto `http`. Questa combinazione non è provata.

## Installazione

```sh
git clone <url-del-repo> portali-immobiliari
claude --plugin-dir ./portali-immobiliari
```

Alla prima apertura chiedi "è tutto pronto?": la skill `benvenuto` prova ogni requisito e dice cosa manca.

## Portali

| Portale | Trasporto | Cosa copre |
|---|---|---|
| immobiliare | chrome | Il portale più grande, agenzie e privati |
| idealista | chrome | Secondo per numero di annunci, soprattutto agenzie |
| casait | chrome | Grande portale nazionale di agenzie |
| wikicasa | chrome | Portale nazionale, permette di escludere aste e nude proprietà |
| subito | chrome | Bacheca generalista, molti privati |
| cercacasa | http | Portale nazionale con filtri di dettaglio |
| casadaprivato | http | Solo annunci di privati |
| bakeca | chrome | Bacheca di annunci per provincia |
| tuttocasa | http | Piccolo portale con pochi filtri |
| properstar | chrome | Vetrina internazionale per acquirenti esteri |
| gateaway | chrome | Portale in inglese per acquirenti esteri, solo vendite |
| astalegale | chrome | Aste giudiziarie e vendite pubbliche |
| astegiudiziarie | http | Aste giudiziarie dei tribunali |

Il trasporto `chrome` esegue `fetch()` dentro una scheda del Chrome dell'utente. Il trasporto `http` usa `urllib`
e si presenta con un User-Agent fisso che dice chi è.

## Strumenti

Il server MCP in `server/server.py` espone sei strumenti:

| Strumento | Cosa fa |
|---|---|
| `dove_giro` | Dice su che sistema gira il server e se può comandare Chrome |
| `elenco_portali` | Elenca i portali con trasporto, descrizione e numero di filtri |
| `elenco_filtri` | Elenca i filtri che un portale accetta, con valori e combinazioni |
| `cerca` | Cerca su un portale e salva gli annunci in un file |
| `unisci` | Unisce più ricerche e riconosce la stessa casa su portali diversi |
| `dettagli` | Legge la scheda completa di un annuncio |

I file delle ricerche vanno in `~/portali-immobiliari/ricerche`. La variabile d'ambiente
`PORTALI_IMMOBILIARI_CARTELLA` cambia la cartella.

La skill `cerca-casa` contiene il metodo: come scegliere i portali, scrivere i filtri, scremare e presentare la
selezione.

## Come è fatto

- `motore/clienti/`: un client per portale. Traduce una richiesta nei parametri del portale e legge la lista.
- `motore/dettaglio/`: un lettore della scheda dell'annuncio per portale.
- `motore/filtri/`: una mappa JSON per portale. Ogni filtro porta il parametro del portale, i valori ammessi e
  la prova che il portale lo applica. La forma è descritta in `motore/filtri/SCHEMA.md`.
- `motore/portali/`: il modello dei dati, il comando di Chrome e l'unione dei doppioni.
- `motore/trasporto.py`: i due trasporti.

## Limiti

- Le mappe dei filtri sono state misurate tra ottobre e novembre 2026, quasi sempre su Trieste. I portali cambiano
  senza preavviso: un filtro provato allora può non valere più.
- Zone, esempi e valori predefiniti sono di Trieste. Gli altri comuni funzionano, ma sono meno provati.
- Questo repo non contiene test. Le prove citate nei campi `evidenza` e nei commenti (`prove/`, `risultati/`,
  `fixtures/`, `strumenti/`, `har/`) sono file del repo di sviluppo, che non è pubblico.

## Uso responsabile

Il plugin fa a mano più veloce quello che una persona fa nel proprio browser, una ricerca per volta.

- Non falsifica impronte, non ruota identità e non risolve CAPTCHA. Davanti a un blocco si ferma e lo dice.
- Non è pensato per raccolte di massa. Non usarlo per copiare o ripubblicare gli annunci di un portale.
- Ogni portale ha le proprie condizioni d'uso, che possono vietare l'accesso automatico. Chi usa il plugin è
  responsabile di leggerle e rispettarle.

Questo progetto non è affiliato a nessuno dei portali citati. I nomi dei portali appartengono ai rispettivi
proprietari.

## Licenza

MIT. Vedi [LICENSE](LICENSE).
