# Pinned versions and licences

Updated in P0 (2026-11-20). Exact Python versions are locked in `uv.lock`, web versions in `pnpm-lock.yaml`; this table is the human-readable record required by docs/10 §3 and §6. Licences are as declared by each project; anything marked *check* should be confirmed before a public release.

## Runtime

| Component | Version | Licence | Notes |
|---|---|---|---|
| Python | 3.12 (`python:3.12-slim-bookworm`) | PSF | |
| uv | 0.12.23 (image `ghcr.io/astral-sh/uv:0.12.23`) | MIT / Apache-2.0 | build stage only |
| Postgres + pgvector | `pgvector/pgvector:pg16` (Postgres 16) | PostgreSQL | tag floats within 16; pin by digest before production |
| Caddy | 2.10 (`caddy:2.10-alpine`) | Apache-2.0 | |
| BusyBox | 1.37 (`busybox:1.37`) | GPL-2.0 | one-shot `init` container only; not part of any shipped image |
| Node | 22 (`node:22-slim`, build stage only) | MIT | local dev also works on Node 24 |
| pnpm | 11.18.0 | MIT | pinned by `packageManager` |

## Python packages

| Package | Version | Licence | Notes |
|---|---|---|---|
| fastapi | 0.165.0 | MIT | |
| uvicorn | 0.54.0 | BSD-3-Clause | |
| pydantic | 2.14.4 | MIT | |
| procrastinate | 3.12.0 | MIT | queue on Postgres |
| psycopg / psycopg-pool / psycopg-binary | 3.3.9 / 3.3.3 / 3.3.9 | **LGPL-3.0** | not permissive; used as an unmodified, dynamically imported library (required by procrastinate 3). Flagged per docs/10 §6 for the owner to accept |
| cadquery | 2.8.0 | Apache-2.0 | cad-worker image only |
| cadquery-ocp | 7.9.3.1.1 | Apache-2.0 (bindings); OpenCascade is LGPL-2.1 with exception | cad-worker image only; pulls `vtk` 9.6.2 (BSD) |
| numba / scipy | 0.68.1 / 1.18.1 | BSD-2/3-Clause | pulled in by cadquery |
| openpyxl | 3.1.5 | MIT | Excel reading & writing (rules & fixtures) |
| pdfplumber | 0.11.4 | MIT | PDF datasheet text and table extraction |
| litellm | 1.63.0 | MIT | LLM router across Groq and Gemini free tiers |
| langgraph | 0.3.0 | MIT | Conversational chat agent graph |
| weasyprint | 70.0 | BSD-3-Clause | PDF parts list (P7). Needs pango, harfbuzz and a font as **system** libraries in the API image: `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b` (LGPL-2.1, dynamically linked, unmodified; flagged for the owner like psycopg) and `fonts-dejavu-core` (Bitstream Vera / public domain). Pulls pydyf, tinycss2, tinyhtml5, cssselect2, pyphen, brotli, zopfli (all BSD/MIT/MPL) |
| hatchling | latest at build | MIT | build backend |
| ruff / mypy / httpx | 0.17.6 / 2.4.1 / 0.28.1 | MIT / MIT / BSD-3-Clause | dev only |

## Web packages

| Package | Version | Licence |
|---|---|---|
| react / react-dom | 19.3.1 | MIT |
| react-router | 8.5.2 | MIT |
| @radix-ui/react-dropdown-menu | 2.2.1 | MIT |
| @radix-ui/react-dialog / @radix-ui/react-tabs | 1.3.1 / 1.2.1 | MIT |
| @tanstack/react-table | 8.21.x (**not 9**: v9 changes the API; v8 supports React 19) | MIT |
| @tanstack/react-virtual | 3.14.x | MIT |
| three / @types/three | 0.187.x | MIT |
| @react-three/fiber | 9.8.x (React 19) | MIT |
| vite / @vitejs/plugin-react | 8.3.8 / 6.3.0 | MIT |
| typescript | 6.0.x (**not 7**: typescript-eslint does not support TS 7 yet) | Apache-2.0 |
| eslint / typescript-eslint / eslint-plugin-jsx-a11y / eslint-plugin-react-hooks | 10.15.0 / 8.73.0 / 6.10.2 / 7.1.1 | MIT |
| json-schema-to-typescript | 16.x | MIT |
