// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
// @ts-expect-error Native SPA is plain JavaScript.
import { ajustesView } from '../../../kairos_web/ui/js/views/ajustes.js';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {status});
const flush = () => new Promise(r => setTimeout(r, 0));
afterEach(() => vi.unstubAllGlobals());

it('saves numeric defaults and explicit unset, then reloads persisted settings', async () => {
  let state: any = {generation: {temperature: 0.3, max_tokens: 256}};
  const saves: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === 'PUT') {state=JSON.parse(String(init.body)); saves.push(state);}
    return json(state);
  }));
  const root = document.createElement('main');
  await ajustesView(root, {});
  const form = root.querySelector('form')!;
  const temp = form.elements.namedItem('temperature') as HTMLInputElement;
  const max = form.elements.namedItem('max_tokens') as HTMLInputElement;
  expect(temp.value).toBe('0.3');
  temp.value='0.7'; max.value='';
  form.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}));
  await flush();
  expect(saves).toEqual([{generation:{temperature:0.7,max_tokens:null}}]);
  expect(root.textContent).toContain('Preferências salvas');
  await ajustesView(root, {});
  expect((root.querySelector('[name=temperature]') as HTMLInputElement).value).toBe('0.7');
});

it('shows save errors and does not update a disposed view', async () => {
  let finish!: (r: Response) => void;
  vi.stubGlobal('fetch',vi.fn(async (_url: string, init: RequestInit) =>
    init.method === 'PUT' ? new Promise<Response>(r => {finish=r;}) : json({generation:{}})));
  const root=document.createElement('main');
  const dispose=await ajustesView(root,{});
  root.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  await flush();
  dispose();
  root.innerHTML='Outra tela';
  finish(json({detail:'invalid'},422));
  await flush();
  expect(root.innerHTML).toBe('Outra tela');
});
