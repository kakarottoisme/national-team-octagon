"""Compact floating tooltip and real index quote display regression."""
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]

class PointReadoutTests(unittest.TestCase):
    def test_compact_floating_display_not_bottom(self):
        source=(ROOT/'index.html').read_text()
        self.assertNotIn('point-readout',source)
        self.assertIn('appendToBody:true,confine:false',source)
        self.assertIn("triggerOn:'mousemove|click'",source)
        fields=source[source.index('const tooltip=compactHover(charts[i]'):source.index('charts[i].setOption',source.index('const tooltip=compactHover(charts[i]'))]
        for name in ('MACD','DIF','DEA'): self.assertNotIn("name:'"+name+"'",fields)
        self.assertIn("name:n+'指数'",fields)
        self.assertIn("name:'ETF价格'",fields)

    def test_formatter_single_time_missing_date_and_outside_position(self):
        source=(ROOT/'index.html').read_text()
        code=source[source.index('const INDEX_CODES='):source.index('/* End compact floating readout. */')]
        fake="""
const assert=require('assert');const uiNumber=v=>v==null||v===''||!Number.isFinite(+v)?null:+v;const uiEsc=v=>String(v).replace(/</g,'&lt;');
let LIVE={date:'20261118',index_minutes:{sh000688:{date:'20261118',t:['0930','0932'],p:[1234.56,1235]}}},view='live';
const document={documentElement:{clientWidth:1000}},innerWidth=1000,innerHeight=800;
let actions=[];const plot={dataset:{},getBoundingClientRect(){return {left:100,right:400,top:200,bottom:440,width:300}},setAttribute(){}};
const chart={getDom(){return plot},dispatchAction(a){actions.push(a)}};
"""
        checks="""
assert.deepStrictEqual(matchedIndexValues(6,{date:'20261118'},['09:30','09:31']),[1234.56,null]);
assert.deepStrictEqual(matchedIndexValues(6,{date:'20261117'},['09:30']),[null]);
const fields=[{name:'指数',data:[1234.56,1235],format:v=>pointNumber(v,2,' 点')},{name:'ETF价格',data:[1.305,0],format:v=>pointNumber(v,3)}];
const opt=compactHover(chart,['09:30','09:31'],fields);let html=opt.formatter([{dataIndex:0},{dataIndex:0}]);assert.equal(html.split('09:30').length,2);assert(html.includes('1234.56 点'));assert(!html.includes('MACD'));
assert.equal(pointNumber(null,2),'—');assert.equal(pointNumber(0,2),'0.00');
let pos=hoverPosition(plot,[20,100],{contentSize:[180,140]});assert.equal(pos[0],314); // outside right edge
const narrow={getBoundingClientRect(){return {left:21,right:369,top:220,bottom:464,width:348}}};document.documentElement.clientWidth=390;pos=hoverPosition(narrow,[170,100],{contentSize:[180,140]});assert(pos[1]+220+140<=220);assert(pos[0]+21>=8);assert(pos[0]+21+180<=382);
plot.onkeydown({key:'Home',preventDefault(){}});assert.equal(actions.at(-1).dataIndex,0);plot.onkeydown({key:'ArrowRight',preventDefault(){}});assert.equal(actions.at(-1).dataIndex,1);
"""
        result=subprocess.run(['node','-e',fake+code+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_index_date_parser_and_optional_failure(self):
        import sys;sys.path.insert(0,str(ROOT))
        from index_quotes import parse_index_minutes,fetch_index_minutes
        payload={'data':{'sh000688':{'data':{'date':'20261118','data':['0930 1234.56 1 2','0931 1235.00 1 2']}}}}
        self.assertEqual(parse_index_minutes(payload,'sh000688','20261118')['p'],[1234.56,1235.0])
        with self.assertRaises(ValueError):parse_index_minutes(payload,'sh000688','20261117')
        self.assertEqual(fetch_index_minutes('20261118',fetch=lambda url:(_ for _ in ()).throw(OSError('offline'))),{})

if __name__=='__main__':unittest.main()
