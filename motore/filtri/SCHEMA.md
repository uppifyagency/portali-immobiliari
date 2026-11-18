# Mappa dei filtri: forma dei dati

Un file per portale: `motore/filtri/<portale>.json`.

```json
{
  "portale": "immobiliare",
  "descrizione": "una frase che aiuta a scegliere dove cercare: chi pubblica, quanti annunci, cosa lo distingue",
  "url_base_ricerca": "https://www.immobiliare.it/vendita-case/trieste/",
  "come_si_applicano": "query string | segmenti di percorso | misto",
  "fonte_catalogo": "dove sta la lista dei filtri nella pagina (es. __NEXT_DATA__ props.x.y, form HTML #filters)",
  "filtri": [
    {
      "chiave": "prezzo_max",
      "etichetta": "Prezzo massimo",
      "gruppo": "prezzo",
      "parametro": "prezzoMassimo",
      "posizione": "query",
      "tipo": "intero",
      "valori": [],
      "unita": "EUR",
      "esempio": "https://www.immobiliare.it/vendita-case/trieste/?prezzoMassimo=300000",
      "evidenza": "recording.har voce 0, __NEXT_DATA__ ... formStateAtom.prezzoMassimo",
      "verifica": "live"
    }
  ],
  "non_trovati": ["filtri che l'interfaccia mostra ma di cui l'HAR non dà il parametro"],
  "note": "limiti, dipendenze tra filtri (es. contratto cambia il percorso)"
}
```

Regole:
- `descrizione`: la mostra lo strumento `elenco_portali` del plugin. Facoltativa per `valida_filtri.py`, ma senza il portale
  compare senza descrizione.
- `chiave`: in snake_case italiano. Usa una chiave canonica quando il significato coincide: contratto, comune, zona, tipologia,
  prezzo_min, prezzo_max, mq_min, mq_max, locali_min, locali_max, bagni_min, piano, ascensore, balcone, terrazzo, giardino,
  box_auto, posto_auto, cantina, arredato, stato_immobile, classe_energetica, riscaldamento, aria_condizionata, vista_mare,
  nuova_costruzione, asta, nuda_proprieta, a_reddito, libero, da_privato, con_foto, con_planimetria, con_video,
  con_virtual_tour, pubblicato_da, ribassato, ordinamento, pagina, parola_chiave, raggio, mappa_area.
  Un filtro che non coincide con nessuna chiave canonica tiene una chiave propria descrittiva.
- `posizione`: "query" | "percorso" | "corpo".
- `tipo`: "intero" | "decimale" | "booleano" | "enum" | "multiplo" | "testo" | "intervallo".
- `valori`: per enum e multiplo, ogni valore ammesso come {"valore": "...", "etichetta": "..."}. Vuoto per intero e testo,
  ma se l'interfaccia offre soglie fisse (es. prezzo 50000, 100000...) elencale tutte.
- `verifica`: "live" (provato dal vivo e il risultato cambia come atteso) | "har" (parametro visto nell'HAR o nella
  configurazione della pagina) | "dedotto" (ragionamento, senza prova). Mai "live" senza una prova registrata.
- `evidenza`: dove l'hai letto, abbastanza preciso da ritrovarlo.

## Prova end to end (fase 4, 03/11/2026)

Ogni voce porta `prova`, un oggetto o una lista di oggetti:

```json
"prova": {"valore": 300000, "attesa": "meno", "comune": "Trieste", "contratto": "vendita"}
```

- `valore`: quello che si passa al client, `Client.cerca(comune, contratto, **{chiave: valore})`. Per enum e multiplo
  un `valore` della lista `valori` (o una lista di valori per multiplo).
- `attesa`: `meno` (il totale scende), `diverso` (totale o annunci cambiano), `ordine_asc` / `ordine_desc` (prezzi
  ordinati), `ordine_diverso` (stesso totale, ordine diverso), `pagina` (nessun id in comune con pagina 1).
- `comune` e `contratto` sono facoltativi (Trieste, vendita). Se a Trieste il filtro dà zero annunci, usa un comune
  più grande: una prova a zero è inconcludente.
- Le chiavi `comune`, `contratto`, `pagina` sono argomenti posizionali del client e si provano lo stesso.
- Per `contratto` e `tipologia` una prova per ogni valore.

`verifica: live` la scrive solo `strumenti/timbra_live.py`, leggendo `prove/prova_mappa/<portale>.tsv`. Ogni prova vale
per la sua impronta di comportamento (`strumenti/impronta.py`), registrata in `prove/prova_mappa/impronte.json`: se
cambiano le richieste che il client manda per quella prova o la lettura delle risposte salvate dei test, le righe fatte
prima tornano vecchie. Una prova di cui le risposte salvate non mostrano il filtro vale per il sorgente intero del client.

`assenti`: lista di `{"chiave": <canonica>, "evidenza": "..."}` per ogni chiave canonica che l'interfaccia del portale
non offre, con dove l'hai controllato (pagina, data, controllo cercato e non trovato).

Il predicato di fatto è `strumenti/cancello.py <portale>`.
