"""Parcours complet de l'interface en mode démo (aucun appel externe)."""

import os, pathlib

os.environ["DEMO_MODE"] = "1"
ROOT = pathlib.Path(__file__).parents[1]


def _click(at, label):
    btn = next(b for b in at.button if b.label.startswith(label))
    return btn.click().run()


def test_full_journey(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILES_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "src" / "app.py"), default_timeout=30).run()
    assert not at.exception
    # 1. Contexte : sans résumé, on ne va pas plus loin.
    assert any("contexte du client" in i.value for i in at.info)
    at.text_input[0].set_value("https://exemple.fr").run()
    at = _click(at, "Analyser le site")
    comp = next(t for t in at.text_area if t.label.startswith("Marques concurrentes"))
    comp.set_value("cartonexpress").run()
    at = _click(at, "Enregistrer le contexte")
    assert not at.exception
    # 2. Données
    at = _click(at, "Charger les termes")
    assert not at.exception, at.exception
    assert at.metric[0].value == "16"
    # 3. Entraînement : un lot est proposé, on valide une carte et on en corrige une.
    assert any(h.value == "3. Entraînement" for h in at.header)
    n_ok = len([b for b in at.button if b.label.startswith("✅")])
    assert n_ok > 0
    at = _click(at, "✅")
    assert len([b for b in at.button if b.label.startswith("✅")]) == n_ok - 1
    at = _click(at, "❌")
    dec = next(s for s in at.selectbox if s.label == "Ta décision")
    dec.set_value("À ignorer").run()
    reason = next(t for t in at.text_input if t.label.startswith("Pourquoi"))
    reason.set_value("Terme à garder sous surveillance").run()
    at = _click(at, "💾")
    assert not at.exception, at.exception
    saved = list(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8")
    assert "Terme à garder sous surveillance" in saved and "cartonexpress" in saved
    # Analyse du reste puis résultats.
    at = _click(at, "Analyser les")
    assert not at.exception, at.exception
    assert any(h.value == "4. Résultats" for h in at.header)
    labels = [m.label for m in at.metric]
    assert "Mots clés à acheter" in labels and "Exclusions à ajouter" in labels
    evaluated = next(m for m in at.metric if m.label == "Termes évalués").value
    assert evaluated == "16/16"
