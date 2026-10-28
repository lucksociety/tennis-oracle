"""The browser engine (docs/engine.js) must reproduce the Python exact model."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from tennis_oracle import scoring as S

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python():
    js = f"""const T=require({json.dumps(str(ROOT / 'docs' / 'engine.js'))});
    console.log(JSON.stringify([T.holdProb(0.63), T.tiebreakProb(0.68,0.62,0,7), T.matchWinProb(0.67,0.62,3,'tb7'),
      T.matchWinProb(0.67,0.62,5,'tb10'), T.mixtureDist(0.66,0.62,3,'tb7',0.25,-0.05,5).pWin]));"""
    out = json.loads(subprocess.check_output(["node", "-e", js], text=True))
    py = [S.hold_prob(0.63), S.tiebreak_prob(0.68, 0.62, S.A, 7), S.match_win_prob(0.67, 0.62, 3, "tb7"),
          S.match_win_prob(0.67, 0.62, 5, "tb10"), S.mixture_distribution(0.66, 0.62, 3, "tb7", 0.25, -0.05, 5).p_win]
    for a, b in zip(out, py):
        assert a == pytest.approx(b, abs=1e-9)
