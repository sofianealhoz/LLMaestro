# Agents confinés

Un agent reçoit une tâche sur un dépôt, boucle entre le modèle et les outils, et s'arrête sur
`finish` ou sur budget. Le sujet intéressant n'est pas la boucle, qui tient en cent lignes, c'est
le confinement.

**Principe : un agent ne touche jamais le dépôt réel.** Il travaille dans un `git worktree`
jetable, sur une branche dédiée. Rien n'arrive dans l'arbre de travail avant un `promote`.

## Usage

```bash
python3 -m llmaestro run --repo ~/projets/mon-depot "écris les tests unitaires de panier.py"
python3 -m llmaestro runs
python3 -m llmaestro inspect <run-id>            # le diff
python3 -m llmaestro inspect <run-id> --journal  # les étapes
python3 -m llmaestro promote <run-id>            # applique, sur ordre
```

Le dépôt cible doit être un dépôt git propre, avec au moins un commit. Le push reste manuel.

## Les sept garde-fous

| Garde-fou | Ce qu'il empêche |
|---|---|
| Worktree jetable | l'arbre de travail réel n'est jamais ouvert en écriture |
| Prison de chemins | `..`, chemins absolus et liens symboliques sortants sont refusés, `.git` est interdit |
| Aucun verbe destructeur | l'inventaire d'outils n'expose ni `delete`, ni `move`, ni `rename` |
| Liste blanche de commandes | `subprocess` sans shell, seules les suites énumérées dans `agent.toml` passent |
| Environnement expurgé | toute variable contenant `API_KEY`, `TOKEN`, `SECRET` ou `PASSWORD` est retirée |
| Budgets | étapes, tokens, fichiers, octets, temps mural. Dépassement égale arrêt propre |
| Promotion explicite | rien ne remonte sans `promote` |

Un refus n'est pas une erreur fatale : l'agent reçoit « refusé » comme résultat d'outil et peut
changer d'approche. C'est ce qui s'est produit en conditions réelles quand un modèle a tenté
`python calc.py` alors que seul `python3` figurait sur la liste.

## Outils disponibles

`list_files`, `read_file`, `search`, `write_file`, `edit_file`, `run_command`, `finish`.

`edit_file` remplace un extrait exact et échoue si l'extrait est absent ou présent plusieurs
fois. Un remplacement ambigu est une erreur, pas un pari.

## Configuration

`agent.toml` s'il existe, `agent.example.toml` sinon. Le premier est gitignoré.

```toml
[agent]
timeout = 120.0
commands = [["python3", "-m", "unittest"], ["git", "status"], ["ls"], ["grep"]]

[agent.budget]
steps = 24
tokens = 120000
files = 40
bytes = 400000
seconds = 900.0
```

Une commande n'est lancée que si elle **commence exactement** par une suite autorisée. `git status`
passe, `git push` non. Aucune chaîne n'est jamais confiée à un shell, donc `git status; rm -rf /`
est refusé comme un tout.

## Ce que ça sait faire

Une tâche dont le résultat se décrit en deux phrases et se vérifie par un test. Écrire les tests
d'une classe existante, ajouter des docstrings, renommer, traduire des commentaires.

Pas une fonctionnalité qui traverse plusieurs fichiers, pas une page blanche, rien qui demande de
trancher.

Le facteur décisif est le modèle, pas la boucle. Sur la même tâche, un petit modèle a inventé le
contenu du fichier et répondu en prose, là où un modèle agentique a écrit un fichier de tests
correct en cinq étapes. Le catalogue permet de choisir, la politique `quality` de router vers le
meilleur disponible.

## Journal

Chaque run écrit un JSONL dans `~/.llmaestro/runs/<run-id>.jsonl` : `run_started`, `step_started`,
`provider_selected`, `provider_failed`, `tool_called`, `tool_result`, `budget_exceeded`,
`run_finished`. C'est ce que lit `inspect --journal`.
