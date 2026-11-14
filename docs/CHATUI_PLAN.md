# FlowPortal — Chat UI : Document de référence & plan d'intégration

> **Statut :** Document de travail — à mettre à jour au fil de l'avancement.
> **Branche :** `chatui` (créée depuis `main`, poussée sur `origin/chatui`).
> **Dernière mise à jour :** session 6 (état réel des jalons et backlog ordonné, §5 bis et §9).
> **Public visé :** utilisateurs **non techniciens** (le mode développeur reste disponible pour les techniciens).

Ce document capture les décisions, recommandations et le plan d'intégration pour
ajouter une **interface conversationnelle (chat)** à FlowPortal, pensée pour les
**non-techniciens**. Il sert de point de référence si la conversation est interrompue
ou reprise plus tard.

---

## 1. Contexte du projet

**FlowPortal** est un portail local de workflows construit sur le moteur **CSFlow**
(Python × CSAgent), avec une interface de bureau **Eel**. Chaque workflow est une
carte : on le lance en un clic, on lit/modifie son YAML, on retrouve le résultat de
chaque lancement.

- **Stack :** Python (`engine.py`, `portal.py`, `assist.py`, `schema.py`, `mcp_client.py`, `mcp_server.py`) + UI web (`ui/portal.html`, `ui/portal.css`, scripts JS).
- **Dépendances :** `pyyaml` + `eel` uniquement.
- **Langue :** français (README, UI).
- **Tests :** 14 fichiers dans `tests/` (engine, portal, MCP, assist, delete, rename, stop, schema, UI).

