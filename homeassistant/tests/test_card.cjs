/* Run with Node + Playwright: node tests/test_card.cjs
 * Only simulated HA data is used. Screenshots are optional local QA outputs.
 */
const {chromium} = require("playwright");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  const page = await browser.newPage({viewport:{width:1500,height:850},deviceScaleFactor:1});
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.setContent(`<html><head><style>body{margin:22px;background:#111315;--ha-card-background:#151719;--primary-text-color:#e4e8ed;--secondary-text-color:#9198a1;color:#eee;font-family:Arial,sans-serif}</style></head><body></body></html>`);
  await page.addScriptTag({path:path.join(__dirname,"../www/rd6006p-card.js")});
  await page.evaluate(() => {
    window.calls=[];
    const states={};
    const put=(key,domain,value,attributes={})=>{
      const entity_id=`${domain}.rd6006p_${key}`;
      states[entity_id]={entity_id,state:String(value),attributes:{rd6006p_serial:"3111",rd6006p_key:key,...attributes}};
    };
    put("output","switch","on",{web_control_url:"http://psu.example.invalid/control/",diagnostics_url:"http://psu.example.invalid/diagnostics/modbus"});
    put("voltage_set","number",2.6,{min:0,max:3,step:.001});
    put("current_set","number",.05,{min:0,max:.1,step:.0001});
    put("voltage","sensor",2.599);
    put("current","sensor",.0247);
    put("power","sensor",.064);
    put("output_mode","sensor","cv");
    put("protection","sensor","normal");
    put("battery_mode","sensor","on");
    put("battery_voltage","sensor",4.17,{unit_of_measurement:"V"});
    put("charge","sensor",3.238,{unit_of_measurement:"Ah"});
    put("energy","sensor",12.088,{unit_of_measurement:"Wh"});
    window.mockHass={language:"ru",states,callApi:async(method,url)=>{
      const now=Date.now(), rows=[[],[]];
      for(let i=0;i<181;i++) {
        const timestamp=new Date(now-3600000+i*20000).toISOString();
        rows[0].push({entity_id:"sensor.rd6006p_voltage",state:i>90&&i<105?"unavailable":String(i<20?0:2.599+Math.sin(i/6)*.002),last_changed:timestamp});
        rows[1].push({entity_id:"sensor.rd6006p_current",state:i>90&&i<105?"unavailable":String(i<20?0:.0247+Math.sin(i/12)*.001),last_changed:timestamp});
      }
      return rows;
    },callService:async(domain,service,data)=>{
      calls.push({domain,service,data});
      await new Promise(resolve=>setTimeout(resolve,30));
      if(window.failCommand)throw new Error("Readback differs; actual state is displayed");
      states[data.entity_id].state=domain==="number"?String(data.value):service==="turn_on"?"on":"off";
      window.card.hass=mockHass;
    }};
    window.card=document.createElement("rd6006p-card");
    card.setConfig({type:"custom:rd6006p-card",title:"Riden RD6006P",language:"ru",hours_to_show:1});
    document.body.append(card);
    card.hass=mockHass;
  });
  const card=page.locator("rd6006p-card");
  await page.waitForFunction(()=>document.querySelector("rd6006p-card")._historyAt>0);
  assert.equal(await card.locator("#current-value").textContent(),"0,0247 A");
  assert.equal((await card.locator("svg path").count()),2);
  await card.locator("#voltage_set-input").fill("2,700");
  assert.equal(await page.evaluate(()=>calls.length),0);
  await card.locator('.plus[data-key="voltage_set"]').click();
  assert.equal(await page.evaluate(()=>calls.length),0);
  await card.locator("#voltage_set-apply").click();
  await page.waitForFunction(()=>!card._pending.size);
  assert.deepEqual(await page.evaluate(()=>calls.at(-1)),{domain:"number",service:"set_value",data:{entity_id:"number.rd6006p_voltage_set",value:2.8}});
  await card.locator("#output-off").click();
  await page.waitForFunction(()=>!card._pending.size);
  assert.equal((await page.evaluate(()=>calls.at(-1))).service,"turn_off");
  await card.locator("#output-on").click();
  await page.waitForFunction(()=>!card._pending.size);
  assert.equal((await page.evaluate(()=>calls.at(-1))).service,"turn_on");
  await card.locator("#current_set-input").fill("0.0001");
  await card.locator("#current_set-apply").click();
  await page.waitForFunction(()=>!card._pending.size);
  assert.equal((await page.evaluate(()=>calls.at(-1))).data.value,.0001);
  const count=await page.evaluate(()=>calls.length);
  await card.locator("#current_set-input").fill("NaN");
  await card.locator("#current_set-apply").click();
  assert.equal(await page.evaluate(()=>calls.length),count);
  await page.evaluate(()=>{window.failCommand=true;});
  await card.locator("#current_set-input").fill("0.02");
  await card.locator("#current_set-apply").click();
  await page.waitForFunction(()=>!card._pending.size);
  assert.match(await card.locator(".message").textContent(),/Readback differs/);
  assert.equal(await card.locator("#current_set-input").inputValue(),"0.02");
  await page.evaluate(()=>{window.failCommand=false;card._error="";card._dirty.clear();card.hass=mockHass;});
  if(process.env.QA_SCREENSHOT)await page.screenshot({path:process.env.QA_SCREENSHOT,fullPage:true});
  await page.setViewportSize({width:390,height:1180});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
  if(process.env.QA_MOBILE_SCREENSHOT)await page.screenshot({path:process.env.QA_MOBILE_SCREENSHOT,fullPage:true});
  await page.evaluate(()=>{mockHass.states['switch.rd6006p_output'].state='unavailable';mockHass.states['sensor.rd6006p_voltage'].state='unavailable';card.hass=mockHass;});
  assert.equal(await card.locator("#voltage-value").textContent(),"—");
  assert.equal(await card.locator("#output-on").isDisabled(),true);
  await page.evaluate(()=>{mockHass.states['sensor.rd6006p_voltage'].state='0';card.hass=mockHass;});
  assert.equal(await card.locator("#voltage-value").textContent(),"0,000 V");
  assert.deepEqual(errors,[]);
  console.log("PASS: card controls, explicit Apply, 0.0001 A, invalid input, readback error, gaps, real zeros, unavailable state and mobile layout");
  await browser.close();
})().catch(err=>{console.error(err);process.exit(1);});
