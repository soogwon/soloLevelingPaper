export type ErrorCode =
  | "INVALID_INPUT"
  | "PAPER_NOT_FOUND"
  | "AMBIGUOUS_PAPER"
  | "PROVIDER_AUTH"
  | "PROVIDER_RATE_LIMIT"
  | "PROVIDER_TIMEOUT"
  | "PROVIDER_RESPONSE_INVALID"
  | "PATH_NOT_FOUND"
  | "BUDGET_EXCEEDED"
  | "INTERNAL_ERROR";

export class AppError extends Error {
  public readonly code: ErrorCode;
  public readonly retryable: boolean;
  public readonly details?: Record<string, unknown>;

  public constructor(
    code: ErrorCode,
    message: string,
    options: { cause?: unknown; retryable?: boolean; details?: Record<string, unknown> } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = "AppError";
    this.code = code;
    this.retryable = options.retryable ?? false;
    if (options.details) this.details = options.details;
  }
}

export const toPublicError = (error: unknown): { code: ErrorCode; message: string; retryable: boolean; details?: Record<string, unknown> } => {
  if (error instanceof AppError) {
    return {
      code: error.code,
      message: error.message,
      retryable: error.retryable,
      ...(error.details ? { details: error.details } : {}),
    };
  }
  return { code: "INTERNAL_ERROR", message: "예기치 않은 내부 오류가 발생했습니다.", retryable: false };
};
