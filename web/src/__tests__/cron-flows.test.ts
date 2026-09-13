// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
// @ts-expect-error Native SPA is plain JavaScript.
import {cronView} from '../../../kairos_web/ui/js/views/cron.js';
const json=(body: unknown)=>new Response(JSON.stringify(body));
const flush=()=>new Promise(r=>setTimeout(r,0));
afterEach(()=>vi.unstubAllGlobals());

it('creates an actual schedule, pauses it and displays persisted execution history',async()=>{
 let jobs:any[]=[];
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
  if(url.endsWith('/status'))return json({running:true,last_tick:null});
  if(url.endsWith('/history'))return json({executions:[{id:'execution',status:'completed',conversation_id:'cron-execution',claimed_at:'2026-09-12T12:00:00Z'}]});
  if(init.method==='POST'){
   const body=JSON.parse(String(init.body));writes.push(body);
   const job={...body,id:'job',enabled:true,paused:false,next_run_at:'2026-09-12T12:00:00Z'};jobs.push(job);return json(job);
  }
  if(init.method==='PATCH'){jobs[0].paused=JSON.parse(String(init.body)).paused;return json(jobs[0]);}
  return json({jobs});
 }));
 const root=document.createElement('main');
 await cronView(root,{});
 (root.querySelector('[name=name]') as HTMLInputElement).value='Rotina';
 (root.querySelector('[name=prompt]') as HTMLInputElement).value='Faça o resumo';
 (root.querySelector('[name=kind]') as HTMLSelectElement).value='interval';
 root.querySelector('[name=kind]')!.dispatchEvent(new Event('change'));
 (root.querySelector('[name=minutes]') as HTMLInputElement).value='15';
 root.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await flush();await flush();
 expect(writes).toEqual([{name:'Rotina',prompt:'Faça o resumo',schedule:{kind:'interval',minutes:15}}]);
 expect(root.querySelectorAll('[data-job]')).toHaveLength(1);
 (root.querySelector('[data-pause]') as HTMLButtonElement).click();await flush();await flush();
 expect(root.textContent).toContain('Retomar');
 (root.querySelector('[data-history]') as HTMLButtonElement).click();await flush();
 expect(root.textContent).toContain('Concluída');
 expect(root.querySelector('a[href="#/chat?session=cron-execution"]')).not.toBeNull();
});

it('escapes stored job content and ignores replies after leaving the page',async()=>{
 let release!:(value:Response)=>void;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>url.endsWith('/status')?json({running:false}):new Promise<Response>(r=>{release=r;})));
 const root=document.createElement('main');
 const controller=new AbortController();
 const mount=cronView(root,{}, {signal:controller.signal});
 await flush();
 controller.abort();root.innerHTML='Outra página';
 release(json({jobs:[{id:'x',name:'<img src=x onerror=alert(1)>',schedule:{kind:'once'}}]}));
 await mount;
 expect(root.innerHTML).toBe('Outra página');
});

it('monitor flow: define, test, run on change and remove a source monitor',async()=>{
 let job:any={id:'j',name:'Vigiado',prompt:'Aja',schedule:{kind:'interval',minutes:5},next_run_at:'2026-09-12T12:00:00Z'};
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
  if(url.endsWith('/status'))return json({running:true,last_tick:null});
  if(url.includes('/monitor')&&init?.method==='PUT'){calls.push('set:'+JSON.parse(String(init.body)).script);job.monitor={type:'script',script:JSON.parse(String(init.body)).script};job.monitor_state={last_checked_at:null,last_changed_at:null};return json(job);}
  if(url.includes('/monitor/run')&&init?.method==='POST'){calls.push('run');return json({ok:true,output_chars:2,decision:'first_run'});}
  if(url.includes('/monitor')&&init?.method==='DELETE'){calls.push('remove');delete job.monitor;job.monitor_state=null;return json({cleared:true});}
  if(url.includes('/monitor'))return json({monitor:job.monitor,monitor_state:job.monitor_state});
  return json({jobs:[job]});
 }));
 const root=document.createElement('main');
 await cronView(root,{});
 // Define via editor button
 (root.querySelector('[data-monitor-input]') as HTMLInputElement).value='echo vigia';
 (root.querySelector('[data-monitor-save]') as HTMLButtonElement).click();await flush();await flush();
 expect(job.monitor!.script).toBe('echo vigia');
 // Test once
 (root.querySelector('[data-monitor-test]') as HTMLButtonElement).click();await flush();await flush();
 expect(calls).toContain('run');
 // Removing returns the editor back
 (root.querySelector('[data-monitor-remove]') as HTMLButtonElement).click();await flush();await flush();
 expect(calls).toContain('remove');
 expect(root.querySelector('[data-monitor-edit]')).not.toBeNull();
});

