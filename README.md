# HIPR (Hardened Isolated Proxy Runtime)

A fault-tolerant, asynchronous HTTP proxy and outbound security runtime built with FastAPI and HTTPX. Designed to eliminate thread starvation, mitigate cascading retry storms, and enforce defense-in-depth Server-Side Request Forgery (SSRF) protections on outbound upstream calls.

---

## Key Architectural Highlights

* **SSRF Quarantine & Anti-DNS-Rebinding:** Pre-flight DNS resolution blocks loopback (`127.0.0.0/8`), link-local metadata endpoints (`169.254.0.0/16`), and unapproved private subnets while safely accommodating explicitly allowlisted LAN targets.
* **Phase-Decoupled Timeouts:** Granular connection timers (`connect=3.0s`, `read=15.0s`) fail fast against dropped network hops without terminating valid streaming responses.
* **Exponential Backoff with Full Jitter:** Mathematical retry decorrelation prevents the "thundering herd" problem during upstream recovery cycles.
* **Persistent Connection Pooling:** Managed under a FastAPI `lifespan` context manager to eliminate ephemeral port exhaustion.
* **Defensive Stream Ingestion:** Strict 1 MiB response size ceiling to prevent memory exhaustion, with graceful fallback from JSON to raw text encapsulation.

## Testing Resilience

Public JSON (Success): https://jsonplaceholder.typicode.com/todos/1

Local LAN Monitoring: Approved devices like http://192.168.1.1

SSRF Protection Check: Attempting to query http://127.0.0.1:8000 or cloud metadata http://169.254.169.254 triggers an immediate 403 Forbidden.

Dropped Connection Handling: Unreachable allowlisted hosts automatically trigger 3 jittered retries before returning a 504 Gateway Timeout

---

## Quick Start

### 1. Clone & Setup Virtual Environment
```bash
git clone [https://github.com/](https://github.com/)<your-username>/HIPR.git
cd HIPR
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