### État actuel (déjà en place)
- `assist.py` : assistant de création/modification de workflows. L'IA (CSAgent) édite
  un **brouillon** (`tmp/assist/<id>/draft.yaml`), jamais le vrai workflow. Le YAML est
  validé par `schema.check` (relance auto en cas d'erreur, 2 fois max). Le portail
  montre les différences, l'utilisateur décide d'appliquer.
- `portal.py` : cartes, prérequis, lecture/écriture validée du YAML, historique des lancements.
- `engine.py` : `WorkflowEngine(..., inputs={...})`.

---

## 2. Objectif

Ajouter une **interface de chat** où l'utilisateur décrit son besoin en **langage
naturel**, et FlowPortal (via CSAgent) trouve ou crée le bon workflow, le montre de
façon **compréhensible**, et l'exécute — **sans que l'utilisateur ne touche au YAML**.

Deux publics, deux vues :
- **Vue simple** (non-techniciens) : étapes en langage clair, pas de jargon.
- **Vue développeur** : workflow complet, YAML, types d'étapes, chemins.

---

## 3. Décisions clés (validées)

1. **Créer/modifier un workflow ne doit JAMAIS passer par l'édition YAML pour un non-technicien.** Il décrit son besoin ; l'IA traduit en structure technique ; l'utilisateur valide une vue simplifiée.
2. **Réutiliser `assist.py`** (modèle Pilote/Copilote existant) plutôt que d'exposer le YAML. Le moteur de création/modification est déjà là — il manque l'interface conversationnelle.
3. **Le chat est une nouvelle interaction, pas un remplacement** : bascule "Cartes ↔ Chat" (et "Vue simple ↔ Vue développeur").
4. **Garde-fous obligatoires** pour les non-techniciens : bibliothèque de composants approuvés, validation humaine, bac à sable.
5. **Le mockup est un support de démonstration** ; la vraie valeur est de le brancher sur le moteur réel.

---

## 4. Recommandation : le modèle "Pilote / Copilote"

| Rôle | Action | Interface |
|---|---|---|
| **Non-technicien** | Définit l'intention et valide le résultat | Chat (Vue simple) |
| **FlowPortal / IA** | Traduit l'intention en logique technique | Moteur CSFlow (`assist.py`) |
| **Technicien** | Audite, sécurise, optimise la structure | Vue Cartes / YAML |

### Création par l'intention
L'utilisateur dit : *"Chaque lundi, récupère les ventes, fais un résumé, envoie-le par mail à mon chef."*
→ L'IA génère le YAML, puis propose un **résumé simplifié** :
- 📥 Récupération des ventes
- 🤖 Rédaction du résumé
- 📧 Envoi du mail
→ L'utilisateur valide : le workflow est créé.

### Modification par le dialogue
*"C'est bien, mais ajoute une vérification si le montant dépasse 10 000 € avant d'envoyer."*
→ L'IA met à jour le YAML en arrière-plan, montre la vue simplifiée avec le nouvel élément.

### Garde-fous
- **Bibliothèque de composants approuvés** : l'IA n'utilise que des types d'étapes pré-validés.
- **Validation humaine obligatoire** : tout workflow créé par l'IA reste en "Brouillon" jusqu'à validation par un technicien.
- **Bac à sable** : limiter les chemins/API accessibles aux workflows créés par des non-techniciens.

---

## 5. Plan d'intégration en 4 jalons

### Jalon 1 — Brancher le chat sur `assist.py` (priorité)
Le chat doit appeler le vrai moteur, pas afficher des scénarios simulés.
- **Nouveau module `chat.py`** : enveloppe `Assistant` avec une API conversationnelle (état de session, historique, annulation).
- **Nouvelles routes Eel** dans `app.py` : `chat_send(texte)`, `chat_apply()`, `chat_cancel()`.
- Le chat envoie la demande à `assist.run()`, reçoit le brouillon + les différences, et les présente en **langage clair** (pas en diff YAML).

### Jalon 2 — La vue "simple" côté serveur
- Fonction qui **traduit un workflow YAML en étapes lisibles** (mapping `friendly` — voir §6), générée côté Python à partir du YAML réel.
- Le chat affiche ces étapes simplifiées ; la vue développeur affiche le YAML/diff.

### Jalon 3 — La création par intention
- Réutiliser `assist.py` pour la **création** (il a déjà `SKELETON` et `build_prompt`).
- Ajouter un **mode "Brouillon"** : un workflow créé par un non-technicien reste en attente de validation par un technicien avant d'être activé.

### Jalon 4 — Garde-fous
- **Bibliothèque de composants approuvés** : restreindre les types d'étapes pour les non-techniciens.
- **Bac à sable** : limiter les chemins/API accessibles.

---

## 5 bis. État réel des jalons

| Jalon | État | Fait | Reste |
|---|---|---|---|
| **1. Chat branché sur `assist.py`** | ✅ Fait (autrement que prévu) | `chat.html` / `chat.js` appellent `portal_assist`, `portal_save` / `portal_create`, `portal_stop`. Les demandes d'accord de l'agent s'affichent dans le chat. Carte de suivi d'exécution. `python app.py --chat`. | Le module `chat.py` et les routes `chat_send/apply/cancel` **ne sont pas nécessaires** (abandonnés). Pas d'historique de conversation côté serveur. |
| **2. Vue simple côté serveur** | 🟡 Partiel | `assist.py` renvoie `view` (étapes, titre, icône, verbe, problèmes) et `changes` (ajouté, retiré, modifié, réordonné, créé). Bascule Vue simple ↔ développeur. | Voir backlog, phase 1. |
| **3. Création par intention** | 🟡 Partiel | Création depuis le chat (`portal_create`, nom de fichier déduit du titre). | Mode **Brouillon** (phase 2). |
| **4. Garde-fous** | ⬜ Non commencé | — | Composants approuvés, bac à sable (phase 2). |

---

## 6. Mapping "friendly" (traduction technique → langage clair)

Extrait du mockup — à générer côté Python à partir du YAML réel.

| Technique (YAML) | Vue simple |
|---|---|
| `sales-report-pipeline` | "Votre rapport de ventes" |
| `create_data · file_write` | "📥 Récupération des données de ventes" |
| `analyze · agent` | "🤖 Rédaction du résumé par l'assistant" |
| `read_report · file_read` | "📖 Relecture du rapport" |
| `line_count · python` | "🧮 Vérification du contenu" |
| `print_report · log` | "📄 Affichage du rapport" |
| `http_get` (météo) | "🌤️ Météo de Paris / Cuvergnon / Bordeaux…" |
| `human_approval` | "👤 Votre validation" |
| `tmp/report.md`, XLSX, Markdown | "📄 Votre rapport est prêt", "📊 Création du fichier Excel" |

**Principe :** cacher la mécanique, montrer le résultat. Les cartes doivent se lire
comme un suivi de progression amical, pas un schéma de pipeline.

---

## 7. Fichiers livrables

### Déjà créés (sur la branche `chatui`)
- `docs/chatui-mockup.html` — maquette interactive du chat, avec bascule **Vue simple ↔ Vue développeur** et **Cartes ↔ Chat**. Scénarios : rapport de ventes, météo, synthèse de réunion (avec validation humaine). Utilise le design system réel de `ui/portal.css`.
- `docs/assessment.html` — évaluation de la maintenabilité/évolutivité du projet.

### À créer (plan)
- `chat.py` — enveloppe conversationnelle de `Assistant`.
- Routes Eel dans `app.py` : `chat_send`, `chat_apply`, `chat_cancel`.
- Fonction de traduction YAML → étapes "friendly" (Jalon 2).
- Mode "Brouillon" (Jalon 3).
- Garde-fous : bibliothèque de composants + bac à sable (Jalon 4).

---

## 8. État git

- **Branche :** `chatui` ; la PR #1 (documentation) est fusionnée dans `main`, le code du chat est sur la branche.
- **Fichiers du chat :** `ui/chat.html`, `ui/chat.js`, `assist.py` (`view` / `changes`), `app.py` (`--chat`), `ui/portal.html` (lien « Chat »).
- `typings/ui/chat.d.ts` est un instantané périmé de `chat.js` (aucune déclaration de types) : à supprimer ou à régénérer.

---

## 9. Backlog ordonné

**Phase 0 — consolider** (fait)
1. Mettre à jour ce document. ✅
2. Corriger les tests `OutputPathWarningTests` (filtre périmé sur `tmp/`). ✅
3. Essai de bout en bout avec le vrai eel et un faux CSAgent : création, demande d'accord, proposition, application. ✅

**Phase 1 — chat utilisable par un non-technicien**
4. Titre du workflow en langage courant sur la carte. ✅ (`friendly_title`)
5. Titres d'étapes lisibles : sans description, afficher le verbe du type, jamais l'identifiant. ✅
6. Icônes et verbes envoyés par le serveur (`friendly.py`), table en double supprimée de `chat.js`. ✅
7. Messages de validation en langage courant, couvrant les messages de `schema.py` et de `portal.py`. ✅ (`friendly_problem`)
8. Consigne à l'assistant (descriptions d'étapes courtes, en français). ✅ — à vérifier avec le vrai CSAgent.
9. Écran d'accueil avec suggestions cliquables. ✅
10. Étape en erreur : « Relancer » et « Demander à l'assistant de corriger ». ✅

