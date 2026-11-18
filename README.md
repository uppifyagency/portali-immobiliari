<p align="center">
  <img src="assets/hero.svg" alt="portali-immobiliari: una ricerca, tredici portali" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licenza MIT" src="https://img.shields.io/badge/licenza-MIT-f4a261?style=flat-square"></a>
  <img alt="Python 3, solo libreria standard" src="https://img.shields.io/badge/python-solo%20libreria%20standard-3a4a6e?style=flat-square">
  <img alt="Plugin per Claude Code" src="https://img.shields.io/badge/Claude%20Code-plugin-3a4a6e?style=flat-square">
  <img alt="macOS" src="https://img.shields.io/badge/macOS-Chrome-3a4a6e?style=flat-square">
</p>

<p align="center">
  <a href="#in-due-minuti">In due minuti</a> ·
  <a href="#come-lavora">Come lavora</a> ·
  <a href="#portali">Portali</a> ·
  <a href="#strumenti">Strumenti</a> ·
  <a href="#uso-responsabile">Uso responsabile</a>
</p>

<br>

<h3 align="center">Descrivi la casa che cerca il tuo cliente.<br>Claude la cerca su 13 portali e ti porta poche case scelte.</h3>

<br>

> "Il cliente cerca un trilocale a Trieste fino a 250.000 euro, con ascensore e possibilmente vista mare."

Il plugin traduce una richiesta come questa nei filtri di ogni portale, legge gli annunci, riconosce la stessa
casa pubblicata su più portali e risponde con una selezione ragionata, ognuna con i suoi link.

Legge soltanto. Non pubblica annunci, non entra con nessun account e non contatta gli inserzionisti.

## In due minuti

```sh
git clone https://github.com/uppifyagency/portali-immobiliari.git
claude --plugin-dir ./portali-immobiliari
```

Poi chiedi a Claude:

```text
è tutto pronto?
```

La skill `benvenuto` prova ogni requisito e dice cosa manca. Quando è tutto pronto, descrivi la casa che cerchi.

**Requisiti**

- macOS. I portali con protezione antibot si leggono da una scheda di Google Chrome, comandata con `osascript`.
- Google Chrome, con la voce **Visualizza > Opzioni per sviluppatori > Consenti JavaScript da Eventi Apple** attiva.
- Python 3. Il plugin usa solo la libreria standard e non installa nulla. È provato con Python 3.12.

Su Linux manca `osascript`, quindi restano solo i portali con trasporto `http`. Questa combinazione non è provata.

## Come lavora

<table>
<tr>
<td width="25%" valign="top"><h4>1. Capisce</h4>Separa i vincoli duri (budget, locali, ascensore) dalle preferenze (vista mare, piano alto).</td>
<td width="25%" valign="top"><h4>2. Cerca</h4>Sceglie i portali adatti e passa a ognuno solo i filtri che quel portale applica davvero.</td>
<td width="25%" valign="top"><h4>3. Unisce</h4>Riconosce la stessa casa su portali diversi e la propone una volta, con tutti i link.</td>
<td width="25%" valign="top"><h4>4. Propone</h4>Da 3 a 5 case in ordine, con il perché e con quello che resta da verificare.</td>
</tr>
</table>

Tre cose lo distinguono da uno scraper:

- **Filtri provati, non indovinati.** Ogni filtro di ogni portale porta la prova che il portale lo applica. Un
  filtro che il portale ignora non viene passato: diventa un controllo sugli annunci.
- **Un dato che manca resta mancante.** Il plugin non deduce la vista mare da "luminoso". Quello che l'annuncio
  non dice finisce sotto "da verificare".
- **Davanti a un blocco si ferma.** Niente CAPTCHA risolti, niente impronte false, niente tentativi ripetuti.

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

```text
portali-immobiliari/
├── server/server.py     server MCP su stdio
├── motore/
│   ├── clienti/         un client per portale: richiesta -> parametri del portale -> lista
│   ├── dettaglio/       un lettore della scheda dell'annuncio per portale
│   ├── filtri/          una mappa JSON per portale, con la prova di ogni filtro
│   ├── portali/         modello dei dati, comando di Chrome, unione dei doppioni
│   └── trasporto.py     i due trasporti: chrome e http
└── skills/              benvenuto, cerca-casa
```

La forma delle mappe dei filtri è descritta in [`motore/filtri/SCHEMA.md`](motore/filtri/SCHEMA.md).

## Limiti

- Le mappe dei filtri sono state misurate tra ottobre e novembre 2026, quasi sempre su Trieste. I portali cambiano
  senza preavviso: un filtro provato allora può non valere più.
- Zone, esempi e valori predefiniti sono di Trieste. Gli altri comuni funzionano, ma sono meno provati.
- Questo repo non contiene test. Le prove citate nei campi `evidenza` e nei commenti (`prove/`, `risultati/`,
  `fixtures/`, `strumenti/`, `har/`) sono file del repo di sviluppo, che non è pubblico.

## Uso responsabile

Il plugin fa più in fretta quello che una persona fa nel proprio browser, una ricerca per volta.

- Non falsifica impronte, non ruota identità e non risolve CAPTCHA. Davanti a un blocco si ferma e lo dice.
- Non è pensato per raccolte di massa. Non usarlo per copiare o ripubblicare gli annunci di un portale.
- Ogni portale ha le proprie condizioni d'uso, che possono vietare l'accesso automatico. Chi usa il plugin è
  responsabile di leggerle e rispettarle.

Questo progetto non è affiliato a nessuno dei portali citati. I nomi dei portali appartengono ai rispettivi
proprietari.

## Licenza

MIT. Vedi [LICENSE](LICENSE).
