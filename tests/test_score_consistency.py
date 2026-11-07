import json,subprocess
from pathlib import Path
from tennis_oracle.jupiter import score_supports

def test_set_and_game_markets_python_js_parity():
    f={"final_score":{"A_sets":2,"B_sets":1,"sets":["6-3","3-6","6-4"]},"games_projected":27}
    cases=[('moneyline',{'name':'Alpha'},True),('moneyline',{'name':'Beta'},False),('handicap',{'name':'Beta','line':1.5},True),('handicap',{'name':'Alpha','line':-1.5},False),('tennis_set_totals',{'ou':'over','line':3},False),('tennis_set_totals',{'ou':'over','line':2.5},True),('tennis_match_totals',{'ou':'over','line':28.5},False),('tennis_match_totals',{'ou':'under','line':28.5},True),('tennis_game_handicap',{'name':'Beta','line':2},False),('tennis_game_handicap',{'name':'Beta','line':2.5},True)]
    for t,lab,want in cases: assert score_supports({'sportsMarketType':t},lab,f,'Alpha','Beta')==want
    js=Path(__file__).parents[1]/'docs/markets.js'
    actual=json.loads(subprocess.check_output(['node','-e',"const M=require(process.argv[1]);const [cs,f]=JSON.parse(process.argv[2]);console.log(JSON.stringify(cs.map(([t,l])=>M.scoreSupports({sportsMarketType:t},l,f,'Alpha','Beta'))))",str(js),json.dumps([cases,f])]))
    assert actual==[x[2] for x in cases]
    assert not score_supports({'sportsMarketType':'moneyline'},{'name':'Alpha'},{},'Alpha','Beta')
