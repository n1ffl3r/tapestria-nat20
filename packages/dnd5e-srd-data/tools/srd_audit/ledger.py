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
from pathlib import Path
from typing import Any

import yaml

from tools.srd_audit import pdf_text, spell_lists, spells
from tools.translators.foundry import spell_srd_corrections

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "audit" / "srd_5_2_1_ledger.json"
DIVERGENCES_PATH = ROOT / "audit" / "srd_5_2_1_divergences.json"
INDEX_PATH = ROOT / "srd" / "index.json"
CANONICAL = ROOT / "src" / "dnd5e_srd_data" / "canonical"
FOUNDRY = ROOT / "raw_sources" / "foundry"
AUDITED_CATEGORIES = ("spell_lists", "spells")
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
        "records": {"spell_lists": list_records, "spells": spell_records},
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
