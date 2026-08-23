/*
** fts5_cjk.c — tokenizador FTS5 "cjk_unicode61": unicode61 + bigramas CJK.
**
** POR QUÊ. O unicode61 do SQLite trata uma sequência CJK como UM token
** ("웅기가말했다" indexa como um único token de 6 caracteres), de modo que
** uma consulta coreana de 2 caracteres nunca casa dentro dele. O tokenizador
** trigram resolve busca por substring mas exige >=3 caracteres por termo —
** e palavras coreanas de 2 caracteres (일본, 구글, 우리, …) caem em varredura
** completa por LIKE, medida em 3-6 s por consulta numa tabela de mensagens de
** 6,8 GB, e o principal fator de latência da busca de sessões.
**
** O QUÊ. Envolve o unicode61. Todo token que ele emite é reexaminado;
** sequências CJK maximais dentro do token são reemitidas como BIGRAMAS de
** caractere sobrepostos (semântica do CJKAnalyzer do Lucene), e segmentos
** não-CJK passam intactos. Um caractere CJK isolado sai como unigrama.
** Como o FTS5 transforma tokens consecutivos vindos de um mesmo termo de
** consulta numa frase, uma palavra como 캘린더 → [캘린][린더] ganha semântica
** exata de substring com velocidade de índice, até termos de 2 caracteres.
**
** Build:  gcc -shared -fPIC -O2 fts5_cjk.c -o libfts5_cjk.so
** Load:   conn.load_extension(path)   # entrypoint sqlite3_ftscjk_init
** Uso:    CREATE VIRTUAL TABLE t USING fts5(c, tokenize='cjk_unicode61');
**         Argumentos extras passam adiante para o unicode61:
**         tokenize='cjk_unicode61 remove_diacritics 2'
**
** Reconstruído para o Kairos a partir de _reversa_sdd/native/ (Tarefa 04).
** A spec marcava 🟡 "faixas exatas de codepoint não extraídas"; foram lidas
** da fonte e estão abaixo.
*/
#include <sqlite3ext.h>
SQLITE_EXTENSION_INIT1

#include <string.h>
#include <stdlib.h>

/* ── Classificação CJK ───────────────────────────────────────────────────
**
** 12 blocos. Hangul aparece cinco vezes porque o coreano moderno usa
** sílabas pré-compostas (AC00..D7A3) enquanto texto decomposto, teclados e
** dados legados usam Jamo — tratar só as sílabas deixaria de fora
** exatamente o material mais irregular.
*/
static int cjk_is_cjk(unsigned int cp) {
  return (cp >= 0xAC00 && cp <= 0xD7A3)      /* sílabas Hangul            */
      || (cp >= 0x1100 && cp <= 0x11FF)      /* Hangul Jamo               */
      || (cp >= 0x3130 && cp <= 0x318F)      /* Hangul Jamo compat        */
      || (cp >= 0xA960 && cp <= 0xA97F)      /* Hangul Jamo ext-A         */
      || (cp >= 0xD7B0 && cp <= 0xD7FF)      /* Hangul Jamo ext-B         */
      || (cp >= 0x4E00 && cp <= 0x9FFF)      /* ideogramas CJK unificados */
      || (cp >= 0x3400 && cp <= 0x4DBF)      /* CJK ext A                 */
      || (cp >= 0xF900 && cp <= 0xFAFF)      /* ideogramas CJK compat     */
      || (cp >= 0x20000 && cp <= 0x2FA1F)    /* CJK ext B..F, compat sup  */
      || (cp >= 0x3040 && cp <= 0x309F)      /* Hiragana                  */
      || (cp >= 0x30A0 && cp <= 0x30FF)      /* Katakana                  */
      || (cp >= 0x31F0 && cp <= 0x31FF);     /* Katakana fonético ext     */
}

