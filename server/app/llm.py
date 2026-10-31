"""Appels à Claude : génération du devoir, correction des photos, dialogue avec le professeur."""

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
        "resume_prof": {"type": "string"},
        "retour_eleve": {"type": "string"},
        "points_a_revoir": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "note_sur_20", "resume_prof", "retour_eleve", "points_a_revoir"],
    "additionalProperties": False,
}

PROGRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "notions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "notion": {"type": "string"},
                    "maitrise": {"type": "string", "enum": ["acquis", "en_cours", "fragile"]},
                    "observations": {"type": "string"},
                    "derniere_seance": {"type": "string", "description": "Date AAAA-MM-JJ"},
                },
                "required": ["notion", "maitrise", "observations", "derniere_seance"],
                "additionalProperties": False,
            },
        },
        "points_forts": {"type": "array", "items": {"type": "string"}},
        "difficultes": {"type": "array", "items": {"type": "string"}},
        "prochaine_etape": {"type": "string"},
    },
    "required": ["notions", "points_forts", "difficultes", "prochaine_etape"],
    "additionalProperties": False,
}

FICHE_SCHEMA = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "contenu": {"type": "string", "description": "Fiche en Markdown, formules en LaTeX entre $...$ ou $$...$$"},
        "resume_prof": {"type": "string"},
    },
    "required": ["titre", "contenu", "resume_prof"],
    "additionalProperties": False,
}

GENERATION_SYSTEM = """\
Tu es professeur de mathématiques. Ton élève est en {niveau}. Chaque jour tu lui donnes un devoir \
à rédiger à la main sur son cahier, prévu pour environ {minutes} minutes de travail. Son téléphone \
reste bloqué tant que le devoir n'est pas validé : le devoir doit donc être faisable dans ce temps \
par un élève sérieux de ce niveau, sans être une formalité.

Principes :
- Suis le programme et la progression fixés par le professeur (ci-dessous). S'il est vide, \
suis la progression usuelle de ce niveau.
- Un seul exercice, en questions progressives : des questions d'application directe pour démarrer, \
puis une question qui demande une petite initiative. Calibre la longueur sur {minutes} minutes et \
indique ta propre estimation dans duree_estimee_min.
- Construis un parcours à partir du carnet de progression de l'élève et de l'historique : \
reviens à intervalles réguliers sur les notions fragiles, consolide celles en cours, et n'aborde \
la notion suivante du programme que lorsque les précédentes tiennent. Ajuste la difficulté : un \
cran au-dessus après des réussites nettes, un cran en dessous après des échecs. Évite de répéter \
un exercice déjà donné.
- L'énoncé est en Markdown ; toutes les formules sont en LaTeX entre $...$ (en ligne) ou $$...$$ \
(centrées). Pas d'autres délimiteurs.
- Le corrigé est complet et rigoureux : il sert au professeur et au correcteur automatique, l'élève \
ne le voit pas.
- Les critères de validation disent ce qui doit figurer sur la copie pour que le travail soit \
accepté (questions indispensables, résultats clés, niveau de justification attendu).

Programme et consignes du professeur :
<programme>
{programme}
</programme>
"""

GRADING_SYSTEM = """\
Tu corriges la copie manuscrite d'un élève ({niveau}), photographiée page par page. Ta décision \
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
recommencer). "resume_prof" résume la copie en deux ou trois phrases pour le professeur.
"""

PROGRESS_SYSTEM = """\
Tu tiens le carnet de progression en mathématiques d'un élève ({niveau}). On te donne le carnet \
actuel et le compte rendu d'une nouvelle séance (le devoir, la décision, l'analyse de sa copie). \
Renvoie le carnet mis à jour.

- Une entrée par notion, avec un nom stable et précis (« sommes télescopiques », pas « calculs »). \
Conserve les notions déjà présentes ; ajoute celles travaillées dans la séance.
- La maîtrise évolue avec prudence : une notion ne passe « acquis » qu'après plusieurs réussites \
nettes, elle redevient « fragile » après une erreur de fond.
- « observations » : ce qu'on a constaté concrètement (types d'erreurs, ce qui est réussi), en une \
ou deux phrases.
- « prochaine_etape » : ce que les prochains devoirs doivent travailler en priorité, en une ou deux \
phrases.
"""

FICHE_SYSTEM = """\
Tu es professeur de mathématiques. Ton élève est en {niveau}. Rédige sa fiche de révision de la \
semaine, à partir des devoirs qu'il a faits et de l'analyse de ses copies.

La fiche, en Markdown, formules en LaTeX entre $...$ ou $$...$$ uniquement :
1. Les notions travaillées cette semaine.
2. L'essentiel du cours sur ces notions : définitions, résultats et formules à connaître, énoncés \
précisément.
3. Les méthodes à retenir, chacune illustrée par un exemple court.
4. Ses erreurs de la semaine et comment les éviter (appuie-toi sur les copies, sans le juger).
5. Trois exercices courts de révision, avec le résultat attendu.

Elle doit tenir en une ou deux pages et s'adresser directement à l'élève (tutoiement). \
« resume_prof » résume la semaine pour le professeur en trois ou quatre phrases (travail fourni, \
progrès, points à surveiller).
"""

