/**
 * Lógica do `AutoField` — formulário derivado do schema.
 *
 * Os plugins declaram `config_schema` e o dashboard renderiza sem componente
 * bespoke por provedor. Sem isso, cada plugin novo exigiria código de
 * frontend, e a extensibilidade pararia na primeira pessoa que não escreve
 * React.
 *
 * 🔧 A spec anterior listava `string`, `int` e `secret`; os tipos reais são
 * `text`, `number` e existe `list`.
 */

export type FieldType = "boolean" | "select" | "number" | "text" | "list";

export const FIELD_TYPES: readonly FieldType[] = [
  "boolean",
  "select",
  "number",
  "text",
  "list",
] as const;

export interface FieldSchema {
  key: string;
  type: FieldType;
  label?: string;
  options?: string[];
  min?: number;
  max?: number;
  required?: boolean;
  /**
   * Mascaramento de segredo. A spec registra que ele NÃO foi localizado no
   * componente do legado; aqui é explícito, porque um campo de chave de API
   * renderizado em texto claro no dashboard é vazamento por
   * compartilhamento de tela.
   */
  secret?: boolean;
}

export type FieldValue = boolean | number | string | string[] | null;

export function resolveFieldType(schema: FieldSchema): FieldType {
  return FIELD_TYPES.includes(schema.type) ? schema.type : "text";
}

export interface ValidationError {
  key: string;
  message: string;
}

export function validateField(schema: FieldSchema, value: FieldValue): ValidationError | null {
  const vazio = value === null || value === "" ||
    (Array.isArray(value) && value.length === 0);

  if (schema.required && vazio) {
    return { key: schema.key, message: `${schema.label ?? schema.key} é obrigatório` };
  }
  if (vazio) return null;

  switch (resolveFieldType(schema)) {
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(n)) {
        return { key: schema.key, message: "deve ser um número" };
      }
      if (schema.min !== undefined && n < schema.min) {
        return { key: schema.key, message: `mínimo ${schema.min}` };
      }
      if (schema.max !== undefined && n > schema.max) {
        return { key: schema.key, message: `máximo ${schema.max}` };
      }
      return null;
    }
    case "select":
      if (schema.options && !schema.options.includes(String(value))) {
        return { key: schema.key, message: `valor fora das opções` };
      }
      return null;
    case "boolean":
      if (typeof value !== "boolean") {
        return { key: schema.key, message: "deve ser booleano" };
      }
      return null;
    case "list":
      if (!Array.isArray(value)) {
        return { key: schema.key, message: "deve ser uma lista" };
      }
      return null;
    default:
      return null;
  }
}

/** Mascara valores de campo marcado como segredo, para exibição. */
export function displayValue(schema: FieldSchema, value: FieldValue): string {
  if (value === null || value === undefined) return "";
  if (schema.secret && typeof value === "string" && value.length > 0) {
    return "•".repeat(Math.min(value.length, 12));
  }
  return Array.isArray(value) ? value.join(", ") : String(value);
}
