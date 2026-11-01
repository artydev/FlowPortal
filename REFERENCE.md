# Référence du YAML CSFlow
_Générée par `python engine.py --nodes --md` à partir des `@register` de `nodes/`._

## Fichier

- `name` : nom du workflow (affiché dans l'interface)
- `description` : description du workflow
- `inputs` : variables d'entrée, utilisables dans tout le workflow avec {{nom}}
- `steps` *(obligatoire)* : liste des étapes

## Mots-clés de toute étape

- `id` *(obligatoire)* : identifiant unique de l'étape ; son résultat s'utilise avec {{id.output}}
- `type` *(obligatoire)* : type d'étape (liste : python engine.py --nodes)
- `description` : libellé affiché dans l'interface
- `skip_if` : expression Python ; si elle est vraie, l'étape est ignorée. Ex : "'{{statut.output}}' != 'ROUGE'"
- `continue_on_error` : true = le workflow continue même si l'étape échoue
- `next` : id de l'étape suivante (défaut : l'étape suivante dans la liste)
- `on_approve` : human_approval : id de l'étape suivante si approuvé
- `on_reject` : human_approval : id de l'étape suivante si rejeté

## Types d'étape (valeurs de `type:`)


### agent
Envoie une instruction à CSAgent et suit son raisonnement en direct ; sortie : sa dernière réponse.

