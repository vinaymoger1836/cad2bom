import { useState, type SubmitEvent as ReactSubmitEvent } from "react";
import { useNavigate } from "react-router";
import type { JobCreate } from "@c2b/ts-types";
import { Qa, WhatHappens } from "../components/Help";
import { ApiError, createJob } from "../lib/api";
import { saveToken } from "../lib/storage";

const PRODUCT_TYPES = [
  "Ceiling fan",
  "Power bank",
  "Bracket or frame assembly",
  "Home appliance",
  "Furniture",
  "Hand or power tool",
  "Electronics enclosure",
  "Small machine",
];

export function NewBom() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: ReactSubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (k: string): string => {
      const v = form.get(k);
      return typeof v === "string" ? v.trim() : "";
    };
    const body: JobCreate = { name: text("name") };
    if (text("description")) body.description = text("description");
    if (text("product_type")) body.product_type = text("product_type");
    if (text("annual_volume")) body.annual_volume = Number(text("annual_volume"));
    if (text("variants")) body.variants = text("variants");

    setBusy(true);
    setError(null);
    try {
      const job = await createJob(body);
      saveToken(job.id, job.token);
      void navigate({ pathname: `/j/${job.id}`, hash: `#${job.token}` });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the BoM. Please try again.");
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Start a new bill of materials</h1>
      <WhatHappens>
        <p>
          Tell us what the product is. We create a private job for it and give you a link. Next
          you add your files (CAD, photos, an existing parts list), and we build a draft BoM
          that you review before anything is final.
        </p>
      </WhatHappens>

      <form className="card form" onSubmit={(e) => void onSubmit(e)} noValidate={false}>
        <div className="field">
          <label htmlFor="name">Product name</label>
          <input id="name" name="name" required maxLength={200} autoComplete="off" />
        </div>
        <div className="field">
          <label htmlFor="description">Short description (optional)</label>
          <textarea id="description" name="description" rows={3} maxLength={4000} />
          <p className="hint">What it does and roughly how it is built. This helps with the questions we ask later.</p>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="product_type">Product type (optional)</label>
            <input id="product_type" name="product_type" list="product-types" maxLength={120} autoComplete="off" />
            <datalist id="product-types">
              {PRODUCT_TYPES.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
            <p className="hint">Pick one or type your own.</p>
          </div>
          <div className="field">
            <label htmlFor="annual_volume">Units per year (optional)</label>
            <input id="annual_volume" name="annual_volume" type="number" min={0} step={1} inputMode="numeric" />
            <p className="hint">Used later for make-or-buy advice.</p>
          </div>
        </div>
        <div className="field">
          <label htmlFor="variants">Variants (optional)</label>
          <textarea id="variants" name="variants" rows={2} maxLength={2000} />
          <p className="hint">For example: “white and black, 3 or 5 blades”.</p>
        </div>

        {error ? (
          <p role="alert" className="alert alert-danger">
            {error}
          </p>
        ) : null}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Creating…" : "Create and continue"}
          </button>
        </div>
      </form>

      <Qa
        items={[
          {
            q: "Do I need an account?",
            a: "No. Each job has a private link that works like a key. Anyone with the full link can open the job, so keep it safe.",
          },
          {
            q: "Which files can I add next?",
            a: "STEP files for the 3D model, photos (JPEG, PNG, WebP), parts lists (Excel or CSV) and PDF datasheets. SolidWorks (.sldprt, .sldasm) and Fusion (.f3d) files can be converted by Autodesk when this server has that set up and you agree for the job; otherwise export them as STEP first (AP242 gives the best result).",
          },
        ]}
      />
    </div>
  );
}
