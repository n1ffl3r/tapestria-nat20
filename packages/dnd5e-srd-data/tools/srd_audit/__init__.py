"""The SRD 5.2.1 rules-text audit (spec 02a, acceptance criterion 4).

``pdf_text`` cuts each record's text out of the pinned PDF, a category module (``spells``)
reads its mechanical fields from that text and from the corpus record, and ``ledger``
compares them and writes ``audit/srd_5_2_1_ledger.json``. Build-time only.
"""
