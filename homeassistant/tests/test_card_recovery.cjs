/* Regression: HA restart while the PSU is off must never blink controls. */
const {JSDOM}=require("jsdom");
const fs=require("node:fs");
const path=require("node:path");
const assert=require("node:assert/strict");
const dom=new JSDOM("<body></body>",{url:"https://ha.example.invalid/",runScripts:"outside-only",pretendToBeVisual:true});
const {window}=dom;
window.console.info=()=>{};
window.eval(fs.readFileSync(path.join(__dirname,"../www/rd6006p-card.js"),"utf8"));
const calls=[];
const hass={language:"ru",states:{},callService:async(...args)=>{calls.push(args);},callApi:async()=>[]};
function makeCard(){
  const card=window.document.createElement("rd6006p-card");
  card.setConfig({type:"custom:rd6006p-card",language:"ru"});
  window.document.body.append(card);
  return card;
}
function expectOffline(card){
  const root=card.shadowRoot;
  assert.equal(root.querySelector(".dot").classList.contains("online"),false);
  assert.equal(root.querySelector("#connection-label").textContent,"Нет свежих данных");
  assert.equal(root.querySelector("#output-value").textContent,"—");
  for(const id of ["output-on","output-off"]){
    const button=root.querySelector(`#${id}`);
    assert.equal(button.classList.contains("active"),false);
    assert.equal(button.disabled,true);
    assert.equal(button.getAttribute("aria-pressed"),"false");
  }
}

(async()=>{
  const card=makeCard();
  const originalShell=card.shadowRoot.querySelector(".shell");
  for(let i=0;i<12;i++){
    card.hass=hass;
    expectOffline(card);
    assert.equal(card.shadowRoot.querySelector(".shell"),originalShell);
  }
  const map={output:"switch.renamed_supply",voltage_set:"number.renamed_v",current_set:"number.renamed_i",connection:"binary_sensor.renamed_link"};
  hass.states[map.connection]={entity_id:map.connection,state:"off",attributes:{rd6006p_serial:"3111",rd6006p_key:"connection",entity_map:map}};
  for(const state of [undefined,null,"","unknown","unavailable","None","on"]){
    if(state===undefined)delete hass.states[map.output];
    else hass.states[map.output]={entity_id:map.output,state,attributes:state==="on"?{restored:true}:{}};
    for(let i=0;i<3;i++){card.hass=hass;expectOffline(card);}
  }
  // Real fresh state arrives after the PSU is switched on. No user command.
  hass.states[map.connection].state="on";
  hass.states[map.output]={entity_id:map.output,state:"off",attributes:{rd6006p_serial:"3111",rd6006p_key:"output"}};
  hass.states[map.voltage_set]={entity_id:map.voltage_set,state:"2.6",attributes:{rd6006p_serial:"3111",rd6006p_key:"voltage_set",unit_of_measurement:"V",max:60.1}};
  hass.states[map.current_set]={entity_id:map.current_set,state:"0.05",attributes:{rd6006p_serial:"3111",rd6006p_key:"current_set",unit_of_measurement:"A",max:6}};
  for(let i=0;i<6;i++){
    card.hass=hass;
    const root=card.shadowRoot;
    assert.equal(root.querySelector(".dot").classList.contains("online"),true);
    assert.equal(root.querySelector("#connection-label").textContent,"На связи");
    assert.equal(root.querySelector("#output-off").classList.contains("active"),true);
    assert.equal(root.querySelector("#output-on").classList.contains("active"),false);
    assert.equal(root.querySelector("#output-on").disabled,false);
    assert.equal(root.querySelector("#voltage_set-actual").textContent,"2,600 V");
  }
  // The state stream can temporarily omit metadata during entity reload.
  hass.states={};
  for(let i=0;i<6;i++){
    card.hass=hass;
    expectOffline(card);
    assert.equal(card._serial,"3111");
    assert.equal(card._ids.output,map.output);
  }
  const fresh=makeCard();
  for(let i=0;i<6;i++){fresh.hass=hass;expectOffline(fresh);}
  assert.equal(calls.length,0);
  console.log("PASS: no blinking with missing/restored states, automatic recovery, stable IDs and no commands during reconnect");
  window.close();
})().catch(err=>{console.error(err);window.close();process.exit(1);});
