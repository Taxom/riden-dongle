/* DOM tests without a browser or network. npm install --no-save jsdom */
const {JSDOM} = require("jsdom");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url:"https://ha.example.invalid/", runScripts:"outside-only", pretendToBeVisual:true
});
const {window} = dom;
window.console.info = () => {};
window.eval(fs.readFileSync(path.join(__dirname,"../www/rd6006p-card.js"),"utf8"));
// Reuse only the simulated HA fixture from the optional browser test.
const setup = fs.readFileSync(path.join(__dirname,"test_card.cjs"),"utf8")
  .split("await page.evaluate(() => {",2)[1].split("\n  });",1)[0];
window.eval(setup);
const card = window.card, root = card.shadowRoot;
const state = key => window.mockHass.states[`sensor.rd6006p_${key}`];
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function settle() {
  for(let i=0;i<100;i++) {
    if(!card._pending.size && !card._historyBusy) return;
    await sleep(5);
  }
  throw new Error("Operation did not finish");
}
function edit(key,value) {
  const input = root.querySelector(`#${key}-input`);
  input.value=value;
  input.dispatchEvent(new window.Event("input",{bubbles:true}));
}

(async()=>{
  await settle();
  assert.equal(root.querySelector(".label").textContent,"Выход");
  assert.equal(root.querySelector("#current-value").textContent,"0,0247 A");
  assert.equal(root.querySelector("#battery_mode-value").textContent,"Включён");
  assert.equal(root.querySelector("#battery_voltage-value").textContent,"4,17 V");
  assert.equal(root.querySelector("#charge-value").textContent,"3,238 Ah");
  assert.equal(root.querySelector("#energy-value").textContent,"12,088 Wh");
  assert.equal(window.calls.length,0);
  state("battery_mode").state="off";
  state("battery_voltage").state="0";
  state("charge").state="3238";
  state("charge").attributes.unit_of_measurement="mAh";
  state("energy").state="0.012088";
  state("energy").attributes.unit_of_measurement="kWh";
  card.hass=window.mockHass;
  assert.equal(root.querySelector("#battery_mode-value").textContent,"Выключен");
  assert.equal(root.querySelector("#battery_voltage-value").textContent,"0,00 V");
  assert.equal(root.querySelector("#charge-value").textContent,"3,238 Ah");
  assert.equal(root.querySelector("#energy-value").textContent,"12,088 Wh");
  assert.equal(root.querySelectorAll("svg path").length,2);
  assert.match(root.querySelector("svg path").getAttribute("d"),/ M.* M/);
  edit("voltage_set","2,700");
  root.querySelector('.plus[data-key="voltage_set"]').click();
  assert.equal(window.calls.length,0);
  root.querySelector("#voltage_set-apply").click();
  await settle();
  assert.equal(window.calls.at(-1).data.value,2.8);
  assert.equal(window.calls.at(-1).service,"set_value");
  root.querySelector("#output-off").click();
  assert.equal(root.querySelector("#output-value").textContent,"Включён");
  assert.equal(root.querySelector("#output-on").disabled,true);
  await settle();
  assert.equal(window.calls.at(-1).service,"turn_off");
  assert.equal(root.querySelector("#output-value").textContent,"Выключен");
  root.querySelector("#output-on").click();
  await settle();
  assert.equal(window.calls.at(-1).service,"turn_on");
  edit("current_set","0.0001");
  root.querySelector("#current_set-apply").click();
  await settle();
  assert.equal(window.calls.at(-1).data.value,.0001);
  const before=window.calls.length;
  for(const invalid of ["NaN","Infinity","-1","0.1001","", "1;alert(1)"]) {
    edit("current_set",invalid);
    root.querySelector("#current_set-apply").click();
    assert.equal(window.calls.length,before);
  }
  window.failCommand=true;
  edit("current_set","0.02");
  root.querySelector("#current_set-apply").click();
  await settle();
  assert.match(root.querySelector(".message").textContent,/Readback differs/);
  assert.equal(root.querySelector("#current_set-input").value,"0.02");
  assert.equal(root.querySelector("#current_set-actual").textContent,"0,0001 A");
  window.failCommand=false;
  window.mockHass.states["number.rd6006p_current_set"].state="50";
  window.mockHass.states["number.rd6006p_current_set"].attributes.unit_of_measurement="mA";
  window.mockHass.states["number.rd6006p_current_set"].attributes.max=100;
  state("current").state="24.7";
  state("current").attributes.unit_of_measurement="mA";
  state("power").state="64";
  state("power").attributes.unit_of_measurement="mW";
  card.hass=window.mockHass;
  assert.equal(root.querySelector("#current-value").textContent,"0,0247 A");
  assert.equal(root.querySelector("#power-value").textContent,"0,064 W");
  assert.equal(root.querySelector("#current_set-actual").textContent,"0,0500 A");
  edit("current_set","0.0200");
  root.querySelector("#current_set-apply").click();
  await settle();
  assert.equal(window.calls.at(-1).data.value,20);
  assert.equal(root.querySelector("#current_set-actual").textContent,"0,0200 A");
  window.mockHass.callApi=async()=>[
    [{entity_id:"sensor.rd6006p_voltage",state:"2.6",last_changed:new Date(Date.now()-1000).toISOString(),attributes:{unit_of_measurement:"V"}},
     {state:"2600",last_changed:new Date().toISOString(),attributes:{unit_of_measurement:"mV"}}],
    [{entity_id:"sensor.rd6006p_current",state:"0.025",last_changed:new Date(Date.now()-1000).toISOString(),attributes:{unit_of_measurement:"A"}},
     {state:"25",last_changed:new Date().toISOString(),attributes:{unit_of_measurement:"mA"}}]
  ];
  await card._loadHistory();
  assert.ok(Math.abs(card._history[0][0].value-card._history[0][1].value)<1e-12);
  assert.ok(Math.abs(card._history[1][0].value-card._history[1][1].value)<1e-12);
  const entity_map={};
  for(const [id,s] of Object.entries(window.mockHass.states)) entity_map[s.attributes.rd6006p_key]=id;
  const metadata_id="binary_sensor.renamed_rd_connection";
  window.mockHass.states[metadata_id]={entity_id:metadata_id,state:"off",attributes:{rd6006p_serial:"3111",rd6006p_key:"connection",entity_map}};
  for(const s of Object.values(window.mockHass.states)) {
    if(s.attributes.rd6006p_key!=="connection") {s.state="unavailable";s.attributes={};}
  }
  card.hass=window.mockHass;
  assert.equal(root.querySelector("#output-on").disabled,true);
  assert.equal(root.querySelector("#voltage-value").textContent,"—");
  for(const key of ["battery_mode","battery_voltage","charge","energy"]) {
    assert.equal(root.querySelector(`#${key}-value`).textContent,"—");
  }
  assert.equal(card._ids.output,"switch.rd6006p_output");
  assert.equal(card._discoveryError,"");
  state("voltage").state="0";
  card.hass=window.mockHass;
  assert.equal(root.querySelector("#voltage-value").textContent,"0,000 V");
  // A reload while offline can still discover entities from connectivity metadata.
  const reload=window.document.createElement("rd6006p-card");
  reload.setConfig({type:"custom:rd6006p-card",language:"ru"});
  window.document.body.append(reload);
  reload.hass=window.mockHass;
  assert.equal(reload._ids.output,"switch.rd6006p_output");
  assert.equal(reload._discoveryError,"");
  assert.equal(reload.shadowRoot.querySelector("#output-on").disabled,true);
  console.log("PASS: card DOM checks: draft/apply, ON/OFF, non-optimistic state, 0.0001 A, invalid input, command error, history gaps, real zero, offline discovery and reload");
  window.close();
})().catch(err=>{console.error(err);window.close();process.exit(1);});
