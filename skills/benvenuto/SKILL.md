---
name: benvenuto
description: Controlla che questo computer sia pronto per la ricerca sui portali immobiliari, dice cosa manca e cosa si può chiedere. Usala alla prima apertura, dopo un aggiornamento, o quando l'utente chiede "è tutto pronto?", "cosa manca?", "controlla l'installazione", "cosa sai fare?".
---

# Benvenuto

Controlla ogni voce della tabella, nell'ordine. Prova davvero ogni voce: non fidarti di quello che ricordi.

| Voce | Come la provi | Pronta quando |
|---|---|---|
| Chrome | chiedi allo strumento del browser l'elenco delle schede aperte | risponde |
| Cartella di lavoro | elenca la cartella collegata a questa sessione | la vedi e ci puoi scrivere |
| Ricerca sui portali | chiama `elenco_portali` | risponde, e nessun portale ha un errore |
| Comando del Mac | chiama `dove_giro` | `osascript` è "funziona" |

Queste prove non aprono nessun portale. Non lanciare una ricerca per provare.

Se per una voce non hai lo strumento, la voce è "manca". Non provare a installare nulla e non chiedere password o token nella chat.

Rispondi con una tabella di tre colonne: voce, esito (pronta o manca), cosa fare. Per ogni voce che manca scrivi una sola azione, in parole semplici, che una persona non tecnica può eseguire. Chiudi con una riga: "Tutto pronto" oppure "Mancano N voci".

## Cosa si può chiedere oggi

Dopo la tabella, dillo in due righe, con il numero di portali che `elenco_portali` ha restituito:

- **Trovare case per un cliente.** L'agente descrive cosa cerca il cliente (comune, budget, locali, desideri) e riceve una selezione con i link, cercata sui portali di annunci e, se serve, sulle aste giudiziarie. Il metodo è nella skill `cerca-casa`. Esempio da proporre: "Il cliente cerca un trilocale a Trieste fino a 250.000 euro, con ascensore e possibilmente vista mare".

Se la voce "Ricerca sui portali" manca, di' che la ricerca non è disponibile finché non è sistemata.
