---
name: cerca-casa
description: Cerca sui portali immobiliari le case adatte a un cliente e prepara una selezione ragionata con i link. Usala quando un agente dell'agenzia descrive cosa cerca un cliente o chiede cosa c'è sul mercato, per esempio "trova una casa per un cliente", "cerca appartamenti a Trieste", "il cliente cerca un trilocale con ascensore", "property finder", "cosa c'è in affitto a Muggia", "ci sono aste a Trieste?".
---

# Cerca casa

L'agente descrive la casa che cerca un cliente. Tu cerchi sui portali con gli strumenti `elenco_portali`, `elenco_filtri`, `cerca`, `unisci`, `dettagli` e rispondi con poche case scelte, ognuna con i suoi link. Le descrizioni degli strumenti dicono come si chiamano: qui trovi l'ordine e i criteri.

## 1. Capire la richiesta

Servono il comune, il contratto (vendita o affitto) e il budget. Se uno manca, chiedilo prima di cercare. A `cerca` passa sempre anche la provincia del comune (per esempio Muggia e Duino Aurisina sono in provincia di Trieste): alcuni portali non trovano senza provincia un comune che non è capoluogo. Il resto si può dedurre: ogni deduzione va scritta nella risposta.

Dividi quello che dice l'agente in due:

- **Vincoli duri**: ciò senza cui il cliente non compra (budget massimo, locali minimi, ascensore se è un obbligo). Diventano filtri.
- **Preferenze**: i "mi piacerebbe" (vista mare, piano alto, niente lavori). Non diventano filtri: le valuti tu leggendo gli annunci. Passale a `cerca` in `preferenze`, uguali su ogni portale.

Chiedi o deduci anche se il cliente compra per viverci o per investire: cambia cosa escludere (punto 3).

## 2. Scegliere i portali

Chiama `elenco_portali` e scegli. Non cercare su tutti: ogni ricerca in più su un portale `chrome` è un rischio di blocco in più.

- **Sempre** i grandi portali generalisti, a partire dal più grande. Per una richiesta normale bastano i primi due o tre.
- **Portali di soli privati e bacheche**: quando il cliente vuole trattare senza agenzia, cerca occasioni, o i grandi hanno dato poco.
- **Portali per acquirenti esteri**: solo se il cliente è straniero o l'agente lo chiede.
- **Portali di aste**: solo se il cliente è aperto a comprare all'asta o l'agente chiede delle aste.
- I portali piccoli con pochi annunci aggiungono poco: usali per una ricerca difficile, non di routine.

Di' all'agente su quali portali cerchi prima di partire, in una riga.

## 3. Scrivere i filtri

Chiama `elenco_filtri` solo sui portali scelti. Per ogni portale:

