"""Non-overlapping linked-grid readout tests; no market data or account writes."""
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]

class PointReadoutTests(unittest.TestCase):
    def test_same_point_readout_is_not_a_floating_tooltip(self):
        source=(ROOT/'index.html').read_text()
        self.assertIn("showContent:false",source)
        self.assertIn("triggerOn:'mousemove|click'",source)
        self.assertEqual(source.count('bindPointReadout(charts['),2)
        self.assertIn('plot.parentElement.append(box)',source)
        self.assertNotIn('position:absolute',source[source.index('/* Point readout lives'):source.index('</style></head>')])

    def test_index_precision_missing_values_and_keyboard_reset(self):
        source=(ROOT/'index.html').read_text()
        block=source[source.index('function pointIndex('):source.index('const POINT_TIP=')]
        fake="""
const assert=require('assert');const uiNumber=v=>v==null||v===''||!Number.isFinite(+v)?null:+v;
const uiEsc=v=>String(v);let handlers={},zhandlers={},actions=[],box;
const plot={id:'c0',dataset:{},setAttribute(){},parentElement:{querySelector(){return box},append(value){box=value}}};
const document={createElement(){let time={},state={},values=[{},{}];return {setAttribute(){},querySelector(s){return s==='time'?time:state},querySelectorAll(){return values},time,state,values}}};
const chart={getDom(){return plot},off(n){delete handlers[n]},on(n,f){handlers[n]=f},getZr(){return {off(n){delete zhandlers[n]},on(n,f){zhandlers[n]=f}}},dispatchAction(a){actions.push(a)}};
"""
        checks="""
assert.equal(pointIndex('09:31',['09:30','09:31']),1);assert.equal(pointIndex(1,['a','b']),1);assert.equal(pointIndex(99,['a']),null);
assert.equal(pointNumber(null,2),'—');assert.equal(pointNumber(0,2,'%'),'0.00%');assert.equal(pointNumber(-.00244,5),'-0.00244');
bindPointReadout(chart,['09:30','09:31'],[{name:'价格',data:[1.305,1.31],format:v=>pointNumber(v,3)},{name:'额',data:[0,null],format:v=>pointNumber(v,4,' 亿')}]);
assert.equal(box.time.textContent,'09:31');handlers.updateAxisPointer({axesInfo:[{axisDim:'x',value:0},{axisDim:'x',value:0}]});assert.equal(box.time.textContent,'09:30');assert.equal(box.values[0].textContent,'1.305');assert.equal(box.values[1].textContent,'0.0000 亿');assert.equal(box.state.textContent,'所选时点');
let prevented=false;plot.onkeydown({key:'ArrowRight',preventDefault(){prevented=true}});assert(prevented);assert.equal(box.time.textContent,'09:31');assert.equal(box.values[1].textContent,'—');assert.equal(actions.at(-1).dataIndex,1);
zhandlers.globalout();assert.equal(box.state.textContent,'最新时点');
bindPointReadout(chart,['10:00'],[{name:'价格',data:[2],format:v=>pointNumber(v,3)}]);assert.equal(box.time.textContent,'10:00');assert.equal(Object.keys(handlers).length,1);
"""
        result=subprocess.run(['node','-e',fake+block+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
