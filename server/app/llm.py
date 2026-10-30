"""Appels à Claude : génération du devoir, correction des photos, dialogue avec le superviseur."""

import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic

# Repli automatique côté serveur si une requête est déclinée par les classifieurs de sécurité.
BETAS = ["server-side-fallback-2026-07-01"]


class LLMError(RuntimeError):
    pass


ASSIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "notions": {"type": "array", "items": {"type": "string"}},
        "enonce": {
            "type": "string",
            "description": "Énoncé en Markdown, formules en LaTeX entre $...$ ou $$...$$",
        },
        "corrige": {"type": "string", "description": "Corrigé détaillé, Markdown + LaTeX"},
        "criteres_validation": {"type": "array", "items": {"type": "string"}},
        "duree_estimee_min": {"type": "integer"},
    },
    "required": ["titre", "notions", "enonce", "corrige", "criteres_validation", "duree_estimee_min"],
    "additionalProperties": False,
}

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["valide", "insuffisant", "illisible", "hors_sujet"]},
        "note_sur_20": {"type": "integer"},
        "resume_parent": {"type": "string"},
        "retour_eleve": {"type": "string"},
        "points_a_revoir": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "note_sur_20", "resume_parent", "retour_eleve", "points_a_revoir"],
    "additionalProperties": False,
}

GENERATION_SYSTEM = """\
Tu es professeur de mathématiques en {niveau}. Chaque jour tu donnes à ton élève un devoir \
d'environ {minutes} minutes, à rédiger à la main sur son cahier. Son téléphone reste bloqué \
tant que le devoir n'est pas validé : le devoir doit donc être faisable dans ce temps par un \
élève sérieux, sans être une formalité.

Principes :
- Suis le programme et la progression fixés par le superviseur (ci-dessous). S'il est vide, \
suis la progression usuelle de la classe.
- Un seul exercice, de style concours, en questions progressives : des questions d'application \
directe pour démarrer, puis une ou deux questions qui demandent une vraie idée.
- Exploite l'historique : reviens sur les notions mal maîtrisées, évite de répéter un exercice \
déjà donné, varie les thèmes.
- L'énoncé est en Markdown ; toutes les formules sont en LaTeX entre $...$ (en ligne) ou $$...$$ \
(centrées). Pas d'autres délimiteurs.
- Le corrigé est complet et rigoureux : il sert au parent et au correcteur automatique, l'élève \
ne le voit pas.
- Les critères de validation disent ce qui doit figurer sur la copie pour que le travail soit \
accepté (questions indispensables, résultats clés, niveau de justification attendu).

Programme et consignes du superviseur :
<programme>
{programme}
</programme>
"""

GRADING_SYSTEM = """\
Tu corriges la copie manuscrite d'un élève de {niveau}, photographiée page par page. Ta décision \
débloque (ou non) son téléphone pour la journée : sois juste, ni laxiste ni tatillon.

Verdict :
- "valide" : travail sérieux, toutes les questions indispensables traitées, raisonnements \
essentiellement justes et justifiés. Des erreurs de calcul mineures ou une dernière question \
inachevée restent acceptables si l'effort est réel.
- "insuffisant" : questions indispensables manquantes, erreurs de fond, ou justifications absentes.
- "illisible" : les photos ne permettent pas de lire la copie (flou, cadrage, lumière).
- "hors_sujet" : ce n'est pas ce devoir, ou ce n'est visiblement pas une copie rédigée à la main \
par l'élève (écran photographié, corrigé imprimé, pages blanches).

"retour_eleve" s'adresse à l'élève (tutoiement, concret, ce qu'il doit corriger s'il doit \
recommencer). "resume_parent" résume la copie en deux ou trois phrases pour le parent.
"""

SUPERVISOR_SYSTEM = """\
Tu assistes le superviseur (parent ou enseignant) d'un élève de {niveau} qui reçoit chaque jour \
un devoir de mathématiques généré par IA ; son téléphone reste bloqué tant que le devoir n'est \
pas validé. Le superviseur te parle depuis Telegram.

Ton rôle : l'aider à piloter le programme. Tu peux consulter et réécrire le texte du programme \
(progression, chapitres en cours, difficultés, consignes de style, durée), consulter l'historique \
des devoirs et leurs résultats, et régénérer le devoir du jour avec des consignes.

- Le programme est un texte libre qui sert de consigne au générateur de devoirs : quand tu le \
modifies, réécris-le en entier, clair et structuré, en conservant ce qui reste valable.
- Avant de modifier le programme ou de régénérer le devoir, assure-toi que c'est bien ce que \
demande le superviseur ; s'il le demande explicitement, fais-le directement.
- Réponds en français, brièvement : tes messages s'affichent sur un téléphone, en texte brut \
(pas de Markdown ; LaTeX seulement si indispensable).
"""

