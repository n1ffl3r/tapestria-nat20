"""Build ``audit/srd_5_2_1_ledger.json``: one entry per audited corpus record.

Each entry holds the record's SRD 5.2.1 page, the fields compared with the SRD values they
were compared against, and one status (spec 02a, "The rules-text audit"):

- ``verified``: the corpus matches the PDF;
- ``corrected``: the pinned Foundry pack did not match and the translator fixes it; the
  entry lists each change with the SRD text it rests on;
- ``divergence``: the corpus does not match and stays so, for the reason and the owner's
  issue that ``audit/srd_5_2_1_divergences.json`` records.

A difference that neither the translator fixes nor the divergences file records fails the
build. Rebuild after a regen (it reads the raw Foundry sources to tell corrected records
from verified ones):

    make srd-ledger            # write the ledger
    make srd-ledger CHECK=1    # fail if the committed ledger is stale (check-provenance)
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from tools.srd_audit import items, pdf_text, spell_lists, spells, stat_blocks
from tools.translators.foundry import (
    item_srd_corrections,
    monster_srd_corrections,
    spell_srd_corrections,
    srd_rules_disabled,
    translate_armor_yaml,
    translate_generic_item_yaml,
    translate_monster_yaml,
    translate_weapon_yaml,
)

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "audit" / "srd_5_2_1_ledger.json"
DIVERGENCES_PATH = ROOT / "audit" / "srd_5_2_1_divergences.json"
INDEX_PATH = ROOT / "srd" / "index.json"
CANONICAL = ROOT / "src" / "dnd5e_srd_data" / "canonical"
FOUNDRY = ROOT / "raw_sources" / "foundry"
AUDITED_CATEGORIES = ("items", "monsters", "spell_lists", "spells")
ALIASES_PATH = ROOT / "srd" / "aliases.json"
# The pages holding every stat block: the summon forms in the Spells chapter, the
# figurines and the Avatar of Death in the Magic Items chapter, and the Monsters and
# Animals chapters.
STAT_BLOCK_PAGES = (104, 364)
STATUSES = ("verified", "corrected", "divergence")
# The header line each translator rule reads its corrected value from.
RULE_HEADERS = {
    "material_cost": "Components",
    "material_consumed": "Components",
    "range": "Range",
    "duration": "Duration",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_records(category: str) -> dict[str, dict[str, Any]]:
    return {path.stem: load_json(path) for path in sorted((CANONICAL / category).glob("*.json"))}


def spell_classes(lists: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    """Each spell slug's classes, from the shipped class spell lists."""
    classes: dict[str, list[str]] = {}
    for class_slug, spell_list in sorted(lists.items()):
        for entry in spell_list["spells"]:
            classes.setdefault(entry["slug"], []).append(class_slug)
    return classes


def contains(text: str, quote: str) -> bool:
    """Whether a quote appears in a record's text, whitespace and hyphenation aside."""
    return re.sub(r"\s+", " ", quote).strip() in pdf_text.flatten(text)


def _int_or_none(raw: Any) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def uncorrected(record: Mapping[str, Any]) -> dict[str, Any]:
    """The spell as the pinned Foundry pack gives it, before the translator's SRD 5.2.1
    corrections: its raw material cost and consumption, its range and duration values
    as plain numbers (a formula reads as no value), and each correction table entry
    reverted to the value it replaces."""
    url = str(record["provenance"]["source_url"])
    raw_path = FOUNDRY / url.split("/blob/", 1)[1].split("/", 1)[1]
    system = yaml.safe_load(raw_path.read_text(encoding="utf-8"))["system"]
    view = copy.deepcopy(dict(record))
    materials = system.get("materials") or {}
    view["materials"]["cost"] = _int_or_none(materials.get("cost")) or 0
    view["materials"]["consumed"] = bool(materials.get("consumed"))
    view["range"]["value"] = _int_or_none((system.get("range") or {}).get("value"))
    view["duration"]["value"] = _int_or_none((system.get("duration") or {}).get("value"))
    for correction in spell_srd_corrections(record["slug"]):
        _set_path(view, correction.path, copy.deepcopy(correction.was))
    return view


