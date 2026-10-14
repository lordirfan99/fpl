import {test,expect} from '@playwright/test';
import {capturedXiPoints} from '../../lib/projection-coverage';
import type {LineupPlayer} from '../../lib/lineup';

function squad():LineupPlayer[] {return Array.from({length:15},(_,i)=>({id:i+1,xpts:3}));}
test('unavailable or partial projections cannot become zero xPts',()=>{
  const s=squad();
  s[14].xpts=undefined;
  expect(capturedXiPoints(s.slice(0,11),s.slice(11),1)).toBeUndefined();
  s[14].xpts=NaN;
  expect(capturedXiPoints(s.slice(0,11),s.slice(11),1)).toBeUndefined();
});
test('known zero projections remain zero and captain is counted once extra',()=>{
  const s=squad();
  expect(capturedXiPoints(s.slice(0,11),s.slice(11),1)).toBe(36);
  s.forEach(p=>p.xpts=0);
  expect(capturedXiPoints(s.slice(0,11),s.slice(11),1)).toBe(0);
});
