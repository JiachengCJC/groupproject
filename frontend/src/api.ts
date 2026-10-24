export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'The request could not be completed. Please try again.';
}
export async function api<T>(path: string, body?: unknown, options: RequestInit = {}): Promise<T> {
  const isForm = body instanceof FormData;
  const response = await fetch('/api' + path, {
    credentials: 'same-origin',
    ...options,
    method: options.method ?? (body === undefined ? 'GET' : 'POST'),
    headers: {
      ...(body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    ...(body !== undefined ? { body: isForm ? body : JSON.stringify(body) } : {}),
  });
  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/'))
      window.dispatchEvent(new Event('session-expired'));
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail
          .map(
            (item: { loc?: string[]; msg?: string }) =>
              `${item.loc?.at(-1) ?? 'Input'}: ${item.msg}`,
          )
          .join('; ')
      : typeof detail === 'string'
        ? detail
        : `Request failed (${response.status}). Please retry or check the service status.`;
    throw new ApiError(message, response.status);
  }
  return data as T;
}