- `prompt` *(obligatoire)* : instruction en langage naturel envoyée à CSAgent
- `csagent_url` : URL de CSAgent (défaut http://localhost:8080)
- `csagent_key` : clé d'API (défaut demo-secret)
- `timeout` : secondes sans donnée avant abandon (défaut 180)
- `auto_confirm` : true = approuver automatiquement les demandes de confirmation de CSAgent

### end
Termine le workflow (étape finale, aucune étape ne peut la suivre).

- `message` : message affiché à la fin du workflow

### file_read
Lit un fichier texte ; sortie : output (le contenu).

- `path` *(obligatoire)* : chemin du fichier à lire (relatif au dossier workflow)

### file_write
Écrit (ou remplace) un fichier texte ; les dossiers manquants sont créés.

- `path` *(obligatoire)* : fichier à écrire (les dossiers sont créés)
- `content` *(obligatoire)* : texte à écrire

### http_get
Requête HTTP GET ; sorties : body, status_code.

- `url` *(obligatoire)* : adresse à appeler
- `headers` : en-têtes HTTP (dictionnaire)
- `timeout` : secondes avant abandon (défaut 30)

### http_post
Requête HTTP POST ; sorties : body, status_code.

- `url` *(obligatoire)* : adresse à appeler
- `body` : corps de la requête
- `headers` : en-têtes HTTP (dictionnaire)
- `timeout` : secondes avant abandon (défaut 30)

### human_approval
Met le workflow en pause jusqu'à ce qu'un humain approuve ou rejette.

- `message` : question posée à l'humain (défaut « Approve to continue? »)

### log
Affiche un message dans la console et dans le log de l'interface.

- `message` *(obligatoire)* : texte affiché (accepte les {{variables}})
- `level` : info | success | warn | error (défaut info)

### mcp_call
Appelle un outil d'un serveur MCP déclaré dans mcp.json (sans CSAgent) ; sortie : le texte rendu par l'outil.

- `server` *(obligatoire)* : nom du serveur dans le fichier de configuration (clé mcpServers)
- `tool` *(obligatoire)* : nom de l'outil à appeler
- `arguments` : arguments de l'outil : un objet YAML (les {{...}} sont remplacés), ou un texte JSON
- `config` : fichier de configuration MCP (défaut mcp.json)
- `timeout` : secondes d'attente de la réponse (défaut 60)
- `save_as` : fichier où écrire le résultat complet (chemin relatif)
- `max_chars` : longueur maximale de la sortie (défaut 50000) ; save_as garde tout

### mcp_tools
Liste les outils d'un serveur MCP déclaré dans mcp.json (sans CSAgent) ; sortie : un outil par ligne.

- `server` *(obligatoire)* : nom du serveur dans le fichier de configuration (clé mcpServers)
- `config` : fichier de configuration MCP (défaut mcp.json)
- `timeout` : secondes d'attente par requête (défaut 60)

### python
Exécute un script Python (fichier ou code inline) avec l'interpréteur embarqué : aucun Python à installer.

- `script` : chemin d'un fichier .py (relatif au dossier de travail)
- `code` : code Python inline (alternative à script)
- `args` : arguments passés au script (liste YAML ou chaîne)
- `cwd` : dossier d'exécution (défaut : dossier de travail)
- `timeout` : secondes avant arrêt du processus (défaut 60)

### shell
Exécute une commande shell (cmd.exe sous Windows, bash ailleurs).

- `cmd` *(obligatoire)* : commande exécutée par cmd.exe (Windows) ou bash ; sorties : output, stdout, stderr, returncode
- `timeout` : secondes avant arrêt du processus (défaut 60)
- `shell` : shell explicite : « bash », « sh » ou « pwsh » (défaut : le shell du système)

### transform
Évalue une expression Python pour transformer des données.

- `expression` *(obligatoire)* : expression Python évaluée ; les {{variables}} sont substituées avant

### user_input
Demande un texte à l'humain (saisie, interface graphique ou terminal) ; sortie : le texte, aussi écrit dans un fichier.

- `prompt` *(obligatoire)* : question posée à l'humain
- `default` : texte pré-rempli, modifiable (défaut : vide)
- `required` : true = refuse un texte vide (défaut true)
- `save_as` : fichier (chemin relatif) où écrire le texte (défaut tmp/saisie/<id de l'étape>.txt)
- `voice` : true = bouton micro : dictée transcrite par CSAgent (interface graphique ; défaut false)
- `language` : langue parlée : fr, en… ou auto (défaut fr)
- `keep_audio` : true = garde l'enregistrement et sa transcription dans recordings/ et transcripts/ (défaut false : supprimés)
- `csagent_url` : URL de CSAgent pour la dictée (défaut http://localhost:8080)
- `csagent_key` : clé d'API de CSAgent (défaut demo-secret)

## Variables {{…}}

- `{{today}}` : date du jour (AAAA-MM-JJ)
- `{{now}}` : date et heure (AAAA-MM-JJTHH:MM:SS)
- `{{env.NOM}}` : variable d'environnement NOM
- `{{id.output}}` : résultat de l'étape « id » ; autres champs : {{id.returncode}}, {{id.stdout}}, {{id.status}}... selon le type
- `{{nom}}` : variable déclarée dans `inputs`

## Client MCP : appeler un serveur sans CSAgent (`mcp_tools`, `mcp_call`)

CSFlow parle lui-même MCP (bibliothèque standard, rien à installer). Les serveurs sont déclarés par leur **nom** dans `mcp.json` (le même format que `csagent --mcp-config`, clé `mcpServers`) :

```json
{"mcpServers": {
  "fichiers": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
  "api":      {"url": "https://exemple.org/mcp", "headers": {"Authorization": "Bearer ${MON_JETON}"}}
}}
```

- Deux transports : **stdio** (`command`, `args`, `env`, `cwd`) et **HTTP** « Streamable HTTP » (`url`, `headers`). Le transport SSE historique n'est pas géré.
- `${NOM}` est remplacé par la variable d'environnement (une variable absente est une erreur, jamais une valeur vide) : les secrets restent hors du fichier.
- Un workflow ne peut désigner qu'un serveur **déclaré** : il ne lance pas de commande et ne vise pas d'adresse de son choix.
- Un serveur reste ouvert pendant le workflow (une seule connexion pour toutes les étapes) et est fermé à sa fin.
- Le résultat d'un outil est une **donnée** : avec `save_as`, il s'écrit dans un fichier qu'un agent relit, sans être collé dans son instruction.
- Le fichier `mcp.json` sert aussi à CSAgent : il peut contenir les deux usages (par exemple le serveur `csflow` de CSAgent et vos serveurs d'outils).

## Mode MCP (`CSFlow --mcp`)

Serveur MCP (stdio, JSON-RPC 2.0) qui expose les workflows du dossier `workflows/` à un client comme CSAgent.

| Outil | Paramètres | Réponse (JSON dans un bloc texte) |
|---|---|---|
| `csflow_list` | — | `cwd`, `workflows[]` : `file`, `name`, `description`, `steps`, `needs_human`, `uses_agent` |
| `csflow_check` | `workflow` | `ok`, `errors`, `warnings`, `issues[]` (`level`, `line`, `message`), `needs_human` |
| `csflow_run` | `workflow`, `wait_seconds` (≤ 55, défaut 50) | état du run, ou `started: false` + `reason` (`validation errors` + `issues`, run déjà en cours) ; erreur d'outil si `needs_human` |
| `csflow_status` | `run_id` (facultatif), `wait_seconds` | état du run |

État d'un run : `run_id`, `workflow`, `status` (`running` \| `done` \| `error`), `elapsed_s`, `progress`, `steps[]`
(`id`, `type`, `status`, `duration`, `returncode`, `output` tronquée), `current_step`, `agent_last_thought`, `next`,
`result`, `error`, `warnings` ; en échec : `failed_step` (`id`, `returncode`, `output` complète).

`workflow` = nom de fichier seul (`demo_report` ou `demo_report.yaml`) ; chemins, `..` et fichiers cachés refusés.
Un seul run à la fois ; un workflow avec `human_approval` ou `user_input` n'est jamais lancé par ce mode.

