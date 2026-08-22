/**
 * Retries a request to this service. The timeout is an end-to-end request
 * deadline; browser fetch cannot isolate TCP connection time from response time.
 */
export async function resilientFetch(url, options = {}) {
  const {
    maxRetries = 3,
    requestTimeoutMs = 30_000,
    ...fetchOptions
  } = options;

  let lastError;

  for (let attempt = 1; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), requestTimeoutMs);

    try {
      const response = await fetch(url, {
        ...fetchOptions,
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Request failed with HTTP ${response.status}`);
      }

      return response;
    } catch (error) {
      if (controller.signal.aborted) {
        lastError = new Error("RequestTimeoutError", { cause: error });
        lastError.name = "RequestTimeoutError";
      } else {
        lastError = error;
      }

      if (attempt === maxRetries) {
        throw lastError;
      }
    } finally {
      // Runs after success, abort, network failure, or an HTTP error.
      clearTimeout(timeoutId);
    }
  }

  throw lastError;
}
