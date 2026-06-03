"""i18n key-pariteit guard voor de portaal-vertalingen (B8).

Borgt dat alle vijf talen (nl/en/de/fr/tr) exact dezelfde set vertaal-keys
hebben. Een key die in één taal ontbreekt = een gat dat gebruikers (en de
client-side PDF-export, die dezelfde t()-tabel gebruikt) als onvertaalde tekst
of fallback te zien krijgen.

Het `var translations = {...}` object is geldige JS met o.a. Franse apostroffen
en accenten; daarom evalueren we het met Node (gezaghebbend) i.p.v. een fragiele
regex-parser. Zonder Node wordt de test overgeslagen.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

PORTAAL = pathlib.Path(__file__).resolve().parent.parent / "templates" / "portaal.html"

_JS_COMPARE = r"""
const langs = Object.keys(T);
const keysOf = (l) => Object.keys(T[l]);
const all = new Set();
langs.forEach((l) => keysOf(l).forEach((k) => all.add(k)));
const gaps = {};
langs.forEach((l) => {
  const ks = new Set(keysOf(l));
  const miss = [...all].filter((k) => !ks.has(k));
  if (miss.length) gaps[l] = miss;
});
console.log(JSON.stringify({
  langs,
  counts: Object.fromEntries(langs.map((l) => [l, keysOf(l).length])),
  gaps,
}));
"""


def _translations_block() -> str:
    """Extraheer het gebalanceerde `{...}` van `var translations = {...}`."""
    html = PORTAAL.read_text(encoding="utf-8")
    anchor = "var translations = "
    s = html.index(anchor) + len(anchor)
    assert html[s] == "{", "verwacht '{' na `var translations = `"
    depth = 0
    for i in range(s, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[s:i + 1]
    raise AssertionError("translations-blok niet gebalanceerd")


def _node_compare() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node niet beschikbaar voor i18n-pariteitscheck")
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("const T = " + _translations_block() + ";\n" + _JS_COMPARE)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, f"node-fout: {out.stderr}"
        return json.loads(out.stdout)
    finally:
        os.unlink(path)


def test_alle_talen_zelfde_keys():
    res = _node_compare()
    assert set(res["langs"]) == {"nl", "en", "de", "fr", "tr"}, res["langs"]
    assert res["gaps"] == {}, "i18n key-gaps tussen talen: " + json.dumps(res["gaps"], ensure_ascii=False)[:600]


def test_elke_taal_heeft_voldoende_keys():
    res = _node_compare()
    for lang, n in res["counts"].items():
        assert n >= 600, f"{lang}: onverwacht weinig keys ({n})"
