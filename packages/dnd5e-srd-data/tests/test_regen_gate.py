"""Tests for tools.regen._gate_for_yaml, the per-file SRD admission gate (spec 02a, AC 2).

Distinct from test_srd_gate.py, which covers gate_decision(). These exercise the YAML path:
the document's name decides against the PDF index, and Foundry's license tag does not.
"""

import textwrap

from tools.regen import _gate_for_yaml


def _write(tmp_path, body: str):
    path = tmp_path / "doc.yml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_gate_admits_an_srd_name_with_a_license_tag(tmp_path):
    path = _write(
        tmp_path,
        """
        name: Shortsword
        flags: {}
        system:
          source:
            license: "CC-BY-4.0"
            rules: "2024"
        """,
    )
    assert _gate_for_yaml(path, "items").is_srd is True


def test_gate_admits_an_srd_name_without_a_license_tag(tmp_path):
    """Foundry's pin carries no CC-BY-4.0 tag on Ice Knife; the PDF lists it."""
    path = _write(
        tmp_path,
        """
        name: Ice Knife
        flags: {}
        system:
          source:
            license: ""
            rules: "2024"
        """,
    )
    assert _gate_for_yaml(path, "spells").is_srd is True


def test_gate_quarantines_a_non_srd_name_with_a_license_tag(tmp_path):
    """Foundry tags Arcane Vigor CC-BY-4.0; SRD 5.2.1 does not contain it."""
    path = _write(
        tmp_path,
        """
        name: Arcane Vigor
        flags: {}
        system:
          source:
            license: "CC-BY-4.0"
            rules: "2024"
        """,
    )
    decision = _gate_for_yaml(path, "spells")
    assert decision.is_srd is False
    assert decision.quarantine_reason == "not_in_srd_5_2_1"


def test_gate_quarantines_a_document_without_a_name(tmp_path):
    path = _write(tmp_path, "flags: {}\n")
    assert _gate_for_yaml(path, "spells").is_srd is False
