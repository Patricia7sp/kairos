/**
 * Estado de conexão e o invariante 12.
 *
 * "Após qualquer swap, socket ativo + perfil ativo + átomos de conexão
 * CONCORDAM."
 */

export interface ConnectionAtoms {
  socketProfile: string | null;
  activeProfile: string | null;
  connectionId: string | null;
}

export class ConnectionDivergence extends Error {}

/**
 * Verifica a concordância dos três átomos.
 *
 * A falha que isto cobre é silenciosa e cara: um swap de perfil que atualiza
 * dois dos três deixa o app mostrando dados do perfil A enquanto escreve no
 * perfil B — e nada na tela denuncia.
 */
export function assertAtomsAgree(atoms: ConnectionAtoms): void {
  const { socketProfile, activeProfile, connectionId } = atoms;

  if (socketProfile === null && activeProfile === null && connectionId === null) {
    return; // desconectado: estado coerente
  }

  if (socketProfile !== activeProfile) {
    throw new ConnectionDivergence(
      `socket aponta para ${socketProfile}, perfil ativo é ${activeProfile}: ` +
        `o app mostraria dados de um e escreveria no outro`,
    );
  }
  if (connectionId === null) {
    throw new ConnectionDivergence(
      `perfil ${activeProfile} ativo sem connectionId: swap incompleto`,
    );
  }
}

export function swapProfile(_atoms: ConnectionAtoms, profile: string, connectionId: string): ConnectionAtoms {
  // Os três mudam JUNTOS. Atualizar em etapas é o que cria a janela de
  // divergência.
  return { socketProfile: profile, activeProfile: profile, connectionId };
}

/**
 * Durabilidade de shutdown é do BACKEND (decisão G-18).
 *
 * O supervisor desktop **não** ganha temporizador de graça. Se um prazo for
 * necessário no futuro, ele deve vir de sinal de prontidão do backend — um
 * número fixo no Electron seria adivinhação sobre um processo que ele não
 * controla.
 */
export const DESKTOP_HAS_NO_GRACE_TIMER = true;
