// Run shipped UI code with a minimal DOM and inspect actual chart formatting.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const header = fs.readFileSync('src/riden_http_server/http_static.h', 'utf8');
const block = header.slice(header.indexOf('static const char HTML_CONTROL_BODY[]'));
const strings = block.slice(0, block.indexOf('// END OF SECTION')).match(/"(?:\\.|[^"\\])*"/g);
const html = strings.map(s => JSON.parse(s)).join('');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const moduleScript = scripts.find(s => s.includes('class TimeSeriesChart'));
const cls = moduleScript.slice(moduleScript.indexOf('class TimeSeriesChart'), moduleScript.indexOf('function selectionHandler'));
const uiScript = scripts.find(s => s.includes('function set_data('));
const ui = uiScript.slice(uiScript.indexOf('function set_data('), uiScript.indexOf('let tick_done'));
const elements = {};
function element(id) {
  return elements[id] ??= {innerHTML:'',value:'',style:{},classList:{add(){},remove(){},toggle(){}},
    setAttribute(name,value) {this[name]=value;}};
}
const d3 = {
  format: spec => value => Number(value).toFixed(Number(spec.match(/\.(\d+)f/)[1])),
  min: (data,fn) => data.length ? Math.min(...data.map(fn)) : undefined,
  max: (data,fn) => data.length ? Math.max(...data.map(fn)) : undefined,
  mean: (data,fn) => data.length ? data.reduce((sum,d)=>sum+fn(d),0)/data.length : undefined,
};
const context = {console, d3, document:{getElementById:element}, Date};
context.self = context;
for(const id of ['set_v','set_c','out_v','out_i','ext_t_c','int_t_c','batt_v','ah','wh']) context[id]=element(id);
vm.createContext(context);
vm.runInContext(cls+'\nthis.TimeSeriesChart=TimeSeriesChart;\n'+ui,context);
context.chart = new context.TimeSeriesChart({data:[],format:'.2f',legendValues:['Min','Max','Avg','Last'],series:[
  {metric:'out_v',label:'Voltage (V)',legend:true}, {metric:'out_c',label:'Current (A)',legend:true},
]});
const fixture = {out_on:false,set_v:2.601,set_c:0.0501,out_v:2.603,out_c:0.0503,batt_v:3.001,
  cvmode:true,prot:'None',batt_mode:false,ext_t_c:null,int_t_c:26,ah:0,wh:0,max_v:60.1,max_c:6,
  range_button:false,i_range:0,precision:3,voltage_precision:3,current_precision:4};
context.set_data(JSON.stringify(fixture),true);
assert.equal(elements.out_v.innerHTML.trim(),'2.603 V');
assert.equal(elements.out_i.innerHTML.trim(),'0.0503 A');
assert.equal(elements.set_v.value,'2.601');assert.equal(elements.set_c.value,'0.0501');
assert.equal(elements.set_v.step,'0.001');assert.equal(elements.set_c.step,'0.0001');
assert.equal(elements.batt_v.innerHTML.trim(),'3.001');
assert.equal(context.chart.data[0].out_c,0.0503);
console.log('PASS: shipped control UI keeps RD6006P 3 V / 4 A decimals, matching input steps and battery precision');

const legends = context.chart._prepare(context.chart.data)[1];
assert(legends[0].text.includes('2.603'));
assert(legends[1].text.includes('0.0503'));
assert.equal(context.chart._formatValue(0.0003,'out_c'),'0.0003');
assert.equal(context.chart._formatValue(1.001,'out_v'),'1.001');
console.log('PASS: chart statistics and tooltip formatter preserve each series resolution');

elements.set_v.value='2.777';elements.set_c.value='0.1234';
context.set_data(JSON.stringify({...fixture,current_precision:3}),false);
assert.equal(elements.set_c.step,'0.001');
assert.equal(elements.set_c.value,'0.1234');assert.equal(elements.set_v.value,'2.777');
assert.equal(context.chart._formatValue(0.0503,'out_c'),'0.050');
console.log('PASS: current-range precision changes without overwriting edits during ordinary polling');

const legacy = {...fixture};delete legacy.voltage_precision;delete legacy.current_precision;legacy.precision=2;
context.set_data(JSON.stringify(legacy),true);
assert.equal(elements.out_v.innerHTML.trim(),'2.60 V');assert.equal(elements.set_c.step,'0.01');
delete legacy.precision;context.set_data(JSON.stringify(legacy),true);
assert.equal(elements.set_v.step,'0.01');
console.log('PASS: older status responses retain the shared-precision and two-decimal fallbacks');
