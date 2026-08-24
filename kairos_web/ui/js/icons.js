/* Ícones do Kairos.
 *
 * Desenhados na mesma grade do símbolo da marca: 24, traço 1.75, terminais
 * arredondados, `currentColor`. Traço uniforme é o que faz um conjunto de
 * ícones parecer um conjunto — misturar espessuras é o que denuncia origens
 * diferentes. Este conjunto é desenhado aqui, do zero.
 */

const svg = (corpo) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${corpo}</svg>`;

export const icons = {
  // painel: quatro áreas, a maior em destaque
  dashboard: svg('<rect x="3" y="3" width="7.5" height="9" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="5" rx="1.5"/><rect x="13.5" y="11" width="7.5" height="10" rx="1.5"/><rect x="3" y="15" width="7.5" height="6" rx="1.5"/>'),
  // skills: um bloco de capacidade com faísca — o que a máquina passa a saber
  skills: svg('<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H17v16H6.5A2.5 2.5 0 0 1 4 17.5z"/><path d="M17 4v16"/><path d="m10 9.5 1.1 2.3 2.4.3-1.8 1.7.5 2.4-2.2-1.2-2.2 1.2.5-2.4L6.5 12l2.4-.3z"/>'),
  sessions: svg('<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.7 8.7 0 0 1-3.8-.9L3 20.5l1.5-5.1A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>'),
  models: svg('<circle cx="12" cy="12" r="2.5"/><circle cx="5" cy="6" r="1.8"/><circle cx="19" cy="6" r="1.8"/><circle cx="5" cy="18" r="1.8"/><circle cx="19" cy="18" r="1.8"/><path d="m6.5 7 3.6 3.4M17.5 7l-3.6 3.4M6.5 17l3.6-3.4M17.5 17l-3.6-3.4"/>'),
  tools: svg('<path d="M14.5 6.2a3.8 3.8 0 0 0 5 5L15 15.7l-2.6-2.6z"/><path d="m11 12-6.5 6.5a1.8 1.8 0 0 0 2.5 2.5L13.5 15"/>'),
  providers: svg('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
  config: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 14a1.5 1.5 0 0 0 .3 1.7l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.5 1.5 0 0 0-2.5 1v.2a2 2 0 1 1-4 0V19a1.5 1.5 0 0 0-2.6-1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.5 1.5 0 0 0 4 12.7H3.8a2 2 0 1 1 0-4H4a1.5 1.5 0 0 0 1-2.6l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.5 1.5 0 0 0 2.6-1V2a2 2 0 1 1 4 0v.2a1.5 1.5 0 0 0 2.5 1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.5 1.5 0 0 0 1 2.5h.2a2 2 0 1 1 0 4H21a1.5 1.5 0 0 0-1.4 1z"/>'),
  search: svg('<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.9-4.9"/>'),
  menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  sun: svg('<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>'),
  moon: svg('<path d="M20 14.2A8.4 8.4 0 0 1 9.8 4 8.5 8.5 0 1 0 20 14.2z"/>'),
  check: svg('<path d="m4.5 12.5 5 5 10-11"/>'),
  save: svg('<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h7V3M8 21v-7h8v7"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  refresh: svg('<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v4.5h-4.5"/>'),
};