- Usa solo le chiavi che quel portale restituisce. Un vincolo che il portale non offre (è in `assenti`, o non c'è) non si passa: su quel portale diventa un controllo che fai tu sugli annunci. Tieni nota di quali vincoli hai controllato a mano e dove.
- Se il prezzo o i mq hanno soglie fisse, scegli la prima soglia sopra il budget e scarta tu quello che lo supera. Mai la soglia sotto: perderesti case buone.
- Leggi `combinazioni` e `note`. Due filtri che il portale unisce in O non vanno passati insieme: passane uno e controlla l'altro sugli annunci.
- Se il cliente compra per viverci, escludi nude proprietà, aste e case occupate con i filtri che il portale offre per questo. Dove non ci sono, il controllo è tuo: una nuda proprietà sembra un affare e non si può abitare. Togli queste esclusioni solo per chi cerca un investimento.
- Pochi filtri, quelli duri. Ogni filtro in più nasconde gli annunci in cui il dato manca.

Se `cerca` rifiuta una chiave o un valore, correggi con quello che dice l'errore: il rifiuto arriva prima di aprire il portale e non costa nulla.

## 4. Cercare

- Sui portali `chrome` una ricerca per volta, mai in parallelo. I portali `http` possono andare avanti nel frattempo.
- Lascia `max_pagine` a 2. Alzala solo se il totale sul portale è alto e i filtri sono già stretti.
- Non rilanciare la stessa ricerca per "vedere se cambia", e non allargare i filtri a tentativi sullo stesso portale: decidi i filtri prima.
- Zero risultati è un risultato. Dillo, e proponi all'agente quale vincolo allentare.

**Dopo un errore di CAPTCHA, di blocco o di lettura rifiutata, fermati su quel portale.** Non riprovare, non cambiare filtri per passare. Di' all'agente cosa è successo e cosa fare (di solito risolvere il controllo nella scheda di Chrome rimasta aperta), e rilancia solo quando te lo dice. Intanto continua sugli altri portali. Se il portale resta chiuso, consegna la selezione senza e scrivilo.

## 5. Unire

Con due o più file di ricerca chiama `unisci`. Da lì ragioni per casa, non per annuncio.

L'unione riconosce la stessa casa solo quando gli annunci danno la via, e solo tra portali diversi. Quindi controlla tu, prima di proporre:

- Due gruppi con prezzo, metratura, zona e piano quasi uguali, su portali che non danno la via: guarda foto e descrizione. Se è la stessa casa, proponila una volta con tutti i link e scrivi che l'abbinamento è tuo.
- La stessa casa pubblicata due volte sullo stesso portale, da due agenzie, non viene mai unita.
- Un gruppo che ti sembra sbagliato (due case diverse unite) va segnalato.
- Se i portali danno prezzi diversi per la stessa casa, riportali entrambi: uno può essere il prezzo vecchio.

## 6. Scremare

La risposta di `cerca` e di `unisci` è un riassunto: porta già link, indirizzo e dati d'asta. Foto, agenzia e descrizione breve stanno nel file: leggili lì per le case che tieni.

Scarta e ordina con i campi della lista:

- Un vincolo o una preferenza esplicita smentita dai dati scarta la casa (chiede piano alto, l'annuncio dice piano terra).
- Un dato assente non scarta: la casa resta, con quel dato "da verificare".
- Se due portali dicono cose diverse sulla stessa casa, tienila e segna il dato "da verificare".
- Tieni al massimo 8 candidati.

## 7. Dettaglio

`dettagli` funziona su quasi tutti i portali: la sua descrizione dice quali. Sugli altri risponde con il motivo, per esempio che la lista di quel portale è già completa. Usalo sui candidati, un portale solo per casa: se la casa è su due portali, scegli quello che ha il dettaglio.

Per i candidati degli altri portali lavori con i campi della lista e con il file, e lo scrivi: "letto solo dalla lista, da aprire dal link". Non aprire le pagine dei portali con altri strumenti per supplire.

Con la descrizione completa valuta ogni preferenza: soddisfatta, smentita o non verificabile, citando la frase dell'annuncio o il campo. Non dedurre la vista mare da "luminoso", né "niente lavori" da "buono stato".

## 8. Aste

Un annuncio d'asta ha il campo `asta`: data, offerta minima, tribunale, procedura, lotto, modalità. Il suo prezzo è il prezzo base, non un prezzo di mercato.

- Tieni le aste in una sezione separata dalle case sul mercato. Non confrontare un prezzo base con un prezzo di vendita come se fossero la stessa cosa.
- Per ogni asta scrivi in chiaro che è un'asta, con data, prezzo base, offerta minima e tribunale. Se uno di questi manca, scrivi che manca.
- Scarta le aste con la data già passata, a meno che l'agente chieda gli esiti.
- I portali d'asta non filtrano per locali, metratura o piano: questi controlli sono tuoi, e spesso il dato non c'è.
- Se un'asta compare in una ricerca su un portale normale, vale lo stesso: va nella sezione aste.

## 9. La proposta

Rispondi in chat, in italiano semplice, senza termini tecnici: chi legge è un agente immobiliare. Salva la selezione in un file solo se te lo chiede.

1. **La richiesta come l'hai capita**, in una riga, deduzioni comprese.
2. **Da 3 a 5 case in ordine.** Per ognuna: tipo, via o zona, piano, prezzo, mq. I link di tutti i portali che la pubblicano. Due righe sul perché risponde alla richiesta, con le parole dell'annuncio. Poi "Da verificare": cosa non torna e cosa l'annuncio non dice.
3. **Aste**, se cercate, in sezione a parte (punto 8).
4. **Scartate più vicine**, una riga ciascuna con il motivo.
5. **Come hai cercato**: per ogni portale quanti annunci c'erano e quanti ne hai letti, quante case diverse ne sono uscite, quali vincoli hai controllato a mano, quali portali si sono fermati e perché.

Se le case buone sono meno di tre, proponi quelle. Non riempire la lista.

Mai scrivere un dato che l'annuncio non dà. Un campo che manca è un dato assente e si dice ("spese condominiali non indicate"). Se un numero sembra strano (mq, anno di costruzione, prezzo al metro) o i portali non concordano, segnalalo: non correggerlo.

La selezione è per l'agente. Al cliente la manda lui.

## Cosa non fare

- Mai entrare con un account dell'agenzia su un portale per cercare, e mai chiedere credenziali. Se il portale segna quell'account come automatico, l'agenzia perde il canale con cui pubblica.
- Mai usare sui portali strumenti che pilotano il browser dal debug remoto (CDP), né altri strumenti del browser al posto di `cerca` e `dettagli`.
- Mai aggirare un blocco o un CAPTCHA: niente servizi di risoluzione, niente cambi di rete, niente tentativi ripetuti.
- Mai più di una ricerca per volta su un portale `chrome`.