SUPERVISOR_TOOLS = [
    {
        "name": "lire_programme",
        "description": "Renvoie le texte actuel du programme et des consignes du superviseur.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "modifier_programme",
        "description": "Remplace entièrement le texte du programme. Utilisé pour les prochains devoirs générés.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"texte": {"type": "string", "description": "Nouveau texte complet du programme"}},
            "required": ["texte"],
            "additionalProperties": False,
        },
    },
    {
        "name": "historique_devoirs",
        "description": "Liste les derniers devoirs (date, titre, notions, résultat, retours).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"nombre": {"type": "integer", "description": "Nombre de devoirs (1 à 60)"}},
            "required": ["nombre"],
            "additionalProperties": False,
        },
    },
    {
        "name": "voir_devoir_du_jour",
        "description": "Renvoie l'énoncé, le corrigé et l'état du devoir du jour.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "regenerer_devoir_du_jour",
        "description": (
            "Remplace le devoir du jour par un nouveau, généré avec les consignes données. "
            "Impossible si le devoir du jour est déjà validé."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"consignes": {"type": "string"}},
            "required": ["consignes"],
            "additionalProperties": False,
        },
    },
]

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


class ClaudeTutor:
    def __init__(self, model: str, level: str, target_minutes: int, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic()
        self._model = model
        self._level = level
        self._minutes = target_minutes

    async def _call(self, **kwargs: Any) -> Any:
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=64000,
                betas=BETAS,
                fallbacks="default",
                **kwargs,
            ) as stream:
                message = await stream.get_final_message()
        except anthropic.APIConnectionError as e:
            raise LLMError(f"API Claude injoignable : {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Erreur API Claude ({e.status_code}) : {e.message}") from e
        if message.stop_reason == "refusal":
            raise LLMError("Requête déclinée par Claude")
        if message.stop_reason == "max_tokens":
            raise LLMError("Réponse tronquée (max_tokens atteint)")
        return message

    @staticmethod
    def _json_output(message: Any) -> dict[str, Any]:
        text = next(b.text for b in message.content if b.type == "text")
        return json.loads(text)

    async def generate_assignment(
        self, programme: str, history: list[dict[str, Any]], consignes: str | None = None
    ) -> dict[str, Any]:
        system = GENERATION_SYSTEM.format(
            niveau=self._level, minutes=self._minutes, programme=programme.strip() or "(aucun)"
        )
        request = (
            "Historique des derniers devoirs (du plus récent au plus ancien) :\n"
            f"{json.dumps(history, ensure_ascii=False, indent=1) if history else '(aucun)'}\n\n"
            "Rédige le devoir du jour."
        )
        if consignes:
            request += f"\n\nConsignes particulières du superviseur pour ce devoir : {consignes}"
        message = await self._call(
            system=system,
            messages=[{"role": "user", "content": request}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": ASSIGNMENT_SCHEMA}},
        )
        return self._json_output(message)

    async def grade(self, assignment: dict[str, Any], photos: list[bytes]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"<enonce>\n{assignment['enonce']}\n</enonce>\n\n"
                    f"<corrige>\n{assignment['corrige']}\n</corrige>\n\n"
                    f"<criteres_validation>\n{assignment['criteres']}\n</criteres_validation>\n\n"
                    f"Voici la copie de l'élève ({len(photos)} page(s)) :"
                ),
            }
        ]
        for i, photo in enumerate(photos, 1):
            content.append({"type": "text", "text": f"Page {i} :"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(photo).decode(),
                    },
                }
            )
        message = await self._call(
            system=GRADING_SYSTEM.format(niveau=self._level),
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": GRADE_SCHEMA}},
        )
        return self._json_output(message)

    async def supervisor_turn(
        self, history: list[dict[str, Any]], user_text: str, run_tool: ToolExecutor, max_steps: int = 10
    ) -> tuple[list[dict[str, Any]], str]:
        """Exécute un tour de conversation. Renvoie (messages à ajouter à l'historique, réponse texte)."""
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
        for _ in range(max_steps):
            message = await self._call(
                system=SUPERVISOR_SYSTEM.format(niveau=self._level),
                tools=SUPERVISOR_TOOLS,
                messages=history + new_messages,
                output_config={"effort": "medium"},
            )
            # Les blocs (y compris thinking) sont renvoyés tels quels aux tours suivants.
            new_messages.append(
                {"role": "assistant", "content": [b.to_dict() for b in message.content]}
            )
            tool_uses = [b for b in message.content if b.type == "tool_use"]
            if message.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in message.content if b.type == "text").strip()
                return new_messages, text or "(pas de réponse)"
            results = []
            for block in tool_uses:
                try:
                    output = await run_tool(block.name, dict(block.input))
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                except Exception as e:  # l'erreur est rendue au modèle, qui peut s'adapter
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": f"Erreur : {e}", "is_error": True}
                    )
            new_messages.append({"role": "user", "content": results})
        return new_messages, "Je n'ai pas réussi à terminer cette demande (trop d'étapes)."
