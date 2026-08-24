# Marca Kairos

## O símbolo

Um **K** monolinha: haste vertical, dois braços a 45°, e um vão entre eles.

O vão é a assinatura. Sem ele o desenho lê como o botão *faixa anterior* de um
player (`|◀`); com ele, lê como K. O braço ascendente é o único elemento em cor
de acento — *kairós*, em grego, é o instante oportuno, e a diagonal que sobe é a
janela que se abre. O braço descendente fica em tinta, para que a leitura da
letra não dependa de cor: em monocromático o K continua K.

Traço de espessura única (8 em 64) e terminais arredondados. Espessura única é
o que impede o desenho de engordar ao reduzir — verificado pixel a pixel em
16 px, onde a haste ocupa 2 px e os braços continuam separados.

## Arquivos

| arquivo | uso |
|---|---|
| `kairos-logo-light.svg` | composição completa, fundo claro — cabeçalho, README, institucional |
| `kairos-logo-dark.svg` | composição completa, fundo escuro |
| `kairos-symbol-light.svg` / `-dark.svg` | símbolo isolado, quando o nome já está no contexto |
| `kairos-symbol-mono.svg` | símbolo em `currentColor` — herda a cor do texto ao redor |
| `kairos-app-icon.svg` | símbolo sobre placa — ícone de aplicativo, avatar, atalho |
| `favicon.svg` / `favicon.ico` | aba do navegador (o `.ico` traz 16→256 px) |
| `kairos-cover-light.svg` / `-dark.svg` | capa 1200×630 — tela de entrada, README, card social |

O favicon usa a placa, não o símbolo solto: um símbolo sem fundo desaparece em
um dos dois temas de aba, e não há como saber qual o leitor usa.

## Cores

| papel | claro | escuro |
|---|---|---|
| tinta | `#0F172A` | `#F8FAFC` |
| acento | `#06B6D4` | `#22D3EE` |
| placa | `#0F172A` | `#0F172A` |

O acento muda entre temas de propósito: `#06B6D4` sobre fundo escuro perde
luminância e o braço some; `#22D3EE` sobre branco fica lavado.

## Uso

- **Margem livre**: pelo menos a largura da haste (8 unidades de 64) em volta.
- **Tamanho mínimo**: 16 px para o símbolo, 96 px de largura para a composição —
  abaixo disso o wordmark fecha os contra-formas e vira mancha.
- **Não** recolorir o braço ascendente para fora da paleta, não adicionar
  contorno, não inclinar, não preencher o vão.

## Tipografia

Wordmark em **Space Grotesk** 600, convertida em contornos — não há dependência
de fonte instalada no leitor. Licença SIL OFL 1.1 em `OFL-SpaceGrotesk.txt`,
que permite uso comercial e redistribuição.

Regenerar (após mudar o texto ou o peso) exige `fonttools`; o procedimento está
no histórico de `brand/` e usa `SVGPathPen` sobre a instância variável.

## A capa

1200×630 — a proporção que serve tanto à tela de entrada quanto ao card de
link. Duas versões, clara e escura, escolhidas por `prefers-color-scheme`.

As penas em leque são a **talaria**, a sandália alada de Kairós. Elas, e não
um peixe, são a iconografia do próprio Kairós: asas nos pés pela velocidade
do instante, e a balança em fio de navalha pela precisão de julgá-lo. O peixe
pertence a outras tradições — o ichthys cristão, o cortejo de Poseidon — e
diria menos sobre o nome do projeto.

Cada pena termina sempre à direita de onde começa. Quando a ponta cruza a
origem as curvas se invertem e nasce um `V` no meio da asa; o desenho tem uma
asserção para isso, porque o defeito é discreto e sobrevive a uma olhada.

A linha sob o logo é o fio: o instante é estreito, e é onde a marca se apoia.

O texto — wordmark e tagline — é contorno, não `<text>`. Um `<text>` depende
de a fonte existir em quem renderiza; onde não existe, sai uma fileira de
caixas vazias. Foi o que aconteceu no primeiro corte desta capa.
