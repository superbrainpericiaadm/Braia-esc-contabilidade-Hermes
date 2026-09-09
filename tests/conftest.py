import json
import re
import unicodedata
from pathlib import Path

import pytest
import yaml


CONFIG_KEYS = {
    "owner_name", "owner_full_name", "owner_email", "owner_telegram_id", "owner_title",
    "business_name", "institutional_email", "calendar_tag",
    "gmail_label_prefix", "manager_name", "manager_email",
    "manager_email_alt", "crc_registration", "corecon_registration",
    "drive_root", "drive_templates_folder_id", "payment_gateway", "crm_name",
}

REQUIRED_CONFIG_KEYS = {
    "owner_name", "owner_email", "owner_telegram_id", "business_name", "institutional_email",
    "calendar_tag", "manager_name", "manager_email",
}

AGENT_SECTIONS = [
    "Identidade e cargo", "Gatilhos de ativação", "Hierarquia", "Escopo",
    "Limites", "Métodos e SOPs", "Ferramentas necessárias",
    "Regras de confirmação", "Referências", "Formato de entrega à Braia",
    "Verificação final",
]


@pytest.fixture(scope="session")
def repo_root():
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def skill_dirs(repo_root):
    return sorted(p for p in (repo_root / "skills").iterdir() if (p / "SKILL.md").is_file())


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", text, re.DOTALL)
    assert match, f"frontmatter ausente ou malformado: {path}"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"frontmatter não é mapa: {path}"
    return data, text[match.end():]


def normalize(text):
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def parse_routing(path):
    text = path.read_text(encoding="utf-8")
    section = text.split("## Roteamento", 1)[1].split("\n## ", 1)[0]
    routes = []
    for line in section.splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0].lower() not in {"gatilho", "---"} and set(cells[0]) != {"-"}:
            routes.append(tuple(cells))
    return routes


def route(text, routes):
    haystack = normalize(text)
    if re.search(r"(?<!\w)(?:cria|criar|contrata|contratar|treina|treinar|melhora|melhorar|revisa|revisar)(?!\w).*(?<!\w)(?:agente|especialista|skill)(?!\w)", haystack):
        return "agente-angelica"
    if re.search(r"(?<!\w)(?:agenda|agendar|marcar)(?!\w)", haystack):
        return "agente-isaura"
    matches = []
    for trigger, skill in routes:
        needle = normalize(trigger)
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            matches.append((len(needle), skill))
    return max(matches, default=(0, None))[1]


def markdown_without_code_fences(text):
    kept = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            kept.append(line)
    return "\n".join(kept)