**Phase 2 — confiance et sécurité**
11. Bouton « Tester » avec résultat dans le chat.
12. Mode Brouillon : un workflow créé depuis le chat attend la validation d'un technicien.
13. Composants approuvés : restreindre les types d'étapes pour les non-techniciens.
14. Bac à sable : limiter chemins et API.

**Phase 3 — automatisation**
15. Déclencheur planifié (« chaque lundi »).
16. Workflows modèles pour le public visé.
17. Exemple `mcp.playwright.example` et workflow « Lire une page web ».

**Phase 4 — distribution**
18. Lancement unique : FlowPortal démarre CSAgent (port libre, clé générée), ouvre le chat, arrête le worker à la fermeture.
19. Réglage de la clé du modèle dans l'interface.
20. Paquet unique (Nuitka) avec CSAgent livré à côté.

---

## 10. Notes de session

- **Session 1 :** création de la branche `chatui`, poussée sur remote. Évaluation du projet.
- **Session 2 :** `docs/assessment.html` et `docs/chatui-mockup.html`.
- **Session 3 :** mockup adapté au design system réel, scénarios interactifs.
- **Session 4 :** bascule **Vue simple ↔ Vue développeur**.
- **Session 5 :** discussion non-techniciens (Pilote/Copilote, garde-fous) ; création de ce document.
- **Session 6 (suite) :** phase 1 terminée (`friendly.py`, accueil, échec avec relance), essais avec le vrai eel et un faux CSAgent.
- **Session 6 :** chat réel (`chat.html`), réponse structurée `view` / `changes`, carte de suivi, demandes d'accord de l'agent, option `--chat` ; état des jalons et backlog ordonné ; phase 0 terminée.
