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
        self.assertIn("indexPointField(i,m,t,n)",fields)
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
view='2026-11-04';const hist={date:'2026-11-04',index_minutes:{sh000905:{date:'2026-11-04',interval_minutes:5,t:['1325','1330'],p:[6384.644,6385]}}};
assert.deepStrictEqual(matchedIndexValues(3,hist,['13:25','13:26']),[6384.644,null]);
const backfill=indexPointField(3,hist,['13:25','13:26'],'中证500');assert.equal(backfill.name,'中证500指数 · 5分钟');assert.equal(backfill.format(backfill.data[0]),'6384.64 点');assert.equal(backfill.format(backfill.data[1]),'无5分钟点');
const missing=indexPointField(3,{date:'2026-11-04'},['13:25'],'中证500');assert.equal(missing.format(null),'当日未存档');
assert.deepStrictEqual(matchedIndexValues(3,{...hist,date:'2026-11-03'},['13:25']),[null]);view='live';
const fields=[{name:'指数',data:[1234.56,1235],format:v=>pointNumber(v,2,' 点')},{name:'ETF价格',data:[1.305,0],format:v=>pointNumber(v,3)}];
const opt=compactHover(chart,['09:30','09:31'],fields);let html=opt.formatter([{dataIndex:0},{dataIndex:0}]);assert.equal(html.split('09:30').length,2);assert(html.includes('1234.56 点'));assert(!html.includes('MACD'));
assert.equal(pointNumber(null,2),'—');assert.equal(pointNumber(0,2),'0.00');
let pos=hoverPosition(plot,[20,100],{contentSize:[180,140]});assert.equal(pos[0],32);assert.equal(pos[1],30); // 12px beside pointer, inside chart
pos=hoverPosition(plot,[280,100],{contentSize:[180,140]});assert.equal(pos[0],88); // flip left while staying inside
const narrow={getBoundingClientRect(){return {left:21,right:369,top:220,bottom:464,width:348}}};document.documentElement.clientWidth=390;pos=hoverPosition(narrow,[170,165],{contentSize:[180,140]});assert.equal(pos[1],13);assert(pos[0]+21>=29);assert(pos[0]+21+180<=361); // above pointer, still inside
const small={getBoundingClientRect(){return {left:400,right:620,top:220,bottom:464,width:220}}};document.documentElement.clientWidth=1000;pos=hoverPosition(small,[160,100],{contentSize:[180,200]});assert.equal(pos[0],172); // only overflow when no in-chart placement fits
const edge={getBoundingClientRect(){return {left:770,right:990,top:550,bottom:790,width:220}}};pos=hoverPosition(edge,[210,220],{contentSize:[240,260]});assert.equal(pos[0],-42);assert(pos[1]+550>=8);assert(pos[1]+550+260<=792);
document.documentElement.clientWidth=390;pos=hoverPosition(narrow,[170,100],{contentSize:[180,140]});assert(pos[0]+21>=8);assert(pos[0]+21+180<=382);assert(pos[1]+220>=8);assert(pos[1]+220+140<=792); // narrow viewport, adjacent overflow rather than distant chart edge
plot.onkeydown({key:'Home',preventDefault(){}});assert.equal(actions.at(-1).dataIndex,0);plot.onkeydown({key:'ArrowRight',preventDefault(){}});assert.equal(actions.at(-1).dataIndex,1);
"""
        result=subprocess.run(['node','-e',fake+code+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_shared_percentage_axis_ignores_price_level_and_invalid_points(self):
        source=(ROOT/'index.html').read_text()
        code=source.split('/* ===== Shared ETF percentage axis: prices never enter this scale ===== */',1)[1].split('/* ===== End shared ETF percentage axis ===== */',1)[0]
        fake="const assert=require('assert');const uiNumber=v=>v==null||v===''||!Number.isFinite(+v)?null:+v;"
        checks="""
const day='2026-11-23';
const series=[{date:'20261123',pre:100,p:[99,99.5]},{date:day,pre:6,p:[6.06,5.88]},{date:day,pre:1,p:[.96,null,NaN,Infinity]}];
const before=JSON.stringify(series),axis=sharedPctAxis(series,day);
assert.deepStrictEqual(axis,{min:-4.5,max:4.5,interval:2.25});assert.equal(JSON.stringify(series),before);
assert.deepStrictEqual(sharedPctAxis([{pre:6000,p:[6060,5880]}]),sharedPctAxis([{pre:6,p:[6.06,5.88]}]));
assert.deepStrictEqual(sharedPctAxis([{pre:1,p:[1,1]}]),{min:-.5,max:.5,interval:.25});
assert.deepStrictEqual(sharedPctAxis([]),{min:-.5,max:.5,interval:.25});
assert.deepStrictEqual(etfPercentSeries({pre:1,p:[null,'',NaN,Infinity,0,-1]}),[null,null,null,null,null,null]);
assert.deepStrictEqual(sharedPctAxis([{date:'20261122',pre:1,p:[2]},...series],day),axis);
const newer=sharedPctAxis([...series,{date:day,pre:1,p:[.9]}],day);assert(newer.max>=11);assert.equal(newer.min,-newer.max);
"""
        result=subprocess.run(['node','-'],input=fake+code+checks,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('sharedPctAxis(ms,TODAY)',source)
        self.assertIn("sharedPctAxis(E.map(e=>m?.etfs?.[e[1]]),m?.date||d)",source)
        self.assertIn('drawEtf(i,m,pctAxis)',source)
        self.assertNotIn('{name:"价格",type:"line",data:p',source)
        self.assertIn("name:'ETF价格',data:p",source)

    def test_real_index_date_parser_and_optional_failure(self):
        import sys;sys.path.insert(0,str(ROOT))
        from index_quotes import parse_index_minutes,fetch_index_minutes
        payload={'data':{'sh000688':{'data':{'date':'20261118','data':['0930 1234.56 1 2','0931 1235.00 1 2']}}}}
        self.assertEqual(parse_index_minutes(payload,'sh000688','20261118')['p'],[1234.56,1235.0])
        with self.assertRaises(ValueError):parse_index_minutes(payload,'sh000688','20261117')
        self.assertEqual(fetch_index_minutes('20261118',fetch=lambda url:(_ for _ in ()).throw(OSError('offline'))),{})

if __name__=='__main__':unittest.main()
