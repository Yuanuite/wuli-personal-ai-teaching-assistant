#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';

const [q2Input,q7Input,outputInput]=process.argv.slice(2);
if(!q2Input||!q7Input||!outputInput){
  console.error('usage: node test_field_trajectory_simulator.mjs <opposite-html> <electric-html> <output-dir>');
  process.exit(2);
}
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1100,height:820}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});
const expectText=async(selector,needle)=>{
  const value=(await page.locator(selector).textContent())||'';
  if(!value.includes(needle))errors.push(`${selector} expected ${JSON.stringify(needle)}, got ${JSON.stringify(value)}`);
};
const setRange=async(selector,value)=>page.locator(selector).evaluate((element,next)=>{
  element.value=String(next);
  element.dispatchEvent(new Event('input',{bubbles:true}));
  element.dispatchEvent(new Event('change',{bubbles:true}));
},value);

await page.goto(pathToFileURL(path.resolve(q2Input)).href);
await page.waitForTimeout(250);
await page.locator('[data-case-id="n4"]').click();
await setRange('#progress-field',1000);
await expectText('#result-field','23π/5');
await expectText('#event-field','回到 P');
await page.locator('[data-case-id="wrong-n4"]').click();
await setRange('#progress-field',1000);
await expectText('#result-field','未回到 P');
await expectText('#event-field','未闭合');
await page.locator('[data-case-id="n2"]').click();
await setRange('#progress-field',1000);
await expectText('#result-field','√3');
await page.locator('[data-layer="force"]').click();
await page.locator('[data-layer="geometry"]').click();
await page.locator('[data-case-id="n4"]').click();
await setRange('#progress-field',1000);
await page.screenshot({path:path.resolve(outputInput,'opposite-circular.png'),fullPage:true});

await page.goto(pathToFileURL(path.resolve(q7Input)).href);
await page.waitForTimeout(250);
await setRange('#b-ratio-field',1);
await setRange('#progress-field',1000);
await expectText('#result-field','恰好相切');
await expectText('#event-field','相切');
await setRange('#b-ratio-field',0.8);
await setRange('#progress-field',1000);
await expectText('#result-field','提前穿出');
await setRange('#b-ratio-field',1.2);
await setRange('#progress-field',1000);
await expectText('#result-field','未触及下边界');
await setRange('#h-ratio-field',1.5);
await expectText('#h-ratio-label-field','1.50');
await setRange('#b-ratio-field',1);
await setRange('#progress-field',1000);
await page.locator('[data-layer="geometry"]').click();
await page.screenshot({path:path.resolve(outputInput,'electric-magnetic.png'),fullPage:true});
await page.setViewportSize({width:390,height:844});
await page.screenshot({path:path.resolve(outputInput,'electric-magnetic-mobile.png'),fullPage:true});

const report={status:errors.length?'failed':'passed',errors,canvas:await page.locator('canvas').count(),q2:path.resolve(q2Input),q7:path.resolve(q7Input)};
console.log(JSON.stringify(report,null,2));
await browser.close();
process.exit(errors.length?1:0);