/* Decodifica um codepoint UTF-8 em p (n bytes disponíveis). Devolve o
** número de bytes consumidos (>=1) e grava o codepoint em *pCp. Bytes
** inválidos decodificam como si mesmos, para que a segmentação sempre
** termine — um erro de encoding degrada a tokenização, nunca trava. */
static int cjk_utf8_decode(const unsigned char *p, int n, unsigned int *pCp) {
  unsigned int c = p[0];
  if (c < 0x80) { *pCp = c; return 1; }
  if ((c & 0xE0) == 0xC0 && n >= 2) {
    *pCp = ((c & 0x1F) << 6) | (p[1] & 0x3F);
    return 2;
  }
  if ((c & 0xF0) == 0xE0 && n >= 3) {
    *pCp = ((c & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F);
    return 3;
  }
  if ((c & 0xF8) == 0xF0 && n >= 4) {
    *pCp = ((c & 0x07) << 18) | ((p[1] & 0x3F) << 12) |
           ((p[2] & 0x3F) << 6) | (p[3] & 0x3F);
    return 4;
  }
  *pCp = c;
  return 1;
}

/* ── Encanamento do tokenizador ─────────────────────────────────────────── */

typedef struct CjkTokenizer CjkTokenizer;
struct CjkTokenizer {
  fts5_tokenizer inner;      /* métodos do unicode61   */
  Fts5Tokenizer *pInner;     /* instância do unicode61 */
};

typedef struct CjkCallbackCtx CjkCallbackCtx;
struct CjkCallbackCtx {
  void *pOuterCtx;
  int (*xOuterToken)(void*, int, const char*, int, int, int);
};

/* Reemite um token do unicode61, quebrando sequências CJK em bigramas.
**
** OFFSETS. O unicode61 informa [iStart,iEnd) no texto ORIGINAL. Para bytes
** CJK a dobra do unicode61 é a identidade, e a dobra de caixa ASCII preserva
** o comprimento em bytes — então mapear offsets de subtoken por posição de
** byte dentro do token é exato para CJK e de comprimento correto para ASCII.
** Para dobras raras que mudam o comprimento (latim acentuado) os offsets de
** destaque podem deslizar alguns bytes dentro daquele token; o casamento não
** é afetado. Todo offset emitido é limitado a [iStart,iEnd).
*/
static int cjk_emit(CjkCallbackCtx *p, int tflags,
                    const char *pToken, int nToken, int iStart, int iEnd) {
  const unsigned char *z = (const unsigned char*)pToken;
  int i = 0;
  int rc = SQLITE_OK;

  /* Caminho rápido: nenhum CJK → passa intacto. A varredura extra se paga
  ** porque a esmagadora maioria dos tokens é latina. */
  int hasCjk = 0;
  while (i < nToken) {
    unsigned int cp;
    i += cjk_utf8_decode(z + i, nToken - i, &cp);
    if (cjk_is_cjk(cp)) { hasCjk = 1; break; }
  }
  if (!hasCjk) {
    return p->xOuterToken(p->pOuterCtx, tflags, pToken, nToken, iStart, iEnd);
  }

#define CJK_CLAMP_END(v) ((iStart + (v)) > iEnd ? iEnd : (iStart + (v)))
  i = 0;
  while (i < nToken && rc == SQLITE_OK) {
    unsigned int cp;
    int segStart = i;
    int len = cjk_utf8_decode(z + i, nToken - i, &cp);
    if (!cjk_is_cjk(cp)) {
      /* segmento não-CJK: estende até o próximo caractere CJK (ou o fim) */
      i += len;
      while (i < nToken) {
        int l2 = cjk_utf8_decode(z + i, nToken - i, &cp);
        if (cjk_is_cjk(cp)) break;
        i += l2;
      }
      rc = p->xOuterToken(p->pOuterCtx, tflags,
                          pToken + segStart, i - segStart,
                          CJK_CLAMP_END(segStart), CJK_CLAMP_END(i));
    } else {
      /* sequência CJK: janela deslizante de três fronteiras de byte. */
      int bounds[3];               /* início, meio, fim */
      bounds[0] = segStart;
      bounds[1] = segStart + len;
      i += len;
      int nChars = 1;
      while (i < nToken) {
        int l2 = cjk_utf8_decode(z + i, nToken - i, &cp);
        if (!cjk_is_cjk(cp)) break;
        i += l2;
        nChars++;
        if (nChars >= 2) {
          bounds[2] = i;
          rc = p->xOuterToken(p->pOuterCtx, tflags,
                              pToken + bounds[0], bounds[2] - bounds[0],
                              CJK_CLAMP_END(bounds[0]), CJK_CLAMP_END(bounds[2]));
          if (rc != SQLITE_OK) break;
          bounds[0] = bounds[1];
          bounds[1] = bounds[2];
        }
      }
      if (rc == SQLITE_OK && nChars == 1) {
        /* caractere CJK isolado: emite como unigrama. Sem isto, um termo de
        ** um caractere seria inindexável — e não há bigrama a formar. */
        rc = p->xOuterToken(p->pOuterCtx, tflags,
                            pToken + segStart, bounds[1] - segStart,
                            CJK_CLAMP_END(segStart), CJK_CLAMP_END(bounds[1]));
      }
    }
  }
#undef CJK_CLAMP_END
  return rc;
}

static int cjkInnerCallback(void *pCtx, int tflags,
                            const char *pToken, int nToken,
                            int iStart, int iEnd) {
  return cjk_emit((CjkCallbackCtx*)pCtx, tflags, pToken, nToken, iStart, iEnd);
}

static int cjkCreate(void *pApiCtx, const char **azArg, int nArg,
                     Fts5Tokenizer **ppOut) {
  fts5_api *pApi = (fts5_api*)pApiCtx;
  CjkTokenizer *p;
  void *pInnerCtx = 0;
  int rc;

  p = (CjkTokenizer*)sqlite3_malloc(sizeof(CjkTokenizer));
  if (!p) return SQLITE_NOMEM;
  memset(p, 0, sizeof(*p));

  /* Os argumentos extras passam adiante sem interpretação, para que
  ** 'cjk_unicode61 remove_diacritics 2' funcione como no unicode61. */
  rc = pApi->xFindTokenizer(pApi, "unicode61", &pInnerCtx, &p->inner);
  if (rc == SQLITE_OK) {
    rc = p->inner.xCreate(pInnerCtx, azArg, nArg, &p->pInner);
  }
  if (rc != SQLITE_OK) {
    sqlite3_free(p);
    return rc;
  }
  *ppOut = (Fts5Tokenizer*)p;
  return SQLITE_OK;
}

static void cjkDelete(Fts5Tokenizer *pTok) {
  CjkTokenizer *p = (CjkTokenizer*)pTok;
  if (p) {
    if (p->pInner) p->inner.xDelete(p->pInner);
    sqlite3_free(p);
  }
}

static int cjkTokenize(Fts5Tokenizer *pTok, void *pCtx, int flags,
                       const char *pText, int nText,
                       int (*xToken)(void*, int, const char*, int, int, int)) {
  CjkTokenizer *p = (CjkTokenizer*)pTok;
  CjkCallbackCtx cb;
  cb.pOuterCtx = pCtx;
  cb.xOuterToken = xToken;
  return p->inner.xTokenize(p->pInner, &cb, flags, pText, nText,
                            cjkInnerCallback);
}

/* ── Registro ────────────────────────────────────────────────────────────
**
** A API do FTS5 não é exposta por sqlite3ext.h; recupera-se por
** SELECT fts5(?) com um ponteiro amarrado. */
static fts5_api *cjkFts5Api(sqlite3 *db) {
  fts5_api *pRet = 0;
  sqlite3_stmt *pStmt = 0;
  if (sqlite3_prepare_v2(db, "SELECT fts5(?1)", -1, &pStmt, 0) == SQLITE_OK) {
    sqlite3_bind_pointer(pStmt, 1, (void*)&pRet, "fts5_api_ptr", 0);
    sqlite3_step(pStmt);
  }
  sqlite3_finalize(pStmt);
  return pRet;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_ftscjk_init(sqlite3 *db, char **pzErrMsg,
                        const sqlite3_api_routines *pApi) {
  fts5_api *pFts;
  static fts5_tokenizer tok = { cjkCreate, cjkDelete, cjkTokenize };
  SQLITE_EXTENSION_INIT2(pApi);
  (void)pzErrMsg;
  pFts = cjkFts5Api(db);
  if (!pFts) {
    if (pzErrMsg) *pzErrMsg = sqlite3_mprintf("fts5_cjk: FTS5 indisponível");
    return SQLITE_ERROR;
  }
  return pFts->xCreateTokenizer(pFts, "cjk_unicode61", (void*)pFts, &tok, 0);
}

/* Apelido para quem soletra o basename com underscore. O SQLite deriva o
** entrypoint do nome do arquivo, e as duas grafias aparecem na prática. */
#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_fts5_cjk_init(sqlite3 *db, char **pzErrMsg,
                          const sqlite3_api_routines *pApi) {
  return sqlite3_ftscjk_init(db, pzErrMsg, pApi);
}
