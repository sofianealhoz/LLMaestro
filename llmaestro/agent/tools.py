"""Inventaire d'outils. Aucun verbe destructeur: ni delete, ni move, ni rename."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from .sandbox import Refused, Workspace

SCHEMAS = [
    {
        "name": "list_files",
        "description": "Liste les fichiers du dépôt de travail.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "dossier, . par défaut"}},
        },
    },
    {
        "name": "read_file",
        "description": "Lit un fichier, numéroté, par tranche de lignes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "description": "première ligne, 1 par défaut"},
                "count": {"type": "integer", "description": "nombre de lignes, 200 par défaut"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "Cherche un texte littéral dans les fichiers.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": "Écrit un fichier entier, créé ou remplacé.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Remplace un extrait exact. Échoue si l'extrait est absent ou présent plusieurs fois.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "run_command",
        "description": "Lance une commande autorisée dans le dépôt de travail, par exemple les tests.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "la commande et ses arguments, séparés",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "finish",
        "description": "Termine le travail et résume ce qui a été fait.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]

WRITERS = ("write_file", "edit_file")


@dataclass
class Outcome:
    content: str
    finished: bool = False
    wrote: bool = False


class Toolbox:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.summary = ""

    @property
    def schemas(self) -> list:
        return SCHEMAS

    def call(self, name: str, arguments: dict) -> Outcome:
        """Un refus est un résultat, pas une exception: l'agent doit pouvoir se corriger."""
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return Outcome(f"outil inconnu: {name}")
        try:
            return handler(arguments or {})
        except Refused as refusal:
            return Outcome(f"refusé: {refusal}")
        except (OSError, ValueError, TypeError) as error:
            return Outcome(f"erreur: {type(error).__name__}: {error}")

    def _list_files(self, args) -> Outcome:
        found = self.workspace.list_files(args.get("path") or ".")
        return Outcome("\n".join(found) or "(aucun fichier)")

    def _read_file(self, args) -> Outcome:
        return Outcome(
            self.workspace.read(
                args["path"], int(args.get("start") or 1), int(args.get("count") or 200)
            )
            or "(fichier vide)"
        )

    def _search(self, args) -> Outcome:
        hits = self.workspace.search(args["pattern"], args.get("path") or ".")
        return Outcome("\n".join(hits) or "(aucune occurrence)")

    def _write_file(self, args) -> Outcome:
        written = self.workspace.write(args["path"], args.get("content") or "")
        return Outcome(f"écrit: {written}", wrote=True)

    def _edit_file(self, args) -> Outcome:
        written = self.workspace.replace(args["path"], args["old"], args.get("new") or "")
        return Outcome(f"modifié: {written}", wrote=True)

    def _run_command(self, args) -> Outcome:
        command = args.get("command")
        if isinstance(command, str):
            # Les modèles envoient souvent une chaîne. On la découpe nous-mêmes,
            # jamais un shell: la liste blanche s'applique de la même façon.
            command = shlex.split(command)
        ran = self.workspace.run(command)
        head = f"code {ran.code}"
        return Outcome(f"{head}\n{ran.output}" if ran.output else head)

    def _finish(self, args) -> Outcome:
        self.summary = str(args.get("summary") or "").strip()
        return Outcome(self.summary or "terminé", finished=True)