it('notepad flow: save a durable note and remove it',async()=>{
 let job:any={id:'j',name:'Rotina',prompt:'Aja',schedule:{kind:'interval',minutes:5},next_run_at:'2026-09-12T12:00:00Z'};
 let notes:any[]=[];
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
  if(url.endsWith('/status'))return json({running:true,last_tick:null});
  if(url.includes('/notepad')&&init?.method==='PUT'){const key=decodeURIComponent(url.split('/notepad/')[1]!);const value=JSON.parse(String(init.body)).value;calls.push('set:'+key+'='+value);notes=[...notes.filter(n=>n.key!==key),{job_id:'j',key,value,updated_at:'2026-09-12T12:00:00Z'}];return json(notes[notes.length-1]);}
  if(url.includes('/notepad')&&init?.method==='DELETE'){const key=decodeURIComponent(url.split('/notepad/')[1]!);calls.push('delete:'+key);notes=notes.filter(n=>n.key!==key);return json({job_id:'j',cleared:true});}
  if(url.includes('/notepad'))return json({job_id:'j',notes});
  return json({jobs:[job]});
 }));
 const root=document.createElement('main');
 await cronView(root,{});
 expect(root.textContent).toContain('Bloco de notas persistente (0)');
 (root.querySelector('[data-note-key-input]') as HTMLInputElement).value='cursor';
 (root.querySelector('[data-note-value-input]') as HTMLInputElement).value='42';
 (root.querySelector('[data-note-save]') as HTMLButtonElement).click();await flush();await flush();
 expect(calls).toContain('set:cursor=42');
 expect(root.textContent).toContain('Bloco de notas persistente (1)');
 expect(root.textContent).toContain('cursor');
(root.querySelector('[data-note-remove]') as HTMLButtonElement).click();await flush();await flush();
  expect(calls).toContain('delete:cursor');
  expect(root.textContent).toContain('Bloco de notas persistente (0)');
});

it('blueprint catalog renders ready-made automations and fills the slot form into POST jobs',async()=>{
 const blueprint={key:'mail-check',title:'Monitor de e-mail importante',category:'email',description:'Avisa só o que importa.',scheduleHuman:'a cada 30 minutos',command:'/blueprint mail-check interval_min=30',fields:[
  {name:'interval_min',type:'enum',label:'De quanto em quanto?',default:'30',options:['15','30','60'],optional:false,strict:true,help:'minutos',},
  {name:'criteria',type:'text',label:'Só me avise se…',default:'precisa de resposta hoje',optional:false,strict:false,help:'',options:[]},
  {name:'deliver',type:'text',label:'Onde entregar?',default:'local',optional:true,strict:false,help:'',options:[]},
 ]};
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
  if(url.endsWith('/status'))return json({running:true,last_tick:null});
  if(url.endsWith('/blueprints'))return json({blueprints:[blueprint]});
  if(url.endsWith('/delivery-targets'))return json({targets:[{id:'local',name:'Local (só grava)'}]});
  if(init.method==='POST'&&url.includes('/blueprints/')){
   const body=JSON.parse(String(init.body));writes.push(body);
   return json({id:'bp',name:'Blueprint',prompt:'Aja',enabled:true,paused:false,schedule:{kind:'interval',minutes:30}});
  }
  return json({jobs:[]});
 }));
 const root=document.createElement('main');
 await cronView(root,{});
 await flush();await flush();
 expect(root.querySelector('[data-blueprint-grid]')!.textContent).toContain('Monitor de e-mail importante');
 (root.querySelector('[data-blueprint-use="mail-check"]') as HTMLButtonElement).click();
 const form=root.querySelector('[data-blueprint-form-inner]') as HTMLFormElement;
 expect(form.querySelector('[name="bp-interval_min"]')).not.toBeNull();
 expect((form.querySelector('[name="bp-interval_min"]') as HTMLSelectElement).value).toBe('30');
 (form.querySelector('[name="bp-criteria"]') as HTMLInputElement).value='menciona prazo';
 (form.querySelector('[name="bp-deliver"]') as HTMLInputElement).value='wpp:+5511999990000';
 (form.querySelector('[name="bp-interval_min"]') as HTMLSelectElement).value='15';
 form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await flush();await flush();
 expect(writes).toEqual([{values:{interval_min:'15',criteria:'menciona prazo',deliver:'wpp:+5511999990000'}}]);
});

it('delivery: manual create sends plataforma:destino, local is omitted and datalist comes from adapters',async()=>{
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
  if(url.endsWith('/status'))return json({running:true,last_tick:null});
  if(url.endsWith('/blueprints'))return json({blueprints:[]});
  if(url.endsWith('/delivery-targets'))return json({targets:[{id:'wpp',name:'WhatsApp'}]});
  if(init.method==='POST'){const body=JSON.parse(String(init.body));writes.push(body);return json({...body,id:'j',enabled:true,paused:false,next_run_at:'2026-09-12T12:00:00Z'});}
  return json({jobs:[]});
 }));
 const root=document.createElement('main');
 await cronView(root,{});
 await flush();await flush();
 expect([...(root.querySelector('datalist#delivery-targets') as HTMLDataListElement).options].map(o=>o.value)).toEqual(['wpp']);
 (root.querySelector('[name=name]') as HTMLInputElement).value='Vigia';
 (root.querySelector('[name=prompt]') as HTMLInputElement).value='Aja';
 (root.querySelector('[name=kind]') as HTMLSelectElement).value='interval';
 root.querySelector('[name=kind]')!.dispatchEvent(new Event('change'));
 (root.querySelector('[name=minutes]') as HTMLInputElement).value='15';
 (root.querySelector('[name=deliver]') as HTMLInputElement).value='wpp:+5511999990000';
 root.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await flush();await flush();
 expect(writes[0]!.delivery).toEqual({target:'wpp:+5511999990000'});
 (root.querySelector('[name=deliver]') as HTMLInputElement).value='';
 root.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await flush();await flush();
 expect(writes[1]!.delivery).toBeUndefined();
});