def _set_path(blob: dict[str, Any], path: str, value: Any) -> None:
    node: Any = blob
    steps = path.split(".")
    for step in steps[:-1]:
        name, _, selector = step.partition("[")
        node = node[name]
        if selector:
            node = next(item for item in node if item.get("id") == selector.rstrip("]"))
    node[steps[-1]] = value


def spell_changes(
    record: Mapping[str, Any], classes: list[str], header: Mapping[str, str]
) -> list[dict[str, Any]]:
    """The audited fields the translator changed, each with the SRD text it rests on: a
    correction table's quote, or the header line a translator rule reads."""
    before = spells.corpus_facts(uncorrected(record), classes)
    after = spells.corpus_facts(record, classes)
    changes = []
    for field in sorted(set(before) | set(after)):
        if before.get(field) == after.get(field):
            continue
        quotes = []
        for correction in spell_srd_corrections(record["slug"]):
            reverted = copy.deepcopy(dict(record))
            _set_path(reverted, correction.path, copy.deepcopy(correction.was))
            if spells.corpus_facts(reverted, classes).get(field) != after.get(field):
                quotes.append(correction.srd)
        if not quotes and field in RULE_HEADERS:
            key = RULE_HEADERS[field]
            quotes.append(f"{key}: {header[key]}")
        if not quotes:
            raise ValueError(f"{record['slug']}: {field} changed with no SRD text to cite")
        changes.append(
            {"field": field, "from": before.get(field), "to": after.get(field), "srd": quotes}
        )
    return changes


def spell_entry(
    slug: str,
    record: Mapping[str, Any],
    text: pdf_text.RecordText,
    srd_classes: list[str],
    classes: list[str],
    divergence: Mapping[str, Any] | None,
    with_changes: bool,
) -> dict[str, Any]:
    raw_srd = spells.pdf_facts(text, srd_classes)
    srd, read = spells.audited_facts(slug, raw_srd)
    for quote in read.values():
        if not contains(text.text, quote):
            raise ValueError(f"{slug}: hand reading quote not in the SRD text: {quote!r}")
    corpus = spells.corpus_facts(record, classes)
    not_compared = spells.NOT_COMPARED.get(slug, {})
    for field in not_compared:
        srd.pop(field, None)
        corpus.pop(field, None)
    differences = spells.compare(srd, corpus)
    entry: dict[str, Any] = {
        "name": record["name"],
        "srd_page": text.page,
        "compared": {field: srd.get(field) for field in spells.compared_fields(srd, corpus)},
    }
    if read:
        entry["read_by_hand"] = dict(sorted(read.items()))
    if not_compared:
        entry["not_compared"] = dict(sorted(not_compared.items()))
    header, _ = spells._header(text)
    for correction in spell_srd_corrections(slug):
        if not contains(text.text, correction.srd):
            raise ValueError(f"{slug}: correction quote not in the SRD text: {correction.srd!r}")
    changes = spell_changes(record, classes, header) if with_changes else []
    if differences:
        if divergence is None or sorted(divergence["fields"]) != sorted(differences):
            raise ValueError(f"{slug}: unrecorded differences from the SRD: {differences}")
        entry["status"] = "divergence"
        entry["differences"] = differences
        entry["divergence"] = dict(divergence)
    elif divergence is not None:
        raise ValueError(f"{slug}: recorded as a divergence but matches the SRD")
    else:
        entry["status"] = "corrected" if changes else "verified"
    if changes:
        entry["changes"] = changes
    return entry


