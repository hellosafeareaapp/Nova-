const {chromium}=require('playwright');
const W=1600,H=900;
(async()=>{
const b=await chromium.launch();
const ctx=await b.newContext({viewport:{width:W,height:H},recordVideo:{dir:'vrec',size:{width:W,height:H}}});
const t0=Date.now();
const p=await ctx.newPage();
const errs=[];p.on('pageerror',e=>errs.push(e.message));
await p.goto('file://'+require('path').resolve(__dirname,'../../mockup/index.html'));
// overlay layer, captions and a phone mock, injected once
await p.evaluate(()=>{
 const st=document.createElement('style');st.textContent=`
 #vo{position:fixed;inset:0;z-index:9000;display:grid;place-items:center;background:#0b1b2b;color:#fff;font-family:Archivo,Inter,system-ui,sans-serif;opacity:1;transition:opacity .6s}
 #vo.off{opacity:0;pointer-events:none}
 #vo .t{text-align:center}#vo .k{font-size:15px;letter-spacing:.3em;text-transform:uppercase;color:#f5b800;font-weight:800}
 #vo h1{font-size:78px;margin:14px 0 6px;font-weight:900;letter-spacing:-.01em}#vo p{font-size:26px;color:#c9d1da;margin:0}
 #vo .sq{display:inline-grid;place-items:center;width:64px;height:64px;background:#f5b800;color:#0b1b2b;font-weight:900;font-size:34px;margin-bottom:10px}
 #vo img{max-width:100%;max-height:100%;object-fit:cover;width:100%;height:100%;transform:scale(1);transition:transform 6s ease-out}
 #vo iframe{width:1000px;height:820px;border:0;border-radius:14px;background:#fff;box-shadow:0 30px 80px rgba(0,0,0,.5)}
 #cap{position:fixed;left:50%;bottom:118px;transform:translate(-50%,20px);z-index:9500;max-width:1100px;padding:16px 26px;border-radius:16px;background:rgba(11,27,43,.92);color:#fff;font:600 27px/1.3 Archivo,Inter,system-ui,sans-serif;text-align:center;opacity:0;transition:opacity .45s,transform .45s;box-shadow:0 12px 40px rgba(0,0,0,.25)}
 #cap.on{opacity:1;transform:translate(-50%,0)}#cap b{color:#f5b800}
 .toast{bottom:200px!important}
 #hlbox{position:fixed;z-index:8000;pointer-events:none;border-radius:24px;box-shadow:0 0 0 5px #f5b800,0 0 44px 10px rgba(245,184,0,.5);opacity:0;transition:opacity .4s,top .4s,left .4s,width .4s,height .4s}#hlbox.on{opacity:1}
 #phone{position:fixed;right:70px;top:110px;z-index:9400;width:300px;height:600px;border-radius:46px;background:#111;padding:12px;box-shadow:0 30px 80px rgba(0,0,0,.45);transform:translateX(420px);transition:transform .7s cubic-bezier(.2,.8,.2,1)}
 #phone.on{transform:none}
 #phone .scr{width:100%;height:100%;border-radius:36px;background:linear-gradient(160deg,#2b3a55,#141c2b);padding:56px 12px 0;box-sizing:border-box;font-family:-apple-system,Inter,system-ui,sans-serif}
 #phone .tm{color:#fff;text-align:center;font-size:56px;font-weight:300;margin:6px 0 20px}
 #phone .nt{background:rgba(255,255,255,.88);border-radius:18px;padding:12px 14px;color:#111;transform:scale(.9);opacity:0;transition:all .5s .4s}
 #phone.on .nt{transform:none;opacity:1}
 #phone .nt .h{display:flex;gap:8px;align-items:center;font-size:12px;color:#555;margin-bottom:4px}#phone .nt .ic{width:20px;height:20px;border-radius:5px;background:#3f6fb0;display:grid;place-items:center;color:#fff;font-size:12px}
 #phone .nt b{font-size:15px;display:block;margin-bottom:2px}#phone .nt span{font-size:14px;line-height:1.3}`;
 document.head.appendChild(st);
 const vo=document.createElement('div');vo.id='vo';document.body.appendChild(vo);
 const cap=document.createElement('div');cap.id='cap';document.body.appendChild(cap);
 const ph=document.createElement('div');ph.id='phone';const hm=(()=>{const d=new Date();return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')})();ph.innerHTML='<div class="scr"><div class="tm">'+hm+'</div><div class="nt"><div class="h"><span class="ic">🔔</span>ntfy · A.D.A. Demo home · now</div><b>🚨 Diesel dropped while the generator was off</b><span class="msg">5 L at '+hm+'. Check the tank and who had access.</span></div></div>';document.body.appendChild(ph);
 const hb=document.createElement('div');hb.id='hlbox';document.body.appendChild(hb);
 setInterval(()=>{const e=V.sel&&document.querySelector(V.sel);if(!e){hb.classList.remove('on');return;}const r=e.getBoundingClientRect();Object.assign(hb.style,{top:(r.top-6)+'px',left:(r.left-6)+'px',width:(r.width+12)+'px',height:(r.height+12)+'px'});},120);
 window.V={
  slide:(html)=>{vo.innerHTML=html;vo.classList.remove('off');},
  hide:()=>vo.classList.add('off'),
  cap:(h)=>{cap.classList.remove('on');setTimeout(()=>{cap.innerHTML=h;if(h)cap.classList.add('on');},h?250:0);},
  hl:(sel)=>{V.sel=sel;const e=sel&&document.querySelector(sel);if(e)e.scrollIntoView({behavior:'smooth',block:'center'});hb.classList.toggle('on',!!e);},
  phone:(on)=>{if(on){const d=new Date(),t=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');ph.querySelector('.tm').textContent=t;ph.querySelector('.nt .msg').textContent='5 L at '+t+'. Check the tank and who had access.';}ph.classList.toggle('on',on);}
 };
 V.slide('<div class="t"><div class="sq">A</div><div class="k">A.D.A.</div><h1>Always-on Domestic Automation</h1><p>Power, diesel and water for Nigerian homes and buildings</p></div>');
});
const tStart=(Date.now()-t0)/1000;
const w=ms=>p.waitForTimeout(ms);
// demo state
await p.evaluate(()=>{S.look.dark=false;S.view='dash';S.dash='home';S.auto='suggest';SIM.src='nepa';SIM.v=228;SIM.batt=86;SIM.tank=48;SIM.fuel=72;SIM.on.pump=0;SIM.on.acLiv=1;SIM.on.acBed=1;SIM.on.tv=1;update();});
await w(4000);
// 1. overview
await p.evaluate(()=>{V.hide();V.cap('One dashboard for the whole building: <b>NEPA, generator, inverter, water and diesel</b>, live');});
await w(3500);await p.evaluate(()=>window.scrollTo({top:420,behavior:'smooth'}));await w(2500);await p.evaluate(()=>window.scrollTo({top:0,behavior:'smooth'}));await w(1500);
// 2. NEPA goes -> inverter, shed
await p.evaluate(()=>{V.hl('[aria-label="Power"]');V.cap('NEPA takes light: A.D.A. switches to <b>inverter</b> and sheds the ACs and pump so the battery lasts the night');});
await w(2200);await p.evaluate(()=>denseAct('dsrc','inv')||update());await p.evaluate(()=>update());await w(5200);
// 3. diesel theft -> phone
await p.evaluate(()=>{S.dash='power';update();});await w(600);
await p.evaluate(()=>{setSrc('nepa');update();V.hl('[aria-label="Generator and diesel"]');V.cap('Diesel goes missing while the generator is off? <b>A.D.A. measures the tank</b> and flags it');});
await w(2600);await p.evaluate(()=>{denseAct('ftheft');update();V.hl('[aria-label="Generator and diesel"]');});await w(1200);
await p.evaluate(()=>{V.phone(true);V.cap('…and the manager’s <b>phone gets an alert</b> straight away');});await w(5000);await p.evaluate(()=>V.phone(false));await w(500);
// 4. short delivery
await p.evaluate(()=>{V.cap('Vendor says <b>25 litres</b>. The tank only rose 20. <b>Short delivery caught.</b>');});
await p.evaluate(()=>{denseAct('dlvopen');update();});await w(900);
await p.fill('#d-vendor','Sunny Oil');await w(300);await p.fill('#d-litres','25');await w(700);
await p.click('[data-act="dlvsave"]');await w(500);
await p.evaluate(()=>{SIM.fuel=40;denseAct('frefill');update();V.hl('[aria-label="Generator and diesel"]');});await w(5200);
// 5. water
await p.evaluate(()=>{V.hl(null);S.dash='water';SIM.tank=34;SIM.on.pump=1;update();V.cap('The borehole pump <b>fills the tank by itself</b> when there is power, and stops at 95%');});
await w(400);await p.evaluate(()=>{const d=document.getElementById('dense');d.style.transformOrigin='0 0';d.style.transition='transform 1.2s ease';d.style.transform='scale(2.3) translate(12px,-6px)';});await w(6600);
await p.evaluate(()=>{const d=document.getElementById('dense');d.style.transform='';});
// 6. weekly report
await p.evaluate(()=>{V.hl(null);V.slide('<iframe id="rep"></iframe>');document.getElementById('rep').srcdoc=reportHTML(demoReport(7));V.cap('Every week: <b>hours on NEPA, gen and inverter, diesel used and its cost</b>, ready for the owner');});
await w(2500);await p.evaluate(()=>{const f=document.getElementById('rep');f.contentWindow.scrollTo({top:620,behavior:'smooth'});});await w(4500);
// 7. 3D panel
await p.evaluate(()=>{V.slide('<img id="r1" src="file:///home/user/Nova-/cad/out/renders/ada_panel_hero.png">');V.cap('The A.D.A. panel: <b>standard DIN-rail parts</b>, installed by your electrician');setTimeout(()=>document.getElementById('r1').style.transform='scale(1.08)',100);});
await w(4200);
await p.evaluate(()=>{V.slide('<img id="r2" src="file:///home/user/Nova-/cad/out/renders/ada_panel_controller_closeup.png">');V.cap('Runs <b>offline</b> on the building’s Wi-Fi. Internet is only needed for phone alerts');setTimeout(()=>document.getElementById('r2').style.transform='scale(1.06)',100);});
await w(4200);
// 8. end card
await p.evaluate(()=>{V.cap('');V.slide('<div class="t"><div class="sq">A</div><div class="k">Pilot A.D.A. in one building</div><h1 style="font-size:64px">Ajala David</h1><p>Founder, A.D.A. · +234 916 107 8549 (call or WhatsApp)</p></div>');});
await w(5000);
const total=(Date.now()-t0)/1000;
await ctx.close();await b.close();
console.log(JSON.stringify({tStart,total,errs}));
})();