SUPERVISOR_SYSTEM = """\
Tu assistes le professeur de mathématiques d'un élève ({niveau}) qui reçoit chaque jour un devoir \
d'environ {minutes} minutes généré par IA ; son téléphone reste bloqué tant que le devoir n'est \
pas validé. Le professeur te parle depuis Telegram.

Ton rôle : l'aider à piloter les devoirs. Tu peux consulter et réécrire le programme (progression, \
chapitres en cours, difficultés, consignes de style), régler le niveau et la durée visée, ajouter \
des sujets suggérés pour les prochains devoirs, consulter l'historique des devoirs et leurs \
résultats, et régénérer le devoir du jour avec des consignes.

- Le programme est un texte libre qui sert de consigne au générateur de devoirs : quand tu le \
modifies, réécris-le en entier, clair et structuré, en conservant ce qui reste valable.
- Un sujet suggéré est utilisé pour un seul devoir, dans l'ordre où ils ont été ajoutés.
- Avant de modifier le programme ou de régénérer le devoir, assure-toi que c'est bien ce que \
demande le professeur ; s'il le demande explicitement, fais-le directement.
- Réponds en français, brièvement : tes messages s'affichent sur un téléphone, en texte brut \
(pas de Markdown ; LaTeX seulement si indispensable).
"""

SUPERVISOR_TOOLS = [
    {
        "name": "lire_programme",
        "description": "Renvoie le programme, le niveau, la durée visée et les sujets suggérés en attente.",
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
    {
        "name": "voir_progression",
        "description": "Renvoie le carnet de progression de l'élève (maîtrise par notion, difficultés, prochaine étape).",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "regler_niveau",
        "description": "Règle le niveau de l'élève et la durée visée des prochains devoirs.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "niveau": {"type": "string", "description": "Ex. « début de MPSI », « MPSI, second semestre »"},
                "duree_min": {"type": "integer", "description": "Durée visée en minutes (10 à 120)"},
            },
            "required": ["niveau", "duree_min"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ajouter_sujet",
        "description": "Ajoute un sujet suggéré, utilisé pour l'un des prochains devoirs (un sujet par devoir).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"sujet": {"type": "string"}},
            "required": ["sujet"],
            "additionalProperties": False,
        },
    },
]

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


class ClaudeTutor:
    def __init__(self, model: str, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic()
        self._model = model

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
        self,
        programme: str,
        history: list[dict[str, Any]],
        reglages: dict[str, Any],
        consignes: str | None = None,
        sujet: str | None = None,
        progression: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = GENERATION_SYSTEM.format(
            niveau=reglages["niveau"], minutes=reglages["duree_min"], programme=programme.strip() or "(aucun)"
        )
        request = (
            "Historique des derniers devoirs (du plus récent au plus ancien) :\n"
            f"{json.dumps(history, ensure_ascii=False, indent=1) if history else '(aucun)'}\n\n"
            "Carnet de progression de l'élève :\n"
            f"{json.dumps(progression, ensure_ascii=False, indent=1) if progression else '(pas encore de suivi)'}\n\n"
            "Rédige le devoir du jour."
        )
        if sujet:
            request += f"\n\nSujet demandé par le professeur pour ce devoir : {sujet}"
        if consignes:
            request += f"\n\nConsignes particulières du professeur pour ce devoir : {consignes}"
        message = await self._call(
            system=system,
            messages=[{"role": "user", "content": request}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": ASSIGNMENT_SCHEMA}},
        )
        return self._json_output(message)

    async def grade(self, assignment: dict[str, Any], photos: list[bytes], reglages: dict[str, Any]) -> dict[str, Any]:
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
            system=GRADING_SYSTEM.format(niveau=reglages["niveau"]),
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": GRADE_SCHEMA}},
        )
        return self._json_output(message)

    async def update_progress(
        self, progression: dict[str, Any] | None, seance: dict[str, Any], reglages: dict[str, Any]
    ) -> dict[str, Any]:
        request = (
            "Carnet actuel :\n"
            f"{json.dumps(progression, ensure_ascii=False, indent=1) if progression else '(vide)'}\n\n"
            f"Nouvelle séance :\n{json.dumps(seance, ensure_ascii=False, indent=1)}"
        )
        message = await self._call(
            system=PROGRESS_SYSTEM.format(niveau=reglages["niveau"]),
            messages=[{"role": "user", "content": request}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PROGRESS_SCHEMA}},
        )
        return self._json_output(message)

    async def weekly_sheet(
        self, semaine: str, seances: list[dict[str, Any]], progression: dict[str, Any] | None, reglages: dict[str, Any]
    ) -> dict[str, Any]:
        request = (
            f"Semaine : {semaine}\n\n"
            f"Devoirs de la semaine :\n{json.dumps(seances, ensure_ascii=False, indent=1)}\n\n"
            "Carnet de progression :\n"
            f"{json.dumps(progression, ensure_ascii=False, indent=1) if progression else '(pas encore de suivi)'}"
        )
        message = await self._call(
            system=FICHE_SYSTEM.format(niveau=reglages["niveau"]),
            messages=[{"role": "user", "content": request}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": FICHE_SCHEMA}},
        )
        return self._json_output(message)

    async def supervisor_turn(
        self,
        history: list[dict[str, Any]],
        user_text: str,
        run_tool: ToolExecutor,
        reglages: dict[str, Any],
        max_steps: int = 10,
    ) -> tuple[list[dict[str, Any]], str]:
        """Exécute un tour de conversation. Renvoie (messages à ajouter à l'historique, réponse texte)."""
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
        for _ in range(max_steps):
            message = await self._call(
                system=SUPERVISOR_SYSTEM.format(niveau=reglages["niveau"], minutes=reglages["duree_min"]),
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