def spell_list_entry(
    class_slug: str,
    spell_list: Mapping[str, Any],
    names: Mapping[str, str],
    heading_levels: Mapping[str, int],
    table: Mapping[str, spell_lists.TableRow],
    page: int,
    divergence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A class spell list against the spell headings (its source) and the class chapter's
    table. A spell on the list the table lacks, or a level that differs, is a difference."""
    shipped = {names[entry["slug"]]: entry["level"] for entry in spell_list["spells"]}
    differences: dict[str, dict[str, Any]] = {}
    if shipped != heading_levels:
        raise ValueError(f"{class_slug}: the shipped list differs from the spell headings")
    table_levels = {name: row.level for name, row in table.items()}
    missing = sorted(set(shipped) - set(table_levels))
    extra = sorted(set(table_levels) - set(shipped))
    levels = sorted(n for n in set(shipped) & set(table_levels) if shipped[n] != table_levels[n])
    if missing or extra or levels:
        differences["spells"] = {
            "on_the_list_not_in_the_chapter_table": missing,
            "in_the_chapter_table_not_on_the_list": extra,
            "level_differs": levels,
        }
    entry: dict[str, Any] = {
        "name": f"{spell_list['class_name']} Spell List",
        "srd_page": page,
        "compared": {"spells": len(shipped), "against": ["spell headings", "chapter table"]},
    }
    if differences:
        if divergence is None or sorted(divergence["fields"]) != sorted(differences):
            raise ValueError(f"{class_slug}: unrecorded differences from the SRD: {differences}")
        entry["status"] = "divergence"
        entry["differences"] = differences
        entry["divergence"] = dict(divergence)
    elif divergence is not None:
        raise ValueError(f"{class_slug}: recorded as a divergence but matches the SRD")
    else:
        entry["status"] = "verified"
    return entry


def _raw_path(record: Mapping[str, Any]) -> Path:
    url = str(record["provenance"]["source_url"])
    return FOUNDRY / url.split("/blob/", 1)[1].split("/", 1)[1]


def _entry_quote(text: pdf_text.RecordText, field: str) -> str | None:
    """The SRD text an action's field rests on: its run-in heading and first sentence."""
    if "." not in field:
        return None
    section, slug = field.split(".", 1)
    _, rest = stat_blocks._header(text)
    for entry in stat_blocks.entries(rest):
        if entry.section == section and stat_blocks._slug(entry.name) == slug:
            head = entry.name + (f" ({entry.tag})" if entry.tag else "")
            # Through the damage it deals, else its first sentence ("ft." is no sentence end).
            first = re.match(r"(.+? damage\b[^.]*\.)", entry.text) or re.match(
                r"(.+?(?<! ft)\.)(?: [A-Z]|$)", entry.text
            )
            return f"{head}. {first.group(1) if first else entry.text}".strip()
    return None


def monster_changes(
    record: Mapping[str, Any], text: pdf_text.RecordText, fields: list[str]
) -> list[dict[str, Any]]:
    """The audited fields the translator changed, each with the SRD text it rests on: a
    correction table's quote, or the action's text for a translator rule (an item's reach
    or Finesse property)."""
    with srd_rules_disabled():
        raw = translate_monster_yaml(
            _raw_path(record), ingest_date=date(2000, 1, 1), ingest_version="ledger"
        ).model_dump(mode="json")
    before = stat_blocks.corpus_facts(raw)
    after = stat_blocks.corpus_facts(record)
    corrections = monster_srd_corrections(record["slug"])
    # A correction to a field the audit does not compare (a summon's reach) is listed too.
    fields = sorted(
        set(fields)
        | {
            f
            for f in set(before) | set(after)
            if any(_corrects(c.path, f, record) for c in corrections)
        }
    )
    changes = []
    for field in fields:
        if before.get(field) == after.get(field):
            continue
        quotes = [c.srd for c in corrections if _corrects(c.path, field, record)]
        if not quotes:
            quote = _entry_quote(text, field)
            if quote is None:
                raise ValueError(f"{record['slug']}: {field} changed with no SRD text to cite")
            quotes.append(quote)
        changes.append(
            {"field": field, "from": before.get(field), "to": after.get(field), "srd": quotes}
        )
    return changes


_KIND_SECTIONS = {
    "action": "actions",
    "bonus_action": "bonus_actions",
    "reaction": "reactions",
    "special": "traits",
    "legendary": "legendary_actions",
}
SECTION_FIELDS = frozenset(_KIND_SECTIONS.values())


def _corrects(path: str, field: str, record: Mapping[str, Any]) -> bool:
    """Whether a monster correction at ``path`` changes the audit ``field``: a header field
    ("movement.climb" is "speed"), an action's numbers ("actions[bite]...." is "actions.bite"
    or, for a bonus action, "bonus_actions.bite"), or, for a ``kind`` correction, the
    section lists and the moved entry's numbers."""
    head = path.split(".")[0]
    found = re.fullmatch(r"(\w+)\[([^\]]+)\]", head)
    if found is None:
        return {"movement": "speed", "saving_throws": "saves"}.get(head, head) == field
    slug = found.group(2)
    if path.endswith(".kind"):
        return field in SECTION_FIELDS or field.endswith(f".{slug}")
    lists = ("actions", "special_abilities", "legendary_actions", "lair_actions")
    kinds = {a["slug"]: a["kind"] for name in lists for a in record.get(name) or []}
    return field == f"{_KIND_SECTIONS.get(kinds.get(slug, ''), 'actions')}.{slug}"


def monster_entry(
    slug: str,
    record: Mapping[str, Any],
    text: pdf_text.RecordText,
    page: int,
    variant: bool,
    divergence: Mapping[str, Any] | None,
    with_changes: bool,
) -> dict[str, Any]:
    srd, read = stat_blocks.audited_facts(slug, stat_blocks.pdf_facts(text))
    for quote in read.values():
        if not contains(text.text, quote):
            raise ValueError(f"{slug}: hand reading quote not in the SRD text: {quote!r}")
    corpus = stat_blocks.corpus_facts(record)
    not_compared = dict(stat_blocks.NOT_COMPARED.get(slug, {}))
    if variant:
        for field in sorted(set(srd) | set(corpus)):
            if field not in stat_blocks.VARIANT_FIELDS:
                not_compared[field] = (
                    "A variant of the parent stat block, which prints this field once per"
                    " variant (srd/aliases.json)."
                )
    for field in not_compared:
        srd.pop(field, None)
        corpus.pop(field, None)
    differences = stat_blocks.compare(srd, corpus)
    compared = spells.compared_fields(srd, corpus)
    entry: dict[str, Any] = {
        "name": record["name"],
        "srd_page": page,
        "compared": {field: srd.get(field) for field in compared},
    }
    if read:
        entry["read_by_hand"] = dict(sorted(read.items()))
    if not_compared:
        entry["not_compared"] = dict(sorted(not_compared.items()))
    for correction in monster_srd_corrections(slug):
        if not contains(text.text, correction.srd):
            raise ValueError(f"{slug}: correction quote not in the SRD text: {correction.srd!r}")
    changes = monster_changes(record, text, compared) if with_changes else []
    if differences:
        if divergence is None or sorted(divergence["fields"]) != sorted(differences):
            raise ValueError(f"{slug}: unrecorded differences from the SRD: {differences}")
        entry["status"] = "divergence"
        entry["differences"] = differences
        entry["divergence"] = dict(divergence)
    elif divergence is not None:
        raise ValueError(f"{slug}: recorded as a divergence but matches the SRD")
    else:
        entry["status"] = "corrected" if changes else "verified"
    if changes:
        entry["changes"] = changes
    return entry


def monster_entries(
    index: Mapping[str, Any], divergences: Mapping[str, Any], with_changes: bool
) -> dict[str, Any]:
    """Every corpus monster against its stat block: its own, or for a variant the parent
    srd/aliases.json names. A record with no stat block to compare is a divergence."""
    texts = stat_blocks.stat_block_texts(pdf_text.plain_lines(*STAT_BLOCK_PAGES))
    pages = {s["name"]: s["page"] for s in index["categories"]["stat_blocks"]}
    aliases = {
        alias["name"]: alias
        for alias in load_json(ALIASES_PATH)["aliases"]
        if alias["corpus_category"] == "monsters"
    }
    entries: dict[str, Any] = {}
    errors: list[str] = []
    for slug, record in canonical_records("monsters").items():
        name = record["name"]
        divergence = divergences["monsters"].get(slug)
        parent = aliases.get(name, {}).get("of", name)
        if parent not in texts:
            if divergence is None:
                errors.append(f"{slug}: no SRD stat block to compare")
            else:
                entries[slug] = {
                    "name": name,
                    "srd_page": int(divergence["srd_page"]),
                    "compared": {},
                    "status": "divergence",
                    "differences": {"stat_block": {"srd": None, "corpus": slug}},
                    "divergence": dict(divergence),
                }
            continue
        try:
            entries[slug] = monster_entry(
                slug,
                record,
                texts[parent],
                pages.get(parent, texts[parent].page),
                parent != name,
                divergence,
                with_changes,
            )
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
    return entries


# The item field each correction path changes.
ITEM_FIELDS = {
    "attunement_constraint": "attunement_by",
    "cost_gp": "cost",
    "item_kind": "category",
    "rarity": "rarity",
    "uses": "charges",
    "versatile_damage": "versatile",
    "weight": "weight",
}


def _translate_item(path: Path) -> dict[str, Any]:
    """The item as the translator gives it with the SRD rules and corrections off, routed
    as ``tools/regen.py`` routes it."""
    if "/weapons/" in path.as_posix():
        translator = translate_weapon_yaml
    elif "/armor/" in path.as_posix():
        translator = translate_armor_yaml
    else:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        system = doc.get("system") or {}
        translator = translate_generic_item_yaml
        if doc.get("type") == "weapon":
            translator = translate_weapon_yaml
        elif doc.get("type") == "equipment" and (system.get("armor") or {}).get("value"):
            translator = translate_armor_yaml
    with srd_rules_disabled():
        item = translator(path, ingest_date=date(2000, 1, 1), ingest_version="ledger")
    return item.model_dump(mode="json")


def item_changes(record: Mapping[str, Any], magic: bool, fields: list[str]) -> list[dict[str, Any]]:
    """The audited fields the translator's corrections changed, each with the SRD text it
    rests on."""
    before = items.corpus_facts(_translate_item(_raw_path(record)), magic)
    after = items.corpus_facts(record, magic)
    corrections = item_srd_corrections(record["slug"])
    changes = []
    for field in sorted(set(fields) | {ITEM_FIELDS[c.path] for c in corrections}):
        if before.get(field) == after.get(field):
            continue
        quotes = [c.srd for c in corrections if ITEM_FIELDS[c.path] == field]
        if not quotes:
            raise ValueError(f"{record['slug']}: {field} changed with no SRD text to cite")
        changes.append(
            {"field": field, "from": before.get(field), "to": after.get(field), "srd": quotes}
        )
    return changes


def item_entry(
    slug: str,
    record: Mapping[str, Any],
    srd_facts: Mapping[str, Any],
    page: int,
    text: str,
    magic: bool,
    parent: str | None,
    divergence: Mapping[str, Any] | None,
    with_changes: bool,
) -> dict[str, Any]:
    srd, corpus, not_compared = items.audited(
        srd_facts, items.corpus_facts(record, magic), magic, parent
    )
    differences = spells.compare(srd, corpus)
    entry: dict[str, Any] = {"name": record["name"], "srd_page": page, "compared": srd}
    if not_compared:
        entry["not_compared"] = dict(sorted(not_compared.items()))
    for correction in item_srd_corrections(slug):
        if not contains(text, correction.srd):
            raise ValueError(f"{slug}: correction quote not in the SRD text: {correction.srd!r}")
    changes = item_changes(record, magic, sorted(srd)) if with_changes else []
    if differences:
        if divergence is None or sorted(divergence["fields"]) != sorted(differences):
            raise ValueError(f"{slug}: unrecorded differences from the SRD: {differences}")
        entry["status"] = "divergence"
        entry["differences"] = differences
        entry["divergence"] = dict(divergence)
    elif divergence is not None:
        raise ValueError(f"{slug}: recorded as a divergence but matches the SRD")
    else:
        entry["status"] = "corrected" if changes else "verified"
    if changes:
        entry["changes"] = changes
    return entry


def item_entries(
    index: Mapping[str, Any], divergences: Mapping[str, Any], with_changes: bool
) -> dict[str, Any]:
    """Every corpus item against its SRD entry: a Magic Items chapter entry, else an
    Equipment chapter row or heading (or a sample poison's). A variant is compared with
    its parent (srd/aliases.json). A record with no SRD item to compare (a trap, or a
    helper record Foundry adds) is a divergence."""
    lines = pdf_text.plain_lines(*items.EQUIPMENT_PAGES) + pdf_text.plain_lines(*items.POISON_PAGES)
    equipment = items.equipment_facts(lines)
    equipment_text = "\n".join(line for _, line in lines)
    texts = items.magic_item_texts()
    pages = {s["name"]: s["page"] for s in index["categories"]["magic_items"]}
    aliases = {
        alias["name"]: alias
        for alias in load_json(ALIASES_PATH)["aliases"]
        if alias["corpus_category"] == "items"
    }
    entries: dict[str, Any] = {}
    errors: list[str] = []
    for slug, record in canonical_records("items").items():
        name = record["name"]
        divergence = divergences["items"].get(slug)
        alias = aliases.get(name)
        srd_name = alias["of"] if alias and alias["kind"] == "variant" else name
        # A magic item's variant is a form of its entry; an equipment variant is the row
        # itself ("Arcane Focus (Varies)").
        magic_variant = alias is not None and alias["of_category"] == "magic_items"
        parent = srd_name if srd_name != name and magic_variant else None
        found = None
        if not (alias and alias["kind"] == "helper"):
            if srd_name in texts:
                text = texts[srd_name]
                found = (
                    items.magic_item_facts(text),
                    pages.get(srd_name, text.page),
                    text.text,
                    True,
                )
            elif (row := items.equipment_entry(equipment, srd_name)) is not None:
                found = (row[1][1], row[1][0], equipment_text, False)
        if found is None:
            if divergence is None:
                errors.append(f"{slug}: no SRD item to compare")
            else:
                entries[slug] = {
                    "name": name,
                    "srd_page": int(divergence["srd_page"]),
                    "compared": {},
                    "status": "divergence",
                    "differences": {"srd_entry": {"srd": None, "corpus": slug}},
                    "divergence": dict(divergence),
                }
            continue
        facts, page, text_body, magic = found
        try:
            entries[slug] = item_entry(
                slug, record, facts, page, text_body, magic, parent, divergence, with_changes
            )
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
    return entries


def build(with_changes: bool = True) -> dict[str, Any]:
    """The ledger. ``with_changes`` reads the raw Foundry sources to list what the
    translator changed; without them every matching record reads as verified."""
    index = load_json(INDEX_PATH)
    divergences = load_json(DIVERGENCES_PATH)
    corpus = canonical_records("spells")
    lists = canonical_records("spell_lists")
    classes = spell_classes(lists)
    by_name = {s["name"]: s for s in index["categories"]["spells"]}
    texts = pdf_text.spell_texts()
    spell_records: dict[str, Any] = {}
    errors: list[str] = []
    for slug, record in corpus.items():
        name = record["name"]
        try:
            spell_records[slug] = spell_entry(
                slug,
                record,
                texts[name],
                by_name[name]["classes"],
                classes.get(slug, []),
                divergences["spells"].get(slug),
                with_changes,
            )
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
    names = {slug: record["name"] for slug, record in corpus.items()}
    tables = spell_lists.chapter_lists()
    pages = spell_lists.chapter_pages()
    list_records: dict[str, Any] = {}
    for class_slug, spell_list in lists.items():
        heading_levels = {
            s["name"]: s["level"]
            for s in index["categories"]["spells"]
            if class_slug in s["classes"]
        }
        list_records[class_slug] = spell_list_entry(
            class_slug,
            spell_list,
            names,
            heading_levels,
            tables[class_slug],
            pages[class_slug],
            divergences["spell_lists"].get(class_slug),
        )
    return {
        "description": (
            "The SRD 5.2.1 rules-text audit (spec 02a, acceptance criterion 4): one entry per"
            " audited corpus record, built by tools/srd_audit/ledger.py from the pinned PDF."
        ),
        "source": index["source"],
        "audited_categories": list(AUDITED_CATEGORIES),
        "records": {
            "items": item_entries(index, divergences, with_changes),
            "monsters": monster_entries(index, divergences, with_changes),
            "spell_lists": list_records,
            "spells": spell_records,
        },
    }


def render(ledger: Mapping[str, Any]) -> str:
    return json.dumps(ledger, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the ledger is stale")
    args = parser.parse_args(argv)
    rendered = render(build())
    if args.check:
        if LEDGER_PATH.read_text(encoding="utf-8") != rendered:
            print("[srd-ledger] audit/srd_5_2_1_ledger.json is stale; run make srd-ledger")
            return 1
        print("[srd-ledger] OK")
        return 0
    LEDGER_PATH.write_text(rendered, encoding="utf-8")
    ledger = load_json(LEDGER_PATH)
    for category in AUDITED_CATEGORIES:
        statuses = [entry["status"] for entry in ledger["records"][category].values()]
        counts = ", ".join(f"{status}={statuses.count(status)}" for status in STATUSES)
        print(f"[srd-ledger] {category}: {len(statuses)} ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
