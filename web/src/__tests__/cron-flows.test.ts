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
