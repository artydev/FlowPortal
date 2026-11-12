# FlowPortal — Chat UI : Document de référence & plan d'intégration

> **Statut :** Document de travail — à mettre à jour au fil de l'avancement.
> **Branche :** `chatui` (créée depuis `main`, poussée sur `origin/chatui`).
> **Dernière mise à jour :** session en cours.

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

- **Branche :** `chatui` (créée depuis `main`, poussée sur `origin/chatui`).
- **Fichiers non suivis :** `docs/assessment.html`, `docs/chatui-mockup.html`.
- **À décider :** fusion de `chatui` via pull request (PR) vers `main`.

---

## 9. Prochaines étapes suggérées

1. **Valider le plan** (Jalons 1-4) avec les parties prenantes.
2. **Commencer le Jalon 1** : créer `chat.py` + routes Eel, brancher le chat sur `assist.py`.
3. **Décider du sort de la branche `chatui`** : PR vers `main` ou continuer sur la branche.
4. **Ajouter un scénario d'échec** au mockup (étape qui passe en rouge avec option de relance) dans les deux vues.

---

## 10. Notes de session

- **Session 1 :** création de la branche `chatui`, poussée sur remote. Évaluation du projet (maintenable, évolutif, mais dépend de l'usage réel).
- **Session 2 :** création de `docs/assessment.html` (évaluation) et `docs/chatui-mockup.html` (maquette chat).
- **Session 3 :** adaptation du mockup au design system réel de l'app + scénarios interactifs avec vrais workflows (sales-report-pipeline, weather, meeting-digest).
- **Session 4 :** ajout de la bascule **Vue simple ↔ Vue développeur** pour les non-techniciens.
- **Session 5 :** discussion sur la création/modification de workflows par des non-techniciens (modèle Pilote/Copilote, garde-fous). Création de ce document de référence.
