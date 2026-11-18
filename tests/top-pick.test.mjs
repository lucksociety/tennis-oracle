import assert from 'node:assert';
import { topPick } from '../worker/src/index.js';
assert.match(topPick('Carlos Alcaraz', 0.84, 0.88), /Top pick: Carlos Alcaraz to win\*\* · model 84% · price about 88¢ · price is above the model’s number/);
assert.match(topPick('Arthur Fils', 0.544, 0.5), /none, too close to call \(Arthur Fils 54%\)/);
assert.match(topPick('A', 0.7, null), /no price available/);
console.log('top-pick: ok');
